// content.js -- runs in the page, collects the page images, and draws
// the translations back over them.
//
// Injected on demand (activeTab + chrome.scripting), never declared as a
// content script in the manifest, so this extension has no standing
// access to any site -- it touches a page only when the person clicks.
//
// This side never sees the token. It asks the service worker to talk to
// the app; see background.js for why.

(() => {
  // Re-injection is normal: the person can click the action again on a
  // page already set up. Keep the existing state instead of stacking a
  // second set of observers and overlays.
  if (window.__baiheContentLoaded) {
    return;
  }
  window.__baiheContentLoaded = true;

  // A cheap size floor, only so a page full of icons and avatars isn't
  // re-encoded to PNG for nothing. This is deliberately NOT a second
  // page-image filter: which images are really pages is decided
  // server-side by sources/generic_import.py's existing filter, so there
  // is one implementation of that judgement, not two that drift apart.
  const MIN_WIDTH = 200;
  const MIN_HEIGHT = 400;

  // -- recognizing a verification/CAPTCHA interstitial -----------------
  //
  // The same categories sources/detect.py already names server-side
  // (CLOUDFLARE_CHALLENGE / BOT_CHALLENGE), reused here so the two
  // detectors describe the same things. Like the Python side, this only
  // *recognizes* a challenge and reports it -- it never tries to solve or
  // pass one. Without this, clicking the extension on an interstitial
  // would silently hand a CAPTCHA graphic to OCR/translation as if it
  // were real page content.
  const CHALLENGE_TITLE_RE =
    /just a moment|attention required|checking your browser|verify you are human|are you a robot/i;
  const CHALLENGE_TEXT_MARKERS = [
    "verify you are human", "are you a robot", "人机验证", "安全验证", "滑动验证",
    "보안문자", "로봇이 아닙니다",
  ];
  const CHALLENGE_SELECTORS = [
    'iframe[src*="captcha" i]', '[class*="g-recaptcha"]', '[class*="h-captcha"]',
    '[class*="hcaptcha"]', '[class*="cf-turnstile"]', '[class*="geetest"]',
    '[id*="captcha" i]',
  ];

  function isPresentedOnTop(el) {
    // Not just "is a matching element somewhere in the DOM" -- a hidden
    // template for a challenge that never triggered would false-positive
    // on that alone. Sample a few points of the element's own box and
    // confirm something in that box is actually what's drawn there.
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const points = [
      [rect.left + rect.width / 2, rect.top + rect.height / 2],
      [rect.left + 2, rect.top + 2],
      [rect.right - 2, rect.bottom - 2],
    ];
    return points.some(([x, y]) => {
      if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return false;
      const top = document.elementFromPoint(x, y);
      return top === el || el.contains(top);
    });
  }

  function looksLikeChallengePage() {
    if (CHALLENGE_TITLE_RE.test(document.title)) return true;
    const bodyText = ((document.body && document.body.innerText) || "")
      .slice(0, 2000).toLowerCase();
    if (CHALLENGE_TEXT_MARKERS.some((m) => bodyText.includes(m))) return true;
    for (const sel of CHALLENGE_SELECTORS) {
      for (const el of document.querySelectorAll(sel)) {
        if (isVisible(el) && isPresentedOnTop(el)) return true;
      }
    }
    return false;
  }

  const state = {
    overlaysVisible: true,
    // hash -> regions, so paging back to a page already translated is
    // instant and nothing is ever translated twice.
    cache: new Map(),
    // element -> { layer, regions, naturalWidth }
    active: new Map(),
    observer: null,
    // The running chapter capture ({cancelled, text}), or null.
    capture: null,
  };

  // -- collecting ------------------------------------------------------

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const style = getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none" &&
           Number(style.opacity || "1") > 0.05;
  }

  function candidateElements() {
    const found = [];
    for (const img of document.images) {
      // naturalWidth is the real pixel size, which is what matters for
      // OCR -- an img displayed small can still be a full-resolution
      // page, and vice versa.
      const w = img.naturalWidth || img.width;
      const h = img.naturalHeight || img.height;
      if (w >= MIN_WIDTH && h >= MIN_HEIGHT && isVisible(img)) {
        found.push(img);
      }
    }
    for (const canvas of document.querySelectorAll("canvas")) {
      if (canvas.width >= MIN_WIDTH && canvas.height >= MIN_HEIGHT && isVisible(canvas)) {
        found.push(canvas);
      }
    }
    // Reading order on screen: top to bottom, then left to right. The
    // app re-derives its own reading order per page anyway; this only
    // decides which image is "page 1" of a send.
    return found.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return (ra.top - rb.top) || (ra.left - rb.left);
    });
  }

  function elementSize(el) {
    if (el.tagName === "CANVAS") return { width: el.width, height: el.height };
    return { width: el.naturalWidth || el.width, height: el.naturalHeight || el.height };
  }

  // A cheap, small-canvas sample of an element's current pixels, used
  // only to tell "still changing" from "settled" -- never the real
  // extraction. Returns null (rather than throwing) on a cross-origin
  // canvas taint, so a tainted element just skips the wait below and
  // reaches extractBytes's own toBlob(), which is what actually reports
  // that failure to the caller.
  function sampleSignature(el, size = 6) {
    try {
      const c = document.createElement("canvas");
      c.width = size;
      c.height = size;
      const ctx = c.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(el, 0, 0, size, size);
      return ctx.getImageData(0, 0, size, size).data.join(",");
    } catch (e) {
      return null;
    }
  }

  // Waits for the element's pixels to stop changing before capture, not
  // a fixed sleep: a page whose own JS is still descrambling/reassembling
  // a page onto a canvas (mangaz's own reader does exactly this) can have
  // the right dimensions well before it has the right pixels. Bounded, and
  // never blocks forever -- if it never settles, capture proceeds anyway
  // with whatever is there, same as if this check didn't exist.
  async function waitForStableSignature(el, stabilityMs = 150, timeoutMs = 1500) {
    let last = sampleSignature(el);
    if (last === null) return;
    const start = performance.now();
    while (performance.now() - start < timeoutMs) {
      await new Promise((r) => requestAnimationFrame(r));
      await new Promise((r) => setTimeout(r, stabilityMs));
      const next = sampleSignature(el);
      if (next === last) return;
      last = next;
    }
  }

  // Draws the element at its own full resolution and reads the pixels
  // back. This is the step that reaches content an adapter can't: a
  // `blob:` image, or a page the site's own reader has already
  // descrambled onto a canvas, is just pixels here -- the browser has
  // done the work, and nothing is being decrypted or unscrambled.
  async function extractBytes(el) {
    const { width, height } = elementSize(el);
    if (!width || !height) throw new Error("that image hasn't finished loading");
    await waitForStableSignature(el);
    let source = el;
    if (el.tagName !== "CANVAS") {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      canvas.getContext("2d").drawImage(el, 0, 0, width, height);
      source = canvas;
    }
    const blob = await new Promise((resolve, reject) => {
      try {
        // toBlob throws a SecurityError on a canvas tainted by a
        // cross-origin image drawn without CORS. That is a real limit,
        // not a bug to route around: the browser is refusing to let any
        // script read those pixels, and this extension respects that.
        source.toBlob((b) => (b ? resolve(b) : reject(new Error("the image could not be read"))),
                      "image/png");
      } catch (e) {
        reject(e);
      }
    });
    const buffer = await blob.arrayBuffer();
    return {
      data: base64(buffer),
      content_type: "image/png",
      width,
      height,
      hash: await sha256Hex(buffer),
      url: el.currentSrc || el.src || location.href,
    };
  }

  function base64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;      // chunked, so a big page can't blow the stack
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  // The hash only keys the page's own cache and de-duplicates images within a
  // page; nothing on the server reads it. crypto.subtle exists only on secure
  // contexts, and many readers are plain http, so there it falls back to a
  // 53-bit non-cryptographic hash plus the byte length.
  async function sha256Hex(buffer) {
    if (!(globalThis.crypto && crypto.subtle)) return weakHash(buffer);
    const digest = await crypto.subtle.digest("SHA-256", buffer);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  // cyrb53 over the bytes. The "w" prefix keeps it from ever equalling a
  // SHA-256 key for the same page.
  function weakHash(buffer) {
    const bytes = new Uint8Array(buffer);
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let i = 0; i < bytes.length; i++) {
      h1 = Math.imul(h1 ^ bytes[i], 2654435761);
      h2 = Math.imul(h2 ^ bytes[i], 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    const mixed = (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16);
    return `w${bytes.length.toString(16)}-${mixed}`;
  }

  // -- overlay ---------------------------------------------------------

  function ensureStyles() {
    if (document.getElementById("baihe-overlay-styles")) return;
    const style = document.createElement("style");
    style.id = "baihe-overlay-styles";
    style.textContent = `
      .baihe-layer { position: absolute; top: 0; left: 0; pointer-events: none;
                     z-index: 2147483000; }
      .baihe-box { position: absolute; box-sizing: border-box; display: flex;
                   align-items: center; justify-content: center; text-align: center;
                   background: rgba(255,255,255,0.93); color: #111;
                   border-radius: 6px; padding: 2px; overflow: hidden;
                   font-family: "Comic Sans MS", "Segoe UI", sans-serif;
                   line-height: 1.12; pointer-events: auto; cursor: help;
                   white-space: pre-wrap; word-break: break-word; }
      .baihe-box[data-kind="narration"] { border-radius: 2px; }
      .baihe-box.baihe-bold { font-weight: 700; }
      .baihe-box.baihe-showing-original { background: rgba(255,244,214,0.96);
                                          font-style: italic; }
      .baihe-hidden { display: none !important; }
      .baihe-capture-chip { display: flex; align-items: center; gap: 10px; }
      .baihe-capture-chip button { font: inherit; cursor: pointer; background: rgba(255,255,255,0.18);
                                   color: #fff; border: none; border-radius: 5px; padding: 3px 9px; }
      .baihe-toast { position: fixed; bottom: 18px; right: 18px; z-index: 2147483600;
                     background: #1b1b1f; color: #fff; padding: 10px 14px;
                     border-radius: 8px; font: 13px/1.4 "Segoe UI", sans-serif;
                     max-width: 340px; box-shadow: 0 4px 16px rgba(0,0,0,0.35); }
      #baihe-text-panel { position: fixed; right: 18px; bottom: 18px; width: 380px;
                          max-height: 70vh; display: flex; flex-direction: column;
                          background: #fff; color: #111; border-radius: 10px;
                          box-shadow: 0 8px 28px rgba(0,0,0,0.4); z-index: 2147483500;
                          font: 14px/1.5 "Segoe UI", sans-serif; overflow: hidden; }
      .baihe-text-panel-header { display: flex; align-items: center; justify-content: space-between;
                                 padding: 10px 12px; background: #1b1b1f; color: #fff;
                                 font-weight: 600; font-size: 13px; gap: 8px; }
      .baihe-text-panel-header button { font: inherit; font-size: 12px; cursor: pointer;
                                        background: rgba(255,255,255,0.14); color: #fff;
                                        border: none; border-radius: 5px; padding: 4px 8px; }
      .baihe-text-panel-body { padding: 12px 14px; overflow-y: auto; white-space: pre-wrap;
                               word-break: break-word; }
    `;
    document.documentElement.appendChild(style);
  }

  function toast(message, ms = 4200) {
    ensureStyles();
    const el = document.createElement("div");
    el.className = "baihe-toast";
    el.textContent = message;
    document.documentElement.appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  function layerFor(el) {
    let entry = state.active.get(el);
    if (entry) return entry;
    ensureStyles();
    const layer = document.createElement("div");
    layer.className = "baihe-layer";
    document.body.appendChild(layer);
    entry = { layer, regions: [], naturalWidth: 0 };
    state.active.set(el, entry);
    return entry;
  }

  function drawOverlay(el, regions) {
    const entry = layerFor(el);
    entry.regions = regions;
    entry.naturalWidth = elementSize(el).width;
    entry.layer.textContent = "";
    for (const region of regions) {
      if (!region.translated_text && !region.source_text) continue;
      const box = document.createElement("div");
      box.className = "baihe-box" +
        (region.font_category === "bold" ? " baihe-bold" : "");
      box.dataset.kind = region.kind || "bubble";
      box.dataset.translated = region.translated_text || "";
      box.dataset.original = region.source_text || "";
      box.textContent = region.translated_text || region.source_text || "";
      box.title = region.source_text ? `Original: ${region.source_text}` : "";
      // Click-to-see-original, so a translation is always checkable
      // against what is actually printed on the page.
      box.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const showingOriginal = box.classList.toggle("baihe-showing-original");
        box.textContent = showingOriginal
          ? (box.dataset.original || "(nothing was read here)")
          : (box.dataset.translated || box.dataset.original || "");
      });
      entry.layer.appendChild(box);
    }
    positionOverlay(el);
  }

  // Image coordinates -> screen coordinates, by the element's own
  // scale. The app returns boxes in the pixels of the image that was
  // sent, so this is the only place that conversion happens.
  function positionOverlay(el) {
    const entry = state.active.get(el);
    if (!entry) return;
    const rect = el.getBoundingClientRect();
    const natural = elementSize(el);
    if (!natural.width || !natural.height) return;
    const scaleX = rect.width / natural.width;
    const scaleY = rect.height / natural.height;
    const layer = entry.layer;
    layer.style.left = `${rect.left + window.scrollX}px`;
    layer.style.top = `${rect.top + window.scrollY}px`;
    layer.style.width = `${rect.width}px`;
    layer.style.height = `${rect.height}px`;
    layer.classList.toggle("baihe-hidden", !state.overlaysVisible);
    const boxes = layer.children;
    for (let i = 0; i < boxes.length && i < entry.regions.length; i += 1) {
      const region = entry.regions[i];
      const box = boxes[i];
      box.style.left = `${region.x * scaleX}px`;
      box.style.top = `${region.y * scaleY}px`;
      box.style.width = `${region.w * scaleX}px`;
      box.style.height = `${region.h * scaleY}px`;
      // Font size follows the box, so a zoomed page keeps readable text
      // instead of overflowing its bubble.
      box.style.fontSize = `${Math.max(8, Math.min(region.h * scaleY * 0.32, 22))}px`;
    }
  }

  function repositionAll() {
    for (const el of state.active.keys()) {
      if (!el.isConnected) {
        const entry = state.active.get(el);
        if (entry) entry.layer.remove();
        state.active.delete(el);
        continue;
      }
      positionOverlay(el);
    }
  }

  let scheduled = false;
  function scheduleReposition() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      repositionAll();
    });
  }

  window.addEventListener("resize", scheduleReposition, { passive: true });
  window.addEventListener("scroll", scheduleReposition, { passive: true });

  // A viewer that paginates in place (mangaz's own does exactly this)
  // swaps the image without a navigation, so a load handler would never
  // fire. Watch the DOM instead, and re-translate the new page -- from
  // the cache when it is one we have already done.
  function watchForPageChanges() {
    if (state.observer) return;
    state.observer = new MutationObserver(() => scheduleReposition());
    state.observer.observe(document.body, {
      subtree: true, attributes: true, childList: true,
      attributeFilter: ["src", "style", "class"],
    });
  }

  function setOverlaysVisible(visible) {
    state.overlaysVisible = visible;
    repositionAll();
    return state.overlaysVisible;
  }

  // -- text capture ----------------------------------------------------
  //
  // The same philosophy as the image mode applies to text-heavy pages:
  // the browser has already rendered the page, so this reads what's on
  // screen rather than fetching anything itself. Which text counts as
  // "the page" is decided here, once, the same way MIN_WIDTH/MIN_HEIGHT
  // above decide which images do -- not per-site.

  const MIN_PARAGRAPH_CHARS = 40;
  const MAX_TEXT_CHARS = 20000;
  // Tag-based, not site-specific: any page can have a <nav> or <aside>,
  // and this skips them the same way on all of them.
  const SKIP_TAGS = new Set(["NAV", "HEADER", "FOOTER", "ASIDE", "SCRIPT", "STYLE"]);

  function insideSkippedAncestor(el) {
    for (let node = el; node; node = node.parentElement) {
      if (SKIP_TAGS.has(node.tagName)) return true;
    }
    return false;
  }

  function paragraphCandidates() {
    const found = [];
    for (const p of document.querySelectorAll("p")) {
      const text = (p.textContent || "").trim();
      if (text.length < MIN_PARAGRAPH_CHARS) continue;
      if (!isVisible(p)) continue;
      if (insideSkippedAncestor(p)) continue;
      found.push(p);
    }
    return found;
  }

  // "Largest contiguous block of paragraph text": group paragraphs by
  // their immediate parent element, and take the group with the most
  // total text. A real article/chapter body is almost always many <p>
  // siblings under one container; nav/sidebar/ad text, if it has
  // paragraphs at all, is short and scattered across different
  // containers. A simple heuristic on purpose, not a full readability
  // implementation.
  function mainContentBlock() {
    const paragraphs = paragraphCandidates();
    if (!paragraphs.length) return "";
    const groups = new Map();
    for (const p of paragraphs) {
      const parent = p.parentElement;
      if (!groups.has(parent)) groups.set(parent, []);
      groups.get(parent).push(p);
    }
    let best = null;
    let bestLength = 0;
    for (const group of groups.values()) {
      const length = group.reduce((sum, p) => sum + p.textContent.trim().length, 0);
      if (length > bestLength) {
        bestLength = length;
        best = group;
      }
    }
    if (!best) return "";
    best.sort((a, b) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
    return best.map((p) => p.textContent.trim()).join("\n\n");
  }

  function selectedText() {
    const selection = window.getSelection();
    return selection ? selection.toString().trim() : "";
  }

  // Selection wins when there is one -- it's an explicit "translate
  // this" from the person -- and the heuristic block is the fallback for
  // "translate this page" with nothing selected.
  function collectPageText() {
    const selected = selectedText();
    const raw = selected || mainContentBlock();
    if (!raw) return { text: "", fromSelection: false, truncated: false };
    const truncated = raw.length > MAX_TEXT_CHARS;
    return {
      text: truncated ? raw.slice(0, MAX_TEXT_CHARS) : raw,
      fromSelection: !!selected,
      truncated,
    };
  }

  // -- text panel --------------------------------------------------------

  function ensureTextPanel() {
    ensureStyles();
    let panel = document.getElementById("baihe-text-panel");
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = "baihe-text-panel";
    const header = document.createElement("div");
    header.className = "baihe-text-panel-header";
    const title = document.createElement("span");
    title.textContent = "Baihe Subtitler — translation";
    const buttons = document.createElement("div");
    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.textContent = "Show original";
    toggleBtn.addEventListener("click", () => {
      const showingOriginal = panel.classList.toggle("baihe-showing-original");
      toggleBtn.textContent = showingOriginal ? "Show translation" : "Show original";
      updateTextPanelBody(panel);
    });
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => panel.remove());
    buttons.append(toggleBtn, closeBtn);
    header.append(title, buttons);
    const body = document.createElement("div");
    body.className = "baihe-text-panel-body";
    panel.append(header, body);
    document.documentElement.appendChild(panel);
    return panel;
  }

  function updateTextPanelBody(panel) {
    const body = panel.querySelector(".baihe-text-panel-body");
    const showingOriginal = panel.classList.contains("baihe-showing-original");
    body.textContent = (showingOriginal ? panel.dataset.original : panel.dataset.translated) || "";
  }

  function showTextPanel({ sourceText, translatedText }) {
    const panel = ensureTextPanel();
    panel.dataset.original = sourceText || "";
    panel.dataset.translated = translatedText || sourceText || "";
    panel.classList.remove("baihe-showing-original");
    const toggleBtn = panel.querySelector(".baihe-text-panel-header button");
    if (toggleBtn) toggleBtn.textContent = "Show original";
    updateTextPanelBody(panel);
  }

  // A page-length block of prose doesn't fit the popup's own 300px width,
  // and a Chrome popup closes as soon as it loses focus -- so unlike the
  // image mode's per-bubble overlay (which has to sit exactly on the
  // page anyway), the natural place for a paragraph-shaped result is a
  // panel injected into the page itself, not the popup.
  async function translatePageText({ sourceLanguage, targetLanguage, store }) {
    const captured = collectPageText();
    if (!captured.text) {
      return {
        ok: false,
        error: "No selectable text found here. Try selecting a passage first, or scroll to " +
              "the part of the page you want translated.",
      };
    }
    const response = await chrome.runtime.sendMessage({
      type: "sendText",
      text: captured.text,
      sourceLanguage,
      targetLanguage,
      store,
    });
    if (!response || !response.ok) {
      return response || { ok: false, error: "No answer from the extension's background worker." };
    }
    showTextPanel({
      sourceText: response.data.source_text,
      translatedText: response.data.translated_text,
    });
    const warning = (response.data.notes || []).find((n) => n[0] === "warning");
    if (warning) toast(warning[1]);
    return {
      ok: true,
      data: { ...response.data, fromSelection: captured.fromSelection,
              truncated: captured.truncated },
    };
  }

  // -- the main action -------------------------------------------------

  // The app refuses more than this many images in one request (413).
  // Kept equal to page_server.MAX_IMAGES_PER_REQUEST by a test; if the
  // two drift apart anyway, sendInBatches learns the real limit from the
  // 413 reply.
  const MAX_IMAGES_PER_REQUEST = 12;

  // Sends `items` in order, one batch at a time: the app runs OCR and
  // translation per image on a single user machine, and sequential
  // batches also keep saved pages (store=true) in chapter order, since
  // the app appends pages in request order. `send(batch, range)` returns
  // the background's {ok, data|error, status}; `onBatch(batch, data)`
  // handles each successful reply. Stops at the first hard error.
  async function sendInBatches(items, send, onBatch, limit = MAX_IMAGES_PER_REQUEST) {
    let done = 0;
    let sentAny = false;
    let retriedLimit = false;
    let size = limit;
    while (done < items.length) {
      const batch = items.slice(done, done + size);
      const range = { from: done + 1, to: done + batch.length, total: items.length };
      const response = await send(batch, range) ||
        { ok: false, error: "No answer from the extension's background worker." };
      if (response.ok) {
        sentAny = true;
        onBatch(batch, response.data || {});
        done += batch.length;
        continue;
      }
      const match = response.status === 413 && /at most (\d+) images/.exec(response.error || "");
      if (match && !retriedLimit && Number(match[1]) > 0 && Number(match[1]) < batch.length) {
        retriedLimit = true;
        size = Number(match[1]);
        continue;
      }
      // Every image in this batch was judged not to be a page: nothing
      // is wrong, so carry on with the next batch.
      if (response.status === 422) {
        onBatch(batch, { pages: [], skipped: batch.map(({ extracted }) => ({
          key: extracted.hash, url: extracted.url, reason: response.error || "" })) });
        done += batch.length;
        continue;
      }
      return { sentAny, limit: size, failure: { done, reason: response.error || "That didn't work.",
                                                status: response.status } };
    }
    return { sentAny, failure: null, limit: size };
  }

  // The popup shows this while a long chapter is in flight. It is only
  // ever a hint, so a popup that has closed (or no listener) is ignored.
  function reportProgress(text) {
    try {
      chrome.runtime.sendMessage({ type: "progress", text }).catch(() => {});
    } catch (e) { /* nobody is listening */ }
  }


  async function translateVisible({ dramaId, store, all }) {
    // A capture sends the same pages itself; running both would save a page twice.
    if (state.capture) {
      return { ok: false, error: "A chapter capture is running on this page; wait for it or cancel it first." };
    }
    if (looksLikeChallengePage()) {
      return {
        ok: false, code: "CHALLENGE_DETECTED",
        error: "This looks like a verification/CAPTCHA page, not the reader -- solve it, " +
               "then try again.",
      };
    }
    const elements = candidateElements();
    if (!elements.length) {
      return { ok: false, error: "No page-sized images found here. If the page is still " +
                                 "loading, or you need to scroll to it, try again." };
    }
    const chosen = all ? elements : [elements[0]];
    // Keyed by content hash, not by element: a reader that shows the
    // same image in two places (a spread and its thumbnail, a preloaded
    // next page) would otherwise encode and upload identical megabytes
    // once per element, and the app would just reject the copies as
    // duplicates. One send per distinct image; every element holding it
    // gets the same overlay.
    const pending = new Map();
    const unreadable = [];
    const fromCache = [];
    for (const el of chosen) {
      try {
        const extracted = await extractBytes(el);
        const cached = state.cache.get(extracted.hash);
        if (cached) {
          drawOverlay(el, cached);
          fromCache.push(el);
          continue;
        }
        const entry = pending.get(extracted.hash);
        if (entry) {
          entry.elements.push(el);
        } else {
          pending.set(extracted.hash, { extracted, elements: [el] });
        }
      } catch (e) {
        unreadable.push(String(e && e.message ? e.message : e));
      }
    }
    const images = [...pending.values()];

    if (!images.length) {
      if (fromCache.length) {
        watchForPageChanges();
        return { ok: true, data: { pages: [], cached: fromCache.length } };
      }
      return {
        ok: false,
        error: unreadable.length
          ? `Your browser wouldn't let this page's image be read (${unreadable[0]}). ` +
            "That happens when the site draws it from another domain without allowing it."
          : "Nothing on this page could be read as an image.",
      };
    }

    const pages = [];
    const skipped = [];
    let drawn = 0;
    const outcome = await sendInBatches(images, async (batch, range) => {
      reportProgress(`Translating ${range.from}-${range.to} of ${range.total}...`);
      return chrome.runtime.sendMessage({
        type: "send",
        images: batch.map(({ extracted }) => ({
          data: extracted.data,
          content_type: extracted.content_type,
          url: extracted.url,
          key: extracted.hash,
        })),
        dramaId,
        store,
        sourceUrl: location.href,
        // A deliberate single send is the image the person pointed at, so
        // the server's page filter shouldn't second-guess it. The server
        // applies the filter to each request on its own, so a batch is
        // clustered against its own neighbours only.
        filterPages: images.length > 1,
      });
    }, (batch, data) => {
      // Drawn per batch so a long chapter shows results as they arrive
      // and keeps them if a later batch fails.
      const byHash = new Map();
      for (const page of data.pages || []) byHash.set(page.key, page.regions || []);
      for (const { extracted, elements } of batch) {
        const regions = byHash.get(extracted.hash);
        if (!regions) continue;
        state.cache.set(extracted.hash, regions);
        for (const el of elements) {
          drawOverlay(el, regions);
          drawn += 1;
        }
      }
      pages.push(...(data.pages || []));
      skipped.push(...(data.skipped || []));
    });

    if (outcome.sentAny) watchForPageChanges();
    const data = { pages, skipped, drawn, cached: fromCache.length };
    if (outcome.failure) {
      const f = outcome.failure;
      if (!pages.length && !skipped.length) {
        return { ok: false, error: f.reason, ...(f.status ? { status: f.status } : {}) };
      }
      data.failed = {
        done: f.done, total: images.length, at: f.done + 1, reason: f.reason,
        message: `Translated ${f.done} of ${images.length} pages; stopped at page ` +
                 `${f.done + 1}: ${f.reason}`,
      };
    }
    return { ok: true, data };
  }


  // -- capturing a whole chapter -----------------------------------------
  //
  // A virtualised reader keeps only the pages near the viewport in the
  // DOM, so "everything visible" sees a handful of a 70-page chapter. This
  // scrolls the reader the way a person would and reads each page as the
  // reader's own JavaScript mounts it. Nothing is requested from the site:
  // no network calls, no tokens, no reader APIs -- only pixels the page has
  // already rendered for this signed-in reader. The pace mirrors
  // page_scroll.SCROLL_THROUGH_JS (about a screen per step).

  // Hard stop on distinct pages per run, so a reader that never ends
  // (endless feed, auto-next-chapter) cannot grow the library unbounded.
  const CAPTURE_MAX_PAGES = 300;
  const CAPTURE_STEP_FRACTION = 0.9;
  const CAPTURE_STEP_MIN_MS = 250;
  const CAPTURE_STEP_MAX_MS = 600;
  // Steps in a row that turn up nothing new before giving up mid-chapter.
  const CAPTURE_STALL_STEPS = 6;
  // Backstop independent of the page cap: duplicates and unreadable pages
  // add no page, so a reader looping the same images must still stop.
  const CAPTURE_MAX_STEPS = 700;
  const CAPTURE_MUTATION_WAIT_MS = 800;
  const CAPTURE_LOAD_WAIT_MS = 1500;
  // Attributes a list renderer uses to number its rows. Used only to order
  // pages, and only when every page in the comparison has one.
  const READER_INDEX_KEYS = ["pageIndex", "pageNum", "pageNumber", "page", "index", "idx"];

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const stepDelay = () => CAPTURE_STEP_MIN_MS +
    Math.random() * (CAPTURE_STEP_MAX_MS - CAPTURE_STEP_MIN_MS);

  const CAPTURE_STOP_MESSAGES = {
    end: "Reached the end of the chapter.",
    stalled: `No new pages after ${CAPTURE_STALL_STEPS} scroll steps.`,
    cap: `Stopped at the ${CAPTURE_MAX_PAGES}-page limit.`,
    cancelled: "Cancelled.",
  };

  function scrollableAncestor(el) {
    for (let node = el && el.parentElement;
         node && node !== document.body && node !== document.documentElement;
         node = node.parentElement) {
      const overflow = getComputedStyle(node).overflowY;
      if ((overflow === "auto" || overflow === "scroll") &&
          node.scrollHeight > node.clientHeight + 50) {
        return node;
      }
    }
    return null;
  }

  // The reader may scroll the window or its own container; both look the
  // same to the loop. positionOf is an offset from the top of the scrolled
  // content, so it stays comparable between steps.
  function makeScroller() {
    const inner = scrollableAncestor(candidateElements()[0]);
    if (inner) {
      return {
        view: () => inner.clientHeight,
        top: () => inner.scrollTop,
        max: () => inner.scrollHeight - inner.clientHeight,
        to: (y) => { inner.scrollTop = y; },
        positionOf: (el) => el.getBoundingClientRect().top -
          inner.getBoundingClientRect().top + inner.scrollTop,
      };
    }
    return {
      view: () => window.innerHeight,
      top: () => window.scrollY,
      max: () => document.documentElement.scrollHeight - window.innerHeight,
      to: (y) => window.scrollTo(0, y),
      positionOf: (el) => el.getBoundingClientRect().top + window.scrollY,
    };
  }

  function readerIndexOf(el) {
    let node = el;
    for (let depth = 0; node && depth < 4; depth += 1, node = node.parentElement) {
      for (const key of READER_INDEX_KEYS) {
        const value = node.dataset && node.dataset[key];
        if (value !== undefined && /^\d+$/.test(value)) return Number(value);
      }
      const posinset = node.getAttribute && node.getAttribute("aria-posinset");
      if (posinset && /^\d+$/.test(posinset)) return Number(posinset);
    }
    return null;
  }

  // Reading order: the reader's own index when both pages carry one, else
  // where they sit in the scrolled content.
  function byReadingOrder(a, b) {
    if (a.order.index !== null && b.order.index !== null && a.order.index !== b.order.index) {
      return a.order.index - b.order.index;
    }
    return (a.order.pos - b.order.pos) || (a.seq - b.seq);
  }

  // The reader's own "5 / 70" counter, when it shows one. Display only: a
  // wrong guess here costs a misleading label, never a skipped page.
  function readerCounter() {
    for (const el of document.querySelectorAll("div, span, p, li")) {
      if (el.childElementCount) continue;
      const text = el.textContent || "";
      if (text.length > 14) continue;
      const match = /^\s*(\d{1,4})\s*\/\s*(\d{1,4})\s*$/.exec(text);
      if (!match) continue;
      const current = Number(match[1]);
      const total = Number(match[2]);
      if (total >= 2 && current >= 1 && current <= total && isVisible(el)) {
        return { current, total };
      }
    }
    return null;
  }

  // After a scroll, give the reader a moment to mount and load its next
  // pages: wait for the DOM to go quiet, then for images still loading.
  async function settleForImages() {
    await new Promise((resolve) => {
      let quiet = null;
      const finish = () => { observer.disconnect(); clearTimeout(quiet); clearTimeout(limit); resolve(); };
      const observer = new MutationObserver(() => {
        clearTimeout(quiet);
        quiet = setTimeout(finish, 150);
      });
      const limit = setTimeout(finish, CAPTURE_MUTATION_WAIT_MS);
      observer.observe(document.body, { subtree: true, childList: true, attributes: true,
                                        attributeFilter: ["src", "style", "class"] });
    });
    const loading = [...document.images].filter((img) => !img.complete);
    if (!loading.length) return;
    const loaded = Promise.all(loading.map((img) => new Promise((resolve) => {
      img.addEventListener("load", resolve, { once: true });
      img.addEventListener("error", resolve, { once: true });
    })));
    await Promise.race([loaded, sleep(CAPTURE_LOAD_WAIT_MS)]);
  }

  // A canvas has no src, so a content key would skip re-reading a canvas the reader repainted
  // with another page. Its identity is only used to check the draw target, not to dedupe.
  const canvasIds = new WeakMap();
  let nextCanvasId = 0;

  function drawTargetKey(el) {
    if (el.tagName !== "CANVAS") return elementKey(el);
    if (!canvasIds.has(el)) canvasIds.set(el, ++nextCanvasId);
    return `canvas#${canvasIds.get(el)}`;
  }

  function elementKey(el) {
    if (el.tagName === "CANVAS") return null;
    const { width, height } = elementSize(el);
    return `${el.currentSrc || el.src}|${width}x${height}`;
  }

  function showCaptureChip(run) {
    ensureStyles();
    const chip = document.createElement("div");
    chip.className = "baihe-toast baihe-capture-chip";
    const label = document.createElement("span");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => {
      run.cancelled = true;
      label.textContent = "Stopping…";
    });
    chip.append(label, cancel);
    document.documentElement.appendChild(chip);
    return { chip, set: (text) => { if (!run.cancelled) label.textContent = text; } };
  }

  async function captureChapter({ dramaId, store, fromHere }) {
    if (state.capture) {
      return { ok: false, error: "A capture is already running on this page." };
    }
    if (looksLikeChallengePage()) {
      return {
        ok: false, code: "CHALLENGE_DETECTED",
        error: "This looks like a verification/CAPTCHA page, not the reader -- solve it, " +
               "then try again.",
      };
    }
    const run = { cancelled: false, text: "Starting…" };
    state.capture = run;
    const ui = showCaptureChip(run);
    const scroller = makeScroller();
    const startTop = scroller.top();
    try {
      return await runCapture(run, ui, scroller, { dramaId, store, fromHere });
    } finally {
      scroller.to(startTop);
      ui.chip.remove();
      state.capture = null;
    }
  }

  async function runCapture(run, ui, scroller, { dramaId, store, fromHere }) {
    // Hashes already handled this run (sent, cached, skipped or duplicate).
    // Distinct pages seen is also what the cap counts.
    const seen = new Set();
    const handled = new WeakMap();
    const queue = [];
    const counts = { translated: 0, stored: 0, cached: 0, skipped: 0, drawn: 0 };
    let seq = 0;
    let limit = MAX_IMAGES_PER_REQUEST;
    let capHit = false;
    let unreadable = "";
    let failure = "";

    const report = (suffix = "") => {
      const counter = readerCounter();
      const base = counter ? `Page ${counter.current} of ${counter.total}`
                           : `${seen.size} page${seen.size === 1 ? "" : "s"} found`;
      run.text = base + suffix;
      ui.set(run.text);
      reportProgress(run.text);
    };

    // New pages among the currently mounted elements, in DOM order. Reads
    // pixels the reader already drew; a page that was unmounted by the
    // time we look is simply not there, which is why this runs every step.
    async function collectNew() {
      let found = 0;
      for (const el of candidateElements()) {
        if (run.cancelled) break;
        if (seen.size >= CAPTURE_MAX_PAGES) { capHit = true; break; }
        const key = elementKey(el);
        if (key !== null && handled.get(el) === key) continue;
        let extracted;
        try {
          extracted = await extractBytes(el);
        } catch (e) {
          unreadable = String(e && e.message ? e.message : e);
          if (key !== null) handled.set(el, key);
          continue;
        }
        if (key !== null) handled.set(el, key);
        const cached = state.cache.get(extracted.hash);
        if (seen.has(extracted.hash)) {
          if (cached && el.isConnected) drawOverlay(el, cached);
          continue;
        }
        seen.add(extracted.hash);
        found += 1;
        if (cached) {
          drawOverlay(el, cached);
          counts.cached += 1;
          continue;
        }
        seq += 1;
        queue.push({ extracted, elements: [el], srcKey: drawTargetKey(el), seq,
                     order: { index: readerIndexOf(el), pos: scroller.positionOf(el) } });
      }
      return found;
    }

    // Sends queued pages in reading order, a batch at a time, through the
    // same sendInBatches the visible-pages path uses. Returns a failure
    // reason, or "".
    async function flush(force) {
      while (!run.cancelled && (queue.length >= limit || (force && queue.length))) {
        queue.sort(byReadingOrder);
        const batch = queue.splice(0, limit);
        const outcome = await sendInBatches(batch, async (items, range) => {
          report(` — translating ${range.from}-${range.to} of ${range.total}`);
          return chrome.runtime.sendMessage({
            type: "send",
            images: items.map(({ extracted }) => ({
              data: extracted.data, content_type: extracted.content_type,
              url: extracted.url, key: extracted.hash,
            })),
            dramaId, store, sourceUrl: location.href,
            filterPages: items.length > 1,
          });
        }, (items, data) => {
          const byHash = new Map();
          for (const page of data.pages || []) byHash.set(page.key, page.regions || []);
          for (const { extracted, elements, srcKey } of items) {
            const regions = byHash.get(extracted.hash);
            if (!regions) continue;
            state.cache.set(extracted.hash, regions);
            // A virtualised reader may have recycled the element for a
            // later page while this batch was in flight; drawing then
            // would put these bubbles on the wrong page.
            for (const el of elements) {
              if (el.isConnected && drawTargetKey(el) === srcKey) {
                drawOverlay(el, regions);
                counts.drawn += 1;
              }
            }
          }
          counts.translated += (data.pages || []).length;
          counts.stored += (data.pages || []).filter((p) => p.stored).length;
          counts.skipped += (data.skipped || []).length;
        }, limit);
        limit = outcome.limit || limit;
        if (outcome.sentAny) watchForPageChanges();
        if (outcome.failure) return outcome.failure.reason;
      }
      return "";
    }

    if (!fromHere) {
      scroller.to(0);
      await sleep(stepDelay());
    }

    let reason = "";
    let stalls = 0;
    let steps = 0;
    let lastMoved = true;
    while (!reason) {
      if (run.cancelled) { reason = "cancelled"; break; }
      if (looksLikeChallengePage()) {
        reason = "error";
        failure = "A verification page appeared; solve it, then capture again.";
        break;
      }
      let found = await collectNew();
      if (!found && !capHit && !run.cancelled) {
        await settleForImages();
        found = await collectNew();
      }
      if (run.cancelled) { reason = "cancelled"; break; }
      stalls = found ? 0 : stalls + 1;
      report();
      if (queue.length >= limit) {
        failure = await flush(false);
        if (failure) { reason = "error"; break; }
        if (run.cancelled) { reason = "cancelled"; break; }
      }
      const atBottom = scroller.top() >= scroller.max() - 4;
      if (capHit) reason = "cap";
      else if (!found && (atBottom || !lastMoved)) reason = "end";
      else if (stalls >= CAPTURE_STALL_STEPS || steps >= CAPTURE_MAX_STEPS) reason = "stalled";
      if (reason) break;
      const before = scroller.top();
      scroller.to(before + Math.max(scroller.view() * CAPTURE_STEP_FRACTION, 200));
      steps += 1;
      await sleep(stepDelay());
      lastMoved = scroller.top() !== before;
    }

    // Cancelled means stop sending as well as scrolling; any other stop
    // still delivers the pages already read.
    if (reason !== "cancelled" && reason !== "error") {
      failure = await flush(true);
      if (failure) reason = "error";
      else if (run.cancelled) reason = "cancelled";
    }

    if (!seen.size) {
      return {
        ok: false,
        error: reason === "error" ? failure
          : unreadable
            ? `Your browser wouldn't let this page's image be read (${unreadable}). ` +
              "That happens when the site draws it from another domain without allowing it."
            : "No page-sized images found here. If the page is still loading, try again.",
      };
    }
    const tally = [`${seen.size} page${seen.size === 1 ? "" : "s"} found`];
    if (counts.translated) tally.push(`${counts.translated} translated`);
    if (counts.cached) tally.push(`${counts.cached} already done`);
    if (counts.skipped) tally.push(`${counts.skipped} skipped as not a page`);
    const message = `${reason === "error" ? failure : CAPTURE_STOP_MESSAGES[reason]} ${tally.join(", ")}.`;
    reportProgress(message);
    try { chrome.runtime.sendMessage({ type: "captureDone" }).catch(() => {}); } catch (e) { /* popup closed */ }
    toast(message, 8000);
    return { ok: true, data: { reason, message, found: seen.size, ...counts, pages: [] } };
  }

  function cancelCapture() {
    if (!state.capture) return false;
    state.capture.cancelled = true;
    return true;
  }

  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    (async () => {
      try {
        switch (message && message.type) {
          case "translateVisible":
            respond(await translateVisible(message));
            break;
          case "translatePageText":
            respond(await translatePageText(message));
            break;
          case "captureChapter":
            respond(await captureChapter(message));
            break;
          case "cancelCapture":
            respond({ ok: true, data: { cancelled: cancelCapture() } });
            break;
          case "toggleOverlays":
            respond({ ok: true, data: { visible: setOverlaysVisible(!state.overlaysVisible) } });
            break;
          case "setOverlays":
            respond({ ok: true, data: { visible: setOverlaysVisible(!!message.visible) } });
            break;
          case "status":
            respond({ ok: true, data: {
              images: candidateElements().length,
              translated: state.active.size,
              overlaysVisible: state.overlaysVisible,
              capture: state.capture ? { running: true, text: state.capture.text } : null,
              // The page's own host. The popup uses this to key "which
              // drama does this site go to", rather than reading
              // `tab.url` -- that needs the `tabs` permission or an
              // activeTab grant, and keying on an empty string would
              // quietly make every site share one remembered drama.
              host: location.host,
            } });
            break;
          default:
            respond({ ok: false, error: `Unknown request: ${message && message.type}` });
        }
      } catch (e) {
        respond({ ok: false, error: String(e && e.message ? e.message : e) });
      }
    })();
    return true;
  });

  // Exposed for the popup's injected checks and for tests.
  window.__baihe = { translateVisible, sendInBatches, setOverlaysVisible, candidateElements, state, toast,
                     translatePageText, collectPageText, mainContentBlock,
                     looksLikeChallengePage, sampleSignature, waitForStableSignature,
                     captureChapter, cancelCapture };
})();
