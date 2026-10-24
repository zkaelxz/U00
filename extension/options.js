// options.js -- where the token is pasted in.
//
// The token is kept in chrome.storage.local, read only by the service
// worker, and never handed to a content script or written into a URL.

const tokenEl = document.getElementById("token");
const serverEl = document.getElementById("server");
const statusEl = document.getElementById("status");

const NO_ENGINE_HINT = " No translation engine is set in Baihe's Settings yet, so pages will come back with" +
  " their original text only.";

const say = (message, bad = false) => showStatus(statusEl, message, bad);

async function load() {
  const { token, serverUrl } = await chrome.storage.local.get(["token", "serverUrl"]);
  if (token) tokenEl.value = token;
  if (serverUrl) serverEl.value = serverUrl;
}

// Blank means Baihe on this computer. Anything else must be an https origin; the
// path, query and credentials a pasted link may carry are dropped.
function serverOrigin(text) {
  if (!text) return "";
  try {
    const url = new URL(text);
    return url.protocol === "https:" && !url.username && !url.password ? url.origin : null;
  } catch (e) {
    return null;
  }
}

function describeConnection({ dramas, engine_configured: engineConfigured }) {
  const count = dramas?.length ?? 0;
  return `Connected to Baihe. ${pluralize(count, "drama")} available.` +
    (engineConfigured ? "" : NO_ENGINE_HINT);
}

async function saveAndTest() {
  const token = tokenEl.value.trim();
  if (!token) return say("Paste the token from Baihe's Settings first.", true);

  const serverUrl = serverOrigin(serverEl.value.trim());
  if (serverUrl === null) return say("The Baihe address must start with https://.", true);
  // Asked in this click, which Chrome requires; loopback needs none.
  if (serverUrl && !(await chrome.permissions.request({ origins: [`${serverUrl}/*`] }))) {
    return say("Access to that address was not allowed.", true);
  }
  serverEl.value = serverUrl;
  await chrome.storage.local.set({ token, serverUrl });
  say("Saved. Testing…");
  const result = await chrome.runtime.sendMessage({ type: "health" });
  if (isFailure(result)) return say(failureMessage(result, "Couldn't reach the app."), true);
  say(describeConnection(result.data));
}

document.getElementById("save").addEventListener("click", saveAndTest);

load();
