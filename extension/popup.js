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
  textDirection: document.getElementById("textDirection"),
  translateText: document.getElementById("translateText"),
  options: document.getElementById("options"),
};

function say(message, bad = false) {
  els.status.textContent = message;
  els.status.classList.toggle("bad", !!bad);
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
  parts.push(`${pages} translated`);
  if (saving && data.stored !== undefined) parts.push(`${data.stored} stored`);
  if (cached) parts.push(`${cached} already done`);
  if (skipped.length) parts.push(`${skipped.length} skipped as not a page`);

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
  const bad = problems.length > 0 || short || notes.some((n) => n[0] === "error");
  return {
    text: parts.join(" · ") + (notes.length ? ` — ${notes[0][1]}` : ""),
    bad,
  };
}

async function load() {
  const health = await chrome.runtime.sendMessage({ type: "health" });
  if (!health || !health.ok) {
    say((health && health.error) || "Couldn't reach the app.", true);
    return;
  }
  const { data } = health;
  const settings = (await chrome.runtime.sendMessage({ type: "getSettings" })).data || {};
  const tab = await activeTab();
  const site = siteOf(tab && tab.url);

  for (const drama of data.dramas || []) {
    const option = document.createElement("option");
    option.value = String(drama.id);
    option.textContent = drama.media_type
      ? `${drama.title} (${drama.media_type})`
      : drama.title;
    els.drama.appendChild(option);
  }
  // Reading a long series shouldn't be a per-page decision.
  const remembered = (settings.dramaBySite || {})[site];
  if (remembered) els.drama.value = String(remembered);
  els.overlay.checked = settings.overlay !== false;

  const direction = settings.textDirection || { source: "zh", target: "en" };
  const directionValue = `${direction.source}:${direction.target}`;
  if ([...els.textDirection.options].some((o) => o.value === directionValue)) {
    els.textDirection.value = directionValue;
  }

  say(data.engine_configured ? "Connected. Ready to translate." : NO_ENGINE_NOTE);
}

async function run(all) {
  const tab = await activeTab();
  if (!tab || !tab.id) return say("No active tab.", true);
  const dramaId = els.drama.value ? Number(els.drama.value) : null;
  if (els.store.checked && !dramaId) {
    return say("Pick a drama to save into, or untick saving.", true);
  }
  say(all ? "Scrolling through the chapter and reading every page — this can take a minute…"
          : "Reading this page…");
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
    const summary = summarizeCapture(result.data, { store: els.store.checked, dramaId });
    say(summary.text, summary.bad);
  } catch (e) {
    // The usual cause is a page the browser won't let an extension into
    // (the Chrome Web Store, a PDF viewer, chrome:// pages).
    say(`Couldn't run on this page (${e.message}).`, true);
  }
}

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

els.translate.addEventListener("click", () => run(false));
els.translateAll.addEventListener("click", () => run(true));
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
els.options.addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});

load();
