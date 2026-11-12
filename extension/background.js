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

// The popup's "Auto" choices for a page sent with no drama picked; all on, so a first send just works.
const AUTO_TITLE_DEFAULTS = { create: true, fill: true, series: true };

async function settings() {
  const stored = await chrome.storage.local.get(
    ["token", "serverUrl", "dramaBySite", "overlay", "textDirection", "autoTitle", "typeset"]);
  return {
    autoTitle: { ...AUTO_TITLE_DEFAULTS, ...(stored.autoTitle || {}) },
    token: stored.token || "",
    serverUrl: stored.serverUrl || "",
    dramaBySite: stored.dramaBySite || {},
    overlay: stored.overlay !== false,
    // Off by default: clean-and-typeset costs seconds per page, so a person turns it on.
    typeset: stored.typeset === true,
    textDirection: stored.textDirection || { source: "zh", target: "en" },
  };
}

// With a server address saved (https only, checked in options.js) the calls go to that
// Baihe's device-token routes; otherwise to the bridge on this computer.
function base(serverUrl) {
  return serverUrl ? `${serverUrl}/api/ext-bridge` : `http://127.0.0.1:${BRIDGE_PORT}`;
}

// Every request carries the token as a header. Never as a query
// parameter: a URL reaches logs, history and referrers, and this repo's
// standing rule is that nothing token-shaped goes into one.
// Chrome may stop a service worker whose single event runs about five minutes,
// which would drop the reply port without a word. A bridge call that is still
// queued behind a long run (or a first model download) is cut off a little
// earlier and reported honestly: the app keeps working on it.
const CALL_TIMEOUT_MS = 4.5 * 60 * 1000;
const STILL_WORKING = "Baihe is still working; send the rest later.";

// Requests in flight, so a Cancel can end the wait for them at once instead of after the app answers.
const inflight = new Set();

function abortSends() {
  for (const controller of inflight) {
    controller.userAbort = true;
    controller.abort();
  }
  return { ok: true, data: { aborted: inflight.size } };
}

async function call(path, { method = "GET", body = null } = {}) {
  const { token, serverUrl } = await settings();
  if (!token) {
    return { ok: false, error: "No token yet. Open the extension's options and paste the token from Baihe's Settings." };
  }
  let response;
  const controller = new AbortController();
  inflight.add(controller);
  const timer = setTimeout(() => controller.abort(), CALL_TIMEOUT_MS);
  try {
    response = await fetch(base(serverUrl) + path, {
      signal: controller.signal,
      method,
      // The token is the only credential: no cookie rides along, and a redirect is not followed.
      credentials: "omit",
      redirect: "error",
      headers: {
        ...(serverUrl ? { Authorization: `Bearer ${token}` } : { "X-Baihe-Token": token }),
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    clearTimeout(timer);
    inflight.delete(controller);
    if (controller.userAbort) {
      // The app may still finish pages of the batch it was already working on; those are kept there.
      return { ok: false, cancelled: true, error: "Cancelled." };
    }
    if (e && e.name === "AbortError") {
      return {
        ok: false,
        timedOut: true,
        error: "Baihe is still working on this. Check the drama in Baihe in a few minutes; pages already read are kept.",
      };
    }
    // A refused connection is the ordinary "the app isn't running" case,
    // and is worth saying plainly rather than as a network error.
    return {
      ok: false,
      error: serverUrl
        ? `Couldn't reach Baihe at ${serverUrl}.`
        : `Couldn't reach Baihe on ${base()}. Is the app running, with the Extension bridge switch on in Settings → Browser extension?`,
    };
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch (e) {
    payload = null;
  }
  clearTimeout(timer);
  inflight.delete(controller);
  if (!response.ok) {
    // The bridge answers {error: text}; the app's own routes answer {error: {message}}.
    const reported = payload && payload.error;
    const detail = (typeof reported === "string" ? reported : reported && reported.message)
      || `HTTP ${response.status}`;
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

// The page's address becomes the title's source link, so only a public web page's is sent:
// a page on this PC or the LAN (site_access.js decides which) saves its text without one.
function publicPageUrl(url) {
  try {
    return permissionTarget(new URL(url)) ? url : "";
  } catch (e) {
    return "";
  }
}

// Finds or creates the title a page belongs to (the app's extension_title_service); the
// options decide how much it may do. Only a public page's address is sent, as for novels.
async function resolveTitle({ url, titles, siteName, language, kind, chapterLinks }) {
  const { autoTitle } = await settings();
  return call("/title", {
    method: "POST",
    body: { url: publicPageUrl(url), titles: titles || [], ...(siteName ? { site_name: siteName } : {}), language: language || "",
            kind: kind === "novel" ? "novel" : "comic",
            chapter_links: publicPageUrl(url) ? chapterLinks || [] : [], ...autoTitle },
  });
}

async function setAutoTitle({ options }) {
  const next = { ...(await settings()).autoTitle };
  for (const key of Object.keys(AUTO_TITLE_DEFAULTS)) {
    if (options && typeof options[key] === "boolean") next[key] = options[key];
  }
  await chrome.storage.local.set({ autoTitle: next });
  return { ok: true, data: next };
}

async function setTypeset({ on }) {
  await chrome.storage.local.set({ typeset: on === true });
  return { ok: true, data: { typeset: on === true } };
}

async function saveNovelText({ dramaId, heading, text, source, url }) {
  if (!dramaId) return { ok: false, error: "Pick a novel title to save into." };
  if (!text) return { ok: false, error: "No text was captured on this page." };
  const pageUrl = publicPageUrl(url);
  return call("/novel", {
    method: "POST",
    body: { drama_id: dramaId, heading: heading || "", text,
            source: pageUrl ? source || "" : "", url: pageUrl },
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
  const { typeset } = await settings();
  const merged = { pages: [], skipped: [], failed: [], sent: images.length, received: 0, stored: 0, alreadyStored: 0 };
  let firstError = null;
  const batches = planBatches(images, cap, filterPages !== false ? 2 : 1);
  for (const [index, batch] of batches.entries()) {
    // /pages is the only route that runs the page filter, so a chapter of two or more images uses it even if the filter later leaves one.
    const answer = await call(batch.length === 1 && images.length === 1 ? "/page" : "/pages", {
      method: "POST",
      body: {
        images: batch,
        drama_id: dramaId || null,
        source_url: sourceUrl || "",
        store: store !== false,
        filter_pages: filterPages !== false,
        // Only sent when on, so an older app never sees the key.
        ...(typeset ? { typeset: true } : {}),
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
    if (answer.cancelled) {
      // The person cancelled: this batch and the rest are neither failed nor skipped, just not done.
      merged.cancelled = true;
      break;
    }
    if (!answer.ok) {
      firstError = firstError || answer;
      for (const image of batch) {
        merged.failed.push({ key: image.key, url: image.url || "", error: answer.error });
      }
      if (answer.timedOut) {
        // The app keeps working on this batch. Sending the next one now would
        // run two at once and interleave the stored pages.
        let notSent = 0;
        for (const later of batches.slice(index + 1)) {
          for (const image of later) {
            merged.failed.push({ key: image.key, url: image.url || "", error: STILL_WORKING });
            notSent += 1;
          }
        }
        merged.stopped = { message: `Baihe is still working on the last batch. ${notSent} later pages were not sent; send them later.` };
        break;
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
  if (merged.cancelled && !merged.pages.length && !merged.skipped.length && !merged.failed.length) {
    return { ok: false, cancelled: true, error: "Cancelled." };
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

// The bridge accepts exactly these (PNG, JPEG or WebP, judged from the bytes) and refuses
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
  const pieces = [];
  const chunk = 0x8000;      // chunked, so a big page can't blow the stack
  for (let i = 0; i < bytes.length; i += chunk) {
    pieces.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunk)));
  }
  // One join instead of `+=`: a 12 MB page would otherwise regrow the string hundreds of times.
  // Synchronous on purpose; FileReader would make every caller async for no gain.
  return btoa(pieces.join(""));
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

// Rule ids are only live for the length of one request; a counter keeps two at once apart.
let refererRuleId = 1;

// The same request again with the Referer set to the page's origin, as the browser would have
// sent it for the page's own image. The rule is limited to this host, to requests the extension
// itself makes, and is removed as soon as the request ends. Without the permission it does nothing.
async function retryWithReferer(href, options, pageOrigin) {
  const rules = chrome.declarativeNetRequest;
  if (!rules || !rules.updateSessionRules) return fetch(href, options);
  const id = refererRuleId;
  refererRuleId = refererRuleId % 1000 + 1;
  const host = new URL(href).hostname;
  await rules.updateSessionRules({
    removeRuleIds: [id],
    addRules: [{
      id, priority: 1,
      action: { type: "modifyHeaders", requestHeaders: [{ header: "referer", operation: "set", value: `${pageOrigin}/` }] },
      condition: { requestDomains: [host], initiatorDomains: [chrome.runtime.id], resourceTypes: ["xmlhttprequest", "other"] },
    }],
  });
  try {
    return await fetch(href, options);
  } finally {
    await rules.updateSessionRules({ removeRuleIds: [id] });
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
    let response = await fetch(parsed.href, options);
    // A worker's fetch cannot set a cross-origin referrer, so a CDN that checks it answers 403;
    // only then is the header set by rule, for this one request.
    if (response.status === 403 && pageOrigin) response = await retryWithReferer(parsed.href, options, pageOrigin);
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

// -- carrying on chapter after chapter ---------------------------------------
// One chapter at a time in the tab: capture it, follow the site's own next-chapter link, repeat.
// It runs here because the page's script is replaced at every navigation and the popup may close.
// Free chapters only: a sign-in or purchase wall ends the run, nothing tries to get past it.
const SERIES_MAX_CHAPTERS = 50;
const SERIES_LOAD_TIMEOUT_MS = 30000;
const SERIES_SETTLE_MS = 1500;
const SERIES_STOPS = {
  limit: "reached the chapter limit",
  end: "no next-chapter link on the last chapter",
  gated: "reached a sign-in or purchase page",
  repeat: "the next link led back to a chapter already done",
  failed: "a chapter failed",
  cancelled: "cancelled",
};
let series = null;
let lastSeriesLine = "";

function seriesSay(text) {
  if (series) series.text = text;
  chrome.runtime.sendMessage({ type: "seriesProgress", text }).catch(() => {});
}

async function tabAsk(tabId, message) {
  try {
    return (await chrome.tabs.sendMessage(tabId, message)) || { ok: false, error: "The page did not answer." };
  } catch (e) {
    return { ok: false, error: `The page could not be reached (${e.message}).` };
  }
}

async function goTo(tabId, url) {
  // The listener is added before the navigation so a fast load cannot be missed.
  const loaded = new Promise((resolve) => {
    const done = () => { chrome.tabs.onUpdated.removeListener(on); clearTimeout(timer); resolve(); };
    const on = (id, info) => { if (id === tabId && info.status === "complete") done(); };
    const timer = setTimeout(done, SERIES_LOAD_TIMEOUT_MS);
    chrome.tabs.onUpdated.addListener(on);
  });
  await chrome.tabs.update(tabId, { url });
  await loaded;
  await new Promise((r) => setTimeout(r, SERIES_SETTLE_MS));
}

function seriesLine(done, stop) {
  const count = (status) => done.filter((c) => c.status === status).length;
  const named = (status) => done.filter((c) => c.status === status).slice(0, 3)
    .map((c) => `${c.name || `chapter ${c.number}`} (${c.reason})`).join(", ");
  const parts = [`Chapters: saved ${count("saved")}, skipped ${count("skipped")}, failed ${count("failed")}`];
  if (count("skipped")) parts.push(`skipped: ${named("skipped")}`);
  if (count("failed")) parts.push(`failed: ${named("failed")}`);
  return `${parts.join("; ")}. Stopped: ${SERIES_STOPS[stop] || stop}.`;
}

async function chapterOutcome(tabId, kind, { dramaId, store }) {
  if (kind === "novel") {
    const r = await tabAsk(tabId, { type: "saveNovelText", dramaId });
    if (!r.ok) return { status: "failed", reason: r.error || "no text saved" };
    return r.data.saved ? { status: "saved" } : { status: "skipped", reason: "already saved" };
  }
  const r = await tabAsk(tabId, { type: "captureChapter", dramaId, store, fromHere: false });
  if (!r.ok) return { status: "failed", reason: r.error || "capture failed" };
  if (r.data.reason === "cancelled") return { status: "cancelled" };
  if (r.data.reason === "error") return { status: "failed", reason: r.data.message };
  return (r.data.stored || r.data.translated) ? { status: "saved" }
    : { status: "skipped", reason: "no pages found" };
}

async function runSeries({ tabId, kind, dramaId, store, limit }) {
  if (series) return { ok: false, error: "A chapter run is already going." };
  if (!Number.isInteger(tabId) || !["novel", "comic"].includes(kind)) {
    return { ok: false, error: "Open the chapter's tab first." };
  }
  const max = Math.min(Math.max(Number(limit) || 1, 1), SERIES_MAX_CHAPTERS);
  series = { tabId, cancelled: false, text: "Starting…" };
  const seen = new Set();
  const done = [];
  let stop = "limit";
  try {
    for (let number = 1; number <= max; number += 1) {
      if (series.cancelled) { stop = "cancelled"; break; }
      await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
      const here = await tabAsk(tabId, { type: "nextChapter" });
      if (!here.ok) { done.push({ number, status: "failed", reason: here.error }); stop = "failed"; break; }
      if (seen.has(here.data.here)) { stop = "repeat"; break; }
      seen.add(here.data.here);
      if (here.data.gated) { stop = "gated"; break; }
      seriesSay(`Chapter ${number} of up to ${max}…`);
      const outcome = await chapterOutcome(tabId, kind, { dramaId, store });
      done.push({ number, name: here.data.heading, ...outcome });
      if (outcome.status === "cancelled" || series.cancelled) { stop = "cancelled"; break; }
      if (outcome.status === "failed") { stop = "failed"; break; }
      if (number === max) break;
      const after = await tabAsk(tabId, { type: "nextChapter" });
      if (!after.ok || !after.data.url) { stop = "end"; break; }
      await goTo(tabId, after.data.url);
    }
  } catch (e) {
    stop = "failed";
    done.push({ number: done.length + 1, status: "failed", reason: e.message });
  } finally {
    series = null;
  }
  lastSeriesLine = seriesLine(done, stop);
  chrome.runtime.sendMessage({ type: "seriesDone", text: lastSeriesLine, failed: stop === "failed" }).catch(() => {});
  return { ok: true, data: { text: lastSeriesLine, stop, chapters: done } };
}

async function cancelSeries() {
  if (!series) return { ok: true, data: { cancelled: false } };
  series.cancelled = true;
  abortSends();
  await tabAsk(series.tabId, { type: "cancelCapture" });
  return { ok: true, data: { cancelled: true } };
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
        case "saveNovelText":
          respond(await saveNovelText(message));
          break;
        case "resolveTitle":
          respond(await resolveTitle(message));
          break;
        case "startSeries": {
          // Answered at once: the run outlasts any message port, and ends with seriesDone.
          const wasRunning = !!series;
          const running = runSeries(message);
          respond(!wasRunning && series ? { ok: true, data: { started: true } } : await running);
          break;
        }
        case "cancelSeries":
          respond(await cancelSeries());
          break;
        case "seriesStatus":
          respond({ ok: true, data: { running: !!series, text: series ? series.text : "", last: lastSeriesLine } });
          break;
        case "seriesProgress":
        case "seriesDone":
          respond({ ok: true });
          break;
        case "abortSends":
          respond(abortSends());
          break;
        case "setTypeset":
          respond(await setTypeset(message));
          break;
        case "setAutoTitle":
          respond(await setAutoTitle(message));
          break;
        case "getSettings":
          // Anyone who can message the worker gets this answer, so the credential stays out of it.
          { const { token, ...safe } = await settings(); respond({ ok: true, data: safe }); }
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
