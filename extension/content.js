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

  const state = {
    overlaysVisible: true,
    // hash -> regions, so paging back to a page already translated is
    // instant and nothing is ever translated twice.
    cache: new Map(),
    // element -> { layer, regions, naturalWidth }
    active: new Map(),
    observer: null,
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

  // Draws the element at its own full resolution and reads the pixels
  // back. This is the step that reaches content an adapter can't: a
  // `blob:` image, or a page the site's own reader has already
  // descrambled onto a canvas, is just pixels here -- the browser has
  // done the work, and nothing is being decrypted or unscrambled.
  async function extractBytes(el) {
    const { width, height } = elementSize(el);
    if (!width || !height) throw new Error("that image hasn't finished loading");
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

  async function sha256Hex(buffer) {
    const digest = await crypto.subtle.digest("SHA-256", buffer);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0")).join("");
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
      .baihe-toast { position: fixed; bottom: 18px; right: 18px; z-index: 2147483600;
                     background: #1b1b1f; color: #fff; padding: 10px 14px;
                     border-radius: 8px; font: 13px/1.4 "Segoe UI", sans-serif;
                     max-width: 340px; box-shadow: 0 4px 16px rgba(0,0,0,0.35); }
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

  // -- the main action -------------------------------------------------

  async function translateVisible({ dramaId, store, all }) {
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

    const response = await chrome.runtime.sendMessage({
      type: "send",
      images: images.map(({ extracted }) => ({
        data: extracted.data,
        content_type: extracted.content_type,
        url: extracted.url,
        key: extracted.hash,
      })),
      dramaId,
      store,
      sourceUrl: location.href,
      // A deliberate single send is the image the person pointed at, so
      // the server's page filter shouldn't second-guess it.
      filterPages: images.length > 1,
    });

    if (!response || !response.ok) {
      return response || { ok: false, error: "No answer from the extension's background worker." };
    }

    const byHash = new Map();
    for (const page of response.data.pages || []) {
      byHash.set(page.key, page.regions || []);
    }
    let drawn = 0;
    for (const { extracted, elements } of images) {
      const regions = byHash.get(extracted.hash);
      if (!regions) continue;
      state.cache.set(extracted.hash, regions);
      for (const el of elements) {
        drawOverlay(el, regions);
        drawn += 1;
      }
    }
    watchForPageChanges();
    return { ok: true, data: { ...response.data, drawn, cached: fromCache.length } };
  }

  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    (async () => {
      try {
        switch (message && message.type) {
          case "translateVisible":
            respond(await translateVisible(message));
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
  window.__baihe = { translateVisible, setOverlaysVisible, candidateElements, state, toast };
})();
