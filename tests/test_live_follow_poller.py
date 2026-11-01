"""The followed-channel poller (services/live_follow_poller.py) with a fake
yt-dlp check: no network, no child process, no Whisper, no keys."""
import json
import socket
import threading
import time

import pytest

import background_jobs
from jobs import job_launch
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


def _follow(principal=ALICE, handle="@name", auto_check=True, mode="notify", **kw):
    """The toggle and mode are set in the row, so adding one starts no thread
    here; the scheduler tests start it themselves."""
    fid = svc.add_follow(handle, principal=principal, **kw)["id"]
    live_follow_store.update(fid, auto_check=int(auto_check), mode=mode)
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
    ({"error": "ERROR: somename is offline"}, "not_live", poller.NOT_LIVE),
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


def test_only_channels_with_the_hourly_check_are_checked_automatically(env):
    a = _follow(handle="@aaa")
    b = _follow(handle="@bbb", auto_check=False)
    order = []
    env["answers"][a] = lambda: order.append(a) or NOT_LIVE
    env["answers"][b] = lambda: order.append(b) or NOT_LIVE
    assert poller.run_due_once(now=NOW) == 1
    assert order == [a]
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
    monkeypatch.setattr(job_launch, "is_stopping", lambda: True)
    assert poller.check_one(fid, now=NOW) == "stopping"
    assert poller.run_due_once(now=NOW) == 0
    assert live_follow_store.get(fid)["last_checked_at"] is None


def test_no_check_while_a_restore_holds_the_library(env):
    fid = _follow()
    assert job_launch.acquire_exclusive("restore")
    try:
        assert poller.check_one(fid, now=NOW) == "held"
    finally:
        job_launch.release_exclusive()
    assert live_follow_store.get(fid)["last_checked_at"] is None
    assert poller.check_one(fid, now=NOW) == "checked"


def test_a_running_check_holds_off_a_restore(env):
    fid = _follow()
    held = []
    env["answers"][fid] = lambda: held.append(job_launch.acquire_exclusive("restore")) \
        or NOT_LIVE
    _check(fid)
    assert held == [False]
    assert not job_launch.maintenance_active()


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
    fid = _follow(OWNER, "@owner_ch", mode="auto", auto_check=True,
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
    ("tool", "not started: Live capture needs ffmpeg, which isn't installed or isn't on PATH on this PC"),
    ("engine_down", "not started: Ollama isn't running on this PC"),
    ("blank", "not started: Live could not start"),
])
def test_auto_start_refused_when_a_gate_fails(env, starts, monkeypatch, gate, note):
    from services import line_ai_service
    from lib.errors import (ConflictError, MissingKeyError,
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
        from engine_backends import engine_registry
        monkeypatch.delitem(engine_registry.ENGINES, "fake")
    elif gate == "default_engine":
        live_follow_store.update(fid, options=json.dumps({"max_minutes": 7}))
        monkeypatch.setattr(live_service, "LIVE_DEFAULT_ENGINE", "gone")
    else:
        from lib.errors import DependencyUnavailableError
        exc = (ConflictError("x") if gate == "conflict" else MissingKeyError("fake") if gate == "key"
               else DependencyUnavailableError("Ollama isn't running on this PC.") if gate == "engine_down"
               else DependencyUnavailableError("") if gate == "blank"
               else DependencyUnavailableError(live_service.FFMPEG_MISSING))

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
    assert poller.start_if_needed(tick=0.05) is True
    assert poller.start_if_needed(tick=0.05) is False
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
    assert poller.start_if_needed(tick=0.05)
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
    assert not job_launch.maintenance_active() and not poller._poll_lock.locked()


def test_an_upcoming_error_without_details_is_not_a_failure(env):
    fid = _follow()
    env["answers"][fid] = {"error": "This live event will begin in a few moments."}
    row = _check(fid)
    assert (row["last_state"], row["poll_minutes"]) == ("upcoming", svc.DEFAULT_POLL_MINUTES)
    assert env["sent"] == []


# --- manual checks and the toggle ------------------------------------------------

def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_no_scheduler_without_an_hourly_toggle(env):
    _follow(auto_check=False)
    assert poller.start_if_needed(tick=0.05) is False
    assert poller._thread is None
    assert all(t.name != "live-follow-poller" for t in threading.enumerate())


def test_the_scheduler_ends_when_the_last_toggle_goes_off(env):
    fid = _follow()
    assert poller.start_if_needed(tick=0.05) is True
    thread = poller._thread
    svc.update_follow(fid, auto_check=False, principal=ALICE)
    poller._wake.set()
    assert _wait(lambda: not thread.is_alive())
    assert poller._thread is None


def test_a_manual_check_shows_the_result_and_notifies_no_one(env, starts):
    fid = _follow(OWNER, "@owner_ch", mode="auto", options={"engine": "fake"})
    env["answers"][fid] = live()
    assert poller.check_one(fid, now=NOW, manual=True) == "checked"
    row = live_follow_store.get(fid)
    assert (row["last_state"], row["stream_title"]) == ("live", "t")
    assert env["sent"] == [] and starts == []
    assert row["poll_minutes"] == svc.DEFAULT_POLL_MINUTES and fid not in poller._due
    # The stream counts as seen: the automatic check does not announce it again
    # (it may still start Live, which is a separate opt-in).
    _check(fid, NOW + 3600)
    assert env["sent"] == [] and len(starts) == 1


def test_a_manual_error_leaves_the_automatic_schedule_alone(env):
    fid = _follow()
    env["answers"][fid] = SIGN_IN
    poller.check_one(fid, now=NOW, manual=True)
    row = live_follow_store.get(fid)
    assert row["last_state"] == "error" and row["poll_minutes"] == svc.DEFAULT_POLL_MINUTES


def test_queued_manual_checks_run_one_at_a_time_then_the_worker_exits(env, monkeypatch):
    monkeypatch.setattr(poller, "MANUAL_GAP_SECONDS", 0.05)
    ids = [_follow(handle=f"@ch{i:03}", auto_check=False) for i in range(3)]
    running, peak, order = [0], [0], []

    def answer(fid):
        def run():
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            order.append(fid)
            time.sleep(0.05)
            running[0] -= 1
            return NOT_LIVE
        return run
    for fid in ids:
        env["answers"][fid] = answer(fid)
    for fid in ids + [ids[0]]:
        assert poller.request_check(fid)
    worker = poller._worker
    assert _wait(lambda: not worker.is_alive())
    assert order == ids and peak[0] == 1
    assert poller._requested == [] and poller._worker is None
    assert env["sent"] == []


def test_a_manual_check_waits_for_a_running_one(env, monkeypatch):
    monkeypatch.setattr(poller, "BUSY_RETRY_SECONDS", 0.05)
    a, b = _follow(handle="@aaa"), _follow(handle="@bbb", auto_check=False)
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
        poller.request_check(b)
        time.sleep(0.2)
        assert live_follow_store.get(b)["last_checked_at"] is None
        assert poller.is_checking(b)
    finally:
        release.set()
        t.join(5)
    assert _wait(lambda: live_follow_store.get(b)["last_checked_at"] is not None)


def test_no_manual_check_during_the_clean_stop(env, monkeypatch):
    fid = _follow(auto_check=False)
    monkeypatch.setattr(job_launch, "is_stopping", lambda: True)
    assert poller.request_check(fid) is False
    assert poller._worker is None


# --- review fixes ---------------------------------------------------------------

def test_a_restored_row_with_a_foreign_url_is_never_probed_or_shown(env):
    fid = _follow(OWNER, "@owner_ch", mode="auto")
    live_follow_store.update(fid, pc_owner=1)
    from sources import store
    with store.connect() as conn:
        conn.execute("UPDATE followed_channels SET channel_url = ? WHERE id = ?",
                     ("http://169.254.169.254/latest/meta-data", fid))
    probed = []
    env["answers"][fid] = lambda: probed.append(fid) or live()
    assert fid not in [r["id"] for r in poller.due_follows(NOW)]
    assert poller.check_one(fid, now=NOW) == "invalid"
    assert probed == []
    item = svc.list_follows(OWNER)["items"][0]
    assert "169.254" not in json.dumps(item) and "meta-data" not in json.dumps(item)
    assert item["state"] == "error" and "remove" in item["last_status"].lower()
    from lib.errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        svc.check_now(fid, principal=OWNER)
    with pytest.raises(InvalidInputError):
        svc.start_from_follow(fid, principal=OWNER)


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_start_time_is_no_start_time(env, bad):
    from services import ytdlp_child
    assert ytdlp_child.live_fields({"live_status": "is_upcoming", "id": "x",
                                    "release_timestamp": bad})["release_timestamp"] is None
    fid = _follow()
    env["answers"][fid] = upcoming(bad)
    row = _check(fid)
    assert row["last_state"] == "upcoming" and row["starts_at"] is None
    assert env["sent"][0][1]["starts_at"] is None
    json.dumps(svc.list_follows(ALICE), allow_nan=False)


def test_a_demoted_admin_follow_loses_the_owner_powers(env, starts, isolated_db, monkeypatch):
    from services import auth_service, settings_service
    u = auth_service.add_user("pc@example.com")
    import db
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    admin = {"user_id": u["id"], "is_admin": True, "is_local_owner": False, "admin_override": True}
    fid = _follow(admin, "@admin_ch", mode="auto", options={"engine": "fake"})
    assert live_follow_store.get(fid)["pc_owner"] == 1
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (u["id"],))
    cookies = []
    monkeypatch.setattr(settings_service, "get_cookie_settings",
                        lambda: cookies.append(1) or {"cookies_browser": "firefox"})
    env["answers"][fid] = live()
    row = _check(fid)
    assert starts == [] and row["mode"] == "notify" and row["pc_owner"] == 0
    assert env["sent"][0][1]["push"] is False


def test_classify_names_the_site_in_fixed_words():
    c = poller.classify(SIGN_IN, "Twitch")
    assert c["status"] == "Twitch asked for a sign-in check."


def test_a_live_twitch_row_notifies_once_per_broadcast(env):
    fid = _follow(handle="https://www.twitch.tv/somename")
    env["answers"][fid] = live("315904859302")
    row = _check(fid)
    assert row["last_state"] == "live" and row["last_stream_id"] == "315904859302"
    assert len(env["sent"]) == 1
    _check(fid, NOW + 3600)
    assert len(env["sent"]) == 1
    env["answers"][fid] = live("315904999999")
    _check(fid, NOW + 7200)
    assert len(env["sent"]) == 2
