"""Grab's wait for a page still being drawn (android/app/src/main/assets/grab.js): the two
functions are cut out of the script and run in Node on a fake DOM, so no browser is needed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

GRAB_JS = Path(__file__).resolve().parent.parent / "android" / "app" / "src" / "main" / "assets" / "grab.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Pages are built as nested [tag, className, children] lists; the picture marked "*" is the
# one asked about.
FAKE_DOM = r"""
function build(spec, parent) {
  const [tag, cls, kids] = spec;
  const el = { tagName: tag, className: cls, parentElement: parent || null, children: [] };
  el.children = (kids || []).map((k) => build(k, el));
  const all = () => el.children.flatMap((c) => [c, ...c.all()]);
  el.all = all;
  el.querySelectorAll = (sel) => all().filter((n) => sel.split(", ").includes(n.tagName));
  el.querySelector = (sel) => all().find((n) => n.className.includes(sel.match(/"(.*)"/)[1])) || null;
  return el;
}
const root = build(JSON.parse(process.argv[1]));
const target = [root, ...root.all()].find((n) => n.className.includes("*"));
"""


def _still_drawing(page):
    src = GRAB_JS.read_text(encoding="utf-8")
    start = src.index("  function holdsAnotherPicture(")
    end = src.index("  // A canvas taller than the screen")
    script = FAKE_DOM + src[start:end] + "\nconsole.log(JSON.stringify(stillDrawing(target)));"
    done = subprocess.run([NODE, "-e", script, json.dumps(page)], capture_output=True, text=True,
                          timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _chapter(first_item, later_item):
    return ["div", "list", [first_item, ["div", "item", [["img", ""]]], later_item]]


def test_a_placeholder_further_down_the_chapter_does_not_hold_a_drawn_page():
    page = _chapter(["div", "item", [["img", "*"]]], ["div", "item", [["img", "lazy-loading"]]])
    assert _still_drawing(page) is False


def test_a_spinner_in_the_page_own_wrapper_still_waits():
    page = _chapter(["div", "item", [["div", "spinner loading"], ["img", "*"]]], ["div", "item", [["img", ""]]])
    assert _still_drawing(page) is True


def test_a_page_wrapper_marked_loading_still_waits():
    page = _chapter(["div", "item is-loading", [["div", "inner", [["img", "*"]]]]], ["div", "item", [["img", ""]]])
    assert _still_drawing(page) is True


def test_a_wrapper_marked_loaded_does_not_wait():
    page = _chapter(["div", "item loaded", [["div", "spinner loading"], ["img", "*"]]], ["div", "item", [["img", ""]]])
    assert _still_drawing(page) is False
