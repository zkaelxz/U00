"""extension/background.js batches a whole chapter to the bridge's per-request
cap and accounts for every page. The JS is run in Node with a fake `chrome` and
`fetch`, so nothing touches a network or a browser."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

BACKGROUND = Path(__file__).resolve().parents[1] / "extension" / "background.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

HARNESS = textwrap.dedent("""
    const vm = require("vm"), fs = require("fs");
    const scenario = JSON.parse(process.argv[2]);
    const requests = [];
    let listener = null;
    const sandbox = {
      chrome: {
        runtime: { onInstalled: { addListener() {} },
                   onMessage: { addListener(fn) { listener = fn; } } },
        storage: { local: { get: async () => ({ token: "t" }), set: async () => {}, remove() {} } },
      },
      fetch: async (url, init) => {
        const path = new URL(url).pathname;
        if (path === "/health") {
          return { ok: true, status: 200, json: async () => ({ max_images_per_request: scenario.cap }) };
        }
        const body = JSON.parse(init.body);
        requests.push({ path, keys: body.images.map((i) => i.key) });
        const reply = scenario.reply(requests.length, body);
        return { ok: reply.status === 200, status: reply.status, json: async () => reply.body };
      },
      console, JSON, URL, Promise, Math, Object, Array, Number, String, Error,
      setTimeout, globalThis: null,
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
    scenario.reply = eval(scenario.replySrc);
    const images = Array.from({ length: scenario.count }, (_, i) =>
      ({ key: "k" + i, data: "x".repeat(scenario.size || 4), content_type: "image/png", url: "u" + i }));
    listener({ type: "send", images, dramaId: 1, store: true, sourceUrl: "https://s/c", filterPages: true },
             {}, (r) => console.log(JSON.stringify({ result: r, requests })));
""")


def run(count, reply_src, cap=12, size=4):
    scenario = json.dumps({"count": count, "cap": cap, "replySrc": reply_src, "size": size})
    out = subprocess.run(["node", "-e", HARNESS, str(BACKGROUND), scenario],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


ECHO = """(n, body) => ({ status: 200, body: {
    pages: body.images.map((i) => ({ key: i.key, regions: [] })),
    skipped: [], failed: [], received: body.images.length, stored: body.images.length } })"""


def test_forty_seven_pages_go_out_in_batches_within_the_cap():
    got = run(47, ECHO, cap=12)
    sizes = [len(r["keys"]) for r in got["requests"]]
    assert sum(sizes) == 47 and max(sizes) <= 12
    sent = [k for r in got["requests"] for k in r["keys"]]
    assert sent == [f"k{i}" for i in range(47)]


def test_results_are_merged_in_order_with_totals():
    data = run(30, ECHO)["result"]["data"]
    assert [p["key"] for p in data["pages"]] == [f"k{i}" for i in range(30)]
    assert (data["sent"], data["received"], data["stored"]) == (30, 30, 30)
    assert data["failed"] == []


def test_a_failed_batch_is_named_and_later_batches_still_go():
    reply = """(n, body) => n === 2
        ? { status: 500, body: { error: "the app failed to answer this request" } }
        : { status: 200, body: { pages: body.images.map((i) => ({ key: i.key, regions: [] })),
              skipped: [], failed: [], received: body.images.length, stored: body.images.length } }"""
    data = run(30, reply, cap=10)["result"]["data"]
    assert data["sent"] == 30 and data["received"] == 20 and data["stored"] == 20
    assert [f["key"] for f in data["failed"]] == [f"k{i}" for i in range(10, 20)]
    assert "failed to answer" in data["failed"][0]["error"]


def test_batches_are_also_bounded_by_bytes():
    got = run(6, ECHO, cap=12, size=9 * 1024 * 1024)
    assert max(len(r["keys"]) for r in got["requests"]) <= 2


def test_older_app_without_the_advertised_cap_gets_small_batches():
    got = run(30, ECHO, cap=None)
    assert max(len(r["keys"]) for r in got["requests"]) <= 8
