"""Grab's wait for a reader still fetching its pages (android/app/src/main/assets/grab.js): the
whole script runs in Node on a fake page with a fake clock, so no browser is needed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

GRAB_JS = Path(__file__).resolve().parent.parent / "android" / "app" / "src" / "main" / "assets" / "grab.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# One lazy picture (its address in data-src) on a page that cannot scroll; the body's text is
# argv[1]. The clock only moves when the script waits, so the 20-second settle wait is instant.
FAKE_PAGE = r"""
let now = 1e12;
Date.now = () => now;
globalThis.setTimeout = (fn, ms) => { now += ms || 0; setImmediate(fn); return 0; };
globalThis.clearTimeout = () => {};
const calls = [];
globalThis.Baihe = new Proxy({}, { get: (_, name) => (...args) => calls.push([name, ...args.slice(1)]) });
const picture = {
  tagName: "IMG", naturalWidth: 0, naturalHeight: 0, width: 0, complete: false, src: "", currentSrc: "",
  getAttribute: (name) => (name === "data-src" ? "https://pages.example/1.jpg" : null),
  getBoundingClientRect: () => ({ top: 0, bottom: 0, left: 0, width: 0, height: 0 }),
  querySelector: () => null, scrollIntoView() {},
};
const box = { scrollTop: 0, scrollHeight: 800, clientHeight: 800, scrollTo(o) { this.scrollTop = 0; } };
globalThis.window = globalThis;
globalThis.innerHeight = 800;
globalThis.innerWidth = 400;
globalThis.scrollBy = () => {};
globalThis.location = new URL("https://reader.example/owl/1");
globalThis.getComputedStyle = () => ({ overflowY: "visible", backgroundImage: "none" });
globalThis.WheelEvent = globalThis.KeyboardEvent = class { constructor() {} };
const body = { innerText: process.argv[1], dispatchEvent() {} };
globalThis.document = {
  title: "Night Owl 1", body, scrollingElement: box, documentElement: { lang: "" }, images: [picture],
  querySelectorAll: (sel) => (sel === "img, canvas, amp-img" ? [picture] : []),
  querySelector: () => null, elementFromPoint: () => body,
};
process.on("exit", () => console.log(JSON.stringify(calls)));
"""


def _grab(page_text):
    script = FAKE_PAGE + GRAB_JS.read_text(encoding="utf-8")
    done = subprocess.run([NODE, "-e", script, page_text], capture_output=True, text=True,
                          timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("page_text", ["载入中", "Page 1 / 5"], ids=["loading-word", "page-counter"])
def test_a_reader_still_fetching_its_pages_still_hands_over_the_chapter(page_text):
    calls = _grab(page_text)
    names = [c[0] for c in calls]
    assert names[0] == "begin" and json.loads(calls[0][1])["count"] == 1
    assert ["pending", "p0", "https://pages.example/1.jpg"] in calls
    assert names[-1] == "done"
