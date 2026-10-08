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
  dramaTitle: document.getElementById("dramaTitle"),
  openLink: document.getElementById("openInBaihe"),
  notice: document.getElementById("notice"),
  noticeText: document.getElementById("noticeText"),
  noticeAction: document.getElementById("noticeAction"),
  controls: [...document.querySelectorAll("#pageSection select, #pageSection input, #pageSection button, #textSection select, #textSection button")],
};

// The service worker words this error; matching its opening is the only way to tell "no token" from
// "unreachable" without the popup reading the token itself.
const NO_TOKEN_PREFIX = "No token yet";
let noticeHandler = null;

// The Baihe app (not the extension bridge on 8756) serves the comic page. It binds this port by
// default (api/api_config.DEFAULT_PORT); opening a tab to it needs no host permission, and the
// link carries only a drama id.
const APP_URL = "http://127.0.0.1:8600";
const dramaTitles = new Map();

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

function say(message, bad = false) {
  els.status.textContent = message;
  els.status.classList.toggle("bad", !!bad);
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
  els.drama.length = 1;
  const settings = (await chrome.runtime.sendMessage({ type: "getSettings" })).data || {};
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

  say(data.engine_configured
    ? "Connected. Ready to translate."
    : "Connected, but no translation engine is set in Baihe's Settings — pages will come " +
      "back with their original text only.");
}

async function run(all) {
  const tab = await activeTab();
  if (!tab || !tab.id) return say("No active tab.", true);
  const dramaId = els.drama.value ? Number(els.drama.value) : null;
  if (els.store.checked && !dramaId) {
    return say("Pick a drama to save into, or untick saving.", true);
  }
  say(all ? "Reading every visible page…" : "Reading this page…");
  try {
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
    if (dramaId && site) {
      await chrome.runtime.sendMessage({ type: "rememberDrama", site, dramaId });
    }
    await chrome.tabs.sendMessage(tab.id, {
      type: "setOverlays", visible: els.overlay.checked });
    await chrome.storage.local.set({ overlay: els.overlay.checked });

    const result = await chrome.tabs.sendMessage(tab.id, {
      type: "translateVisible", dramaId, store: els.store.checked, all });
    if (!result || !result.ok) {
      return say((result && result.error) || "That didn't work.", true);
    }
    const pageList = result.data.pages || [];
    const cached = result.data.cached || 0;
    const skipped = (result.data.skipped || []).length;
    const notes = pageList.flatMap((p) => p.notes || []);
    // The server reports per page whether it was really saved (no drama, or store off, means not).
    const saved = pageList.filter((p) => p.stored).length;
    const store = els.store.checked;
    const parts = describeDestination({
      sent: store ? saved : pageList.length, cached, store, dramaId });
    if (store && pageList.length > saved) {
      parts.push(`${pageList.length - saved} drawn only, not saved`);
    }
    if (skipped) parts.push(`${skipped} skipped as not a page`);
    const failed = result.data.failed;
    if (failed) parts.push(failed.message);
    say(parts.join(", ") + (notes.length ? ` — ${notes[0][1]}` : ""),
        !!failed || notes.some((n) => n[0] === "error"));
    if (store && dramaId && saved) showOpenLink(dramaId);
  } catch (e) {
    // The usual cause is a page the browser won't let an extension into
    // (the Chrome Web Store, a PDF viewer, chrome:// pages).
    say(`Couldn't run on this page (${e.message}).`, true);
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

async function runCapture(fromHere) {
  const tab = await activeTab();
  if (!tab || !tab.id) return say("No active tab.", true);
  const dramaId = els.drama.value ? Number(els.drama.value) : null;
  if (els.store.checked && !dramaId) {
    return say("Pick a drama to save into, or untick saving.", true);
  }
  say("Starting…");
  showCapturing(true);
  try {
    await ensureContentScript(tab.id);
    if (dramaId) {
      const status = await chrome.tabs.sendMessage(tab.id, { type: "status" });
      const site = siteOf(tab.url) || (status && status.ok ? status.data.host : "");
      if (site) await chrome.runtime.sendMessage({ type: "rememberDrama", site, dramaId });
    }
    await chrome.tabs.sendMessage(tab.id, { type: "setOverlays", visible: els.overlay.checked });
    const result = await chrome.tabs.sendMessage(tab.id, {
      type: "captureChapter", dramaId, store: els.store.checked, fromHere });
    if (!result || !result.ok) return say((result && result.error) || "That didn't work.", true);
    const { message, translated = 0, stored = 0 } = result.data;
    const store = els.store.checked;
    const destination = describeDestination({
      sent: store ? stored : translated, cached: 0, store, dramaId }).join(", ");
    say(destination ? `${message} ${destination}.` : message, result.data.reason === "error");
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
  if (message && message.type === "captureDone") showCapturing(false);
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

els.drama.addEventListener("change", showDramaTitle);
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

load().then(syncCaptureUi);
