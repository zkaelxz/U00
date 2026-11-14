"""The pages of one extension request overlap (the next is read while one translates) but keep
the order a one-at-a-time loop gave. The scanlate steps are faked; nothing loads a model or calls
a network."""
import threading
import time

import pytest

import db
import page_server
from memory_headroom import HeadroomError
from services import extension_bridge_service as bridge

from tests.test_page_server import _b64, _png_bytes, _reset_module_state  # noqa: F401

CHAPTER = "https://example.com/comic/1"


def _images(count):
    return [{"data": _b64(_png_bytes(colour=(i * 40, 30, 90))), "content_type": "image/png", "key": f"k{i}"}
            for i in range(count)]


@pytest.fixture
def pipeline(isolated_db, monkeypatch):
    """Page n's one bubble reads "p<n>"; each step is logged in the order it happened. The
    first translation waits (up to 5 s) for the second page's read to begin, so `overlapped`
    says whether the two ran at the same time."""
    import scanlate
    import scanlate_translate
    monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
    state = {"reads": 0, "log": [], "contexts": {}, "second_read": threading.Event(),
             "overlapped": None, "fail_read": None}
    lock = threading.Lock()

    def detect_and_ocr_page(image_path, source_language, **kwargs):
        with lock:
            n = state["reads"]
            state["reads"] += 1
            state["log"].append(("read", n))
        if n == 1:
            state["second_read"].set()
        if state["fail_read"] == n:
            raise HeadroomError("no room")
        return ([{"x": 1, "y": 2, "w": 30, "h": 40, "source_text": f"p{n}", "translated_text": "",
                  "kind": "bubble", "font_category": "regular", "reading_order": 0}], [])

    def translate_page_bubbles(bubbles, engine, drama_meta, previous_context="", **kwargs):
        text = bubbles[0]["source_text"]
        if text == "p0":
            state["overlapped"] = state["second_read"].wait(5)
        with lock:
            state["log"].append(("translate", text))
            state["contexts"][text] = previous_context
        for b in bubbles:
            b["translated_text"] = "EN " + b["source_text"]
        return "after " + text

    # A page held up before its read (here: in the blank check) must still be read first.
    first = bridge._decode_images(_images(1))[0]["content"]
    real_blank = bridge.page_capture_checks.looks_blank

    def looks_blank(content):
        if content == first and state["hold_first"]:
            time.sleep(0.3)
        return real_blank(content)
    state["hold_first"] = False
    monkeypatch.setattr(bridge.page_capture_checks, "looks_blank", looks_blank)
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect_and_ocr_page)
    monkeypatch.setattr(scanlate_translate, "translate_page_bubbles", translate_page_bubbles)
    bridge._cancelled_runs.clear()
    return state


def _send(count, **extra):
    payload = {"images": _images(count), "store": False, "filter_pages": False, "source_url": CHAPTER,
               **extra}
    return bridge.run_images(payload, payload["images"])


def test_the_next_page_is_read_while_one_translates(pipeline):
    answer = _send(3)
    assert pipeline["overlapped"] is True
    assert [p["key"] for p in answer["pages"]] == ["k0", "k1", "k2"]
    assert [r["regions"][0]["translated_text"] for r in answer["pages"]] == ["EN p0", "EN p1", "EN p2"]


def test_pages_still_read_and_translate_in_page_order_with_the_page_before_as_context(pipeline):
    pipeline["hold_first"] = True   # the first page is late to start; the second must still wait
    answer = _send(4)
    assert [p["regions"][0]["translated_text"] for p in answer["pages"]] == ["EN p0", "EN p1", "EN p2", "EN p3"]
    translations = [t for step, t in pipeline["log"] if step == "translate"]
    assert translations == ["p0", "p1", "p2", "p3"]
    assert pipeline["contexts"] == {"p0": "", "p1": "after p0", "p2": "after p1", "p3": "after p2"}


def test_a_captured_chapter_is_stored_in_the_order_it_was_sent(pipeline):
    pipeline["hold_first"] = True
    drama = db.create_drama(title_en="Order", media_type="manga", source_language="ja")
    payload = {"images": _images(3), "drama_id": drama, "store": True, "filter_pages": False}
    answer = bridge.run_images(payload, payload["images"])
    ids = [p["page_id"] for p in answer["pages"]]
    assert [p["key"] for p in answer["pages"]] == ["k0", "k1", "k2"]
    assert [p["id"] for p in db.list_pages(drama)] == ids
    assert [b["source_text"] for i in ids for b in db.load_bubbles(i)] == ["p0", "p1", "p2"]


def test_a_failure_every_page_would_repeat_stops_before_the_next_page_is_read(pipeline):
    pipeline["fail_read"] = 0
    answer = _send(3)
    assert pipeline["reads"] == 1
    assert answer["pages"] == [] and [f["key"] for f in answer["failed"]] == ["k0"]
    assert "not enough free memory" in answer["stopped"]


def test_a_cancel_during_the_run_leaves_the_later_pages_alone(pipeline):
    run_id = "0f8fad5b-d9cb-469f-a165-70867728950e"

    def cancel_after_first(page):
        if page["key"] == "k0":
            bridge.cancel_run({"run_id": run_id})
    payload = {"images": _images(4), "store": False, "filter_pages": False, "run_id": run_id}
    answer = bridge.run_images(payload, payload["images"], on_page=cancel_after_first)
    # The second page may already be reading when the cancel lands; nothing after it starts.
    assert pipeline["reads"] <= 2
    assert answer["stopped"] == "cancelled by the extension"
    assert answer["failed"] == []


def test_each_page_is_heard_as_it_finishes_and_a_listener_that_fails_stops_nothing(pipeline):
    heard = []

    def listener(page):
        heard.append(page["key"])
        raise OSError("the extension went away")
    payload = {"images": _images(3), "store": False, "filter_pages": False}
    answer = bridge.run_images(payload, payload["images"], on_page=listener)
    assert sorted(heard) == ["k0", "k1", "k2"]
    assert len(answer["pages"]) == 3
