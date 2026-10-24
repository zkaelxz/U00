// popup.js -- picks the target drama and kicks off a translate.
//
// The content script is injected here, on a click, rather than declared
// in the manifest: with activeTab that means this extension has no
// standing access to any site, and touches a page only when asked.

const els = {
  status: document.getElementById("status"),
  drama: document.getElementById("drama"),
  store: document.getElementById("store"),
  overlay: document.getElementById("overlay"),
  translate: document.getElementById("translate"),
  translateAll: document.getElementById("translateAll"),
  toggle: document.getElementById("toggle"),
  captureChapter: document.getElementById("captureChapter"),
  captureFromHere: document.getElementById("captureFromHere"),
  cancelCapture: document.getElementById("cancelCapture"),
  textDirection: document.getElementById("textDirection"),
  translateText: document.getElementById("translateText"),
  novelSection: document.getElementById("novelSection"),
  saveNovel: document.getElementById("saveNovel"),
  dramaTitle: document.getElementById("dramaTitle"),
  autoCreate: document.getElementById("autoCreate"),
  autoFill: document.getElementById("autoFill"),
  autoSeries: document.getElementById("autoSeries"),
  openLink: document.getElementById("openInBaihe"),
  allowSites: document.getElementById("allowSites"),
  notice: document.getElementById("notice"),
  noticeText: document.getElementById("noticeText"),
  noticeAction: document.getElementById("noticeAction"),
  controls: [...document.querySelectorAll(".pane select, .pane input, .pane button")],
};

// The service worker words this error; matching its opening is the only way to tell "no token" from
// "unreachable" without the popup reading the token itself.
const NO_TOKEN_PREFIX = "No token yet";
let noticeHandler = null;

// The Baihe app (not the extension bridge on 8756) serves the comic page. This is the default port
// (api/api_config.DEFAULT_PORT); /health reports the real one, which BAIHE_API_PORT or the service's
// set-port can move. Opening a tab to it needs no host permission, and the link carries only a drama id.
let APP_URL = "http://127.0.0.1:8600";
const dramaTitles = new Map();
const dramaMedia = new Map();

// The worker's saved "Automatic title options"; its defaults all on.
let auto = { create: true, fill: true, series: true };

function autoPickLabel(createEnabled) {
  return createEnabled ? "Auto: create or reuse from this page" : "Auto: reuse a matching title only";
}

const AUTO_FIELD_NAMES = { title_zh: "original title", title_en: "English title", source_url: "source link" };

function describeAutoTitle({ created, title, series_id: seriesId, filled = [] }) {
  const name = `“${title}”`;
  if (created) return `Created ${name}${seriesId ? " in a series" : ""}.`;
  const added = filled.map((f) => AUTO_FIELD_NAMES[f]).filter(Boolean);
  if (!added.length) return `Using the existing ${name}.`;
  const list = added.length > 1 ? `${added.slice(0, -1).join(", ")} and ${added[added.length - 1]}` : added[0];
  return `Using the existing ${name}; filled its blank ${list}.`;
}

function showAutoOptions() {
  els.autoCreate.checked = auto.create;
  els.autoFill.checked = auto.fill;
  els.autoSeries.checked = auto.series;
  els.drama.options[0].textContent = autoPickLabel(auto.create);
}

function addDramaOption(id, title, mediaType) {
  const value = String(id);
  if (![...els.drama.options].some((o) => o.value === value)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = mediaType ? `${title} (${mediaType})` : title;
    option.title = title;
    els.drama.appendChild(option);
  }
  dramaTitles.set(value, title);
  dramaMedia.set(value, mediaType || "");
}

// With no drama picked the app finds or creates the title this page belongs to; the picker then
// shows it, so the next send in this popup goes to the same one. A pick made by hand always wins.
async function autoTitle(tab, kind) {
  say("Finding the title for this page…");
  await ensureContentScript(tab.id);
  const facts = await chrome.tabs.sendMessage(tab.id, { type: "pageFacts" });
  if (!facts || !facts.ok) return say((facts && facts.error) || "Couldn't read this page's title.", true);
  const made = await chrome.runtime.sendMessage({ type: "resolveTitle", ...facts.data, kind });
  if (!made || !made.ok) return say((made && made.error) || "Couldn't set up a title for this page.", true);
  const data = made.data;
  addDramaOption(data.drama_id, data.title, data.media_type);
  els.drama.value = String(data.drama_id);
  showDramaTitle();
  return { dramaId: data.drama_id, note: describeAutoTitle(data) };
}

async function saveAutoOption(name, value) {
  auto = { ...auto, [name]: value };
  showAutoOptions();
  await chrome.runtime.sendMessage({ type: "setAutoTitle", options: { [name]: value } });
}

function setControlsEnabled(enabled) {
  for (const el of els.controls) el.disabled = !enabled;
}

function showNotice(text, actionLabel, handler) {
  els.noticeText.textContent = text;
  els.noticeAction.textContent = actionLabel;
  noticeHandler = handler;
  els.notice.hidden = false;
  setControlsEnabled(false);
}

// Set while the "Allow <site>" buttons are showing: the action to repeat once one is granted.
let pendingRetry = null;

// The permission prompt can close the popup on some platforms, which would lose the in-memory retry.
// What to repeat is therefore kept in session storage (never in a content script's reach) and
// offered again the next time the popup opens on the same tab.
const PENDING_ACCESS_KEY = "pendingSiteAccess";

function retryFor(action) {
  return action.kind === "capture" ? () => runCapture(!!action.fromHere) : () => run(!!action.all);
}

async function rememberPendingAccess(tabId, origins, action) {
  try {
    await chrome.storage.session.set({ [PENDING_ACCESS_KEY]: { tabId, origins, action } });
  } catch (e) {
    // Without it the buttons still work while this popup stays open.
  }
}

async function forgetPendingAccess() {
  try {
    await chrome.storage.session.remove(PENDING_ACCESS_KEY);
  } catch (e) {
    // Nothing stored, or no session storage: nothing to forget.
  }
}

async function restorePendingAccess() {
  try {
    const stored = (await chrome.storage.session.get(PENDING_ACCESS_KEY))[PENDING_ACCESS_KEY];
    const tab = await activeTab();
    if (!stored || !tab || tab.id !== stored.tabId) return;
    say("Allow the site below to finish translating this page.", true);
    offerSiteAccess({ origins: stored.origins }, stored.action);
  } catch (e) {
    // A popup that cannot read the stored request just opens clean.
  }
}

// One button per origin, never one request for all of them: the origins come from the page, so a
// hostile page could otherwise bundle a real CDN with names it wants the person to approve unseen.
function offerSiteAccess(result, action) {
  pendingRetry = retryFor(action);
  els.allowSites.replaceChildren();
  const origins = [...new Set(result.origins || [])].filter(sitePattern);
  for (const origin of origins) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.textContent = `Allow ${origin}`;
    button.addEventListener("click", () => allowSite(origin));
    els.allowSites.append(button);
  }
  els.allowSites.hidden = !origins.length;
  activeTab().then((tab) => {
    if (tab && tab.id && origins.length) return rememberPendingAccess(tab.id, origins, action);
  });
}

// chrome.permissions.request needs the click that happens here, which is why the worker only reports
// the need and never asks itself. The optional permission is per origin, so nothing broader is granted.
async function allowSite(origin) {
  const retry = pendingRetry;
  const pattern = sitePattern(origin);
  if (!retry || !pattern) return;
  try {
    const granted = await chrome.permissions.request({ origins: [pattern] });
    if (!granted) {
      say("Not allowed, so the page's images still can't be read.", true);
      els.allowSites.hidden = false;
      return;
    }
  } catch (e) {
    say(`Couldn't ask for that permission (${e.message}).`, true);
    els.allowSites.hidden = false;
    return;
  }
  pendingRetry = null;
  forgetPendingAccess();
  els.allowSites.hidden = true;
  retry();
}

function say(message, bad = false) {
  showStatus(els.status, message, bad);
  els.allowSites.hidden = true;
  // A new message replaces the result it described, so its link goes too.
  els.openLink.hidden = true;
}

function showDramaTitle() {
  // A <select> truncates long titles with no way to wrap them, so the full title is repeated
  // here (and as a tooltip) once one is picked.
  const title = dramaTitles.get(els.drama.value) || "";
  els.dramaTitle.textContent = title;
  els.dramaTitle.hidden = !title;
  els.drama.title = title;
  els.saveNovel.textContent = novelButtonLabel(title, dramaMedia.get(els.drama.value));
}

// The button names where the text will go, so a wrong pick shows before the click.
function novelButtonLabel(title, mediaType) {
  if (!title) return "Save text into a title";
  if ((mediaType || "").toLowerCase() !== "novel") return "Pick a novel title to save text";
  return `Save text into ${title}`;
}

// Offered only where the page reads as prose or the person has selected text; on a comic
// page the comic actions above are the ones that apply.
async function showNovelSection() {
  try {
    const tab = await activeTab();
    if (!tab || !tab.id) return;
    await ensureContentScript(tab.id);
    const status = await chrome.tabs.sendMessage(tab.id, { type: "status" });
    els.novelSection.hidden = !(status && status.ok &&
                                (status.data.textPage || status.data.hasSelection));
  } catch (e) { /* a page the browser keeps extensions out of */ }
}

function showOpenLink(dramaId) {
  els.openLink.href = `${APP_URL}/#/comic/${dramaId}`;
  els.openLink.hidden = false;
}

// The result line has to say where pages went: a bare count left it unclear whether anything
// was saved.
function describeDestination({ sent, cached, store, dramaId }) {
  const title = dramaTitles.get(String(dramaId)) || "the drama";
  const parts = [];
  if (!store) {
    if (sent) parts.push(`Drew ${sent} page${sent === 1 ? "" : "s"} on the page only, not saved`);
  } else if (sent) {
    parts.push(`Sent ${sent} page${sent === 1 ? "" : "s"} to ${title}`);
  }
  if (cached) {
    parts.push(store
      ? `${cached} already translated, not sent again`
      : `${cached} already translated, redrawn only`);
  }
  return parts;
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function siteOf(url) {
  try {
    return new URL(url).host;
  } catch (e) {
    return "";
  }
}

async function ensureContentScript(tabId) {
  // Injecting twice is harmless -- content.js returns early if it is
  // already there.
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
}

// Names the exact place, because "Settings" alone sent people to the wrong
// section; Ollama is the one engine that needs no account or key.
const NO_ENGINE_NOTE =
  "Connected, but no translation engine is chosen yet, so pages come back with their " +
  "original text only. In Baihe, open Settings → Browser extension and pick one under " +
  "“Translation engine”. For a free option that runs on your PC, install Ollama and pick " +
  "Ollama there.";

const MAX_NAMED_PROBLEMS = 3;

// One line that accounts for every page, so a shortfall shows up as a
// mismatch between the counts instead of a quietly smaller total.
function summarizeCapture(data, { store, dramaId }) {
  const pages = (data.pages || []).length;
  const cached = data.cached || 0;
  const skipped = data.skipped || [];
  const failed = data.failed || [];
  const unreadable = data.unreadable || [];
  const saving = !!(store && dramaId);
  const notes = (data.pages || []).flatMap((p) => p.notes || []);

  const parts = [];
  if (data.captured !== undefined) {
    parts.push(`${data.captured} captured`);
    if (data.sent !== undefined) parts.push(`${data.sent} sent`);
    if (data.received !== undefined) parts.push(`${data.received} received`);
  }
  const reused = data.alreadyStored || 0;
  parts.push(`${pages - reused} translated`);
  if (saving && data.stored !== undefined) {
    parts.push(`${data.stored} stored in ${dramaTitles.get(String(dramaId)) || "the drama"}`);
  }
  if (cached) parts.push(`${cached} already done`);
  if (reused) parts.push(`${reused} already in library`);
  if (skipped.length) parts.push(`${skipped.length} skipped as not a page`);
  if (data.unverified) {
    parts.push(`${data.unverified} read from the page's source image, so the text may not match what is drawn`);
  }

  const problems = [
    ...unreadable.map((u) => `page ${u.position}: ${u.error}`),
    ...failed.map((f) => `page ${f.position || "?"}: ${f.error}`),
  ];
  if (problems.length) {
    const shown = problems.slice(0, MAX_NAMED_PROBLEMS).join("; ");
    const more = problems.length > MAX_NAMED_PROBLEMS
      ? `; and ${problems.length - MAX_NAMED_PROBLEMS} more` : "";
    parts.push(`${problems.length} not delivered (${shown}${more})`);
  }

  const short = (data.sent !== undefined && data.received !== undefined &&
                 data.received < data.sent) ||
                (saving && data.stored !== undefined && data.stored < pages);
  const stopped = data.stopped && data.stopped.message;
  if (stopped) parts.push(stopped);
  const bad = problems.length > 0 || short || !!stopped || notes.some((n) => n[0] === "error");
  return {
    text: parts.join(" · ") + (notes.length ? ` — ${notes[0][1]}` : ""),
    bad,
  };
}

async function load() {
  els.notice.hidden = true;
  say("Checking the app…");
  const health = await chrome.runtime.sendMessage({ type: "health" });
  if (!health || !health.ok) {
    if (health && health.error && health.error.startsWith(NO_TOKEN_PREFIX)) {
      showNotice("Paste your token to get started.", "Open options", () => chrome.runtime.openOptionsPage());
      say("Actions are off until a token is saved.");
    } else {
      showNotice("Can't reach Baihe on this PC.", "Retry", load);
      say("Check that Baihe is running with the Extension bridge on.");
    }
    return;
  }
  setControlsEnabled(true);
  const { data } = health;
  const settings = (await chrome.runtime.sendMessage({ type: "getSettings" })).data || {};
  if (settings.serverUrl) APP_URL = settings.serverUrl;
  else if (Number.isInteger(data.app_port)) APP_URL = `http://127.0.0.1:${data.app_port}`;
  els.drama.length = 1;
  auto = { ...auto, ...(settings.autoTitle || {}) };
  showAutoOptions();
  const tab = await activeTab();
  const site = siteOf(tab && tab.url);

  for (const drama of data.dramas || []) {
    const option = document.createElement("option");
    option.value = String(drama.id);
    option.textContent = drama.media_type
      ? `${drama.title} (${drama.media_type})`
      : drama.title;
    option.title = drama.title;
    dramaTitles.set(option.value, drama.title);
    dramaMedia.set(option.value, drama.media_type || "");
    els.drama.appendChild(option);
  }
  // Reading a long series shouldn't be a per-page decision.
  const remembered = (settings.dramaBySite || {})[site];
  if (remembered) els.drama.value = String(remembered);
  showDramaTitle();
  els.overlay.checked = settings.overlay !== false;

  const direction = settings.textDirection || { source: "zh", target: "en" };
  const directionValue = `${direction.source}:${direction.target}`;
  if ([...els.textDirection.options].some((o) => o.value === directionValue)) {
    els.textDirection.value = directionValue;
  }

  say(data.engine_configured ? "Connected. Ready to translate." : NO_ENGINE_NOTE);
}

let pageRunInFlight = false;

async function run(all) {
  // Claimed before any await: a second click lands while activeTab() is pending.
  if (pageRunInFlight || captureInFlight) return;
  pageRunInFlight = true;
  // A capture started now would send the same pages a second time.
  els.captureChapter.disabled = true;
  els.captureFromHere.disabled = true;
  try {
    const tab = await activeTab();
    if (!tab || !tab.id) return say("No active tab.", true);
    let dramaId = els.drama.value ? Number(els.drama.value) : null;
    let autoNote = "";
    if (els.store.checked && !dramaId) {
      const made = await autoTitle(tab, "comic");
      if (!made) return;
      ({ dramaId, note: autoNote } = made);
    }
    say(all ? "Reading every visible page…" : "Reading this page…");
    forgetPendingAccess();
    await ensureContentScript(tab.id);
    // Prefer the host the page reports about itself: `tab.url` is only
    // populated when this extension has access to that tab, and keying
    // the per-site memory on an empty string would make every site share
    // one remembered drama.
    let site = siteOf(tab.url);
    if (!site) {
      const status = await chrome.tabs.sendMessage(tab.id, { type: "status" });
      if (status && status.ok) site = status.data.host || "";
    }
    // An automatic title is not remembered for the site: the next series there needs its own.
    if (dramaId && site && !autoNote) {
      await chrome.runtime.sendMessage({ type: "rememberDrama", site, dramaId });
    }
    await chrome.tabs.sendMessage(tab.id, {
      type: "setOverlays", visible: els.overlay.checked });
    await chrome.storage.local.set({ overlay: els.overlay.checked });

    const result = await chrome.tabs.sendMessage(tab.id, {
      type: "translateVisible", dramaId, store: els.store.checked, all });
    if (!result || !result.ok) {
      say((result && result.error) || "That didn't work.", true);
      if (result && result.code === "NEEDS_SITE_ACCESS") offerSiteAccess(result, { kind: "run", all });
      return;
    }
    const summary = summarizeCapture(result.data, { store: els.store.checked, dramaId });
    const saved = (result.data.pages || []).filter((p) => p.stored).length;
    say(autoNote ? `${autoNote} ${summary.text}` : summary.text, summary.bad);
    if (result.data.origins) offerSiteAccess(result.data, { kind: "run", all });
    if (els.store.checked && dramaId && saved) showOpenLink(dramaId);
  } catch (e) {
    // The usual cause is a page the browser won't let an extension into
    // (the Chrome Web Store, a PDF viewer, chrome:// pages).
    say(`Couldn't run on this page (${e.message}).`, true);
  } finally {
    pageRunInFlight = false;
    els.captureChapter.disabled = false;
    els.captureFromHere.disabled = false;
  }
}

// Interim status from a long translateVisible run in the page.
chrome.runtime.onMessage.addListener((message) => {
  if (message && message.type === "progress" && message.text) say(message.text);
});

// Scrolls the reader and translates every page it mounts. The work runs in
// the page, so it carries on if this popup closes; the page shows its own
// progress and Cancel for that case.
function showCapturing(running) {
  els.cancelCapture.hidden = !running;
  els.captureChapter.disabled = running;
  els.captureFromHere.disabled = running;
  // The page refuses a plain translate during a capture; disabling the buttons says why up front.
  els.translate.disabled = running;
  els.translateAll.disabled = running;
}

let captureInFlight = false;

async function runCapture(fromHere) {
  if (pageRunInFlight || captureInFlight) return;
  captureInFlight = true;
  try {
    await startCapture(fromHere);
  } finally {
    captureInFlight = false;
  }
}

async function startCapture(fromHere) {
  const tab = await activeTab();
  if (!tab || !tab.id) return say("No active tab.", true);
  let dramaId = els.drama.value ? Number(els.drama.value) : null;
  let autoNote = "";
  if (els.store.checked && !dramaId) {
    const made = await autoTitle(tab, "comic");
    if (!made) return;
    ({ dramaId, note: autoNote } = made);
  }
  say("Starting…");
  forgetPendingAccess();
  showCapturing(true);
  try {
    await ensureContentScript(tab.id);
    if (dramaId && !autoNote) {
      const status = await chrome.tabs.sendMessage(tab.id, { type: "status" });
      const site = siteOf(tab.url) || (status && status.ok ? status.data.host : "");
      if (site) await chrome.runtime.sendMessage({ type: "rememberDrama", site, dramaId });
    }
    await chrome.tabs.sendMessage(tab.id, { type: "setOverlays", visible: els.overlay.checked });
    await chrome.storage.local.set({ overlay: els.overlay.checked });
    const result = await chrome.tabs.sendMessage(tab.id, {
      type: "captureChapter", dramaId, store: els.store.checked, fromHere });
    if (!result || !result.ok) {
      say((result && result.error) || "That didn't work.", true);
      if (result && result.code === "NEEDS_SITE_ACCESS") offerSiteAccess(result, { kind: "capture", fromHere });
      return;
    }
    const { message, translated = 0, stored = 0 } = result.data;
    const store = els.store.checked;
    const destination = describeDestination({
      sent: store ? stored : translated, cached: 0, store, dramaId }).join(", ");
    const line = destination ? `${message} ${destination}.` : message;
    say(autoNote ? `${autoNote} ${line}` : line, result.data.reason === "error" || result.data.failed > 0);
    if (result.data.origins) offerSiteAccess(result.data, { kind: "capture", fromHere });
    if (store && dramaId && stored) showOpenLink(dramaId);
  } catch (e) {
    say(`Couldn't run on this page (${e.message}).`, true);
  } finally {
    showCapturing(false);
  }
}

// A popup reopened mid-capture picks the run back up.
async function syncCaptureUi() {
  try {
    const tab = await activeTab();
    const status = await chrome.tabs.sendMessage(tab.id, { type: "status" });
    const capture = status && status.ok && status.data.capture;
    if (capture) {
      showCapturing(true);
      say(capture.text);
    }
  } catch (e) { /* no content script on this tab yet */ }
}

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.type !== "captureDone") return;
  showCapturing(false);
  // A popup that was closed during the run never saw the result; one that
  // started it says it itself, with the saved-destination detail.
  if (!captureInFlight && message.text) say(message.text, !!message.failed);
});

async function runText() {
  const tab = await activeTab();
  if (!tab || !tab.id) return say("No active tab.", true);
  const [sourceLanguage, targetLanguage] = els.textDirection.value.split(":");
  await chrome.storage.local.set({ textDirection: { source: sourceLanguage, target: targetLanguage } });
  say("Reading the page's text…");
  try {
    await ensureContentScript(tab.id);
    const result = await chrome.tabs.sendMessage(tab.id, {
      type: "translatePageText", sourceLanguage, targetLanguage, store: true });
    if (!result || !result.ok) {
      return say((result && result.error) || "That didn't work.", true);
    }
    const notes = result.data.notes || [];
    const parts = [result.data.fromSelection
      ? "Translated the selected text" : "Translated the page's main text block"];
    if (result.data.truncated) parts.push("truncated to fit");
    say(parts.join(", ") + (notes.length ? ` — ${notes[0][1]}` : ""),
        notes.some((n) => n[0] === "error"));
  } catch (e) {
    // The usual cause is a page the browser won't let an extension into
    // (the Chrome Web Store, a PDF viewer, chrome:// pages).
    say(`Couldn't run on this page (${e.message}).`, true);
  }
}

// One line for the save's outcome: whether a chapter was added, and from what.
function describeNovelSave(data) {
  const title = dramaTitles.get(String(data.drama_id)) || "the title";
  if (!data.saved) return `Already saved in ${title}; nothing was added.`;
  const from = data.fromSelection ? "the selected text" : "the page's main text";
  return `Saved ${from} (${data.chars} characters) into ${title} as a new chapter.`;
}

let novelSaveInFlight = false;

async function runSaveNovel() {
  if (novelSaveInFlight) return;
  let dramaId = els.drama.value ? Number(els.drama.value) : null;
  if (dramaId && (dramaMedia.get(els.drama.value) || "").toLowerCase() !== "novel") {
    return say("Pick a novel title to save the text into.", true);
  }
  novelSaveInFlight = true;
  els.saveNovel.disabled = true;
  try {
    const tab = await activeTab();
    if (!tab || !tab.id) return say("No active tab.", true);
    let autoNote = "";
    if (!dramaId) {
      const made = await autoTitle(tab, "novel");
      if (!made) return;
      ({ dramaId, note: autoNote } = made);
    }
    say("Saving the text…");
    await ensureContentScript(tab.id);
    const result = await chrome.tabs.sendMessage(tab.id, { type: "saveNovelText", dramaId });
    if (!result || !result.ok) return say((result && result.error) || "That didn't work.", true);
    const line = describeNovelSave({ ...result.data, drama_id: dramaId });
    say(autoNote ? `${autoNote} ${line}` : line);
  } catch (e) {
    say(`Couldn't run on this page (${e.message}).`, true);
  } finally {
    novelSaveInFlight = false;
    els.saveNovel.disabled = false;
  }
}

els.drama.addEventListener("change", showDramaTitle);
els.autoCreate.addEventListener("change", () => saveAutoOption("create", els.autoCreate.checked));
els.autoFill.addEventListener("change", () => saveAutoOption("fill", els.autoFill.checked));
els.autoSeries.addEventListener("change", () => saveAutoOption("series", els.autoSeries.checked));
els.translate.addEventListener("click", () => run(false));
els.translateAll.addEventListener("click", () => run(true));
els.captureChapter.addEventListener("click", () => runCapture(false));
els.captureFromHere.addEventListener("click", () => runCapture(true));
els.cancelCapture.addEventListener("click", async () => {
  const tab = await activeTab();
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "cancelCapture" });
    say("Stopping…");
  } catch (e) {
    say(`Couldn't reach this page (${e.message}).`, true);
  }
});
els.translateText.addEventListener("click", runText);
els.saveNovel.addEventListener("click", runSaveNovel);
els.toggle.addEventListener("click", async () => {
  const tab = await activeTab();
  if (!tab || !tab.id) return;
  try {
    await ensureContentScript(tab.id);
    const result = await chrome.tabs.sendMessage(tab.id, { type: "toggleOverlays" });
    if (result && result.ok) {
      els.overlay.checked = result.data.visible;
      await chrome.storage.local.set({ overlay: result.data.visible });
      say(result.data.visible ? "Translations shown." : "Translations hidden.");
    }
  } catch (e) {
    say(`Couldn't reach this page (${e.message}).`, true);
  }
});
els.openLink.addEventListener("click", (event) => {
  // An extension popup can't follow a plain link into a new tab on its own.
  event.preventDefault();
  chrome.tabs.create({ url: els.openLink.href });
});
els.noticeAction.addEventListener("click", () => noticeHandler && noticeHandler());

const TAB_KEY = "popupTab";
const tabButtons = [...document.querySelectorAll('[role="tab"]')];

function selectTab(button, { focus = false } = {}) {
  for (const tab of tabButtons) {
    const on = tab === button;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !on;
  }
  if (focus) button.focus();
  try { localStorage.setItem(TAB_KEY, button.id); } catch (e) { /* the tab just isn't remembered */ }
}

tabButtons.forEach((tab, i) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (step) selectTab(tabButtons[(i + step + tabButtons.length) % tabButtons.length], { focus: true });
  });
});
try {
  const saved = tabButtons.find((tab) => tab.id === localStorage.getItem(TAB_KEY));
  if (saved) selectTab(saved);
} catch (e) { /* storage blocked: start on Page */ }

load().then(syncCaptureUi).then(restorePendingAccess).then(showNovelSection);
