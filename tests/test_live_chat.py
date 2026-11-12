"""Tests for live_chat.py and the chat side of a Live session. No network: YouTube's answers are fixtures."""
import json
import threading
from collections import deque

import pytest

import background_jobs
import live_chat
from lib import http
from lib.errors import InvalidInputError
from services import live_service
from tests.test_live_service import _start, _terminal, _wait, live  # noqa: F401 -- the fixture

VID = "dQw4w9WgXcQ"


def _page(continuation="tok1"):
    data = {"contents": {"liveChatRenderer": {"continuations": [
        {"invalidationContinuationData": {"continuation": continuation, "timeoutMs": 5000}}]}}}
    return ('<script>"INNERTUBE_API_KEY":"KEY123","INNERTUBE_CONTEXT_CLIENT_VERSION":"2.2024"</script>'
            f'<script>window["ytInitialData"] = {json.dumps(data)};</script>')


def _text(mid, author, *runs):
    return {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "id": mid, "authorName": {"simpleText": author}, "message": {"runs": list(runs)}}}}}


def _reply(actions, token="tok2", wait=3000):
    return {"continuationContents": {"liveChatContinuation": {
        "actions": actions, "continuations": [{"timedContinuationData": {"continuation": token, "timeoutMs": wait}}]}}}


@pytest.mark.parametrize("url, expected", [
    (f"https://www.youtube.com/watch?v={VID}", VID),
    (f"https://youtu.be/{VID}?t=3", VID),
    (f"https://www.youtube.com/live/{VID}", VID),
    (f"https://m.youtube.com/watch?v={VID}&list=x", VID),
    ("https://www.youtube.com/@someone/live", None),
    ("https://www.youtube.com/watch?v=short", None),
    (f"https://evil.example/watch?v={VID}", None),
    ("not a url", None),
])
def test_video_id(url, expected):
    assert live_chat.video_id(url) == expected


def test_require_video_id_refuses_other_links():
    with pytest.raises(InvalidInputError):
        live_chat.require_video_id("https://www.bilibili.com/video/BV1")


def test_parse_page_reads_key_version_and_token():
    assert live_chat.parse_page(_page("abc")) == ("KEY123", "2.2024", "abc")


def test_parse_page_without_chat_is_none():
    assert live_chat.parse_page("<html>Chat is disabled</html>") is None


def test_parse_reply_keeps_text_and_drops_custom_emotes_and_links():
    reply = _reply([
        _text("m1", "Ann", {"text": "你好 "}, {"emoji": {"isCustomEmoji": True, "emojiId": "UC/x", "shortcuts": [":hi:"]}},
              {"text": "https://spam.example/x"}),
        {"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {
            "id": "m2", "authorName": {"simpleText": "Bo"}, "purchaseAmountText": {"simpleText": "$5.00"},
            "message": {"runs": [{"text": "加油"}]}}}}},
        {"addChatItemAction": {"item": {"liveChatMembershipItemRenderer": {"id": "m3"}}}},
    ])
    messages, following = live_chat.parse_reply(reply)
    assert [(m["author"], m["text"], m["paid"]) for m in messages] == [
        ("Ann", "你好 [link]", ""), ("Bo", "加油", "$5.00")]
    assert following == ("tok2", 3000)


def test_parse_reply_keeps_the_send_time_and_zero_for_a_missing_or_garbled_one():
    reply = _reply([
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "id": f"m{n}", "authorName": {"simpleText": "Ann"}, "message": {"runs": [{"text": "你好"}]},
            **({"timestampUsec": stamp} if stamp is not None else {})}}}}
        for n, stamp in enumerate(["1762950600123456", None, "soon", "-5", "9" * 20], 1)])
    messages, _ = live_chat.parse_reply(reply)
    assert [m["sent_at"] for m in messages] == [pytest.approx(1762950600.123456), 0.0, 0.0, 0.0, 0.0]


def test_board_keeps_the_send_time_with_the_message():
    board = live_chat.ChatBoard()
    board.add("Ann", "你好", "", "pending", 1762950600.0)
    board.add("Bo", "加油", "", "pending")
    assert [m["sent_at"] for m in board.snapshot()["messages"]] == [1762950600.0, 0.0]


def test_parse_reply_with_no_continuation_means_the_chat_ended():
    messages, following = live_chat.parse_reply({"continuationContents": {"liveChatContinuation": {"actions": []}}})
    assert messages == [] and following is None


def test_clean_text_is_one_short_line_without_control_characters():
    assert live_chat.clean_text("a\n\x07b   :wave: c") == "a b c"
    assert len(live_chat.clean_text("字" * 500)) == live_chat.MAX_TEXT_CHARS


@pytest.mark.parametrize("text, lang, worth", [
    ("今天的直播太好看了", "zh", True),
    ("hello everyone", "zh", False),
    ("🎉🎉🎉", "ja", False),
    ("こんにちは", "ja", True),
    ("안녕하세요", "ko", True),
    ("wwww", "ja", False),
    ("哈哈哈哈哈", "zh", False),
    ("[link]", "zh", False),
])
def test_worth_translating(text, lang, worth):
    assert live_chat.worth_translating(text, lang, deque(maxlen=10)) is worth


def test_a_repeated_line_is_translated_once():
    recent = deque(maxlen=10)
    assert live_chat.worth_translating("好きです", "ja", recent)
    assert not live_chat.worth_translating("好きです", "ja", recent)


class FakeTranslator:
    def __init__(self, fail=False):
        self.batches, self.fail = [], fail

    def translate_chunk(self, texts):
        self.batches.append(list(texts))
        return {} if self.fail else {i: f"EN {t}" for i, t in enumerate(texts)}


def _worker(translator, fresh_rounds, monkeypatch, sleeps=None):
    """A worker whose polls answer fresh_rounds in turn, then say the chat ended."""
    rounds = iter(fresh_rounds)
    page = http.Response(200, {}, _page().encode(), live_chat.CHAT_PAGE)

    def get(url, **kw):
        assert kw["timeout"] and "key" not in url
        return page

    def post(url, **kw):
        assert kw["timeout"]
        try:
            body = next(rounds)
        except StopIteration:
            body = {}
        return http.Response(200, {}, json.dumps(body).encode(), url)

    monkeypatch.setattr(http, "get", get)
    monkeypatch.setattr(http, "post", post)
    board = live_chat.ChatBoard()
    notes = []
    worker = live_chat.ChatWorker("live_x", VID, board, translator, "zh",
                                  report_note=lambda text, key=None: notes.append(text),
                                  sleep=(sleeps.append if sleeps is not None else lambda s: None))
    return worker, board, notes


def test_worker_translates_new_lines_and_skips_the_rest(monkeypatch):
    translator = FakeTranslator()
    worker, board, _ = _worker(translator, [_reply([
        _text("a", "Ann", {"text": "今天好开心"}), _text("b", "Bo", {"text": "hello"}),
        _text("a", "Ann", {"text": "今天好开心"})])], monkeypatch)
    worker._read()
    snap = board.snapshot()
    assert [(m["text"], m["state"], m["translation"]) for m in snap["messages"]] == [
        ("今天好开心", "done", "EN 今天好开心"), ("hello", "skipped", "")]
    assert translator.batches == [["今天好开心"]]
    assert snap["state"] == "ended"


def test_worker_marks_a_failed_batch_and_keeps_reading(monkeypatch):
    worker, board, _ = _worker(FakeTranslator(fail=True), [_reply([_text("a", "Ann", {"text": "你好呀朋友"})])], monkeypatch)
    worker._read()
    assert [m["state"] for m in board.snapshot()["messages"]] == ["failed"]


def test_worker_translates_only_the_newest_when_far_behind(monkeypatch):
    lines = [_text(f"m{i}", "A", {"text": f"第{i}句话真好"}) for i in range(live_chat.MAX_BACKLOG + 5)]
    translator = FakeTranslator()
    worker, board, _ = _worker(translator, [_reply(lines)], monkeypatch)
    worker._read()
    states = [m["state"] for m in board.snapshot()["messages"]]
    assert states.count("skipped") == 5 and states.count("done") == live_chat.MAX_BACKLOG
    assert max(len(b) for b in translator.batches) <= live_chat.BATCH_SIZE


def test_worker_stops_translating_at_the_session_cap(monkeypatch):
    monkeypatch.setattr(live_chat, "MAX_TRANSLATED", 2)
    lines = [_text(f"m{i}", "A", {"text": f"第{i}句话真好"}) for i in range(4)]
    translator = FakeTranslator()
    worker, board, notes = _worker(translator, [_reply(lines)], monkeypatch)
    worker._read()
    assert [m["state"] for m in board.snapshot()["messages"]] == ["done", "done", "skipped", "skipped"]
    assert set(notes) == {live_chat.CAP_REACHED}


def test_worker_waits_the_chats_own_interval_within_bounds(monkeypatch):
    sleeps = []
    worker, _, _ = _worker(FakeTranslator(), [_reply([], wait=100), _reply([], wait=60000)], monkeypatch, sleeps)
    worker._read()
    assert sleeps == [live_chat.MIN_POLL_SECONDS, live_chat.MAX_POLL_SECONDS]


def test_a_page_without_chat_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: http.Response(200, {}, b"<html></html>", url))
    board = live_chat.ChatBoard()
    notes = []
    live_chat.ChatWorker("live_x", VID, board, FakeTranslator(), "zh",
                         report_note=lambda text, key=None: notes.append(text))._read()
    assert board.snapshot()["state"] == "unavailable" and notes == [live_chat.NO_CHAT]


def test_repeated_fetch_failures_give_up(monkeypatch):
    worker, board, _ = _worker(FakeTranslator(), [], monkeypatch)

    def boom(url, **kw):
        raise http.FetchError()

    monkeypatch.setattr(http, "post", boom)
    worker._read()
    assert board.snapshot()["state"] == "lost"


def test_stopping_the_reader_ends_a_chat_still_marked_reading(monkeypatch):
    worker, board, _ = _worker(FakeTranslator(), [], monkeypatch)
    board.set_state("reading", live_chat.READING)
    worker.stop()
    assert board.snapshot()["state"] == "ended"
    assert board.snapshot()["message"] == live_chat.SESSION_ENDED


def test_stopping_keeps_a_reason_the_chat_already_gave(monkeypatch):
    worker, board, _ = _worker(FakeTranslator(), [], monkeypatch)
    board.set_state("lost", live_chat.LOST)
    worker.stop()
    assert board.snapshot()["state"] == "lost"


def test_a_cancelled_session_stops_the_reader(monkeypatch):
    worker, board, _ = _worker(FakeTranslator(), [_reply([])] * 50, monkeypatch)
    monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda sid: True)
    worker._read()
    assert board.snapshot()["messages"] == []


def test_snapshot_returns_only_messages_after_the_id():
    board = live_chat.ChatBoard()
    first = board.add("A", "x", "", live_chat.PENDING)
    board.add("B", "y", "", live_chat.PENDING)
    board.finish(first, "X", live_chat.DONE)
    assert [m["id"] for m in board.snapshot(1)["messages"]] == [2]
    assert board.snapshot()["messages"][0]["translation"] == "X"
    assert board.snapshot()["next_id"] == 2


def test_the_spend_guard_wrapper_never_counts_a_failure():
    calls = []

    class Guard:
        def before(self):
            calls.append("before")

        def after(self, error=None, usable=True):
            calls.append((error, usable))

    wrapped = live_chat.NoStreakGuard(Guard())
    wrapped.before()
    wrapped.after(RuntimeError("x"), True)
    wrapped.after(None, True)
    assert calls == ["before", (None, False), (None, False)]


# --- the session ----------------------------------------------------------

@pytest.fixture
def chat_pages(monkeypatch):
    """The chat answers one line, then goes quiet; the session itself stays running."""
    stop = threading.Event()
    monkeypatch.setattr(http, "get", lambda url, **kw: http.Response(200, {}, _page().encode(), url))
    sent = []

    def post(url, **kw):
        if not sent:
            sent.append(1)
            return http.Response(200, {}, json.dumps(_reply([_text("a", "Ann", {"text": "今天好开心"})])).encode(), url)
        stop.wait(0.05)
        return http.Response(200, {}, json.dumps(_reply([])).encode(), url)

    monkeypatch.setattr(http, "post", post)
    monkeypatch.setattr(live_chat, "MIN_POLL_SECONDS", 0.01)
    yield
    stop.set()


def test_start_refuses_chat_for_a_link_without_a_youtube_video(live):
    with pytest.raises(InvalidInputError, match="YouTube"):
        _start(url="https://www.bilibili.com/video/BV1xx", read_chat=True)
    assert live_service._sessions == {}


def test_a_session_without_chat_has_no_chat_page(live):
    sid = _start()
    assert live_service.get_session(sid)["chat"] is False
    from lib.errors import NotFoundError
    with pytest.raises(NotFoundError):
        live_service.get_chat(sid)


def test_a_session_reads_and_translates_its_chat(live, chat_pages):
    sid = _start(url=f"https://www.youtube.com/watch?v={VID}", read_chat=True)
    assert live_service.get_session(sid)["chat"] is True
    assert _wait(lambda: any(m["state"] != "pending" for m in live_service.get_chat(sid)["messages"]))
    page = live_service.get_chat(sid)
    assert page["messages"][0]["text"] == "今天好开心"
    live_service.stop_session(sid)
    live_service.stop_session(sid)
    assert _terminal(sid)


def test_the_chat_thread_carries_the_members_cloud_key(monkeypatch):
    from engine_backends import local
    seen = []
    monkeypatch.setattr(local, "member_cloud_key", lambda: "k1")
    monkeypatch.setattr(local, "carry_member_cloud_key", lambda fn, key: (seen.append(key), fn)[1])
    worker = live_chat.ChatWorker("live_x", VID, live_chat.ChatBoard(), FakeTranslator(), "zh")
    monkeypatch.setattr(worker, "_run", lambda: None)
    worker.start()
    worker.stop()
    assert seen == ["k1"]


def test_chat_uses_its_own_engine(live, chat_pages):
    sid = _start(url=f"https://www.youtube.com/watch?v={VID}", read_chat=True)
    assert _wait(lambda: live_service.get_chat(sid)["messages"])
    live_service.stop_session(sid)
    live_service.stop_session(sid)
    assert _terminal(sid)
