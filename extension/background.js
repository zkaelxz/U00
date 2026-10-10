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

importScripts("site_access.js");

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

// minSize 2 when the app's page filter runs: it only filters a request of two
// or more images, so a lone leftover would skip it and store an ad strip.
function planBatches(images, cap, minSize = 1) {
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
  const last = batches[batches.length - 1];
  const prev = batches[batches.length - 2];
  if (last && prev && last.length < minSize) {
    while (last.length < minSize && prev.length > minSize) last.unshift(prev.pop());
    // A batch closed by bytes can be too small to lend an image, so the short
    // tail joins it; the 64MB body cap leaves room for a few more images.
    if (last.length < minSize && prev.length + last.length <= cap) {
      prev.push(...last);
      batches.pop();
    }
  }
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
  const merged = { pages: [], skipped: [], failed: [], sent: images.length, received: 0, stored: 0, alreadyStored: 0 };
  let firstError = null;
  for (const batch of planBatches(images, cap, filterPages !== false ? 2 : 1)) {
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
    // The app refuses a whole filtered batch with 422 when none of it looks
    // like comic pages: that is a verdict on the images, not a failed delivery.
    if (!answer.ok && answer.status === 422 && filterPages !== false && batch.length > 1) {
      for (const image of batch) {
        merged.skipped.push({ key: image.key, url: image.url || "", reason: answer.error });
      }
      merged.received += batch.length;
      continue;
    }
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
    merged.alreadyStored += Number.isFinite(data.already_stored) ? data.already_stored : 0;
    if (data.stopped) {
      merged.stopped = data.stopped;
      break;
    }
  }
  // Nothing arrived at all: surface the app's own refusal (bad token, bridge
  // off, no such drama) rather than a table of identical per-page failures.
  if (firstError && !merged.pages.length && !merged.skipped.length && !merged.received) {
    return firstError;
  }
  return { ok: true, data: merged };
}

// -- re-fetching an image the page won't let a script read -----------------
//
// A reader that draws a cross-origin <img> (or paints it onto a canvas) without
// CORS taints the canvas, so content.js cannot read its pixels back. The browser
// still lets this worker download the file once the person has allowed that one
// image origin, so the bytes come from here and go through the same upload path.

// The bridge accepts exactly these (page_server.ALLOWED_IMAGE_TYPES) and refuses
// anything over 12 MB, so a download that could never be sent is cut off early.
const FETCH_IMAGE_MAX_BYTES = 12 * 1024 * 1024;
const FETCH_IMAGE_TIMEOUT_MS = 20000;

// Decided from the bytes, not the Content-Type header: a CDN that labels a page
// "application/octet-stream" is still serving an image, and one that labels HTML
// "image/jpeg" is not.
function sniffImageType(bytes) {
  const startsWith = (...sig) => sig.every((b, i) => bytes[i] === b);
  if (startsWith(0xff, 0xd8, 0xff)) return "image/jpeg";
  if (startsWith(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)) return "image/png";
  if (startsWith(0x52, 0x49, 0x46, 0x46) && bytes[8] === 0x57 && bytes[9] === 0x45
      && bytes[10] === 0x42 && bytes[11] === 0x50) return "image/webp";
  return "";
}

function base64Of(bytes) {
  let binary = "";
  const chunk = 0x8000;      // chunked, so a big page can't blow the stack
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

async function readCapped(response, controller) {
  const reader = response.body.getReader();
  const parts = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > FETCH_IMAGE_MAX_BYTES) {
      controller.abort();
      return null;
    }
    parts.push(value);
  }
  const bytes = new Uint8Array(total);
  let at = 0;
  for (const part of parts) { bytes.set(part, at); at += part.length; }
  return bytes;
}

function senderOrigin(sender) {
  try {
    const page = new URL((sender && (sender.url || (sender.tab && sender.tab.url))) || "");
    return page.protocol === "https:" || page.protocol === "http:" ? page.origin : "";
  } catch (e) {
    return "";
  }
}

async function fetchImage({ url }, sender) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch (e) {
    return { ok: false, error: "that image address isn't valid" };
  }
  const target = permissionTarget(parsed);
  if (!target) return { ok: false, error: "that image is not on a public web address" };
  const origins = [target.pattern];
  if (!(await chrome.permissions.contains({ origins }))) {
    // Only the popup can ask: the prompt needs the click that happens there.
    return {
      ok: false, code: "NEEDS_PERMISSION", origin: target.origin,
      error: `The page draws its image from ${target.origin}, which the browser won't let the page read. ` +
             `Click "Allow ${target.origin}" in the extension popup to let it download that image itself.`,
    };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_IMAGE_TIMEOUT_MS);
  try {
    // Cookies are omitted: with a granted host permission the request counts as
    // first-party, so a hostile page could otherwise read another site's private
    // images through the person's login. The referrer is only the page's origin,
    // what a browser sends natively, for CDNs that check it; browsers may ignore
    // a cross-origin referrer set from a worker.
    const options = { credentials: "omit", redirect: "error", signal: controller.signal };
    const pageOrigin = senderOrigin(sender);
    if (pageOrigin) Object.assign(options, { referrer: `${pageOrigin}/`, referrerPolicy: "origin" });
    // The result reports parsed.href, not response.url, so a redirect target can never reach the page.
    // redirect "error" because a redirect target would be requested before it
    // could be checked, letting a granted origin bounce the fetch to a LAN address.
    const response = await fetch(parsed.href, options);
    if (!response.ok) return { ok: false, error: `the image server answered HTTP ${response.status}` };
    const declared = Number(response.headers.get("content-length") || 0);
    if (declared > FETCH_IMAGE_MAX_BYTES) {
      controller.abort();
      return { ok: false, error: "the image is larger than the 12 MB the app accepts" };
    }
    const bytes = await readCapped(response, controller);
    if (!bytes) return { ok: false, error: "the image is larger than the 12 MB the app accepts" };
    const contentType = sniffImageType(bytes);
    if (!contentType) {
      return { ok: false, error: "the address returned something that isn't a PNG, JPEG or WebP image" };
    }
    return { ok: true, data: { data: base64Of(bytes), content_type: contentType, url: parsed.href } };
  } catch (e) {
    return {
      ok: false,
      error: e && e.name === "AbortError" ? "the image download took too long" : "the image could not be downloaded",
    };
  } finally {
    clearTimeout(timer);
  }
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
        case "fetchImage":
          respond(await fetchImage(message, sender));
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
