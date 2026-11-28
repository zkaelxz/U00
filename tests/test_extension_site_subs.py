"""extension/content.js and popup.js: Baihe subs drawn over a page's own player, which follow the
media's time and turn off when the page moves on to another video. Run in Node with a stub DOM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "extension_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _node(kind, argument):
    out = subprocess.run(["node", str(HARNESS), kind, argument],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# A player page with one <video> at media time 1.5 s, and an app that has the title.
_PAGE = """
  const made = [];
  document.createElement = (tag) => {
    const e = { tag, children: [], style: {}, className: "", textContent: "", parentNode: null,
                append(...c) { this.children.push(...c); }, appendChild(c) { this.children.push(c); },
                addEventListener() {}, remove() { this.removed = true; } };
    made.push(e);
    return e;
  };
  document.getElementById = () => null;
  document.documentElement.appendChild = (c) => { c.parentNode = document.documentElement; };
  window.removeEventListener = () => {};
  document.removeEventListener = () => {};
  const media = { tagName: "VIDEO", paused: false, duration: 100, currentTime: 1.5, isConnected: true,
                  addEventListener() {}, removeEventListener() {},
                  getBoundingClientRect: () => ({ left: 10, top: 20, width: 640, height: 360 }) };
  document.querySelectorAll = (sel) => (sel === "video, audio" ? [media] : []);
  location.href = "https://www.youtube.com/watch?v=AAA";
  const asked = [];
  chrome.runtime.sendMessage = async (m) => {
    asked.push(m);
    return { ok: true, data: { found: true, title: "Sample", cut_off: false,
                               cues: [{ start: 3, end: 4, en: "Later", speaker: "" },
                                      { start: 1, end: 2, en: "Hello", speaker: "Alex Example" }] } };
  };
  const shown = () => window.__baihe.state.subs.lines.children.map((div) => div.children[0].textContent);
"""


def _run(body):
    return _node("content_expression", f"(async () => {{ {_PAGE} {body} }})()")


def test_the_line_at_the_players_time_is_drawn_over_it():
    out = _run("""
      const answer = await window.__baihe.startSubs();
      const subs = window.__baihe.state.subs;
      const result = { answer, asked: asked[0].type, shown: shown(), left: subs.root.style.left,
                       width: subs.root.style.width };
      window.__baihe.turnOffSubs();
      return result;
    """)
    assert out["answer"] == {"ok": True, "data": {"media": True, "found": True, "title": "Sample",
                                                  "count": 2, "cutOff": False}}
    assert out["asked"] == "fetchSiteSubs"
    assert out["shown"] == ["Hello"]
    assert (out["left"], out["width"]) == ("10px", "640px")


def test_on_off_on_before_the_first_answer_leaves_one_overlay():
    out = _run("""
      let release;
      const gate = new Promise((r) => { release = r; });
      const reply = chrome.runtime.sendMessage;
      chrome.runtime.sendMessage = async (m) => { await gate; return reply(m); };
      const first = window.__baihe.startSubs();
      window.__baihe.turnOffSubs();
      const second = window.__baihe.startSubs();
      release();
      const [a, b] = await Promise.all([first, second]);
      const roots = made.filter((e) => e.id === "baihe-subs");
      const result = { a: a.data, b: b.data.found, roots: roots.length,
                       current: window.__baihe.state.subs.root === roots[0] };
      window.__baihe.turnOffSubs();
      return { ...result, removed: roots[0].removed === true, running: window.__baihe.state.subs !== null };
    """)
    assert out == {"a": {"superseded": True}, "b": True, "roots": 1, "current": True,
                   "removed": True, "running": False}


def test_off_before_the_answer_draws_nothing():
    out = _run("""
      let release;
      const gate = new Promise((r) => { release = r; });
      const reply = chrome.runtime.sendMessage;
      chrome.runtime.sendMessage = async (m) => { await gate; return reply(m); };
      const first = window.__baihe.startSubs();
      window.__baihe.turnOffSubs();
      release();
      const a = await first;
      return { a: a.data, roots: made.filter((e) => e.id === "baihe-subs").length,
               running: window.__baihe.state.subs !== null };
    """)
    assert out == {"a": {"superseded": True}, "roots": 0, "running": False}


def test_a_fullscreen_video_hides_the_lines_it_cannot_draw():
    out = _run("""
      await window.__baihe.startSubs();
      const subs = window.__baihe.state.subs;
      const before = subs.root.style.display;
      document.fullscreenElement = media;
      await new Promise((r) => setTimeout(r, 300));
      const during = subs.root.style.display;
      document.fullscreenElement = null;
      await new Promise((r) => setTimeout(r, 300));
      const after = subs.root.style.display;
      window.__baihe.turnOffSubs();
      return [before, during, after];
    """)
    assert out == ["", "none", ""]


def test_a_player_that_goes_away_hides_the_lines():
    out = _run("""
      await window.__baihe.startSubs();
      const subs = window.__baihe.state.subs;
      media.isConnected = false;
      document.querySelectorAll = () => [];
      await new Promise((r) => setTimeout(r, 300));
      const display = subs.root.style.display;
      window.__baihe.turnOffSubs();
      return display;
    """)
    assert out == "none"


def test_another_video_on_the_same_page_turns_the_subs_off():
    out = _run("""
      await window.__baihe.startSubs();
      location.href = "https://www.youtube.com/watch?v=BBB&t=5s";
      await new Promise((r) => setTimeout(r, 400));
      return window.__baihe.state.subs === null;
    """)
    assert out is True


def test_a_page_with_no_player_asks_nothing():
    out = _run("""
      document.querySelectorAll = () => [];
      const answer = await window.__baihe.startSubs();
      return { answer, asked: asked.length };
    """)
    assert out == {"answer": {"ok": True, "data": {"media": False}}, "asked": 0}


def test_a_page_not_in_the_library_draws_nothing():
    out = _run("""
      chrome.runtime.sendMessage = async () => ({ ok: true, data: { found: false } });
      const answer = await window.__baihe.startSubs();
      return { answer, running: window.__baihe.state.subs !== null };
    """)
    assert out == {"answer": {"ok": True, "data": {"media": True, "found": False, "title": "", "count": 0}},
                   "running": False}


def test_cues_at_picks_every_line_showing_and_stops_at_later_ones():
    out = _node("content_expression", """window.__baihe.cuesAt(
        [{start: 0, end: 5, en: "a"}, {start: 1, end: 2, en: "b"}, {start: 3, end: 4, en: "c"}], 1.5
      ).map((c) => c.en)""")
    assert out == ["a", "b"]


def test_the_popup_says_what_happened():
    out = _node("popup_expression", """[
      describeSiteSubs({ media: false }),
      describeSiteSubs({ media: true, found: false }),
      describeSiteSubs({ media: true, found: true, title: "Sample", count: 0 }),
      describeSiteSubs({ media: true, found: true, title: "Sample", count: 12, cutOff: true }),
    ]""")
    assert out[0] == "No video or audio on this page."
    assert "Not in your library" in out[1] and "Subtitle this video" in out[1]
    assert out[2] == "Sample has no English lines yet."
    assert out[3] == "Showing Sample (12 lines, the first part only)."
