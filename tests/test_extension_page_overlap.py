"""The pages of one extension request overlap (the next is read while one translates) but keep
the order a one-at-a-time loop gave, and a request that asks for `stream` hears each page as it
finishes. The scanlate steps are faked; nothing loads a model or calls a network."""
import json
import threading
import time

import pytest

import db
from services import page_server
from memory_headroom import HeadroomError
from services import extension_bridge_service as bridge

from tests.test_page_server import _b64, _png_bytes, _post, token, _reset_module_state  # noqa: F401

CHAPTER = "https://example.com/comic/1"


def _images(count):
    return [{"data": _b64(_png_bytes(colour=(i * 40, 30, 90))), "content_type": "image/png", "key": f"k{i}"}
            for i in range(count)]


@pytest.fixture
def pipeline(isolated_db, monkeypatch):
    """Page n's one bubble reads "p<n>"; each step is logged in the order it happened. The
    first translation waits (up to 5 s) for the second page's read to begin, so `overlapped`
    says whether the two ran at the same time."""
    from scanlate import scanlate
    from scanlate import scanlate_translate
    monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
    state = {"reads": 0, "log": [], "contexts": {}, "second_read": threading.Event(),
             "overlapped": None, "fail_read": None, "on_read": None, "in_translation": 0,
             "most_translating": 0, "translating": {"p0": threading.Event(), "p1": threading.Event()},
             "first_two_meet": False, "previous_lines": {}, "hold_p0": None}
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
        if state["on_read"] is not None:
            state["on_read"](n)
        return ([{"x": 1, "y": 2, "w": 30, "h": 40, "source_text": f"p{n}", "translated_text": "",
                  "kind": "bubble", "font_category": "regular", "reading_order": 0}], [])

    def translate_page_bubbles(bubbles, engine, drama_meta, previous_context="", **kwargs):
        text = bubbles[0]["source_text"]
        with lock:
            state["in_translation"] += 1
            state["most_translating"] = max(state["most_translating"], state["in_translation"])
        if text in ("p0", "p1") and state["first_two_meet"]:
            # Each waits for the other, so both are in translation at once unless one waits
            # for the other to finish.
            mine, theirs = ("p0", "p1") if text == "p0" else ("p1", "p0")
            state["translating"][mine].set()
            state["translating"][theirs].wait(5)
        if text == "p0":
            state["overlapped"] = state["second_read"].wait(5)
            if state["hold_p0"] is not None:
                state["hold_p0"].wait(5)
        with lock:
            state["log"].append(("translate", text))
            state["contexts"][text] = previous_context
            state["previous_lines"][text] = kwargs.get("previous_lines")
            state["in_translation"] -= 1
        for b in bubbles:
            b["translated_text"] = "EN " + b["source_text"]
        return "after " + text

    # A page held up before its read (here: in the blank check) must still be read first.
    first = bridge._decode_images(_images(1))[0]["content"]
    real_flat = bridge.page_capture_checks.flat_colour

    def flat_colour(content):
        if content == first and state["hold_first"]:
            state["held"] = True   # so a run that skips this per-page check fails the tests below
            time.sleep(0.3)
        return real_flat(content)
    state["hold_first"] = False
    state["held"] = False
    monkeypatch.setattr(bridge.page_capture_checks, "flat_colour", flat_colour)
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


def test_saved_pages_still_read_and_translate_in_page_order_with_the_page_before_as_context(pipeline):
    pipeline["hold_first"] = True   # the first page is late to start; the second must still wait
    drama = db.create_drama(title_en="Order", media_type="manga", source_language="ja")
    payload = {"images": _images(4), "drama_id": drama, "store": True, "filter_pages": False}
    answer = bridge.run_images(payload, payload["images"])
    assert [p["regions"][0]["translated_text"] for p in answer["pages"]] == ["EN p0", "EN p1", "EN p2", "EN p3"]
    translations = [t for step, t in pipeline["log"] if step == "translate"]
    assert translations == ["p0", "p1", "p2", "p3"]
    assert pipeline["contexts"] == {"p0": "", "p1": "after p0", "p2": "after p1", "p3": "after p2"}
    assert pipeline["most_translating"] == 1
    assert pipeline["held"]


def test_quick_read_pages_translate_side_by_side_but_still_read_in_order(pipeline):
    pipeline["hold_first"] = True
    pipeline["first_two_meet"] = True
    answer = _send(4)
    assert [p["key"] for p in answer["pages"]] == ["k0", "k1", "k2", "k3"]
    assert [p["regions"][0]["translated_text"] for p in answer["pages"]] == ["EN p0", "EN p1", "EN p2", "EN p3"]
    assert [n for step, n in pipeline["log"] if step == "read"] == [0, 1, 2, 3]
    # The first two pages only translate at the same time if the second does not wait for the
    # first, so the second cannot have the first page's summary.
    assert pipeline["most_translating"] >= 2
    assert pipeline["contexts"]["p1"] != "after p0"
    assert pipeline["held"]


def test_a_captured_chapter_is_stored_in_the_order_it_was_sent(pipeline, monkeypatch):
    # A page's answer carries only a fixed failure text; the exception goes to the log. Kept here
    # so a failure under CI load names its cause.
    logged = []
    monkeypatch.setattr(bridge.page_capture_checks, "log_page_failure", lambda e: logged.append(repr(e)))
    pipeline["hold_first"] = True
    drama = db.create_drama(title_en="Order", media_type="manga", source_language="ja")
    payload = {"images": _images(3), "drama_id": drama, "store": True, "filter_pages": False}
    answer = bridge.run_images(payload, payload["images"])
    ids = [p["page_id"] for p in answer["pages"]]
    assert [p["key"] for p in answer["pages"]] == ["k0", "k1", "k2"], (answer["failed"], answer.get("stopped"), logged)
    assert [p["id"] for p in db.list_pages(drama)] == ids
    assert [b["source_text"] for i in ids for b in db.load_bubbles(i)] == ["p0", "p1", "p2"]
    assert pipeline["held"]


def test_a_failure_every_page_would_repeat_stops_before_the_next_page_is_read(pipeline):
    pipeline["fail_read"] = 0
    answer = _send(3)
    assert pipeline["reads"] == 1
    assert answer["pages"] == [] and [f["key"] for f in answer["failed"]] == ["k0"]
    assert "not enough free memory" in answer["stopped"]


@pytest.mark.parametrize("store", [True, False])
def test_a_cancel_during_the_run_leaves_the_later_pages_alone(pipeline, store):
    run_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    payload = {"images": _images(4), "store": store, "filter_pages": False, "run_id": run_id}
    if store:
        payload["drama_id"] = db.create_drama(title_en="Cancel", media_type="manga", source_language="ja")
    pipeline["on_read"] = lambda n: n == 1 and bridge.cancel_run({"run_id": run_id})
    answer = bridge.run_images(payload, payload["images"])
    # The cancel lands while the second page reads; no later page is read.
    assert pipeline["reads"] == 2
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


@pytest.mark.parametrize("count", [1, 3])
def test_every_page_runs_for_the_member_who_sent_the_batch(pipeline, monkeypatch, count):
    """The member's identity picks their key and books the spend to them, so a page worked on a
    thread of its own must still see it."""
    from lib import acting
    seen = []
    real = page_server.translate_image

    def spy(*args, **kwargs):
        seen.append(acting.acting_principal())
        return real(*args, **kwargs)
    monkeypatch.setattr(page_server, "translate_image", spy)
    member = {"id": 7, "email": "m@example.com"}
    bound = acting.bind_request()
    try:
        acting.note_acting_principal(member)
        _send(count)
    finally:
        acting.unbind_request(bound)
    assert seen == [member] * count


def _lines(handler):
    return [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]


def test_a_streamed_batch_sends_one_line_per_page_then_the_rest_of_the_answer(token, pipeline):
    handler = _post(token, {"images": _images(3), "store": False, "filter_pages": False,
                            "stream": True}, path="/pages")
    assert handler.status == 200
    assert handler.header("Content-Type") == bridge.STREAM_CONTENT_TYPE
    lines = _lines(handler)
    assert sorted(line["page"]["key"] for line in lines[:-1]) == ["k0", "k1", "k2"]
    done = lines[-1]["done"]
    assert "pages" not in done and done["received"] == 3 and done["failed"] == []


def test_a_stream_refused_before_any_page_is_an_ordinary_error_answer(token, pipeline):
    images = _images(2)
    images[1]["data"] = "not base64!"
    handler = _post(token, {"images": images, "store": False, "stream": True}, path="/pages")
    assert handler.status == 400
    assert "base64" in handler.payload["error"]


def test_without_stream_the_answer_is_unchanged(token, pipeline):
    handler = _post(token, {"images": _images(2), "store": False, "filter_pages": False}, path="/pages")
    assert handler.status == 200 and [p["key"] for p in handler.payload["pages"]] == ["k0", "k1"]


class TestDeviceTokenRoute:
    def test_a_members_batch_streams_too(self, pipeline):
        from tests.test_extension_bridge_routes import BASE, _auth, _client, _member, _png
        _, member_token = _member("a@example.com")
        img = lambda key: {"key": key, "data": _png(), "content_type": "image/png"}  # noqa: E731
        with _client().stream("POST", f"{BASE}/pages", headers=_auth(member_token), json={
                "images": [img("a"), img("b")], "store": False, "filter_pages": False,
                "stream": True}) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith(bridge.STREAM_CONTENT_TYPE)
            lines = [json.loads(line) for line in r.iter_lines() if line]
        assert sorted(line["page"]["key"] for line in lines[:-1]) == ["a", "b"]
        assert "pages" not in lines[-1]["done"]

    def test_a_members_stream_into_someone_elses_title_is_still_a_404(self, pipeline):
        from tests.test_extension_bridge_routes import BASE, _auth, _client, _drama, _member, _png
        _, member_token = _member("a@example.com")
        other, _ = _member("b@example.com")
        r = _client().post(f"{BASE}/pages", headers=_auth(member_token), json={
            "drama_id": _drama(other["id"], "Theirs"), "stream": True,
            "images": [{"key": "k", "data": _png(), "content_type": "image/png"}]})
        assert (r.status_code, r.json()) == (404, {"error": "no such drama"})


def test_quick_read_answers_a_page_it_already_read_without_reading_it_again(pipeline):
    first = _send(2)
    again = _send(2)
    assert pipeline["reads"] == 2
    assert [p["regions"][0]["translated_text"] for p in again["pages"]] == ["EN p0", "EN p1"]
    assert all(p["cached"] is True for p in again["pages"]) and "cached" not in first["pages"][0]


def test_a_saved_send_and_a_page_with_a_warning_are_never_answered_from_memory(pipeline, monkeypatch):
    drama = db.create_drama(title_en="Saved", media_type="manga", source_language="ja")
    for _ in range(2):
        payload = {"images": _images(1), "drama_id": drama, "store": True, "filter_pages": False}
        bridge.run_images(payload, payload["images"])
    monkeypatch.setattr(page_server, "_build_engine", lambda config: None)   # "no engine" warning
    _send(1)
    _send(1)
    # One read for the saved page (the second send reuses the library's copy), two for Quick read.
    assert pipeline["reads"] == 3


def _wait_until(check, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    return check()


def _send_in_background(answers, name, **extra):
    thread = threading.Thread(target=lambda: answers.__setitem__(name, _send(1, **extra)), daemon=True)
    thread.start()
    return thread


def test_a_page_still_being_read_is_shared_with_a_repeat_ask_not_read_again(pipeline):
    """A phone whose stream dropped asks again for the pages it did not get; one still being read
    for the first ask answers both."""
    release = threading.Event()
    pipeline["on_read"] = lambda n: n == 0 and release.wait(5)
    pipeline["second_read"].set()   # one page here: its translation must not wait for a second read
    answers = {}
    first = _send_in_background(answers, "first", run_id="run-aaaaaaaa")
    assert _wait_until(lambda: pipeline["reads"] == 1)
    again = _send_in_background(answers, "again", run_id="run-aaaaaaaa")
    time.sleep(0.3)   # the repeat would have started its own read by now
    release.set()
    first.join(5)
    again.join(5)
    assert pipeline["reads"] == 1
    assert [p["regions"][0]["translated_text"] for p in answers["again"]["pages"]] == ["EN p0"]
    assert answers["again"]["pages"][0]["cached"] is True and "cached" not in answers["first"]["pages"][0]
    assert bridge._in_flight == {}


def test_a_repeat_ask_waiting_on_a_page_stops_when_its_run_is_dropped(pipeline):
    release = threading.Event()
    pipeline["on_read"] = lambda n: n == 0 and release.wait(5)
    pipeline["second_read"].set()   # one page here: its translation must not wait for a second read
    answers = {}
    first = _send_in_background(answers, "first")
    assert _wait_until(lambda: pipeline["reads"] == 1)
    again = _send_in_background(answers, "again", run_id="run-bbbbbbbb")
    time.sleep(0.1)
    bridge.cancel_run({"run_id": "run-bbbbbbbb"})
    again.join(5)
    assert answers["again"]["pages"] == [] and answers["again"]["stopped"]
    release.set()
    first.join(5)
    assert len(answers["first"]["pages"]) == 1 and pipeline["reads"] == 1


def test_a_run_nobody_listens_to_finishes_its_page_in_progress_and_drops_the_rest(pipeline, monkeypatch):
    run_id = "run-cccccccc"
    monkeypatch.setattr(bridge, "STREAM_GONE_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(bridge, "_streams", {})

    def phone_goes_away(n):
        if n == 0:
            bridge.stream_closed(run_id, stream, lost=True)
            assert _wait_until(lambda: bridge._run_cancelled(run_id))

    pipeline["on_read"] = phone_goes_away
    pipeline["second_read"].set()
    stream = bridge.stream_opened(run_id)
    answer = _send(3, run_id=run_id)
    assert pipeline["reads"] == 1
    assert [p["key"] for p in answer["pages"]] == ["k0"]
    assert "stopped listening" in answer["stopped"]
    # The page that was in progress is remembered, so asking for it again costs nothing.
    assert _send(1)["pages"][0]["cached"] is True and pipeline["reads"] == 1


def _send_pages_in_background(answers, name, images, **extra):
    payload = {"images": images, "store": False, "filter_pages": False, "source_url": CHAPTER, **extra}
    thread = threading.Thread(target=lambda: answers.__setitem__(name, bridge.run_images(payload, images)),
                              daemon=True)
    thread.start()
    return thread


def test_a_shared_page_keeps_reading_order_and_gives_the_next_page_its_lines(pipeline):
    release = pipeline["hold_p0"] = threading.Event()   # the first page stays in translation
    pipeline["second_read"].set()
    images = _images(2)
    answers = {}
    first = _send_pages_in_background(answers, "first", images[:1])
    assert _wait_until(lambda: pipeline["reads"] == 1)
    again = _send_pages_in_background(answers, "again", images)
    # The second page reads while the first waits for the other request's answer.
    assert _wait_until(lambda: pipeline["reads"] == 2)
    assert not release.is_set()
    release.set()
    first.join(5)
    again.join(5)
    assert pipeline["reads"] == 2
    assert [p["key"] for p in answers["again"]["pages"]] == ["k0", "k1"]
    assert pipeline["previous_lines"]["p1"] == [{"source": "p0", "english": "EN p0"}]


def test_a_page_waiting_on_another_request_stops_when_its_own_request_halts(pipeline):
    release = pipeline["hold_p0"] = threading.Event()
    pipeline["second_read"].set()
    images = _images(2)
    answers = {}
    first = _send_pages_in_background(answers, "first", images[:1])
    assert _wait_until(lambda: pipeline["reads"] == 1)
    pipeline["fail_read"] = 1   # the second page's read fails in a way every page would repeat
    again = _send_pages_in_background(answers, "again", images)
    again.join(4)
    assert not again.is_alive() and not release.is_set()
    assert answers["again"]["pages"] == [] and "not enough free memory" in answers["again"]["stopped"]
    release.set()
    first.join(5)
    assert len(answers["first"]["pages"]) == 1
