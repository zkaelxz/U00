"""The extension's side of auto-import: what the content script reads off a
page, what the worker sends to POST /title (and keeps as options), and what
the popup says. Run in Node against fake pages (tests/js/extension_harness.mjs)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _node(scenario):
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "extension_harness.mjs"), "novel", scenario],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_page_facts_list_the_sharing_title_then_the_heading_then_the_tab_title():
    facts = _node("page_facts")
    assert facts["full"]["titles"] == ["一人之下 第12话", "第12话 追击", "一人之下 - 腾讯漫画"]
    assert facts["full"]["language"] == "zh-CN"
    assert facts["full"]["url"] == "https://novel.example/ch/3"


def test_page_facts_skip_blank_and_repeated_titles_and_cap_the_length():
    facts = _node("page_facts")
    assert facts["bare"]["titles"] == []
    assert facts["dupes"]["titles"] == ["同名"]
    assert all(len(t) <= 300 for t in facts["many"]["titles"])


def test_the_worker_posts_the_page_to_title_with_the_three_options_on_by_default():
    out = _node("worker_title")
    assert out["path"] == "/title"
    assert out["body"] == {"url": "https://www.twmanga.com/c/1?x=1", "titles": ["一人之下"],
                           "language": "zh", "kind": "comic",
                           "chapter_links": [], "create": True, "fill": True, "series": True}
    assert out["reply"]["ok"] is True and out["reply"]["data"]["drama_id"] == 9
    assert out["settings"] == {"create": True, "fill": True, "series": True}


def test_the_saved_options_are_sent_and_a_lan_page_address_is_dropped():
    out = _node("worker_title")
    assert (out["off"]["create"], out["off"]["fill"], out["off"]["series"]) == (False, False, False)
    assert out["lanUrl"] == ""


def test_saving_options_keeps_the_other_ones_and_ignores_unknown_keys():
    out = _node("worker_save_options")
    assert out["reply"]["ok"] is True
    assert out["saved"] == [{"autoTitle": {"create": True, "fill": False, "series": False}}]


def test_the_popup_names_the_auto_choice_and_says_what_happened():
    out = _node("popup_auto")
    assert out["on"] == "Auto: create or reuse from this page"
    assert out["off"] == "Auto: reuse a matching title only"
    assert out["made"] == "Created “一人之下” in a series."
    assert out["madeNoSeries"] == "Created “一人之下”."
    assert out["reused"] == "Using the existing “一人之下”."
    assert out["filled"] == "Using the existing “一人之下”; filled its blank English title and source link."
