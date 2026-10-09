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

async function sendImages({ images, dramaId, sourceUrl, store, filterPages }) {
  if (!images || !images.length) {
    return { ok: false, error: "No page images were found on this page." };
  }
  const route = images.length === 1 ? "/page" : "/pages";
  return call(route, {
    method: "POST",
    body: {
      images,
      drama_id: dramaId || null,
      source_url: sourceUrl || "",
      store: store !== false,
      filter_pages: filterPages !== false,
    },
  });
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

// The page chooses this URL, so it must not be able to aim the person's browser
// at their own machine or network (including the app on 8600 and the bridge).
function isPrivateHost(hostname) {
  const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (/^(?:.*\.)?local(?:host)?$/.test(host)) return true;
  if (host.includes(":")) return true;     // IPv6 literals: loopback, link-local, ULA
  const m = host.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254)
    || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
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

async function fetchImage({ url }) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch (e) {
    return { ok: false, error: "that image address isn't valid" };
  }
  if ((parsed.protocol !== "https:" && parsed.protocol !== "http:") || isPrivateHost(parsed.hostname)) {
    return { ok: false, error: "that image is not on a public web address" };
  }
  const origins = [`${parsed.origin}/*`];
  if (!(await chrome.permissions.contains({ origins }))) {
    // Only the popup can ask: the prompt needs the click that happens there.
    return {
      ok: false, code: "NEEDS_PERMISSION", origin: parsed.origin,
      error: `The page draws its image from ${parsed.origin}, which the browser won't let the page read. ` +
             'Click "Allow this site" in the extension popup to let it download that image itself.',
    };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_IMAGE_TIMEOUT_MS);
  try {
    // credentials: "include" because some CDNs only serve images to a browser
    // holding the site's cookies. A worker cannot send the page as Referer.
    const response = await fetch(parsed.href, { credentials: "include", signal: controller.signal });
    if (!response.ok) return { ok: false, error: `the image server answered HTTP ${response.status}` };
    // A redirect can lead somewhere the first check would have refused.
    if (response.url && isPrivateHost(new URL(response.url).hostname)) {
      return { ok: false, error: "that image is not on a public web address" };
    }
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
    return { ok: true, data: { data: base64Of(bytes), content_type: contentType, url: response.url || parsed.href } };
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
          respond(await fetchImage(message));
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
