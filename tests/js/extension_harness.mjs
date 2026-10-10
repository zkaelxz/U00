// Runs extension/content.js and extension/background.js against a fake
// browser (no network, no real DOM) and prints one JSON result per scenario.
// tests/test_extension_tainted_canvas.py drives it; see there for the intent.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "extension");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

const TAINT = Object.assign(
  new Error("Failed to execute 'toBlob' on 'HTMLCanvasElement': Tainted canvases may not be exported."),
  { name: "SecurityError" });

const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 5, 6, 7, 8]);
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3, 4]);

function fakeCanvas(width = 0, height = 0) {
  return {
    tagName: "CANVAS", width, height,
    getContext: () => ({ drawImage() {}, getImageData() { throw TAINT; } }),
    toBlob() { throw TAINT; },
    getAttribute: () => null, parentElement: null,
    getBoundingClientRect: () => ({ top: 0, left: 0, width, height }),
  };
}

function fakeImg(src, w = 800, h = 1200) {
  return {
    tagName: "IMG", naturalWidth: w, naturalHeight: h, width: w, height: h,
    currentSrc: src, src, complete: true, isConnected: true,
    getAttribute: () => null, parentElement: null,
    getBoundingClientRect: () => ({ top: 0, left: 0, width: w, height: h }),
  };
}

function container(children) {
  const parent = { getAttribute: () => null, parentElement: null, querySelectorAll: () => children };
  for (const c of children) c.parentElement = parent;
  return parent;
}

function loadContent({ images = [], canvases = [], fetchImage }) {
  const sent = [];
  const fetched = [];
  let listener = null;
  const document = {
    title: "", body: { innerText: "", appendChild() {} },
    documentElement: { appendChild() {}, scrollHeight: 800 },
    images, head: { appendChild() {} },
    querySelectorAll: (sel) => (sel === "canvas" ? canvases : []),
    createElement: (tag) => (tag === "canvas" ? fakeCanvas() : { style: {}, dataset: {}, append() {}, addEventListener() {}, remove() {}, appendChild() {} }),
    getElementById: () => null,
  };
  const sandbox = {
    document, console, URL, setTimeout, clearTimeout, performance, crypto: globalThis.crypto,
    atob, btoa, Blob, Uint8Array, Promise, Map, Set, WeakMap, WeakSet, Math, Number, String, Array, Error, Date,
    requestAnimationFrame: (cb) => setTimeout(cb, 0),
    getComputedStyle: () => ({ visibility: "visible", display: "block", opacity: "1", backgroundImage: "none", overflowY: "visible" }),
    MutationObserver: class { observe() {} disconnect() {} },
    ResizeObserver: class { observe() {} disconnect() {} },
    createImageBitmap: async (blob) => {
      const dims = sandbox.__bitmap;
      return { width: dims.width, height: dims.height, close() {} };
    },
    location: { href: "https://www.twmanga.com/comic/chapter/x/0_1.html", host: "www.twmanga.com" },
    chrome: {
      runtime: {
        onMessage: { addListener: (fn) => { listener = fn; } },
        sendMessage: async (message) => {
          if (message.type === "fetchImage") {
            fetched.push(message.url);
            return fetchImage(message);
          }
          if (message.type === "send") { sent.push(message); return { ok: true, data: { pages: [] } }; }
          return { ok: true };
        },
      },
    },
  };
  sandbox.window = sandbox;
  sandbox.addEventListener = () => {};
  document.addEventListener = () => {};
  sandbox.innerHeight = 800;
  sandbox.scrollY = 0;
  sandbox.scrollTo = () => {};
  sandbox.__bitmap = { width: 800, height: 1200 };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(read("content.js"), sandbox);
  const ask = (message, sender = {}) => new Promise((resolve) => { listener(message, sender, resolve); });
  return { ask, sent, fetched, sandbox };
}

const okImage = (bytes = JPEG, type = "image/jpeg") => async () => ({
  ok: true, data: { data: Buffer.from(bytes).toString("base64"), content_type: type, url: "https://s1.bzcdn.net/p/1.jpg" },
});
const needsPermission = async () => ({
  ok: false, code: "NEEDS_PERMISSION", origin: "https://s1.bzcdn.net", error: "needs the site",
});

const IMG_URL = "https://s1.bzcdn.net/p/1.jpg";

async function content(scenario) {
  const visible = { type: "translateVisible", dramaId: null, store: false, all: false };
  const capture = { type: "captureChapter", dramaId: null, store: false, fromHere: true };
  const img = () => fakeImg(IMG_URL);
  const summary = (h, result) => ({
    ok: result.ok, code: result.code, origins: result.origins, error: result.error,
    fetched: h.fetched,
    sent: h.sent.flatMap((m) => m.images.map((i) => ({ content_type: i.content_type, url: i.url, bytes: Buffer.from(i.data, "base64").length }))),
  });
  const run = async (message, opts) => {
    const h = loadContent(opts);
    if (opts.bitmap) h.sandbox.__bitmap = opts.bitmap;
    const result = await h.ask(message);
    return summary(h, result);
  };

  switch (scenario) {
    case "img_tainted_fetches_in_worker":
      return run(visible, { images: [img()], fetchImage: okImage() });
    case "img_tainted_needs_permission":
      return run(visible, { images: [img()], fetchImage: needsPermission });
    case "img_tainted_worker_rejects":
      return run(visible, { images: [img()], fetchImage: async () => ({ ok: false, error: "the address returned something that isn't a PNG, JPEG or WebP image" }) });
    case "canvas_tainted_url_from_sibling_img": {
      const canvas = fakeCanvas(800, 1200);
      container([canvas, { tagName: "IMG", currentSrc: "", src: IMG_URL, getAttribute: () => null }]);
      return run(visible, { canvases: [canvas], fetchImage: okImage(PNG, "image/png") });
    }
    case "canvas_tainted_url_from_data_attribute": {
      const canvas = fakeCanvas(800, 1200);
      canvas.getAttribute = (a) => (a === "data-src" ? IMG_URL : null);
      return run(visible, { canvases: [canvas], fetchImage: okImage() });
    }
    case "canvas_tainted_no_url": {
      const canvas = fakeCanvas(800, 1200);
      container([canvas]);
      return run(visible, { canvases: [canvas], fetchImage: okImage() });
    }
    case "canvas_tainted_file_is_not_the_drawn_page": {
      const canvas = fakeCanvas(800, 1200);
      canvas.getAttribute = (a) => (a === "data-src" ? IMG_URL : null);
      return run(visible, { canvases: [canvas], fetchImage: okImage(), bitmap: { width: 800, height: 400 } });
    }
    case "capture_tainted_img_fetches_in_worker":
      return run(capture, { images: [img()], fetchImage: okImage() });
    case "capture_tainted_img_needs_permission":
      return run(capture, { images: [img()], fetchImage: needsPermission });
    case "capture_tainted_canvas_no_url": {
      const canvas = fakeCanvas(800, 1200);
      container([canvas]);
      return run(capture, { canvases: [canvas], fetchImage: okImage() });
    }
  }
  throw new Error(`unknown scenario ${scenario}`);
}

function loadBackground({ granted, response, fetchImpl, stored = {} }) {
  let listener = null;
  const fetchCalls = [];
  const sandbox = {
    console, setTimeout, clearTimeout, AbortController, URL, Uint8Array, btoa, Response,
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      if (fetchImpl) return fetchImpl(url, options);
      return response();
    },
    chrome: {
      runtime: { onInstalled: { addListener() {} }, onMessage: { addListener: (fn) => { listener = fn; } } },
      storage: { local: { remove() {}, get: async () => stored, set: async () => {} } },
      permissions: { contains: async ({ origins }) => granted(origins) },
    },
  };
  sandbox.importScripts = (name) => vm.runInContext(read(name), sandbox);
  vm.createContext(sandbox);
  vm.runInContext(read("background.js"), sandbox);
  const ask = (message, sender = {}) => new Promise((resolve) => { listener(message, sender, resolve); });
  return { ask, fetchCalls, sandbox };
}

// The shared origin check, as the popup loads it (a plain script, no worker).
function loadSiteAccess() {
  const sandbox = { URL };
  vm.createContext(sandbox);
  vm.runInContext(read("site_access.js"), sandbox);
  return sandbox;
}

// taintedKey is private to content.js's closure, so it is lifted out by its source text.
function loadTaintedKey(findImageUrl) {
  const src = read("content.js");
  const start = src.indexOf("const taintedIds");
  const end = src.indexOf("// A canvas has no src, so its draw target");
  if (start < 0 || end < 0) throw new Error("taintedKey not found");
  const sandbox = { WeakMap, findImageUrl };
  vm.createContext(sandbox);
  vm.runInContext(`${src.slice(start, end)}; this.taintedKey = taintedKey;`, sandbox);
  return sandbox.taintedKey;
}

async function popup(scenario) {
  const { sitePattern } = loadSiteAccess();
  switch (scenario) {
    case "site_patterns": {
      const out = {};
      for (const origin of ["https://s1.bzcdn.net", "https://cdn.example:8443", "http://nas", "http://camera",
        "https://router.lan", "https://printer.internal", "https://x.home.arpa", "http://localhost", "http://127.0.0.1",
        "http://192.168.1.5", "https://8.8.8.8", "https://*.victim.com", "file:///etc/passwd", "not a url"]) {
        out[origin] = sitePattern(origin);
      }
      return out;
    }
    case "tainted_keys": {
      const urls = new Map();
      const a = { width: 800, height: 1200 };
      const b = { width: 800, height: 1200 };
      const resized = { width: 400, height: 600 };
      const blank = { width: 800, height: 1200 };
      urls.set(a, IMG_URL); urls.set(b, IMG_URL); urls.set(resized, IMG_URL); urls.set(blank, "");
      const key = loadTaintedKey((el) => urls.get(el));
      const first = key(a);
      urls.set(a, "https://s1.bzcdn.net/p/2.jpg");
      return {
        firstIsStable: first === (urls.set(a, IMG_URL), key(a)),
        twoCanvasesDiffer: key(a) !== key(b), resizedDiffers: key(resized) !== key(b),
        emptyUrl: key(blank),
      };
    }
  }
  throw new Error(`unknown scenario ${scenario}`);
}

async function background(scenario) {
  const url = "https://s1.bzcdn.net/p/1.jpg";
  const image = (bytes, type = "image/jpeg", headers = {}) => () =>
    new Response(bytes, { status: 200, headers: { "content-type": type, ...headers } });
  const go = async (opts, target = url) => {
    const h = loadBackground({ granted: () => true, ...opts });
    const r = await h.ask({ type: "fetchImage", url: target });
    return { ...r, fetchCalls: h.fetchCalls.length, options: h.fetchCalls[0] && h.fetchCalls[0].options };
  };
  switch (scenario) {
    case "base64_matches_btoa": {
      const { base64Of } = loadBackground({ granted: () => true }).sandbox;
      const out = {};
      // 0x8000 is the chunk size; one byte over it exercises the join across chunks.
      for (const size of [0, 1, 5, 0x8000, 0x8001, 3 * 0x8000 + 7]) {
        const bytes = Uint8Array.from({ length: size }, (_, i) => (i * 31 + 7) & 0xff);
        out[size] = base64Of(bytes) === Buffer.from(bytes).toString("base64");
      }
      return out;
    }
    case "valid_jpeg": {
      const r = await go({ response: image(JPEG) });
      return { ok: r.ok, content_type: r.data && r.data.content_type, credentials: r.options.credentials,
        redirect: r.options.redirect, hasSignal: !!r.options.signal };
    }
    case "type_comes_from_the_bytes_not_the_header": {
      const r = await go({ response: image(PNG, "application/octet-stream") });
      return { ok: r.ok, content_type: r.data && r.data.content_type };
    }
    case "html_labelled_as_image":
      return go({ response: image("<html>not an image</html>", "image/jpeg") });
    case "svg_rejected":
      return go({ response: image("<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml") });
    case "oversize_declared":
      return go({ response: image(JPEG, "image/jpeg", { "content-length": String(13 * 1024 * 1024) }) });
    case "oversize_streamed": {
      const big = new Uint8Array(12 * 1024 * 1024 + 10);
      big.set(JPEG);
      return go({ response: image(big) });
    }
    case "http_error":
      return go({ response: () => new Response("no", { status: 403 }) });
    case "timeout": {
      const fetchImpl = (u, options) => new Promise((_, reject) => {
        options.signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      });
      // The real timer is 20 s; shorten it by running the worker's own clock faster.
      const realSetTimeout = globalThis.setTimeout;
      globalThis.setTimeout = (fn, ms) => realSetTimeout(fn, ms > 1000 ? 5 : ms);
      try {
        return await (async () => {
          const h = loadBackground({ granted: () => true, fetchImpl });
          return h.ask({ type: "fetchImage", url });
        })();
      } finally { globalThis.setTimeout = realSetTimeout; }
    }
    case "needs_permission": {
      const h = loadBackground({ granted: () => false, response: image(JPEG) });
      const r = await h.ask({ type: "fetchImage", url });
      return { ...r, fetchCalls: h.fetchCalls.length };
    }
    case "referrer_is_only_the_page_origin": {
      const h = loadBackground({ granted: () => true, response: image(JPEG) });
      await h.ask({ type: "fetchImage", url }, { url: "https://reader.example/ch/1?token=secret" });
      const o = h.fetchCalls[0].options;
      return { referrer: o.referrer, referrerPolicy: o.referrerPolicy };
    }
    case "no_referrer_without_a_page": {
      const h = loadBackground({ granted: () => true, response: image(JPEG) });
      await h.ask({ type: "fetchImage", url });
      return { keys: Object.keys(h.fetchCalls[0].options).sort() };
    }
    case "permission_patterns": {
      const out = {};
      for (const target of ["https://*/x.jpg", "https://*.victim.com/x.jpg", "https://a%2eb/x.jpg",
        "https://u:p@host.example/x.jpg", "https://HOST.Example/x.jpg", "https://bücher.example/x.jpg",
        "https://xn--bcher-kva.example/x.jpg", "https://cdn.example:8443/x.jpg", "http://cdn.example:80/x.jpg",
        "https://cdn.example./x.jpg", "https://8.8.8.8/x.jpg"]) {
        const asked = [];
        const h = loadBackground({ granted: (origins) => { asked.push(...origins); return false; }, response: image(JPEG) });
        const r = await h.ask({ type: "fetchImage", url: target });
        out[target] = { code: r.code, asked, fetchCalls: h.fetchCalls.length };
      }
      return out;
    }
    case "private_hosts_refused": {
      const out = {};
      for (const target of ["http://127.0.0.1:8600/api", "http://localhost/x.jpg", "http://192.168.1.5/x.jpg",
        "http://169.254.169.254/x.jpg", "http://[::1]/x.jpg", "file:///etc/passwd", "blob:https://a/b",
        "http://localhost./x.jpg", "http://127.0.0.1.nip.io/x.jpg", "http://a.sslip.io/x.jpg",
        "http://a.localtest.me/x.jpg", "http://100.64.0.1/x.jpg", "http://198.18.0.1/x.jpg",
        "http://224.0.0.1/x.jpg", "http://240.0.0.1/x.jpg", "http://[::ffff:127.0.0.1]/x.jpg",
        "http://[fc00::1]/x.jpg", "http://[fe80::1]/x.jpg", "http://nas/x.jpg", "http://camera/snapshot.jpg",
        "http://router.lan/x.jpg", "http://printer.internal/x.jpg", "http://x.home.arpa/x.jpg",
        "http://a.intranet/x.jpg", "http://a.corp/x.jpg", "http://a.localdomain/x.jpg", "http://nas./x.jpg"]) {
        const r = await go({ response: image(JPEG) }, target);
        out[target] = { ok: r.ok, fetchCalls: r.fetchCalls };
      }
      return out;
    }
  }
  throw new Error(`unknown scenario ${scenario}`);
}

// A novel reader: paragraphs under one container, an optional selection, no page images
// unless given. Returns the content script's API and what it asked the worker to send.
function loadNovelPage({ paragraphs = [], selection = "", images = [], heading = "第三章 夜雨" }) {
  const sent = [];
  const box = { tagName: "DIV", parentElement: null };
  const ps = paragraphs.map((text, i) => ({
    tagName: "P", textContent: text, parentElement: box, index: i,
    getBoundingClientRect: () => ({ top: i * 20, left: 0, width: 600, height: 20 }),
    compareDocumentPosition(other) { return other.index > this.index ? 4 : 2; },
  }));
  const h1 = { textContent: `  ${heading}  ` };
  const document = {
    title: `${heading}_书名_小说网`, body: { innerText: "", appendChild() {} },
    documentElement: { appendChild() {} }, images, head: { appendChild() {} },
    querySelectorAll: (sel) => (sel === "p" ? ps : []),
    querySelector: (sel) => (sel === "h1" ? h1 : null),
    createElement: () => ({ style: {}, dataset: {}, append() {}, addEventListener() {}, remove() {}, appendChild() {} }),
    getElementById: () => null, addEventListener() {},
  };
  const sandbox = {
    document, console, URL, setTimeout, clearTimeout, Promise, Map, Set, WeakMap, WeakSet, Math, Number,
    String, Array, Error, Date, Node: { DOCUMENT_POSITION_FOLLOWING: 4 },
    getSelection: () => ({ toString: () => selection }),
    getComputedStyle: () => ({ visibility: "visible", display: "block", opacity: "1" }),
    MutationObserver: class { observe() {} disconnect() {} },
    ResizeObserver: class { observe() {} disconnect() {} },
    location: { href: "https://novel.example/ch/3", host: "novel.example" },
    chrome: { runtime: { onMessage: { addListener() {} },
      sendMessage: async (message) => { sent.push(message); return { ok: true, data: { saved: true } }; } } },
  };
  sandbox.window = sandbox;
  sandbox.addEventListener = () => {};
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(read("content.js"), sandbox);
  return { api: sandbox.__baihe, sent };
}

function loadPopupScript() {
  const el = () => ({ addEventListener() {}, classList: { toggle() {} }, appendChild() {},
    options: [], value: "", checked: true, textContent: "", hidden: false });
  const sandbox = {
    document: { getElementById: () => el(), querySelectorAll: () => [] },
    chrome: { runtime: { sendMessage: async () => ({ ok: false, error: "x" }), onMessage: { addListener() {} } },
              tabs: { query: async () => [] } },
    console, JSON, URL, Promise, Math, Object, Array, Number, String, Error, Map, Set,
  };
  vm.createContext(sandbox);
  vm.runInContext(`${read("site_access.js")}\n${read("popup.js")}\n;this.__popup = { novelButtonLabel, describeNovelSave, dramaTitles };`, sandbox);
  return sandbox.__popup;
}

async function novel(scenario) {
  const prose = ["雨下了一整夜。".repeat(20), "他推开门，看见院子里的灯还亮着。".repeat(10), "她没有回头。".repeat(20)];
  switch (scenario) {
    case "classify":
      return {
        prose: loadNovelPage({ paragraphs: prose }).api.isTextPage(),
        comic: loadNovelPage({ paragraphs: prose, images: [fakeImg(IMG_URL)] }).api.isTextPage(),
        short: loadNovelPage({ paragraphs: ["一句很短的话，只有这一点点而已，不到三百个字符的样子。"] }).api.isTextPage(),
      };
    case "selection_wins": {
      const h = loadNovelPage({ paragraphs: prose, selection: "只要这一段。" });
      const r = await h.api.saveNovelText({ dramaId: 4 });
      return { sent: h.sent[0], fromSelection: r.data.fromSelection };
    }
    case "main_text": {
      const h = loadNovelPage({ paragraphs: prose });
      const r = await h.api.saveNovelText({ dramaId: 4 });
      return { sent: h.sent[0], fromSelection: r.data.fromSelection };
    }
    case "long_heading": {
      const h = loadNovelPage({ paragraphs: prose, heading: "a".repeat(199) + "😀" });
      await h.api.saveNovelText({ dramaId: 4 });
      return { heading: h.sent[0].heading };
    }
    case "over_cap": {
      const h = loadNovelPage({ paragraphs: prose, selection: "字".repeat(200001) });
      const r = await h.api.saveNovelText({ dramaId: 4 });
      return { ok: r.ok, sentCount: h.sent.length };
    }
    case "worker": {
      const post = async (url) => {
        const calls = [];
        const h = loadBackground({
          granted: () => true, stored: { token: "t" },
          fetchImpl: async (target, options) => {
            calls.push({ path: new URL(target).pathname, body: JSON.parse(options.body) });
            return new Response(JSON.stringify({ saved: true }), { status: 200 });
          },
        });
        await h.ask({ type: "saveNovelText", dramaId: 4, heading: "h", text: "正文", source: "s", url });
        return calls[0];
      };
      return { public: await post("https://novel.example/ch/3"), private: await post("http://192.168.1.5/ch/3") };
    }
    case "popup_labels": {
      const p = loadPopupScript();
      p.dramaTitles.set("4", "书");
      return {
        none: p.novelButtonLabel("", ""), novel: p.novelButtonLabel("书", "novel"),
        comic: p.novelButtonLabel("漫", "comic"),
        saved: p.describeNovelSave({ saved: true, drama_id: 4, chars: 120, fromSelection: true }),
        repeat: p.describeNovelSave({ saved: false, drama_id: 4 }),
      };
    }
  }
  throw new Error(`unknown scenario ${scenario}`);
}

const [kind, scenario] = process.argv.slice(2);
const handlers = { content, background, popup, novel };
const result = await handlers[kind](scenario);
process.stdout.write(JSON.stringify(result));
process.exit(0);
