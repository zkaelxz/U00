"""
services/live_follow_poller.py -- checks followed channels
(services/live_follow_service.py) for live and scheduled streams.

One daemon thread, started with the API's other schedulers (api/server.py)
and stopped by the clean stop (services/shutdown_service.py). Each channel
is checked about every DEFAULT_POLL_MINUTES (jittered, never sooner than
MIN_POLL_MINUTES apart, except the one extra check below). A check is yt-dlp
metadata only, in the killable child (services/ytdlp_child.run_live_probe)
behind the guarded egress proxy, with a hard time limit and an output cap.
One check runs at a time, server-wide; none starts during the clean stop or
while a library restore holds the library (a check counts as maintenance,
so a restore waits for it instead of swapping sources.db under it).

A failed check (network, sign-in check, private, members-only) is stored as
fixed plain words and doubles the channel's interval up to
MAX_BACKOFF_MINUTES; any answer resets it. A stream scheduled to start
within the hour gets one extra check a minute after its start time, so the
first minutes are not missed by an hourly cadence.

Each stream is notified once: its id is written to the row before the
notification goes out, so a restart never notifies the same stream again.
"""

import random
import re
import shutil
import threading
import time

import background_jobs
import storage
from services import live_follow_service as follows
from services import notification_service
from sources import live_follow_store as follow_store

PROBE_TIMEOUT_SECONDS = 45
PROBE_OUTPUT_CHARS = 200_000
TICK_SECONDS = 60
JITTER = 0.1
UPCOMING_WINDOW_SECONDS = 3600
EXTRA_CHECK_DELAY_SECONDS = 60
# Spreads overdue channels after a restart, so they are not all checked at once.
STARTUP_SPREAD_SECONDS = 600

LIVE = "Live now"
UPCOMING = "Scheduled"
NOT_LIVE = "Not live"
# Fixed wording only: a yt-dlp message can carry the URL or site text.
# (pattern, state, words); state "not_live"/"upcoming" are answers, not failures.
_ERRORS = (
    (r"not currently live|is not live|no live stream|does not have a live|not streaming",
     "not_live", NOT_LIVE),
    # A scheduled stream yt-dlp still refused: no id or start time to notify with.
    (r"will begin|premieres? in|is scheduled|upcoming live", "upcoming", UPCOMING),
    # Before the sign-in pattern: YouTube's members-only, age and private
    # messages also say "sign in".
    (r"members[- ]only|join this channel|membership", "error", "Members-only stream."),
    (r"confirm your age|age[- ]restricted|inappropriate", "error", "Age-restricted."),
    (r"private", "error", "The channel or stream is private."),
    (r"sign in to confirm|not a bot|sign in|log in|cookies", "error",
     "YouTube asked for a sign-in check."),
    (r"does not exist|not found|404|unavailable|terminated|removed", "error", "Channel not found."),
    (r"timed? ?out|resolve|connection|network|unreachable|proxy|ssl|errno", "error",
     "Could not reach YouTube."),
)
_ERROR_RES = tuple((re.compile(p, re.I), state, text) for p, state, text in _ERRORS)
TIMED_OUT = "The check took too long."
NO_YTDLP = "yt-dlp is not installed."
FAILED = "The check failed."
_STREAM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

_poll_lock = threading.Lock()
_state_lock = threading.Lock()
_due = {}           # follow id -> time of its next regular check
_extra = {}         # follow id -> time of the one extra check before a scheduled start
_extra_used = {}    # follow id -> the stream id that extra check was for
_requested = []     # follow ids asked for (Check now, a new follow), oldest first
_wake = threading.Event()
_stop = threading.Event()
_thread = None


def _log(fmt, *args):
    try:
        import applog
        from translate_engines import redact_secrets
        applog.get_logger().warning(fmt, *(redact_secrets(str(a)) for a in args))
    except Exception:
        pass


def request_check(follow_id) -> None:
    with _state_lock:
        if follow_id not in _requested:
            _requested.append(follow_id)
    _wake.set()


def forget(follow_id) -> None:
    with _state_lock:
        for table in (_due, _extra, _extra_used):
            table.pop(follow_id, None)
        if follow_id in _requested:
            _requested.remove(follow_id)


def reset_for_tests() -> None:
    with _state_lock:
        _due.clear()
        _extra.clear()
        _extra_used.clear()
        _requested.clear()
    _stop.clear()
    _wake.clear()


def _interval_seconds(minutes) -> float:
    base = max(follows.MIN_POLL_MINUTES, int(minutes or follows.DEFAULT_POLL_MINUTES)) * 60
    return max(follows.MIN_POLL_MINUTES * 60, base * random.uniform(1 - JITTER, 1 + JITTER))


def _due_at(row, now) -> float:
    with _state_lock:
        if row["id"] in _due:
            return _due[row["id"]]
        last = row.get("last_checked_at")
        due = now if last is None else last + _interval_seconds(row["poll_minutes"])
        if last is not None and due < now:
            due = now + random.uniform(0, STARTUP_SPREAD_SECONDS)
        _due[row["id"]] = due
        return due


def due_follows(now=None) -> list:
    """Rows to check now: asked-for ones first, then those whose regular or
    extra check is due."""
    now = time.time() if now is None else now
    rows = {r["id"]: r for r in follow_store.list_all()}
    with _state_lock:
        asked = [i for i in _requested if i in rows]
        extra = [i for i, t in _extra.items() if t <= now and i in rows]
    regular = sorted((i for i, r in rows.items() if _due_at(r, now) <= now),
                     key=lambda i: _due[i])
    order = list(dict.fromkeys(asked + extra + regular))
    return [rows[i] for i in order]


def _halted(stop=None) -> bool:
    return background_jobs.is_stopping() or _stop.is_set() or (stop is not None and stop.is_set())


def run_due_once(now=None, stop=None) -> int:
    """Checks every due channel, one after another. Returns how many checks ran."""
    ran = 0
    for row in due_follows(now):
        if _halted(stop):
            break
        if check_one(row["id"], now=now) in ("checked", "gone"):
            ran += 1
    return ran


def check_one(follow_id, now=None) -> str:
    """"checked", or why not: "stopping", "busy" (another check is running),
    "held" (a restore holds the library), "gone"."""
    if _halted():
        return "stopping"
    if not _poll_lock.acquire(blocking=False):
        return "busy"
    try:
        if not background_jobs.enter_maintenance():
            return "held"
        try:
            row = follow_store.get(follow_id)
            if row is None:
                forget(follow_id)
                return "gone"
            try:
                result = probe(row)
            except Exception as exc:
                # Counted as a failed check (backoff), not retried every tick.
                _log("followed-channel check could not run: %s", type(exc).__name__)
                result = {"error": ""}
            if result.get("cancelled"):
                return "stopping"
            _apply(row, result, time.time() if now is None else now)
        finally:
            background_jobs.exit_maintenance()
    finally:
        _poll_lock.release()
    with _state_lock:
        if follow_id in _requested:
            _requested.remove(follow_id)
    return "checked"


# --- the check itself ------------------------------------------------------

def probe(row) -> dict:
    """One yt-dlp metadata read of the channel's live page: {"live": {...}},
    {"error": text}, {"missing": True}, {"timed_out": True} or
    {"cancelled": True}. Saved cookies only for the PC owner's own follows,
    so a member's follow never reads YouTube as the owner's account."""
    from services import egress_proxy, settings_service, ytdlp_child
    spec = {"url": row["channel_url"]}
    if row.get("pc_owner"):
        spec.update(settings_service.get_cookie_settings())
    tmp = storage.new_workdir("live_follow")
    result, end = {}, {}
    try:
        with storage.holding(tmp), egress_proxy.GuardedProxy() as proxy:
            spec["proxy"] = proxy.url
            events = ytdlp_child.run_live_probe(tmp, spec, PROBE_TIMEOUT_SECONDS, _stop.is_set,
                                                PROBE_OUTPUT_CHARS)
            try:
                for item in events:
                    if "event" in item:
                        result.update(item["event"])
                    else:
                        end = item
            finally:
                events.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if end.get("cancelled"):
        return {"cancelled": True}
    if end.get("timed_out"):
        return {"timed_out": True}
    if end.get("too_much_output"):
        return {"error": ""}
    return result


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def classify(result) -> dict:
    """{state, status, stream_id, channel, starts_at}. state is live,
    upcoming, not_live or error; status is fixed plain words."""
    out = {"state": "error", "status": FAILED, "stream_id": "", "channel": "",
           "starts_at": None}
    live = result.get("live") if isinstance(result, dict) else None
    if isinstance(live, dict):
        status = live.get("status")
        stream_id = live.get("id") if isinstance(live.get("id"), str) else ""
        channel = live.get("channel") if isinstance(live.get("channel"), str) else ""
        out.update(stream_id=stream_id if _STREAM_ID.fullmatch(stream_id) else "",
                   channel=channel)
        if status == "is_live":
            out.update(state="live", status=LIVE)
        elif status == "is_upcoming":
            out.update(state="upcoming", status=UPCOMING,
                       starts_at=_number(live.get("release_timestamp")))
        else:
            out.update(state="not_live", status=NOT_LIVE, stream_id="")
        return out
    if result.get("timed_out"):
        return dict(out, status=TIMED_OUT)
    if result.get("missing"):
        return dict(out, status=NO_YTDLP)
    text = str(result.get("error") or "")
    for rx, state, words in _ERROR_RES:
        if rx.search(text):
            return dict(out, state=state, status=words)
    return out


def _tidy(text, limit) -> str:
    text = " ".join("".join(c if c.isprintable() else " " for c in str(text or "")).split())
    return text[:limit]


def _apply(row, result, now) -> None:
    c = classify(result)
    fields = {"last_checked_at": now, "last_state": c["state"], "last_status": c["status"],
              "starts_at": c["starts_at"],
              "poll_minutes": follows.DEFAULT_POLL_MINUTES if c["state"] != "error"
              else min(int(row["poll_minutes"]) * 2, follows.MAX_BACKOFF_MINUTES)}
    name = _tidy(c["channel"], follows.MAX_NAME_LEN)
    if name:
        fields["channel_name"] = name
    stream_id, notify = c["stream_id"], None
    if c["state"] == "live" and stream_id and not (
            row.get("last_stream_id") == stream_id and row.get("notified_state") == "live"):
        notify = "live"
    elif c["state"] == "upcoming" and stream_id and row.get("last_stream_id") != stream_id:
        notify = "upcoming"
    if notify:
        fields.update(last_stream_id=stream_id, notified_state=notify)
    follow_store.update(row["id"], **fields)
    row = dict(row, **fields)
    _schedule(row, c, now)
    note = None
    if c["state"] == "live" and stream_id:
        note = _auto_start(row, stream_id)
        if note:
            follow_store.update(row["id"], last_status=f"{LIVE} · {note}"[:follows.MAX_STATUS_LEN])
    if notify:
        _notify(row, notify, c, note, now)


def _schedule(row, c, now) -> None:
    with _state_lock:
        _due[row["id"]] = now + _interval_seconds(row["poll_minutes"])
        _extra.pop(row["id"], None)
        starts_at, stream_id = c["starts_at"], c["stream_id"]
        if (c["state"] == "upcoming" and starts_at is not None and stream_id
                and 0 < starts_at - now <= UPCOMING_WINDOW_SECONDS
                and _extra_used.get(row["id"]) != stream_id):
            _extra[row["id"]] = starts_at + EXTRA_CHECK_DELAY_SECONDS
            _extra_used[row["id"]] = stream_id


# --- what a check leads to ---------------------------------------------------

def _gate_refusal(engine) -> str:
    """Why an automatic start may not happen now, else ""."""
    from services import line_ai_service, live_service, settings_service
    from services.service_errors import ServiceError
    if any(s["status"] in ("queued", "running") for s in live_service.list_sessions(None)):
        return "not started: another Live session is running"
    if any(j.get("gpu_touching") and j.get("status") in ("queued", "running")
           for j in background_jobs.list_all_jobs().values()):
        return "not started: a GPU job is running"
    import translate_engines
    if engine not in translate_engines.ENGINES:
        return "not started: that engine is not available"
    try:
        line_ai_service.refuse_if_over_monthly_cap(engine, settings_service.get("gemini_free_tier"))
    except ServiceError:
        return "not started: this month's spending cap is used up"
    return ""


def _auto_start(row, stream_id):
    """Starts Live for the PC owner's own auto-start follow, once per
    stream id, as a manual start would. Returns a short note, or None when
    this follow does not auto-start."""
    from services import live_service
    from services.service_errors import (ConflictError, DependencyUnavailableError,
                                         InvalidInputError, MissingKeyError, ServiceError)
    if row.get("mode") != "auto" or not row.get("pc_owner") \
            or row.get("started_stream_id") == stream_id:
        return None
    try:
        opts = follows._options(row, strict=True)
    except InvalidInputError:
        return "not started: a saved Live option is no longer valid"
    refusal = _gate_refusal(opts["engine"] or live_service.LIVE_DEFAULT_ENGINE)
    if refusal:
        return refusal
    try:
        live_service.start_session(
            follows.stream_url(row), source_language=opts["source_language"],
            whisper_size=opts["whisper_size"], engine=opts["engine"], model=opts["model"],
            max_minutes=opts["max_minutes"], use_gpu=opts["use_gpu"], use_saved_cookies=True,
            glossary_drama_id=opts["glossary_drama_id"], principal=None)
    except ConflictError:
        return "not started: another Live session is running"
    except MissingKeyError:
        return "not started: no key is saved for that engine"
    except DependencyUnavailableError:
        return "not started: the engine is not available"
    except InvalidInputError:
        return "not started: a saved Live option is no longer valid"
    except ServiceError:
        return "not started: Live could not start"
    except Exception as exc:
        _log("automatic Live start failed: %s", type(exc).__name__)
        return "not started: Live could not start"
    follow_store.update(row["id"], started_stream_id=stream_id)
    return "started Live automatically"


def _notify(row, kind, c, note, now) -> None:
    name = row.get("channel_name") or follows._label(row["channel_url"])
    push = bool(row.get("pc_owner"))
    if kind == "live":
        started = note == "started Live automatically"
        text = f"{name} is live now" + (f" ({note})" if note else "")
        notification_service.notify_live_follow(text, row.get("owner_user_id"), row["id"],
                                                start_action=not started, push=push)
    else:
        starts_at = c["starts_at"]
        text = f"{name} scheduled a stream"
        push_text = text
        if starts_at is not None and starts_at > now:
            push_text += f", starting in about {max(1, round((starts_at - now) / 60))} min"
        notification_service.notify_live_follow(text, row.get("owner_user_id"), row["id"],
                                                starts_at=starts_at, push=push,
                                                push_text=push_text)


# --- the thread ----------------------------------------------------------------

def start(tick: float = TICK_SECONDS) -> bool:
    """Starts the poller thread once; False if one is already running."""
    global _thread
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()

        def loop():
            while not _stop.is_set():
                try:
                    run_due_once()
                except Exception as exc:
                    _log("followed-channel check failed: %s", type(exc).__name__)
                _wake.wait(tick)
                _wake.clear()

        _thread = threading.Thread(target=loop, daemon=True, name="live-follow-poller")
        _thread.start()
        return True


def stop(timeout: float = 5.0) -> None:
    """For good in this process: no new check, and a running one's child is
    killed (its cancel is the same stop flag)."""
    global _thread
    _stop.set()
    _wake.set()
    with _state_lock:
        thread, _thread = _thread, None
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)
