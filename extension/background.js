// background.js -- the only place that holds the token and talks to the app.
//
// Why the fetch lives here and not in the content script:
//
//   1. Security. The token must never enter a web page's JavaScript
//      context. A content script shares the page's world closely enough
//      that putting a shared secret there is asking for it to leak, so
//      the page side never sees it -- it asks this worker to send things
//      on its behalf.
//   2. CORS. A content script's fetch is subject to the page's own CORS
//      rules, and the endpoint deliberately answers no preflight and
//      sends no Access-Control-Allow-Origin (that refusal is what stops
//      any open tab from driving the app). A service-worker fetch under
//      `host_permissions` is not subject to page CORS, so it works
//      without the endpoint having to open itself up.
//
// If you ever see a CORS error here, the fix is to make the request from
// this worker -- never to add a permissive header on the server.

// Fixed on purpose: the app's bridge always binds this port (page_server.DEFAULT_PORT)
// and the manifest's host permission is narrowed to it, so it is not a setting.
const BRIDGE_PORT = 8756;

// An earlier version stored a "port" the app never honoured; drop it.
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.remove("port");
});

async function settings() {
  const stored = await chrome.storage.local.get(
    ["token", "dramaBySite", "overlay", "textDirection"]);
  return {
    token: stored.token || "",
    dramaBySite: stored.dramaBySite || {},
    overlay: stored.overlay !== false,
    textDirection: stored.textDirection || { source: "zh", target: "en" },
  };
}

function base() {
  return `http://127.0.0.1:${BRIDGE_PORT}`;
}

// Every request carries the token as a header. Never as a query
// parameter: a URL reaches logs, history and referrers, and this repo's
// standing rule is that nothing token-shaped goes into one.
async function call(path, { method = "GET", body = null } = {}) {
  const { token } = await settings();
  if (!token) {
    return { ok: false, error: "No token yet. Open the extension's options and paste the token from Baihe's Settings." };
  }
  let response;
  try {
    response = await fetch(base() + path, {
      method,
      headers: {
        "X-Baihe-Token": token,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    // A refused connection is the ordinary "the app isn't running" case,
    // and is worth saying plainly rather than as a network error.
    return {
      ok: false,
      error: `Couldn't reach Baihe on ${base()}. Is the app running, with the Extension bridge switch on in Settings → Browser extension?`,
    };
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch (e) {
    payload = null;
  }
  if (!response.ok) {
    const detail = (payload && payload.error) || `HTTP ${response.status}`;
    return { ok: false, error: detail, status: response.status };
  }
  return { ok: true, data: payload };
}

async function health() {
  return call("/health");
}

// Text mode sends the app raw text instead of an image -- see
// content.js's collectPageText. Same token, same server, no new auth.
async function sendText({ text, sourceLanguage, targetLanguage, store }) {
  if (!text) {
    return { ok: false, error: "No text was captured on this page." };
  }
  return call("/text", {
    method: "POST",
    body: {
      text,
      source_language: sourceLanguage || "zh",
      target_language: targetLanguage || "en",
      store: store !== false,
    },
  });
}

// An app that predates the advertised cap accepts at least this many.
const FALLBACK_BATCH_IMAGES = 8;
// Base64 of PNG pages is large; the bridge refuses a body over 64MB, so a
// batch stays well under that however few images it holds.
const MAX_BATCH_BYTES = 24 * 1024 * 1024;

async function batchCap() {
  const answer = await health();
  const advertised = answer.ok && answer.data && Number(answer.data.max_images_per_request);
  return advertised > 0 ? advertised : FALLBACK_BATCH_IMAGES;
}

function planBatches(images, cap) {
  const batches = [];
  let current = [];
  let bytes = 0;
  for (const image of images) {
    const size = (image.data || "").length;
    if (current.length && (current.length >= cap || bytes + size > MAX_BATCH_BYTES)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(image);
    bytes += size;
  }
  if (current.length) batches.push(current);
  return batches;
}

// A whole chapter is many requests, never one: the bridge caps a request, and
// one failing batch must not hide the pages that did arrive. The totals let
// the popup show sent vs. received vs. stored instead of a bare page count.
async function sendImages({ images, dramaId, sourceUrl, store, filterPages }) {
  if (!images || !images.length) {
    return { ok: false, error: "No page images were found on this page." };
  }
  const cap = await batchCap();
  const merged = { pages: [], skipped: [], failed: [], sent: images.length, received: 0, stored: 0 };
  let firstError = null;
  for (const batch of planBatches(images, cap)) {
    const answer = await call(batch.length === 1 && images.length === 1 ? "/page" : "/pages", {
      method: "POST",
      body: {
        images: batch,
        drama_id: dramaId || null,
        source_url: sourceUrl || "",
        store: store !== false,
        filter_pages: filterPages !== false,
      },
    });
    if (!answer.ok) {
      firstError = firstError || answer;
      for (const image of batch) {
        merged.failed.push({ key: image.key, url: image.url || "", error: answer.error });
      }
      continue;
    }
    const data = answer.data || {};
    merged.pages.push(...(data.pages || []));
    merged.skipped.push(...(data.skipped || []));
    merged.failed.push(...(data.failed || []));
    merged.received += Number.isFinite(data.received) ? data.received : batch.length;
    merged.stored += Number.isFinite(data.stored) ? data.stored : 0;
  }
  // Nothing arrived at all: surface the app's own refusal (bad token, bridge
  // off, no such drama) rather than a table of identical per-page failures.
  if (firstError && !merged.pages.length && !merged.skipped.length && !merged.received) {
    return firstError;
  }
  return { ok: true, data: merged };
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  (async () => {
    try {
      switch (message && message.type) {
        case "health":
          respond(await health());
          break;
        case "send":
          respond(await sendImages(message));
          break;
        // Interim status from the content script; the popup, if open,
        // receives it too. Nothing to do here.
        case "progress":
        case "captureDone":
          respond({ ok: true });
          break;
        case "sendText":
          respond(await sendText(message));
          break;
        case "getSettings":
          respond({ ok: true, data: await settings() });
          break;
        case "rememberDrama": {
          const { dramaBySite } = await settings();
          dramaBySite[message.site] = message.dramaId;
          await chrome.storage.local.set({ dramaBySite });
          respond({ ok: true });
          break;
        }
        default:
          respond({ ok: false, error: `Unknown request: ${message && message.type}` });
      }
    } catch (e) {
      respond({ ok: false, error: String(e && e.message ? e.message : e) });
    }
  })();
  return true;   // keeps the message channel open for the async respond
});
