// options.js -- where the token is pasted in.
//
// The token is kept in chrome.storage.local, read only by the service
// worker, and never handed to a content script or written into a URL.

const tokenEl = document.getElementById("token");
const statusEl = document.getElementById("status");

const NO_ENGINE_HINT = " No translation engine is set in Baihe's Settings yet, so pages will come back with" +
  " their original text only.";

const say = (message, bad = false) => showStatus(statusEl, message, bad);

async function load() {
  const { token } = await chrome.storage.local.get(["token"]);
  if (token) tokenEl.value = token;
}

function describeConnection({ dramas, engine_configured: engineConfigured }) {
  const count = dramas?.length ?? 0;
  return `Connected to Baihe. ${pluralize(count, "drama")} available.` +
    (engineConfigured ? "" : NO_ENGINE_HINT);
}

async function saveAndTest() {
  const token = tokenEl.value.trim();
  if (!token) return say("Paste the token from Baihe's Settings first.", true);

  await chrome.storage.local.set({ token });
  say("Saved. Testing…");
  const result = await chrome.runtime.sendMessage({ type: "health" });
  if (isFailure(result)) return say(failureMessage(result, "Couldn't reach the app."), true);
  say(describeConnection(result.data));
}

document.getElementById("save").addEventListener("click", saveAndTest);

load();
