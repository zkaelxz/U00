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

  // Not shared.js: this file is injected on its own, and a second copy of
  // shared.js's top-level consts would throw on re-injection.
  const errorText = (e) => String(e && e.message ? e.message : e);

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
    visible: null,
    missed: null,
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

  // True when the element currently paints a single colour. A canvas the
  // reader has sized but not drawn yet looks exactly like this, and sending
  // it would store an empty page. An unreadable (tainted) element is not
  // called blank: the real read below reports that itself.
  function looksUnpainted(el) {
    const signature = sampleSignature(el);
    if (signature === null) return false;
    const values = signature.split(",");
    for (let i = 4; i < values.length; i += 4) {
      if (values[i] !== values[0] || values[i + 1] !== values[1] || values[i + 2] !== values[2]) {
        return false;
      }
    }
    return true;
  }

  // Gives a late-painting page time to appear before calling it blank.
  async function waitUntilPainted(el, attempts = 6, delayMs = 400) {
    for (let i = 0; i < attempts; i += 1) {
      if (!looksUnpainted(el)) return true;
      await new Promise((r) => setTimeout(r, delayMs));
    }
    return !looksUnpainted(el);
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
    if (!(await waitUntilPainted(el))) {
      throw new Error("the page was still blank after waiting for it to draw");
    }
    let source = el;
    if (el.tagName !== "CANVAS") {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      canvas.getContext("2d").drawImage(el, 0, 0, width, height);
      source = canvas;
    }
    let blob;
    try {
      // toBlob throws a SecurityError on a canvas tainted by a cross-origin
      // image drawn without CORS: the browser will not let any script read
      // those pixels, and nothing here tries to get around that.
      blob = await new Promise((resolve, reject) => {
        try {
          source.toBlob((b) => (b ? resolve(b) : reject(new Error("the image could not be read"))),
                        "image/png");
        } catch (e) {
          reject(e);
        }
      });
    } catch (e) {
      if (!(e && e.name === "SecurityError")) throw e;
      return extractViaWorker(el, e);
    }
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

  // Canvases whose pixels came from the worker's download, because their own
  // are unreadable. They have no pixel hash to key an overlay on.
  const taintedCanvases = new WeakSet();

  const IMAGE_URL_ATTRS = ["data-src", "data-original", "data-lazy-src", "data-url", "src"];

  function httpUrl(value) {
    if (!value) return "";
    try {
      const url = new URL(value, location.href);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
    } catch (e) {
      return "";
    }
  }

  // The file behind an element whose pixels are unreadable: its own src, or for
  // a canvas the image the reader painted it from, kept as a sibling <img>, a
  // data attribute or a CSS background on the canvas or its container.
  function findImageUrl(el) {
    if (el.tagName !== "CANVAS") return httpUrl(el.currentSrc || el.src);
    const holders = [el];
    for (let node = el.parentElement; node && holders.length < 4; node = node.parentElement) {
      holders.push(node);
    }
    for (const holder of holders) {
      for (const attr of IMAGE_URL_ATTRS) {
        const found = httpUrl(holder.getAttribute(attr));
        if (found) return found;
      }
      const background = /url\(["']?([^"')]+)["']?\)/.exec(getComputedStyle(holder).backgroundImage || "");
      if (background && httpUrl(background[1])) return httpUrl(background[1]);
    }
    return siblingImageUrl(el);
  }

  function imageUrlOf(node) {
    return httpUrl(node.currentSrc || node.src
      || IMAGE_URL_ATTRS.map((a) => node.getAttribute(a)).find(Boolean));
  }

  const hasBox = (r) => !!r && r.width > 0 && r.height > 0;

  // A reader with one <img> and one canvas per page under one container would hand every canvas
  // the first page's image if "the first image" were taken, and capture would then drop pages
  // 2..N as duplicates. So a sibling counts only when it sits exactly where the canvas sits, or,
  // when nothing has a box yet, at the same index; any doubt is "no image" rather than a wrong one.
  function siblingImageUrl(el) {
    const parent = el.parentElement;
    if (!parent) return "";
    const images = [...parent.querySelectorAll("img, amp-img")]
      .map((node) => ({ node, url: imageUrlOf(node) })).filter((c) => c.url);
    if (!images.length) return "";
    const box = el.getBoundingClientRect();
    if (hasBox(box)) {
      const slack = (a, b) => Math.abs(a - b) <= Math.max(4, Math.max(a, b) * 0.02);
      const aligned = images.filter(({ node }) => {
        const r = node.getBoundingClientRect();
        return hasBox(r) && slack(r.width, box.width) && slack(r.height, box.height)
          && slack(r.left, box.left) && slack(r.top, box.top);
      });
      return aligned.length === 1 ? aligned[0].url : "";
    }
    const canvases = [...parent.querySelectorAll("canvas")];
    const index = canvases.indexOf(el);
    return index >= 0 && canvases.length === images.length ? images[index].url : "";
  }

  // Asks the service worker to download the file instead. For a canvas the download must have the
  // canvas's proportions. That only rules out a thumbnail or another image: a scrambled tile sheet
  // has the same size as the page reassembled from it, so such a file is passed on flagged
  // `unverified` for the popup to say where the text came from.
  async function extractViaWorker(el, taintError) {
    const url = findImageUrl(el);
    if (!url) throw taintError;
    const response = await chrome.runtime.sendMessage({ type: "fetchImage", url });
    if (!response || !response.ok) {
      const reason = (response && response.error) || "the image could not be downloaded";
      const err = new Error(reason);
      if (response && response.code === "NEEDS_PERMISSION") {
        err.code = response.code;
        err.origin = response.origin;
      }
      throw err;
    }
    const { data, content_type } = response.data;
    const raw = atob(data);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const bitmap = await createImageBitmap(new Blob([bytes], { type: content_type }));
    const { width, height } = bitmap;
    bitmap.close();
    if (el.tagName === "CANVAS") {
      const same = (a, b) => Math.abs(a - b) <= Math.max(a, b) * 0.02;
      if (!(same(width / height, el.width / el.height))) {
        throw new Error("the image file doesn't match what the page draws, so it can't be used");
      }
      taintedCanvases.add(el);
    }
    return {
      data, content_type, width, height,
      hash: await sha256Hex(bytes.buffer), url: response.data.url || url,
      ...(el.tagName === "CANVAS" ? { unverified: true } : {}),
    };
  }

  // The refusal when nothing could be read. A page whose images only need the
  // person's go-ahead for one site gets a code the popup turns into a button.
  function unreadableResult(reason, needAccess) {
    if (needAccess.size) {
      const origins = [...needAccess];
      return {
        ok: false, code: "NEEDS_SITE_ACCESS", origins,
        error: `This page draws its images from ${origins.join(", ")}, and the browser won't let the page ` +
               'read them. Click an "Allow" button to let the extension download them itself.',
      };
    }
    return {
      ok: false,
      error: `Your browser wouldn't let this page's image be read (${reason}). ` +
             "That happens when the site draws it from another domain without allowing it.",
    };
  }

  // Sent with a partial result too, so the popup still offers Allow for the pages that were left out.
  function accessNeeded(needAccess) {
    return needAccess.size ? { origins: [...needAccess] } : {};
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
      .baihe-box { background: rgba(255,255,255,0.98); }
      .baihe-typeset { position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                       pointer-events: none; }
      .baihe-box.baihe-untranslated { outline: 2px dashed #c0392b; outline-offset: -2px; }
      .baihe-box.baihe-bold { font-weight: 700; }
      .baihe-box.baihe-showing-original { background: rgba(255,244,214,0.96);
                                          font-style: italic; }
      .baihe-hidden { display: none !important; }
      .baihe-capture-chip { display: flex; align-items: center; gap: 10px; }
      .baihe-capture-chip progress { width: 90px; height: 6px; }
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
    entry.naturalWidth = elementSize(el).width;
    entry.layer.textContent = "";
    if (regions.typesetImage) {
      // Baihe's own cleaned and typeset page replaces the preview boxes: it erases the lettering,
      // fits the text to each balloon's real shape and leaves sound effects alone.
      const image = document.createElement("img");
      image.className = "baihe-typeset";
      image.src = regions.typesetImage;
      image.alt = "";
      entry.regions = [];
      entry.layer.appendChild(image);
      positionOverlay(el);
      return;
    }
    entry.regions = regions;
    for (const region of regions) {
      if (!region.translated_text && !region.source_text) continue;
      // Sound effects and skipped regions keep their original lettering, as in the typeset page.
      if (region.left_alone) continue;
      const box = document.createElement("div");
      box.className = "baihe-box" +
        (region.font_category === "bold" ? " baihe-bold" : "") +
        (region.untranslated ? " baihe-untranslated" : "");
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
    const visible = entry.regions.filter((r) => (r.translated_text || r.source_text) && !r.left_alone);
    for (let i = 0; i < boxes.length && i < visible.length; i += 1) {
      placeBox(boxes[i], visible[i], scaleX, scaleY, rect.width, rect.height);
    }
  }

  // A box the page is cropped around (the edge of the image) would clip its text, so it is
  // kept inside the page; the text is then shrunk until it fits what is left.
  function placeBox(box, region, scaleX, scaleY, maxW, maxH) {
    const left = Math.max(0, region.x * scaleX);
    const top = Math.max(0, region.y * scaleY);
    const width = Math.max(1, Math.min(region.w * scaleX, maxW - left));
    const height = Math.max(1, Math.min(region.h * scaleY, maxH - top));
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.style.width = `${width}px`;
    box.style.height = `${height}px`;
    // An ellipse only has about 70% of its bounding box to write in; narration stays a rounded box.
    const round = box.dataset.kind === "bubble" || box.dataset.kind === "thought";
    box.style.borderRadius = round ? "50%" : "";
    const inset = round ? 0.15 : 0;
    box.style.padding = `${Math.max(2, height * inset)}px ${Math.max(2, width * inset)}px`;
    // Scrolling repositions every box; measuring text on each scroll would stall the page, so
    // the fit is redone only when the box's size changed.
    const sizeKey = `${Math.round(width)}x${Math.round(height)}`;
    if (box.dataset.fitted !== sizeKey) {
      box.dataset.fitted = sizeKey;
      fitText(box, Math.max(8, Math.min(region.h * scaleY * 0.32, 22)));
    }
  }

  // Largest size, down to a legible floor, at which the whole text sits inside the box.
  function fitText(box, start) {
    let size = start;
    box.style.fontSize = `${size}px`;
    while (size > 7 && (box.scrollHeight > box.clientHeight + 1 || box.scrollWidth > box.clientWidth + 1)) {
      size -= 1;
      box.style.fontSize = `${size}px`;
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

  // -- saving a novel page into a title -----------------------------------

  // Prose, not a comic: no page-sized images (the same test the comic
  // actions use) and a main text block of at least a few paragraphs.
  const MIN_TEXT_PAGE_CHARS = 300;
  // extension_novel_service.MAX_CAPTURE_CHARS, kept equal by a test. Over it
  // the save is refused rather than cut, since a cut chapter would be saved
  // as though it were whole.
  const MAX_NOVEL_CHARS = 200000;

  function isTextPage() {
    return !candidateElements().length && mainContentBlock().length >= MIN_TEXT_PAGE_CHARS;
  }

  // Novel readers put the chapter title in the first heading; the tab title
  // usually adds the book and site names, so it is only the fallback.
  // Cut by code point: a UTF-16 slice can split an emoji or rare CJK
  // character, and the lone surrogate left behind cannot be encoded as UTF-8.
  function capHeading(text) {
    return Array.from(text).slice(0, 200).join("");
  }

  function chapterHeading() {
    for (const tag of ["h1", "h2", "h3"]) {
      const el = document.querySelector(tag);
      const text = el ? (el.textContent || "").replace(/\s+/g, " ").trim() : "";
      if (text) return capHeading(text);
    }
    return capHeading((document.title || "").trim());
  }

  // What the app names an automatic title from. The app decides which candidate is the
  // original name (CJK script) and which the English one, and strips chapter numbers and
  // the site's name, so this only collects them, in the order a site is most likely to be right.
  const MAX_TITLE_CANDIDATE_CHARS = 300;

  function pageFacts() {
    const shared = document.querySelector('meta[property="og:title"]');
    const heading = document.querySelector("h1");
    const titles = [];
    for (const raw of [shared && shared.content, heading && heading.textContent, document.title]) {
      const text = Array.from(String(raw || "").replace(/\s+/g, " ").trim())
        .slice(0, MAX_TITLE_CANDIDATE_CHARS).join("");
      if (text && !titles.includes(text)) titles.push(text);
    }
    const siteMeta = document.querySelector('meta[property="og:site_name"]');
    const siteName = String((siteMeta && siteMeta.content) || "").replace(/\s+/g, " ").trim()
      .slice(0, MAX_TITLE_CANDIDATE_CHARS);
    return { url: location.href, language: document.documentElement.lang || "", titles, siteName,
             chapterLinks: chapterLinks() };
  }

  // Chapter links on the page (a series page's list, or a chapter page's previous/next),
  // for the app's chapter list on sites it has no adapter for. Same site only; the app
  // keeps just the address without its query.
  const MAX_CHAPTER_LINKS = 500;
  const CHAPTER_LINK_TEXT = /第\s*[\d０-９一二三四五六七八九十百零]+\s*[章话話回集卷期]|(?:chapter|chap|ch|episode|ep|vol)\.?\s*[\d.]+|[\d.]+\s*[화話话章]|^\s*#?\d+\s*$/i;
  function chapterLinks() {
    const out = [];
    const seen = new Set();
    for (const a of document.querySelectorAll("a[href]")) {
      if (out.length >= MAX_CHAPTER_LINKS) break;
      const text = (a.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120);
      if (!text || !CHAPTER_LINK_TEXT.test(text)) continue;
      let url;
      try { url = new URL(a.href); } catch (e) { continue; }
      if (url.host !== location.host || !/^https?:$/.test(url.protocol)) continue;
      const key = url.origin + url.pathname;
      if (seen.has(key) || key === location.origin + location.pathname) continue;
      seen.add(key);
      out.push({ title: text, url: key });
    }
    return out;
  }

  async function saveNovelText({ dramaId }) {
    const selected = selectedText();
    const text = selected || mainContentBlock();
    if (!text) {
      return { ok: false, error: "No chapter text found here. Select the text to save, or open the chapter itself." };
    }
    if (text.length > MAX_NOVEL_CHARS) {
      return { ok: false, error: `That is more than ${MAX_NOVEL_CHARS} characters; select one chapter's worth and save that.` };
    }
    const response = await chrome.runtime.sendMessage({
      type: "saveNovelText", dramaId, heading: chapterHeading(), text,
      source: location.host, url: location.href,
    });
    if (!response || !response.ok) {
      return response || { ok: false, error: "No answer from the extension's background worker." };
    }
    return { ok: true, data: { ...response.data, fromSelection: !!selected } };
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
    title.textContent = "Baihe Studio — translation";
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
  // handles each successful reply and may return true to stop the run.
  // Stops at the first hard error.
  async function sendInBatches(items, send, onBatch, limit = MAX_IMAGES_PER_REQUEST,
                               isCancelled = () => false) {
    let done = 0;
    let sentAny = false;
    let retriedLimit = false;
    let size = limit;
    while (done < items.length) {
      // Checked between requests: one already sent finishes and is kept.
      if (isCancelled()) return { sentAny, limit: size, failure: null, cancelled: true, done };
      const batch = items.slice(done, done + size);
      const range = { from: done + 1, to: done + batch.length, total: items.length };
      const response = await send(batch, range) ||
        { ok: false, error: "No answer from the extension's background worker." };
      if (response.ok) {
        sentAny = true;
        const halt = await onBatch(batch, response.data || {});
        done += batch.length;
        if (response.data && response.data.cancelled) return { sentAny, limit: size, failure: null, cancelled: true, done };
        // The app stopped the whole request (every later page would fail
        // the same way); sending the rest would only repeat that.
        if (halt === true) return { sentAny, limit: size, failure: null, halted: true };
        continue;
      }
      if (response.cancelled) return { sentAny, limit: size, failure: null, cancelled: true, done };
      const match = response.status === 413 && /at most (\d+) images/.exec(response.error || "");
      if (match && !retriedLimit && Number(match[1]) > 0 && Number(match[1]) < batch.length) {
        retriedLimit = true;
        size = Number(match[1]);
        continue;
      }
      // Every image in this batch was judged not to be a page: nothing
      // is wrong, so carry on with the next batch.
      if (response.status === 422) {
        await onBatch(batch, { pages: [], skipped: batch.map(({ extracted }) => ({
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
  function reportProgress(text, fraction = null) {
    try {
      chrome.runtime.sendMessage({ type: "progress", text, fraction }).catch(() => {});
    } catch (e) { /* nobody is listening */ }
  }

  // The one message both the visible-pages and whole-chapter paths send, so
  // the worker sees the same shape from either.
  function sendBatch(items, { dramaId, store, filterPages }) {
    return chrome.runtime.sendMessage({
      type: "send",
      images: items.map(({ extracted }) => ({
        data: extracted.data,
        content_type: extracted.content_type,
        url: extracted.url,
        key: extracted.hash,
      })),
      dramaId,
      store,
      sourceUrl: location.href,
      filterPages,
    });
  }

  function regionsByKey(data) {
    const byKey = new Map();
    for (const page of data.pages || []) {
      const regions = page.regions || [];
      // Rides on the array so every place that caches or redraws a page's regions carries it too.
      regions.typesetImage = typeof page.typeset_image === "string" ? page.typeset_image : "";
      byKey.set(page.key, regions);
    }
    return byKey;
  }

  async function translateVisible(options) {
    // A capture sends the same pages itself; running both would save a page twice.
    if (state.capture) {
      return { ok: false, error: "A chapter capture is running on this page; wait for it or cancel it first." };
    }
    if (state.visible) {
      return { ok: false, error: "This page is already being translated; wait for it or cancel it first." };
    }
    const run = { cancelled: false, text: "Reading pages…", startedAt: Date.now() };
    state.visible = run;
    let done = { failed: true, text: "Translating the page's images stopped unexpectedly." };
    try {
      const result = await translateVisibleRun(options, run);
      done = result.ok
        ? { failed: false, text: (result.data.stopped && result.data.stopped.message) || "Finished." }
        : { failed: true, text: result.error };
      return result;
    } finally {
      state.visible = null;
      // A popup reopened mid-run waits for this to hide its Cancel button.
      try { chrome.runtime.sendMessage({ type: "captureDone", ...done }).catch(() => {}); } catch (e) { /* popup closed */ }
    }
  }

  async function translateVisibleRun({ dramaId, store, all }, run) {
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
    const needAccess = new Set();
    const unverified = new Set();
    const fromCache = [];
    for (const [position, el] of chosen.entries()) {
      try {
        const extracted = await extractBytes(el);
        const cached = state.cache.get(extracted.hash);
        if (cached) {
          drawOverlay(el, cached);
          fromCache.push(el);
          continue;
        }
        if (extracted.unverified) unverified.add(extracted.hash);
        const entry = pending.get(extracted.hash);
        if (entry) {
          entry.elements.push(el);
        } else {
          pending.set(extracted.hash, { extracted, elements: [el], position: position + 1 });
        }
      } catch (e) {
        if (e && e.code === "NEEDS_PERMISSION") needAccess.add(e.origin);
        unreadable.push({ position: position + 1,
                          error: errorText(e) });
      }
    }
    const images = [...pending.values()];

    if (!images.length) {
      if (fromCache.length) {
        watchForPageChanges();
        return { ok: true, data: { pages: [], cached: fromCache.length,
                                   captured: chosen.length, unreadable, ...accessNeeded(needAccess) } };
      }
      return unreadable.length
        ? unreadableResult(unreadable[0].error, needAccess)
        : { ok: false, error: "Nothing on this page could be read as an image." };
    }

    const pages = [];
    const skipped = [];
    const failedPages = [];
    const totals = { sent: 0, received: 0, stored: 0, alreadyStored: 0 };
    // Name a problem page by where it sits in the chapter, which is what a
    // person can check, not by its content hash.
    const positionByKey = new Map(images.map((i) => [i.extracted.hash, i.position]));
    let drawn = 0;
    let serverStop = "";
    const outcome = await sendInBatches(images, async (batch, range) => {
      run.text = `Translating ${range.from}-${range.to} of ${range.total}...`;
      reportProgress(run.text, (range.from - 1) / range.total);
      // A deliberate single send is the image the person pointed at, so
      // the server's page filter shouldn't second-guess it. The server
      // applies the filter to each request on its own, so a batch is
      // clustered against its own neighbours only.
      return sendBatch(batch, { dramaId, store, filterPages: images.length > 1 });
    }, (batch, data) => {
      // Drawn per batch so a long chapter shows results as they arrive
      // and keeps them if a later batch fails.
      const byHash = regionsByKey(data);
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
      for (const item of [...(data.failed || []), ...(data.skipped || [])]) {
        item.position = positionByKey.get(item.key);
      }
      failedPages.push(...(data.failed || []));
      totals.sent += Number.isFinite(data.sent) ? data.sent : batch.length;
      totals.received += Number.isFinite(data.received) ? data.received : batch.length;
      totals.stored += Number.isFinite(data.stored) ? data.stored : 0;
      totals.alreadyStored += Number.isFinite(data.alreadyStored) ? data.alreadyStored : 0;
      if (data.stopped) serverStop = String(data.stopped);
      return !!data.stopped;
    }, undefined, () => run.cancelled);

    if (outcome.sentAny) watchForPageChanges();
    const data = { pages, skipped, failed: failedPages, ...totals, drawn,
                   cached: fromCache.length, captured: chosen.length, unreadable,
                   unverified: unverified.size, ...accessNeeded(needAccess) };
    if (outcome.failure) {
      const f = outcome.failure;
      if (!pages.length && !skipped.length) {
        return { ok: false, error: f.reason, ...(f.status ? { status: f.status } : {}) };
      }
      data.stopped = {
        done: f.done, total: images.length, at: f.done + 1, reason: f.reason,
        // The reason can be a raw network error, so the message names the
        // place only.
        message: `Translated ${f.done} of ${images.length} pages; stopped at page ` +
                 `${f.done + 1} because the app stopped answering`,
      };
    }
    if (serverStop) {
      data.stopped = { reason: serverStop, message: `Stopped early: ${serverStop}` };
    }
    if (outcome.cancelled) {
      data.stopped = { reason: "cancelled",
                       message: `Cancelled after ${pages.length + skipped.length + failedPages.length} ` +
                                `of ${images.length} pages; the pages already saved are kept.` };
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
  // A slow lazy-loader can go quiet for longer than a few scroll steps. Before ending short of
  // the page count the reader shows (or with images still loading), wait this long and sweep
  // again, a bounded number of times so a wrong counter only costs a few seconds.
  const CAPTURE_PATIENT_WAIT_MS = 3000;
  const CAPTURE_PATIENT_TRIES = 3;
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

  // Whether the run should wait and sweep once more instead of ending: the reader's counter says
  // more pages exist than were found, or images are still loading.
  function keepWaiting(found, expected, loadingImages, tries) {
    if (tries >= CAPTURE_PATIENT_TRIES) return false;
    return (expected > found) || loadingImages > 0;
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

  // The page's own canvas identity and size are in the key because a reader that repaints a canvas
  // without touching its data-src would otherwise keep the key, and the previous page's overlay.
  // No URL means no key, so nothing is ever reused for such a canvas.
  const taintedIds = new WeakMap();
  let nextTaintedId = 0;
  function taintedKey(el) {
    const url = findImageUrl(el);
    if (!url) return null;
    if (!taintedIds.has(el)) taintedIds.set(el, ++nextTaintedId);
    return `tainted:${taintedIds.get(el)}:${el.width}x${el.height}:${url}`;
  }

  // A canvas has no src, so its draw target is identified by the hash of its pixels: a reader that
  // repaints the canvas with another page while a batch is in flight changes the hash, and the
  // bubbles are not drawn on the wrong page.
  function drawTargetKey(el, hash) {
    if (taintedCanvases.has(el)) return taintedKey(el);
    return el.tagName === "CANVAS" ? hash : elementKey(el);
  }

  async function currentDrawTargetKey(el) {
    if (el.tagName !== "CANVAS") return elementKey(el);
    // Its pixels can never be hashed; the file it was painted from stands in.
    if (taintedCanvases.has(el)) return taintedKey(el);
    try {
      const blob = await new Promise((resolve, reject) => {
        el.toBlob((b) => (b ? resolve(b) : reject(new Error("unreadable"))), "image/png");
      });
      return await sha256Hex(await blob.arrayBuffer());
    } catch (e) {
      return null;
    }
  }

  // Unreadable pages are counted once per element and what it held: a
  // virtualised reader recycles elements, so the element alone would drop a
  // later miss, while re-reading the same failure every step would repeat it.
  // elementKey is null for a canvas, and a virtualised reader recycles
  // same-sized canvases, so the page's place in the chapter must be part of
  // the signature or a second failed page on one canvas is never counted.
  function canvasSignature(el, place) {
    const { width, height } = elementSize(el);
    return `canvas|${width}x${height}|${place}`;
  }

  function unreadableLog() {
    const last = new WeakMap();
    return {
      isNew(el, signature) {
        if (last.get(el) === signature) return false;
        last.set(el, signature);
        return true;
      },
      read(el) { last.delete(el); },
    };
  }

  function elementKey(el) {
    if (el.tagName === "CANVAS") return null;
    const { width, height } = elementSize(el);
    return `${el.currentSrc || el.src}|${width}x${height}`;
  }

  function formatElapsed(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const seconds = String(total % 60).padStart(2, "0");
    return total >= 3600
      ? `${Math.floor(total / 3600)}:${String(Math.floor(total / 60) % 60).padStart(2, "0")}:${seconds}`
      : `${Math.floor(total / 60)}:${seconds}`;
  }

  function showCaptureChip(run) {
    ensureStyles();
    const chip = document.createElement("div");
    chip.className = "baihe-toast baihe-capture-chip";
    const label = document.createElement("span");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => requestCancel(run));
    const bar = document.createElement("progress");
    bar.max = 1;
    bar.hidden = true;
    chip.append(label, bar, cancel);
    document.documentElement.appendChild(chip);
    return {
      chip,
      // A null fraction (the reader shows no page counter) hides the bar rather than guessing.
      set: (text, fraction = null) => {
        label.textContent = text;
        bar.hidden = fraction === null;
        if (fraction !== null) bar.value = fraction;
      },
    };
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
    const run = { cancelled: false, text: "Starting…", startedAt: Date.now(), tick: null };
    state.capture = run;
    const ui = showCaptureChip(run);
    run.ui = ui;
    // A batch can take a minute (more with typeset on), so the clock keeps moving between reports.
    const ticker = setInterval(() => { if (run.tick) run.tick(); }, 1000);
    const scroller = makeScroller();
    const startTop = scroller.top();
    // A popup reopened mid-run waits for this, so every exit path sends it.
    let done = { failed: true, text: "The capture stopped unexpectedly." };
    try {
      const result = await runCapture(run, ui, scroller, { dramaId, store, fromHere });
      done = result.ok
        ? { failed: result.data.reason === "error", text: result.data.message }
        : { failed: true, text: result.error };
      return result;
    } finally {
      clearInterval(ticker);
      scroller.to(startTop);
      ui.chip.remove();
      state.capture = null;
      try { chrome.runtime.sendMessage({ type: "captureDone", ...done }).catch(() => {}); } catch (e) { /* popup closed */ }
    }
  }

  // The closing line of a chapter capture: every page found is either
  // counted in it or named as unreadable, so a shortfall is never silent.
  function captureTally(found, counts, firstFailure, unreadablePages, firstSkip = "", englishProblem = "") {
    const tally = [`${found} page${found === 1 ? "" : "s"} found`];
    if (counts.translated) tally.push(`${counts.translated} translated`);
    if (counts.cached) tally.push(`${counts.cached} already done`);
    if (counts.alreadyStored) tally.push(`${counts.alreadyStored} already in library`);
    if (counts.skipped) {
      tally.push(`${counts.skipped} skipped as not a page (${firstSkip || "no reason was given"})`);
    }
    if (counts.noText) tally.push(`${counts.noText} saved with no text found on them (notice pages and art without lettering look like this)`);
    if (counts.noEnglish) {
      tally.push(`${counts.noEnglish} saved with text read but no English back` +
                 (englishProblem ? ` (${englishProblem})` : " (check the engine under Settings > Browser extension)"));
    }
    if (counts.failed) tally.push(`${counts.failed} failed (${firstFailure})`);
    // An image identical to an earlier page (a repeated credit card, a banner) is sent once.
    if (counts.duplicates) tally.push(`${counts.duplicates} identical to a page already read`);
    if (counts.unverified) tally.push(`${counts.unverified} read from the page's source image, not checked against what is drawn`);
    if (unreadablePages.length) {
      const named = unreadablePages.slice(0, 3)
        .map((u) => `page ${u.position}: ${u.error}`).join("; ");
      const more = unreadablePages.length > 3 ? `; and ${unreadablePages.length - 3} more` : "";
      tally.push(`${unreadablePages.length} unreadable (${named}${more})`);
    }
    // Every page found must land in one bucket above; the rest were never answered for (a stop
    // part-way, a cancel, or a reply that left a page out), and saying so keeps a short count from
    // looking like a complete one.
    const accounted = counts.translated + counts.cached + counts.alreadyStored + counts.skipped +
                      counts.failed + unreadablePages.length;
    if (found > accounted) tally.push(`${found - accounted} not sent or not answered`);
    return tally.join(", ");
  }

  // The line that accounts for every page found: saved, typeset, and each one that is neither, by
  // its place in the chapter and the reason, so a short count is never unexplained.
  function completenessLine(found, entries, repeats, unreadable, counts, timing, expected = 0) {
    const place = (list, why) => list.slice(0, 6).map((e) => `page ${e.position} (${why(e)})`).join(", ") +
                                 (list.length > 6 ? `, and ${list.length - 6} more` : "");
    const saved = entries.filter((e) => e.status === "saved" || e.status === "read").length;
    const parts = [`Saved ${saved} of ${found} pages`];
    if (expected && expected !== found) parts.push(`the reader shows ${expected} pages, found ${found}`);
    const drawn = entries.filter((e) => e.typeset !== undefined);
    if (drawn.length) parts.push(`typeset ${drawn.filter((e) => e.typeset === true).length} of ${drawn.length}`);
    const missed = entries.filter((e) => e.status !== "saved" && e.status !== "read")
                          .sort((a, b) => a.position - b.position);
    const reasons = { skipped: (e) => `skipped: ${e.reason || "not judged a page"}`,
                      failed: (e) => `failed: ${e.reason || "no reason given"}`,
                      queued: () => "never sent or no answer" };
    if (missed.length) parts.push(`${missed.length} missed: ${place(missed, (e) => reasons[e.status](e))}`);
    if (unreadable.length) parts.push(`${unreadable.length} unreadable: ${place(unreadable, (u) => u.error)}`);
    const plain = drawn.filter((e) => e.typeset === false);
    if (plain.length) {
      parts.push(`${plain.length} saved without a typeset image: ` +
                 place(plain, (e) => (e.noText ? "no text found" : "typeset failed")));
    }
    if (repeats.length) parts.push(`${repeats.length} repeat${repeats.length === 1 ? "" : "s"} of an earlier page left out`);
    if (counts.untranslatedLines) parts.push(`${counts.untranslatedLines} line${counts.untranslatedLines === 1 ? "" : "s"} with no English`);
    if (timing.pages) {
      const average = (total) => (total / timing.pages).toFixed(1);
      parts.push(`per page: read text ${average(timing.read)}s, translate ${average(timing.translate)}s` +
                 (timing.typeset ? `, typeset ${average(timing.typeset)}s` : ""));
    }
    return parts.join("; ");
  }

  // Sends the pages a finished capture could not save once more, without the page filter's judgement.
  async function retryMissed() {
    const missed = state.missed;
    if (!missed || !missed.items.length) return { ok: false, error: "No missed pages to retry." };
    if (state.capture || state.visible) return { ok: false, error: "Wait for the running capture to finish first." };
    let saved = 0;
    const outcome = await sendInBatches(missed.items, (items) =>
      sendBatch(items, { dramaId: missed.dramaId, store: missed.store, filterPages: false }),
    (items, data) => { saved += (data.pages || []).length; return !!data.stopped; });
    const left = missed.items.length - saved;
    state.missed = left > 0 && outcome.failure ? { ...missed, items: missed.items.slice(saved) } : null;
    const why = outcome.failure ? ` (${outcome.failure.reason})` : "";
    return { ok: true, data: { message: `Retried ${missed.items.length} missed pages: ${saved} saved, ${Math.max(0, left)} still missing${why}.`,
                               missedCount: state.missed ? state.missed.items.length : 0 } };
  }

  async function runCapture(run, ui, scroller, { dramaId, store, fromHere }) {
    // Hashes already handled this run (sent, cached, skipped or duplicate).
    // Distinct pages seen is also what the cap counts.
    const seen = new Set();
    const handled = new WeakMap();
    const queue = [];
    // Every distinct page met, by hash: where it sits, and what became of it. This is what lets the
    // closing line say which pages are not saved, and why, instead of only how many.
    const ledger = new Map();
    const repeats = [];
    const timing = { read: 0, translate: 0, typeset: 0, pages: 0 };
    const counts = { translated: 0, stored: 0, alreadyStored: 0, cached: 0, skipped: 0, failed: 0, drawn: 0, unverified: 0, duplicates: 0, noText: 0, noEnglish: 0, untranslatedLines: 0 };
    let firstFailure = "";
    let firstSkip = "";
    let firstEnglishProblem = "";
    let serverStop = "";
    let seq = 0;
    let limit = MAX_IMAGES_PER_REQUEST;
    let capHit = false;
    let unreadable = "";
    const needAccess = new Set();
    // A tainted canvas has no element key to skip it by, so without this every step would download,
    // re-encode and re-hash it again (up to 12 MB each).
    const taintedReads = new Map();
    // Pages that could not be read, numbered in the order they were met, so
    // the closing summary can name them like Translate-visible does.
    const unreadablePages = [];
    const unreadableAs = unreadableLog();
    let failure = "";

    let line = "";
    let fraction = null;
    const emit = () => {
      // After a cancel the line stays "Cancelling…" however late a batch reports back.
      if (run.cancelled) return;
      run.text = `${line} · ${formatElapsed(Date.now() - run.startedAt)}`;
      ui.set(run.text, fraction);
      reportProgress(run.text, fraction);
    };
    run.tick = emit;
    line = fromHere ? "Looking for pages from here" : "Scrolling to the top to start";
    emit();
    const report = (suffix = "") => {
      const counter = readerCounter();
      line = (counter ? `Page ${counter.current} of ${counter.total}`
                      : `${seen.size} page${seen.size === 1 ? "" : "s"} found`) + suffix;
      fraction = counter ? counter.current / counter.total : null;
      emit();
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
          const taintedId = el.tagName === "CANVAS" ? taintedKey(el) : null;
          extracted = taintedId && taintedReads.get(taintedId);
          if (!extracted) {
            extracted = await extractBytes(el);
            if (taintedCanvases.has(el) && taintedId) taintedReads.set(taintedId, extracted);
          }
        } catch (e) {
          unreadable = errorText(e);
          if (e && e.code === "NEEDS_PERMISSION") needAccess.add(e.origin);
          const signature = key !== null ? key
            : canvasSignature(el, readerIndexOf(el) ?? Math.round(scroller.positionOf(el)));
          if (unreadableAs.isNew(el, signature)) {
            unreadablePages.push({ position: seen.size + unreadablePages.length + 1,
                                   error: unreadable });
          }
          if (key !== null) handled.set(el, key);
          continue;
        }
        unreadableAs.read(el);
        if (key !== null) handled.set(el, key);
        const cached = state.cache.get(extracted.hash);
        if (seen.has(extracted.hash)) {
          counts.duplicates += 1;
          repeats.push(ledger.get(extracted.hash)?.position);
          if (cached && el.isConnected) drawOverlay(el, cached);
          continue;
        }
        seen.add(extracted.hash);
        // A page answered earlier in this tab was saved by that run.
        const entry = { position: seen.size, status: cached ? "saved" : "queued", item: null };
        ledger.set(extracted.hash, entry);
        found += 1;
        if (extracted.unverified) counts.unverified += 1;
        if (cached) {
          drawOverlay(el, cached);
          counts.cached += 1;
          continue;
        }
        seq += 1;
        entry.item = { extracted, elements: [el], srcKey: drawTargetKey(el, extracted.hash), seq,
                       order: { index: readerIndexOf(el), pos: scroller.positionOf(el) } };
        queue.push(entry.item);
      }
      return found;
    }

    // Sends queued pages in reading order, a batch at a time, through the
    // same sendInBatches the visible-pages path uses. Returns a failure
    // reason, or "".
    const sendRange = async (items, range) => {
      report(` — translating ${range.from}-${range.to} of ${range.total}`);
      return sendBatch(items, { dramaId, store, filterPages: items.length > 1 });
    };
    const onBatch = async (items, data) => {
      recordAnswers(data);
      const byHash = regionsByKey(data);
      for (const { extracted, elements, srcKey } of items) {
        const regions = byHash.get(extracted.hash);
        if (!regions) continue;
        state.cache.set(extracted.hash, regions);
        // A virtualised reader may have recycled the element for a
        // later page while this batch was in flight; drawing then
        // would put these bubbles on the wrong page.
        for (const el of elements) {
          if (srcKey && el.isConnected && await currentDrawTargetKey(el) === srcKey) {
            drawOverlay(el, regions);
            counts.drawn += 1;
          }
        }
      }
      const reused = (data.pages || []).filter((p) => p.already_stored).length;
      counts.translated += (data.pages || []).length - reused;
      counts.alreadyStored += reused;
      counts.stored += (data.pages || []).filter((p) => p.stored).length;
      counts.skipped += (data.skipped || []).length;
      counts.failed += (data.failed || []).length;
      // A saved page is only "translated" in the line if English came back, so the three ways
      // it can come back without it are told apart: no text read, no engine answer, or both.
      for (const page of data.pages || []) {
        const regions = page.regions || [];
        if (!regions.length) {
          counts.noText += 1;
        } else if (regions.some((r) => r.untranslated)) {
          counts.noEnglish += 1;
          if (!firstEnglishProblem) {
            const note = (page.notes || []).find((n) => /translat|engine|key/i.test(String(n[1])));
            firstEnglishProblem = note ? String(note[1]) : "";
          }
        }
      }
      if (!firstFailure && (data.failed || []).length) firstFailure = data.failed[0].error;
      if (!firstSkip && (data.skipped || []).length) firstSkip = data.skipped[0].reason || "";
      if (data.stopped) serverStop = String(data.stopped);
      return !!data.stopped;
    };

    // Records what each answered page became, so the closing line can account for every page.
    function recordAnswers(data) {
      for (const page of data.pages || []) {
        const entry = ledger.get(page.key);
        if (!entry) continue;
        entry.status = page.stored || page.already_stored ? "saved" : "read";
        entry.typeset = page.typeset;
        entry.noText = !(page.regions || []).length;
        entry.item = null;
        counts.untranslatedLines += page.untranslated_lines || 0;
        const seconds = page.seconds || {};
        timing.read += seconds.read || 0;
        timing.translate += seconds.translate || 0;
        timing.typeset += seconds.typeset || 0;
        timing.pages += 1;
      }
      for (const item of data.skipped || []) {
        const entry = ledger.get(item.key);
        if (entry) Object.assign(entry, { status: "skipped", reason: item.reason || "" });
      }
      for (const item of data.failed || []) {
        const entry = ledger.get(item.key);
        if (entry) Object.assign(entry, { status: "failed", reason: item.error || "" });
      }
    }

    async function flush(force) {
      while (!run.cancelled && (queue.length >= limit || (force && queue.length))) {
        queue.sort(byReadingOrder);
        const batch = queue.splice(0, limit);
        const outcome = await sendInBatches(batch, sendRange, onBatch, limit, () => run.cancelled);
        limit = outcome.limit || limit;
        if (outcome.sentAny) watchForPageChanges();
        if (outcome.cancelled || run.cancelled) return "";
        if (outcome.failure) return outcome.failure.reason;
        if (serverStop) return serverStop;
      }
      return "";
    }

    // One more try for pages that failed on delivery (a dropped connection, a busy app), without the
    // page filter's judgement, before the closing line reports them as missed.
    async function retryFailed() {
      const again = [...ledger.values()].filter((e) => e.status === "failed" && e.item).map((e) => e.item);
      if (!again.length || run.cancelled) return;
      counts.failed = Math.max(0, counts.failed - again.length);
      for (const item of again) ledger.get(item.extracted.hash).status = "queued";
      await sendInBatches(again, async (items, range) => {
        report(` — retrying ${range.from}-${range.to} of ${range.total} missed`);
        return sendBatch(items, { dramaId, store, filterPages: false });
      }, onBatch, limit, () => run.cancelled);
      // Whatever is still unanswered stays queued; the closing line names it.
    }

    if (!fromHere) {
      scroller.to(0);
      await sleep(stepDelay());
    }

    let reason = "";
    let stalls = 0;
    let steps = 0;
    let lastMoved = true;
    let expected = 0;
    let patientTries = 0;
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
      // Scrolling the whole chapter cannot help until the person allows the site.
      if (needAccess.size && !seen.size) { reason = "end"; break; }
      stalls = found ? 0 : stalls + 1;
      const counter = readerCounter();
      if (counter) expected = Math.max(expected, counter.total);
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
      if ((reason === "end" || reason === "stalled") && !capHit && steps < CAPTURE_MAX_STEPS
          && keepWaiting(seen.size, expected, [...document.images].filter((i) => !i.complete).length,
                         patientTries)) {
        patientTries += 1;
        reason = "";
        stalls = 0;
        report(` — waiting for more pages${expected ? ` (${seen.size} of ${expected})` : ""}`);
        await sleep(CAPTURE_PATIENT_WAIT_MS);
        await settleForImages();
        scroller.to(scroller.max());
        await sleep(stepDelay());
        lastMoved = true;
        continue;
      }
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
      else await retryFailed();
      if (run.cancelled) reason = "cancelled";
    }

    if (!seen.size) {
      if (reason === "error") return { ok: false, error: failure };
      return unreadable
        ? unreadableResult(unreadable, needAccess)
        : { ok: false, error: "No page-sized images found here. If the page is still loading, try again." };
    }
    const tally = captureTally(seen.size, counts, firstFailure, unreadablePages, firstSkip, firstEnglishProblem);
    const entries = [...ledger.values()];
    const complete = completenessLine(seen.size, entries, repeats, unreadablePages, counts, timing, expected);
    const missedItems = entries.filter((e) => e.status !== "saved" && e.status !== "read" && e.item)
                               .map((e) => e.item);
    state.missed = missedItems.length ? { items: missedItems, dramaId, store } : null;
    const message = `${reason === "error" ? failure : CAPTURE_STOP_MESSAGES[reason]} ${complete} (${tally}). ` +
                    `Took ${formatElapsed(Date.now() - run.startedAt)}.`;
    reportProgress(message);
    // A failure text can name the server's address, which a page's own scripts could read from the DOM.
    if (CAPTURE_STOP_MESSAGES[reason]) toast(message, 8000);
    return { ok: true, data: { reason, message, found: seen.size, ...counts,
                               missedCount: missedItems.length,
                               unreadable: unreadablePages, pages: [],
                               ...accessNeeded(needAccess) } };
  }

  // The one way a run is cancelled, from the page's chip or the popup: the label and the popup's
  // line change at once, and the request in flight is dropped instead of waited for.
  function requestCancel(run) {
    if (run.cancelled) return;
    run.cancelled = true;
    run.text = "Cancelling… stopping the request in flight";
    if (run.ui) run.ui.set(run.text, null);
    reportProgress(run.text, null);
    try { chrome.runtime.sendMessage({ type: "abortSends" }).catch(() => {}); } catch (e) { /* worker asleep */ }
  }

  function cancelCapture() {
    const running = state.capture || state.visible;
    if (!running) return false;
    requestCancel(running);
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
          case "retryMissed":
            respond(await retryMissed());
            break;
          case "saveNovelText":
            respond(await saveNovelText(message));
            break;
          case "pageFacts":
            respond({ ok: true, data: pageFacts() });
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
              // The popup shows Cancel for either kind of run, on any tab, and again when reopened.
              visibleRun: state.visible ? { running: true, text: state.visible.text } : null,
              textPage: isTextPage(),
              hasSelection: !!selectedText(),
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
        respond({ ok: false, error: errorText(e) });
      }
    })();
    return true;
  });

  // Exposed for the popup's injected checks and for tests.
  window.__baihe = { canvasSignature, translateVisible, sendInBatches, setOverlaysVisible, candidateElements, state, toast,
                     translatePageText, collectPageText, mainContentBlock,
                     isTextPage, chapterHeading, saveNovelText, pageFacts,
                     looksLikeChallengePage, sampleSignature, waitForStableSignature,
                     captureChapter, cancelCapture, captureTally, keepWaiting, unreadableLog, placeBox, regionsByKey, requestCancel,
                     completenessLine };
})();
