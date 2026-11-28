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

# Pages are built as nested [tag, className, children, width] lists (a picture is 600 wide
# unless given); the picture whose class has "*" is the one asked about.
FAKE_DOM = r"""
const MIN_SIDE = 200;
const PICTURES = ["img", "canvas", "amp-img"];
function build(spec, parent) {
  const [tag, cls, kids, width] = spec;
  const el = { tagName: tag, className: cls, parentElement: parent || null };
  el.children = (kids || []).map((k) => build(k, el));
  el.all = () => el.children.flatMap((c) => [c, ...c.all()]);
  el.contains = (other) => { for (let n = other; n; n = n.parentElement) if (n === el) return true; return false; };
  el.getBoundingClientRect = () => ({ width: width ?? (PICTURES.includes(tag) ? 600 : 0) });
  el.querySelectorAll = (sel) => el.all().filter((n) => sel.startsWith("[class*=")
    ? n.className.includes(sel.match(/"(.*)"/)[1]) : sel.split(", ").includes(n.tagName));
  return el;
}
const root = build(JSON.parse(process.argv[1]));
const target = [root, ...root.all()].find((n) => n.className.includes("*"));
"""


def _still_drawing(page):
    src = GRAB_JS.read_text(encoding="utf-8")
    start = src.index("  // Whole class words only")
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


def test_a_spinner_picture_in_the_page_own_wrapper_still_waits():
    page = _chapter(["div", "item", [["img", "spinner loading", [], 40], ["img", "*"]]], ["div", "item", [["img", ""]]])
    assert _still_drawing(page) is True


def test_a_loading_wrapper_with_a_small_badge_still_waits():
    page = _chapter(["div", "item loading", [["img", "badge", [], 24], ["img", "*"]]], ["div", "item", [["img", ""]]])
    assert _still_drawing(page) is True


def test_the_img_inside_an_amp_img_is_not_another_page():
    page = _chapter(["div", "item", [["div", "spinner loading"], ["amp-img", "*", [["img", ""]]]]],
                    ["div", "item", [["img", ""]]])
    assert _still_drawing(page) is True


def test_a_lazy_loader_mark_on_the_picture_does_not_wait():
    page = _chapter(["div", "item", [["img", "* lazyloading"]]], ["div", "item", [["img", ""]]])
    assert _still_drawing(page) is False
