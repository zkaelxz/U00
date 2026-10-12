"""The followed-channel poller (services/live_follow_poller.py) with a fake
yt-dlp check: no network, no child process, no Whisper, no keys."""
import json
import socket
import threading
import time

import pytest

import background_jobs
from services import (live_follow_poller as poller, live_follow_service as svc, live_service,
                      notification_service)
from sources import live_follow_store

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443))]
OWNER = {"user_id": None, "is_admin": True, "is_local_owner": True, "admin_override": True}
ALICE = {"user_id": 11, "is_admin": False, "is_local_owner": False}
NOW = 1_800_000_000.0
SECRET = "sk-ant-" + "b" * 40


def live(stream_id="abcdefghijk", channel="Some Streamer"):
    return {"live": {"status": "is_live", "id": stream_id, "title": "t", "channel": channel,
                     "release_timestamp": None}}


def upcoming(starts_at, stream_id="upcomingid1"):
    return {"live": {"status": "is_upcoming", "id": stream_id, "title": "t",
                     "channel": "Some Streamer", "release_timestamp": starts_at}}


NOT_LIVE = {"live": {"status": "not_live", "id": "", "title": "", "channel": "Some Streamer",
                     "release_timestamp": None}}
NOT_LIVE_ERROR = {"error": "ERROR: [youtube:tab] @x: The channel is not currently live"}
SIGN_IN = {"error": f"ERROR: [youtube] abc: Sign in to confirm you're not a bot. "
                    f"https://www.youtube.com/watch?v=abc /home/me/cookies.txt {SECRET}"}


@pytest.fixture
def env(isolated_db, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    poller.reset_for_tests()
    sent = []
    monkeypatch.setattr(notification_service, "notify_live_follow",
                        lambda *a, **k: sent.append((a, k)))
    answers = {}

    def fake_probe(row):
        answer = answers.get(row["id"], NOT_LIVE)
        if isinstance(answer, BaseException):
            raise answer
        return answer() if callable(answer) else answer
    monkeypatch.setattr(poller, "probe", fake_probe)
    yield {"answers": answers, "sent": sent}
    poller.stop()
    poller.reset_for_tests()


def _follow(principal=ALICE, handle="@name", **kw):
    fid = svc.add_follow(handle, principal=principal, **kw)["id"]
    poller.reset_for_tests()
    return fid


def _check(fid, now=NOW):
    assert poller.check_one(fid, now=now) == "checked"
    return live_follow_store.get(fid)


@pytest.mark.parametrize("result, state, status", [
    (live(), "live", poller.LIVE),
    (upcoming(NOW + 600), "upcoming", poller.UPCOMING),
    (NOT_LIVE, "not_live", poller.NOT_LIVE),
    (NOT_LIVE_ERROR, "not_live", poller.NOT_LIVE),
    ({"error": "ERROR: [youtube] abc: This live event will begin in 20 minutes."}, "upcoming",
     poller.UPCOMING),
    (SIGN_IN, "error", "YouTube asked for a sign-in check."),
    ({"error": "Private video. Sign in if you've been granted access"}, "error",
     "The channel or stream is private."),
    ({"error": "Join this channel to get access to members-only content"}, "error",
     "Members-only stream."),
    ({"error": "Sign in to confirm your age"}, "error", "Age-restricted."),
    ({"error": "Unable to download webpage: <urlopen error timed out>"}, "error",
     "Could not reach YouTube."),
    ({"timed_out": True}, "error", poller.TIMED_OUT),
    ({"error": "yt-dlp is not installed", "missing": True}, "error", poller.NO_YTDLP),
    ({"error": "something odd"}, "error", poller.FAILED),
    ({}, "error", poller.FAILED),
    ({"live": {"status": "is_live", "id": "../x?y", "channel": 5}}, "live", poller.LIVE),
])
def test_classify(result, state, status):
    c = poller.classify(result)
    assert (c["state"], c["status"]) == (state, status)
    assert set(c["stream_id"]) <= set("abcdefghijklmnopqrstuvwxyz0123456789_-ABCDEFGHIJ")


def test_no_url_path_or_key_in_any_stored_status_or_response(env):
    fid = _follow()
    env["answers"][fid] = SIGN_IN
    row = _check(fid)
    stored = json.dumps(row)
    for bad in ("watch?v=", "/home/me", SECRET, "cookies.txt"):
        assert bad not in stored
    view = json.dumps(svc.list_follows(ALICE))
    for bad in ("http", "youtube.com", "/home/me", SECRET):
        assert bad not in view


def test_live_notifies_once_per_stream_even_across_a_restart(env):
    fid = _follow()
    env["answers"][fid] = live()
    row = _check(fid)
    assert row["last_state"] == "live" and row["last_stream_id"] == "abcdefghijk"
    assert row["channel_name"] == "Some Streamer"
    assert len(env["sent"]) == 1
    args, kw = env["sent"][0]
    assert args[0] == "Some Streamer is live now" and args[1] == 11 and args[2] == fid
    assert kw["start_action"] is True and kw["push"] is False
    _check(fid, NOW + 3600)
    assert len(env["sent"]) == 1
    # A restart: the in-memory state is gone, the row is not.
    poller.reset_for_tests()
    _check(fid, NOW + 7200)
    assert len(env["sent"]) == 1
    env["answers"][fid] = live("newstreamid")
    _check(fid, NOW + 10800)
    assert len(env["sent"]) == 2


def test_upcoming_notifies_once_with_its_start_time_then_again_when_live(env):
    fid = _follow()
    env["answers"][fid] = upcoming(NOW + 1800)
    _check(fid)
    _check(fid, NOW + 60)
    assert len(env["sent"]) == 1
    args, kw = env["sent"][0]
    assert args[0] == "Some Streamer scheduled a stream" and kw["starts_at"] == NOW + 1800
    assert "30 min" in kw["push_text"]
    assert live_follow_store.get(fid)["starts_at"] == NOW + 1800
    env["answers"][fid] = live("upcomingid1")
    _check(fid, NOW + 1900)
    assert len(env["sent"]) == 2 and env["sent"][1][0][0].endswith("is live now")


def test_an_upcoming_stream_within_the_hour_gets_one_extra_check(env):
    fid = _follow()
    env["answers"][fid] = upcoming(NOW + 1200)
    _check(fid)
    assert poller._extra[fid] == NOW + 1200 + poller.EXTRA_CHECK_DELAY_SECONDS
    assert poller._due[fid] >= NOW + svc.MIN_POLL_MINUTES * 60
    assert [r["id"] for r in poller.due_follows(NOW + 1000)] == []
    assert [r["id"] for r in poller.due_follows(NOW + 1300)] == [fid]
    # Still upcoming at the extra check (a late start): no second extra one.
    _check(fid, NOW + 1300)
    assert fid not in poller._extra
    # The extra timer is rebuilt from each check, so a restart re-arms it once.
    poller.reset_for_tests()
    env["answers"][fid] = upcoming(NOW + 7200 + 1800, "otherstream")
    _check(fid, NOW + 7200)
    assert fid in poller._extra


def test_no_extra_check_for_a_stream_more_than_an_hour_away(env):
    fid = _follow()
    env["answers"][fid] = upcoming(NOW + 5400)
    _check(fid)
    assert fid not in poller._extra


def test_backoff_doubles_up_to_six_hours_and_resets(env):
    fid = _follow()
    env["answers"][fid] = SIGN_IN
    seen = []
    for i in range(5):
        seen.append(_check(fid, NOW + i * 100_000)["poll_minutes"])
    assert seen == [120, 240, 360, 360, 360]
    assert poller._due[fid] >= NOW + 400_000 + 360 * 60 * (1 - poller.JITTER)
    env["answers"][fid] = NOT_LIVE
    assert _check(fid, NOW + 900_000)["poll_minutes"] == svc.DEFAULT_POLL_MINUTES


def test_checks_are_hourly_and_jittered_never_under_thirty_minutes(env, monkeypatch):
    fid = _follow()
    for factor in (0.0, 1.0):
        monkeypatch.setattr(poller.random, "uniform", lambda a, b, f=factor: a + (b - a) * f)
        _check(fid)
        gap = poller._due[fid] - NOW
        assert svc.MIN_POLL_MINUTES * 60 <= gap
        assert 54 * 60 <= gap <= 66 * 60
    live_follow_store.update(fid, poll_minutes=1)
    _check(fid)
    assert poller._due[fid] - NOW >= svc.MIN_POLL_MINUTES * 60


def test_a_new_follow_is_due_at_once_and_overdue_ones_spread_after_a_restart(env):
    fid = _follow()
    assert [r["id"] for r in poller.due_follows(NOW)] == [fid]
    live_follow_store.update(fid, last_checked_at=NOW - 10 * 3600)
    poller.reset_for_tests()
    poller.due_follows(NOW)
    assert NOW <= poller._due[fid] <= NOW + poller.STARTUP_SPREAD_SECONDS


def test_run_due_once_checks_requested_channels_first(env):
    a, b = _follow(handle="@aaa"), _follow(handle="@bbb")
    order = []
    env["answers"][a] = lambda: order.append(a) or NOT_LIVE
    env["answers"][b] = lambda: order.append(b) or NOT_LIVE
    poller.request_check(b)
    assert poller.run_due_once(now=NOW) == 2
    assert order == [b, a] and poller._requested == []
    assert poller.run_due_once(now=NOW + 60) == 0


def test_one_check_at_a_time(env):
    a, b = _follow(handle="@aaa"), _follow(handle="@bbb")
    inside, release = threading.Event(), threading.Event()

    def slow():
        inside.set()
        release.wait(5)
        return NOT_LIVE
    env["answers"][a] = slow
    t = threading.Thread(target=poller.check_one, args=(a,), kwargs={"now": NOW})
    t.start()
    try:
        assert inside.wait(5)
        assert poller.check_one(b, now=NOW) == "busy"
        assert live_follow_store.get(b)["last_checked_at"] is None
    finally:
        release.set()
        t.join(5)
    assert poller.check_one(b, now=NOW) == "checked"


def test_no_check_during_the_clean_stop(env, monkeypatch):
    fid = _follow()
    monkeypatch.setattr(background_jobs, "is_stopping", lambda: True)
    assert poller.check_one(fid, now=NOW) == "stopping"
    assert poller.run_due_once(now=NOW) == 0
    assert live_follow_store.get(fid)["last_checked_at"] is None


def test_no_check_while_a_restore_holds_the_library(env):
    fid = _follow()
    assert background_jobs.acquire_exclusive("restore")
    try:
        assert poller.check_one(fid, now=NOW) == "held"
    finally:
        background_jobs.release_exclusive()
    assert live_follow_store.get(fid)["last_checked_at"] is None
    assert poller.check_one(fid, now=NOW) == "checked"


def test_a_running_check_holds_off_a_restore(env):
    fid = _follow()
    held = []
    env["answers"][fid] = lambda: held.append(background_jobs.acquire_exclusive("restore")) \
        or NOT_LIVE
    _check(fid)
    assert held == [False]
    assert not background_jobs.maintenance_active()


def test_a_deleted_follow_is_skipped(env):
    fid = _follow()
    live_follow_store.delete(fid)
    assert poller.check_one(fid, now=NOW) == "gone"


# --- push and auto-start -------------------------------------------------------

def test_push_only_for_the_pc_owners_own_follow(env):
    mine = _follow(OWNER, "@owner_ch")
    theirs = _follow(ALICE, "@alice_ch")
    env["answers"][mine] = live("aaaaaaaaaaa")
    env["answers"][theirs] = live("bbbbbbbbbbb")
    _check(mine)
    _check(theirs)
    pushes = {a[2]: k["push"] for a, k in env["sent"]}
    assert pushes == {mine: True, theirs: False}


@pytest.fixture
def starts(monkeypatch):
    calls = []
    monkeypatch.setattr(live_service, "start_session",
                        lambda url, **kw: calls.append(dict(kw, url=url)) or {"session_id": "s"})
    monkeypatch.setattr(live_service, "list_sessions", lambda principal=None: [])
    return calls


def _auto(env):
    fid = _follow(OWNER, "@owner_ch", mode="auto",
                  options={"engine": "fake", "max_minutes": 7, "whisper_size": "tiny"})
    env["answers"][fid] = live()
    return fid


def test_auto_start_starts_once_and_never_restarts_the_same_stream(env, starts):
    fid = _auto(env)
    row = _check(fid)
    assert len(starts) == 1
    call = starts[0]
    assert call["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert call["max_minutes"] == 7 and call["engine"] == "fake" and call["principal"] is None
    assert call["whisper_size"] == "tiny" and call["use_saved_cookies"] is True
    assert row["started_stream_id"] == "abcdefghijk"
    assert row["last_status"] == "Live now · started Live automatically"
    assert env["sent"][0][1]["start_action"] is False
    # The session ended (or was stopped); the same stream is still live.
    _check(fid, NOW + 3600)
    poller.reset_for_tests()
    _check(fid, NOW + 7200)
    assert len(starts) == 1
    env["answers"][fid] = live("nextstream1")
    _check(fid, NOW + 10800)
    assert len(starts) == 2


def test_auto_start_refused_for_a_member_follow(env, starts):
    fid = _follow(ALICE)
    # Even a row forced to auto (it can't be set through the service) stays notify-only.
    live_follow_store.update(fid, mode="auto")
    env["answers"][fid] = live()
    _check(fid)
    assert starts == [] and env["sent"][0][1]["start_action"] is True


@pytest.mark.parametrize("gate, note", [
    ("session", "not started: another Live session is running"),
    ("gpu", "not started: a GPU job is running"),
    ("cap", "not started: this month's spending cap is used up"),
    ("engine", "not started: a saved Live option is no longer valid"),
    ("default_engine", "not started: that engine is not available"),
    ("conflict", "not started: another Live session is running"),
    ("key", "not started: no key is saved for that engine"),
])
def test_auto_start_refused_when_a_gate_fails(env, starts, monkeypatch, gate, note):
    from services import line_ai_service
    from services.service_errors import (ConflictError, MissingKeyError,
                                         UnsupportedOperationError)
    fid = _auto(env)
    if gate == "session":
        monkeypatch.setattr(live_service, "list_sessions",
                            lambda principal=None: [{"status": "running"}])
    elif gate == "gpu":
        monkeypatch.setattr(background_jobs, "list_all_jobs",
                            lambda: {"t": {"status": "running", "gpu_touching": True}})
    elif gate == "cap":
        def over(engine, free_tier):
            raise UnsupportedOperationError("cap")
        monkeypatch.setattr(line_ai_service, "refuse_if_over_monthly_cap", over)
    elif gate == "engine":
        import translate_engines
        monkeypatch.delitem(translate_engines.ENGINES, "fake")
    elif gate == "default_engine":
        live_follow_store.update(fid, options=json.dumps({"max_minutes": 7}))
        monkeypatch.setattr(live_service, "LIVE_DEFAULT_ENGINE", "gone")
    else:
        exc = ConflictError("x") if gate == "conflict" else MissingKeyError("fake")

        def refuse(url, **kw):
            raise exc
        monkeypatch.setattr(live_service, "start_session", refuse)
    row = _check(fid)
    assert row["last_status"] == f"Live now · {note}"
    assert row["started_stream_id"] == ""
    assert starts == []
    args, kw = env["sent"][0]
    assert note in args[0] and kw["start_action"] is True
    # Tried again on the next check while the stream is still on.
    monkeypatch.undo()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    monkeypatch.setattr(poller, "probe", lambda row: live())
    monkeypatch.setattr(notification_service, "notify_live_follow", lambda *a, **k: None)
    monkeypatch.setattr(live_service, "list_sessions", lambda principal=None: [])
    monkeypatch.setattr(live_service, "start_session",
                        lambda url, **kw: starts.append(url) or {"session_id": "s"})
    assert _check(fid, NOW + 3600)["started_stream_id"] == "abcdefghijk"
    assert len(starts) == 1


def test_auto_start_uses_the_monthly_cap_helper(env, starts, monkeypatch):
    from services import line_ai_service
    seen = []
    monkeypatch.setattr(line_ai_service, "refuse_if_over_monthly_cap",
                        lambda engine, free_tier: seen.append(engine))
    _check(_auto(env))
    assert seen == ["fake"] and len(starts) == 1


# --- the thread -----------------------------------------------------------------

def test_scheduler_thread_checks_and_stops_cleanly(env):
    fid = _follow()
    assert poller.start(tick=0.05) is True
    assert poller.start(tick=0.05) is False
    end = time.time() + 5
    while time.time() < end and live_follow_store.get(fid)["last_checked_at"] is None:
        time.sleep(0.02)
    assert live_follow_store.get(fid)["last_checked_at"] is not None
    thread = poller._thread
    poller.stop(timeout=5)
    assert not thread.is_alive()


def test_scheduler_survives_a_failing_check(env, monkeypatch):
    fid = _follow()
    env["answers"][fid] = RuntimeError("boom")
    assert poller.start(tick=0.05)
    time.sleep(0.2)
    thread = poller._thread
    assert thread.is_alive()
    poller.stop(timeout=5)
    assert not thread.is_alive()



def test_push_goes_out_only_when_asked_and_with_its_own_text(isolated_db, monkeypatch):
    notification_service.reset_for_tests()
    monkeypatch.delenv(notification_service.DISABLED_ENV, raising=False)
    monkeypatch.setattr(notification_service, "configured_channels", lambda: ["ntfy"])
    monkeypatch.setattr(notification_service, "_schedule_flush", lambda: None)
    notification_service.notify_live_follow("Member ch is live now", 11, 1, start_action=True)
    assert notification_service._pending == []
    notification_service.notify_live_follow("Mine scheduled a stream", None, 2, starts_at=NOW,
                                            push=True, push_text="Mine scheduled a stream, "
                                            "starting in about 5 min")
    assert notification_service._pending == [
        ("live", "Mine scheduled a stream, starting in about 5 min", "live")]
    notification_service.reset_for_tests()


def test_a_check_that_cannot_run_counts_as_a_failure(env):
    fid = _follow()
    env["answers"][fid] = OSError("proxy did not start")
    row = _check(fid)
    assert (row["last_state"], row["last_status"], row["poll_minutes"]) == ("error", poller.FAILED, 120)
    assert not background_jobs.maintenance_active() and not poller._poll_lock.locked()


def test_an_upcoming_error_without_details_is_not_a_failure(env):
    fid = _follow()
    env["answers"][fid] = {"error": "This live event will begin in a few moments."}
    row = _check(fid)
    assert (row["last_state"], row["poll_minutes"]) == ("upcoming", svc.DEFAULT_POLL_MINUTES)
    assert env["sent"] == []
