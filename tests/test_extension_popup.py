"""extension/popup.js: the result line must show how many pages were captured,
sent, received and stored, and name failures, so a mismatch is visible. Run in
Node with a stub DOM."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

POPUP = Path(__file__).resolve().parents[1] / "extension" / "popup.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

HARNESS = textwrap.dedent("""
    const vm = require("vm"), fs = require("fs");
    const el = () => ({ addEventListener() {}, classList: { toggle() {} }, appendChild() {},
                        options: [], value: "", checked: true, textContent: "" });
    const status = el();
    const sandbox = {
      document: { getElementById: (id) => (id === "status" ? status : el()), querySelectorAll: () => [] },
      chrome: { runtime: { sendMessage: async () => ({ ok: false, error: "x" }),
                           onMessage: { addListener() {} } },
                tabs: { query: async () => [] } },
      console, JSON, URL, Promise, Math, Object, Array, Number, String, Error,
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(process.argv[1], "utf8") +
      "\\n;this.__out = JSON.stringify(" + process.argv[2] + ");", sandbox);
    console.log(sandbox.__out);
""")


def js(expression):
    out = subprocess.run(["node", "-e", HARNESS, str(POPUP), expression],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def summary(data, store=True, drama=True):
    return js(f"summarizeCapture({json.dumps(data)}, {json.dumps({'store': store, 'dramaId': 5 if drama else None})})")


def pages(n):
    return [{"key": f"k{i}", "notes": []} for i in range(n)]


def test_a_clean_chapter_shows_every_count_and_is_not_flagged():
    out = summary({"captured": 47, "sent": 47, "received": 47, "stored": 47, "pages": pages(47),
                   "skipped": [], "failed": [], "unreadable": []})
    assert out["bad"] is False
    for part in ("47 captured", "47 sent", "47 received", "47 stored"):
        assert part in out["text"]


def test_a_sent_vs_received_mismatch_is_flagged_and_failures_named():
    out = summary({"captured": 47, "sent": 47, "received": 8, "stored": 8, "pages": pages(8),
                   "skipped": [], "unreadable": [],
                   "failed": [{"key": "a", "position": 9, "error": "HTTP 413"},
                              {"key": "b", "position": 10, "error": "HTTP 413"}]})
    assert out["bad"] is True
    assert "8 received" in out["text"] and "47 sent" in out["text"]
    assert "page 9" in out["text"] and "HTTP 413" in out["text"]


def test_unreadable_pages_are_named_by_position():
    out = summary({"captured": 5, "sent": 4, "received": 4, "stored": 4, "pages": pages(4),
                   "skipped": [], "failed": [],
                   "unreadable": [{"position": 2, "error": "the page was still blank"}]})
    assert out["bad"] is True and "page 2" in out["text"] and "blank" in out["text"]


def test_stored_short_of_translated_is_flagged():
    out = summary({"captured": 3, "sent": 3, "received": 3, "stored": 1, "pages": pages(3),
                   "skipped": [], "failed": [], "unreadable": []})
    assert out["bad"] is True and "1 stored" in out["text"]


def test_not_saving_does_not_report_stored_or_flag():
    out = summary({"captured": 2, "sent": 2, "received": 2, "stored": 0, "pages": pages(2),
                   "skipped": [], "failed": [], "unreadable": []}, store=False)
    assert out["bad"] is False and "stored" not in out["text"]


def test_the_no_engine_note_says_where_to_set_one_and_offers_ollama():
    note = js("NO_ENGINE_NOTE")
    assert "Settings" in note and "Browser extension" in note
    assert "Ollama" in note and "free" in note.lower()


def test_a_stopped_run_shows_its_message_and_is_flagged():
    out = summary({"captured": 10, "sent": 3, "received": 3, "stored": 3, "pages": pages(3),
                   "skipped": [], "failed": [], "unreadable": [],
                   "stopped": {"message": "Translated 3 of 10 pages; stopped at page 4 because "
                                          "the app stopped answering"}})
    assert out["bad"] is True and "stopped at page 4" in out["text"]


CONTENT = Path(__file__).resolve().parents[1] / "extension" / "content.js"

CONTENT_HARNESS = textwrap.dedent("""
    const vm = require("vm"), fs = require("fs");
    const noop = () => {};
    const el = () => ({ addEventListener: noop, appendChild: noop, style: {}, classList: { add: noop, remove: noop } });
    const window = { addEventListener: noop, location: { href: "https://s/c" } };
    const sandbox = {
      window, location: window.location,
      document: { addEventListener: noop, createElement: el, documentElement: el(), body: el(),
                  querySelectorAll: () => [], querySelector: () => null, title: "" },
      chrome: { runtime: { sendMessage: async () => ({}), onMessage: { addListener: noop } } },
      MutationObserver: class { observe() {} disconnect() {} },
      ResizeObserver: class { observe() {} disconnect() {} },
      IntersectionObserver: class { observe() {} disconnect() {} },
      console, JSON, URL, Promise, Math, Object, Array, Number, String, Error, Map, Set, WeakMap, WeakSet,
      setTimeout, clearTimeout, setInterval, clearInterval, getComputedStyle: () => ({}),
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
    console.log(JSON.stringify(eval(process.argv[2])));
""")


def tally(found, counts, first_failure, unreadable):
    expression = ("sandbox.window.__baihe.captureTally(" +
                  ", ".join(json.dumps(a) for a in (found, counts, first_failure, unreadable)) + ")")
    out = subprocess.run(["node", "-e", CONTENT_HARNESS, str(CONTENT), expression],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_chapter_capture_names_the_pages_it_could_not_read():
    counts = {"translated": 2, "cached": 0, "skipped": 0, "failed": 0}
    text = tally(3, counts, "", [{"position": 2, "error": "the page was still blank after waiting for it to draw"}])
    assert "2 translated" in text
    assert "1 unreadable (page 2: the page was still blank after waiting for it to draw)" in text


def test_many_unreadable_pages_are_counted_and_only_some_named():
    counts = {"translated": 0, "cached": 0, "skipped": 0, "failed": 0}
    text = tally(5, counts, "", [{"position": i, "error": "blank"} for i in range(1, 6)])
    assert "5 unreadable" in text and "and 2 more" in text


def test_a_clean_capture_mentions_no_unreadable_pages():
    counts = {"translated": 3, "cached": 0, "skipped": 0, "failed": 0}
    assert "unreadable" not in tally(3, counts, "", [])


def test_pages_already_in_the_library_are_not_called_translated():
    out = summary({"captured": 47, "sent": 47, "received": 47, "stored": 47, "alreadyStored": 47,
                   "pages": pages(47), "skipped": [], "failed": [], "unreadable": []})
    assert "0 translated" in out["text"] and "47 already in library" in out["text"]
    assert out["bad"] is False


def test_a_recycled_canvas_is_counted_again_but_a_repeat_failure_is_not():
    expression = """(() => {
        const log = sandbox.window.__baihe.unreadableLog();
        const canvas = {};
        const out = [log.isNew(canvas, "canvas|800x1200"), log.isNew(canvas, "canvas|800x1200")];
        log.read(canvas);
        out.push(log.isNew(canvas, "canvas|800x1200"));
        const img = {};
        out.push(log.isNew(img, "a.png|10x10"), log.isNew(img, "b.png|10x10"));
        return out;
    })()"""
    out = subprocess.run(["node", "-e", CONTENT_HARNESS, str(CONTENT), expression],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [True, False, True, True, True]
