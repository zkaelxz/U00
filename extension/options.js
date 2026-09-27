// options.js -- where the token is pasted in.
//
// The token is kept in chrome.storage.local, read only by the service
// worker, and never handed to a content script or written into a URL.

const tokenEl = document.getElementById("token");
const portEl = document.getElementById("port");
const statusEl = document.getElementById("status");

function say(message, bad = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("bad", !!bad);
}

async function load() {
  const stored = await chrome.storage.local.get(["token", "port"]);
  if (stored.token) tokenEl.value = stored.token;
  if (stored.port) portEl.value = stored.port;
}

document.getElementById("save").addEventListener("click", async () => {
  const token = tokenEl.value.trim();
  const port = Number(portEl.value) || 8756;
  if (!token) return say("Paste the token from Baihe's Settings first.", true);
  await chrome.storage.local.set({ token, port });
  say("Saved. Testing…");
  const result = await chrome.runtime.sendMessage({ type: "health" });
  if (!result || !result.ok) {
    return say((result && result.error) || "Couldn't reach the app.", true);
  }
  const count = (result.data.dramas || []).length;
  say(`Connected to Baihe. ${count} drama${count === 1 ? "" : "s"} available.` +
      (result.data.engine_configured ? "" :
       " No translation engine is set in Baihe's Settings yet, so pages will come back with" +
       " their original text only."));
});

load();
