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


def test_the_settings_answer_never_carries_the_token():
    out = _node("worker_get_settings")
    assert out["reply"]["ok"] is True
    assert "SECRET-TOKEN" not in json.dumps(out["reply"])
    assert out["reply"]["data"]["serverUrl"] == "https://baihe.example"


def test_the_popup_names_the_auto_choice_and_says_what_happened():
    out = _node("popup_auto")
    assert out["on"] == "Auto: create or reuse from this page"
    assert out["off"] == "Auto: reuse a matching title only"
    assert out["made"] == "Created “一人之下” in a series."
    assert out["madeNoSeries"] == "Created “一人之下”."
    assert out["reused"] == "Using the existing “一人之下”."
    assert out["filled"] == "Using the existing “一人之下”; filled its blank English title and source link."


def test_a_token_the_app_refuses_is_not_reported_as_the_app_being_down():
    out = _node("popup_health_failure")
    assert out["revoked"] == out["refused"] == "Baihe didn't accept this token."
    assert out["down"] == "Can't reach Baihe on this PC."


def test_typeset_is_off_by_default_and_sent_when_on_whether_or_not_the_page_is_saved():
    out = _node("worker_typeset")
    assert "typeset" not in out["offBody"]
    assert out["onBody"]["typeset"] is True
    assert out["notSaved"]["typeset"] is True
    assert out["saved"] == [{"typeset": True}] and out["settings"] is True


def test_cancel_drops_the_request_in_flight_instead_of_waiting_for_the_app():
    out = _node("worker_abort_send")
    assert out["aborted"]["data"]["aborted"] == 1
    assert out["reply"] == {"ok": False, "cancelled": True, "error": "Cancelled."}


def test_the_next_chapter_link_is_the_sites_own_short_label_on_the_same_site():
    out = _node("next_chapter")
    assert out["rel"] == "https://novel.example/ch/4"
    for key in ("text", "korean", "japanese", "chinese", "label"):
        assert out[key] == "https://novel.example/ch/4", key
    # A sentence that merely contains "next", another site, the page itself, or no link: nothing to follow.
    # 下一页 and the like are the next piece of the same chapter, so each would be saved as a chapter.
    assert out["nextPage"] == ""
    assert out["sentence"] == "" and out["otherSite"] == "" and out["samePage"] == "" and out["none"] == ""


def test_a_sign_in_or_purchase_page_is_recognised_but_a_chapter_with_text_is_not():
    out = _node("gated")
    assert out["password"] is True and out["notice"] is True and out["purchase"] is True
    assert out["plain"] is False and out["prose"] is False


def test_a_run_over_several_chapters_follows_the_next_link_and_reports_each_outcome():
    out = _node("worker_series")
    text = {name: out[name]["done"]["text"] for name in out if name != "cancelled"}
    saves = lambda run: [c for c in out[run]["calls"] if c[0] == "saveNovelText"]
    assert out["three"]["started"]["data"]["started"] is True
    assert text["three"] == "Chapters: saved 3, skipped 0, failed 0. Stopped: reached the chapter limit."
    assert len(saves("three")) == 3
    assert "no next-chapter link" in text["end"] and "saved 2" in text["end"]
    assert "skipped 1" in text["duplicate"] and "Chapter 2 (already saved)" in text["duplicate"]
    # A sign-in or purchase page ends the run without trying to read it.
    assert "sign-in or purchase" in text["gated"] and len(saves("gated")) == 2
    assert "failed 1" in text["broken"] and "No chapter text found" in text["broken"]
    assert "led back to a chapter already done" in text["loop"]
    assert "captureChapter" in [c[0] for c in out["comic"]["calls"]] and "saved 2" in text["comic"]


def test_a_second_chapter_run_is_refused_and_cancel_ends_the_first():
    out = _node("worker_series")["cancelled"]
    assert out["second"] == {"ok": False, "error": "A chapter run is already going."}
    assert out["done"]["text"].endswith("Stopped: cancelled.")
