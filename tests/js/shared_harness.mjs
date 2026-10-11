// Runs extension/shared.js and extension/options.js against a fake page and
// browser, printing one JSON result per scenario. Driven by
// tests/test_extension_shared.py.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "extension");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

function fakeEl() {
  const classes = new Set();
  return {
    textContent: "", value: "", handlers: {},
    classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), has: (name) => classes.has(name) },
    addEventListener(type, fn) { this.handlers[type] = fn; },
  };
}

function loadShared() {
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(`${read("shared.js")}; this.api = { pluralize, isFailure, failureMessage, showStatus };`, sandbox);
  return sandbox.api;
}

// options.js runs top to bottom on load, so the fake page is built first and
// the script's own click handler is what the scenarios drive.
async function loadOptions({ stored = {}, reply }) {
  const els = { token: fakeEl(), status: fakeEl(), save: fakeEl() };
  const calls = { set: [], sent: [] };
  const sandbox = {
    document: { getElementById: (id) => els[id] },
    chrome: {
      storage: { local: {
        get: async () => stored,
        set: async (value) => { calls.set.push(value); },
      } },
      runtime: { sendMessage: async (message) => { calls.sent.push(message); return reply; } },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${read("shared.js")}\n${read("options.js")}`, sandbox);
  await new Promise((resolve) => setImmediate(resolve));
  return { els, calls, click: () => els.save.handlers.click() };
}

const snapshot = ({ els, calls }) => ({
  token: els.token.value, status: els.status.textContent, bad: els.status.classList.has("bad"), ...calls,
});

async function shared(scenario) {
  const api = loadShared();
  switch (scenario) {
    case "pluralize":
      return [0, 1, 2].map((n) => api.pluralize(n, "page"));
    case "failure_detection":
      return [undefined, null, {}, { ok: false }, { ok: true }].map(api.isFailure);
    case "failure_message":
      return [
        api.failureMessage(undefined), api.failureMessage({ ok: false }), api.failureMessage({ error: "boom" }),
        api.failureMessage(null, "Couldn't reach the app."), api.failureMessage({ error: "boom" }, "other"),
      ];
    case "show_status": {
      const el = fakeEl();
      api.showStatus(el, "bad one", true);
      const first = [el.textContent, el.classList.has("bad")];
      api.showStatus(el, "fine");
      return { first, second: [el.textContent, el.classList.has("bad")] };
    }
  }
  throw new Error(`unknown scenario ${scenario}`);
}

async function options(scenario) {
  switch (scenario) {
    case "prefills_stored_token": {
      const page = await loadOptions({ stored: { token: "abc" } });
      return snapshot(page);
    }
    case "empty_token_refused": {
      const page = await loadOptions({ reply: { ok: true, data: {} } });
      page.els.token.value = "   ";
      await page.click();
      return snapshot(page);
    }
    case "connected_one_drama":
    case "connected_no_engine": {
      const page = await loadOptions({ reply: {
        ok: true, data: { dramas: [{}], engine_configured: scenario === "connected_one_drama" } } });
      page.els.token.value = "  tok  ";
      await page.click();
      return snapshot(page);
    }
    case "connected_two_dramas": {
      const page = await loadOptions({ reply: { ok: true, data: { dramas: [{}, {}], engine_configured: true } } });
      page.els.token.value = "tok";
      await page.click();
      return snapshot(page);
    }
    case "worker_error_shown":
    case "no_reply_fallback": {
      const page = await loadOptions({ reply: scenario === "worker_error_shown" ? { ok: false, error: "Bridge is off." } : undefined });
      page.els.token.value = "tok";
      await page.click();
      return snapshot(page);
    }
  }
  throw new Error(`unknown scenario ${scenario}`);
}

const [kind, scenario] = process.argv.slice(2);
const result = await ({ shared, options }[kind])(scenario);
process.stdout.write(JSON.stringify(result));
process.exit(0);
