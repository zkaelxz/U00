"""
services/live_follow_poller.py -- checks followed channels
(services/live_follow_service.py) for live and scheduled streams.

A check is yt-dlp metadata only, in the killable child
(services/ytdlp_child.run_live_probe) behind the guarded egress proxy, with
a hard time limit and an output cap. One check runs at a time, server-wide;
none starts during the clean stop or while a library restore holds the
library (a check counts as maintenance, so a restore waits for it instead
of swapping sources.db under it).

Checks are manual by default (owner decision): "Check now" and "Check all"
queue channels for a worker thread that exists only while the queue has
something in it and checks them one after another, MANUAL_GAP_SECONDS apart.
A manual check shows its result in the row and notifies no one (the person
is looking at it).

"Check automatically every hour" is a per-channel toggle, off by default.
The scheduler thread runs only while at least one channel has it on (it is
started by api/server.py at startup and when a toggle is turned on, and ends
its loop when none is left). Such a channel is checked about every
DEFAULT_POLL_MINUTES (jittered, never sooner than MIN_POLL_MINUTES apart,
except the one extra check before a scheduled start within the hour). A
failed automatic check doubles the interval up to MAX_BACKOFF_MINUTES; any
answer resets it. An automatic check notifies once per stream: the stream id
is written to the row before the notification goes out, so a restart never
notifies the same stream again. Only an automatic check may start Live on
its own (the PC owner's opt-in).
"""

import math
import random
import re
import shutil
import threading
import time

import background_jobs
from jobs import job_launch
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
# Between two queued manual checks ("Check all"), so a batch is not a burst.
MANUAL_GAP_SECONDS = 3.0
# How long the manual worker waits before retrying a check another one, or
# a restore, kept from running.
BUSY_RETRY_SECONDS = 2.0
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
_TITLE_MAX = 150

_poll_lock = threading.Lock()
_state_lock = threading.Lock()
_due = {}           # follow id -> time of its next automatic check
_extra = {}         # follow id -> time of the one extra check before a scheduled start
_extra_used = {}    # follow id -> the stream id that extra check was for
_requested = []     # follow ids queued for a manual check, oldest first
_current = []       # the follow id being checked right now, if any
_manual_checks = {} # channel URL -> time of its last manual check (the cooldown, server-wide)
_wake = threading.Event()
_stop = threading.Event()
_thread = None      # the automatic-check scheduler, while a channel has the toggle on
_worker = None      # the manual-check worker, while its queue has something in it


def _log(fmt, *args):
    try:
        import applog
        from translate_engines import redact_secrets
        applog.get_logger().warning(fmt, *(redact_secrets(str(a)) for a in args))
    except Exception:
        pass


def _spawn(target, name) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True, name=name)
    thread.start()
    return thread


def _halted(stop=None) -> bool:
    return job_launch.is_stopping() or _stop.is_set() or (stop is not None and stop.is_set())


# --- manual checks -----------------------------------------------------------

def request_check(follow_id, channel_url=None, now=None) -> bool:
    """Queues one manual check; a channel already queued or being checked is
    not queued twice. False during the clean stop. `channel_url` starts the
    channel's cooldown (last_manual_check)."""
    global _worker
    if _halted():
        return False
    with _state_lock:
        if channel_url:
            _manual_checks[channel_url] = time.time() if now is None else now
        if follow_id not in _requested:
            _requested.append(follow_id)
        if _worker is None or not _worker.is_alive():
            _worker = _spawn(_work_queue, "live-follow-checks")
    return True


def last_manual_check(channel_url) -> float:
    with _state_lock:
        return _manual_checks.get(channel_url, 0.0)


def is_checking(follow_id) -> bool:
    with _state_lock:
        return follow_id in _requested or follow_id in _current


def _work_queue() -> None:
    global _worker
    first = True
    while not _halted():
        with _state_lock:
            if not _requested:
                _worker = None
                return
            follow_id = _requested[0]
        if not first and _stop.wait(MANUAL_GAP_SECONDS):
            break
        first = False
        try:
            outcome = check_one(follow_id, manual=True)
        except Exception as exc:
            _log("manual followed-channel check failed: %s", type(exc).__name__)
            outcome = "checked"
        if outcome in ("busy", "held"):
            _stop.wait(BUSY_RETRY_SECONDS)
            continue
        with _state_lock:
            if follow_id in _requested:
                _requested.remove(follow_id)
    with _state_lock:
        _requested.clear()
        _worker = None


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
        _current.clear()
        _manual_checks.clear()
    follows.reset_for_tests()
    _stop.clear()
    _wake.clear()


# --- automatic checks --------------------------------------------------------

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
    """Channels with the automatic check on whose regular or extra check is due."""
    now = time.time() if now is None else now
    rows = {r["id"]: r for r in follow_store.list_all()
            if r.get("auto_check") and follows.valid_url(r)}
    with _state_lock:
        extra = [i for i, t in _extra.items() if t <= now and i in rows]
    regular = sorted((i for i, r in rows.items() if _due_at(r, now) <= now),
                     key=lambda i: _due[i])
    order = list(dict.fromkeys(extra + regular))
    return [rows[i] for i in order]


def run_due_once(now=None, stop=None) -> int:
    """Checks every due channel, one after another. Returns how many checks ran."""
    ran = 0
    for row in due_follows(now):
        if _halted(stop):
            break
        if check_one(row["id"], now=now) in ("checked", "gone"):
            ran += 1
    return ran


def check_one(follow_id, now=None, manual=False) -> str:
    """"checked", or why not: "stopping", "busy" (another check is running),
    "held" (a restore holds the library), "gone", "invalid" (a stored URL
    that is not the server-built form, e.g. from a restored backup)."""
    if _halted():
        return "stopping"
    if not _poll_lock.acquire(blocking=False):
        return "busy"
    try:
        if not job_launch.enter_maintenance():
            return "held"
        try:
            row = follow_store.get(follow_id)
            if row is None:
                forget(follow_id)
                return "gone"
            if not follows.valid_url(row):
                forget(follow_id)
                return "invalid"
            if row.get("pc_owner") and not follows.owner_still_pc_owner(row):
                row = follows.drop_owner_powers(row)
            with _state_lock:
                _current[:] = [follow_id]
            try:
                result = probe(row)
            except Exception as exc:
                # Counted as a failed check (backoff), not retried every tick.
                _log("followed-channel check could not run: %s", type(exc).__name__)
                result = {"error": ""}
            if result.get("cancelled"):
                return "stopping"
            _apply(row, result, time.time() if now is None else now, manual=manual)
        finally:
            with _state_lock:
                _current.clear()
            job_launch.exit_maintenance()
    finally:
        _poll_lock.release()
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
    # Not inf either: Python's JSON reads "Infinity", and it breaks the list and bell.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def classify(result) -> dict:
    """{state, status, stream_id, channel, starts_at}. state is live,
    upcoming, not_live or error; status is fixed plain words."""
    out = {"state": "error", "status": FAILED, "stream_id": "", "channel": "", "title": "",
           "starts_at": None}
    live = result.get("live") if isinstance(result, dict) else None
    if isinstance(live, dict):
        status = live.get("status")
        stream_id = live.get("id") if isinstance(live.get("id"), str) else ""
        channel = live.get("channel") if isinstance(live.get("channel"), str) else ""
        title = live.get("title") if isinstance(live.get("title"), str) else ""
        out.update(stream_id=stream_id if _STREAM_ID.fullmatch(stream_id) else "",
                   channel=channel, title=title)
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


def _apply(row, result, now, manual=False) -> None:
    """Stores the answer. A manual check only shows it: it leaves the
    automatic schedule alone, notifies no one and starts nothing, but it
    does mark the stream as seen, so a later automatic check does not
    announce what the person already looked at."""
    c = classify(result)
    fields = {"last_checked_at": now, "last_state": c["state"], "last_status": c["status"],
              "starts_at": c["starts_at"],
              "stream_title": _tidy(c["title"], _TITLE_MAX) if c["state"] in ("live", "upcoming")
              else ""}
    if not manual:
        fields["poll_minutes"] = follows.DEFAULT_POLL_MINUTES if c["state"] != "error" \
            else min(int(row["poll_minutes"]) * 2, follows.MAX_BACKOFF_MINUTES)
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
    if manual:
        return
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
    from services import line_ai_service, live_service, paid_job_service, settings_service
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
        paid_job_service.require_cap_for_unattended(engine, bool(settings_service.get("gemini_free_tier")))
    except ServiceError:
        return "not started: set a monthly spending cap in Settings first"
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
    if row.get("mode") != "auto" or not row.get("pc_owner") or not row.get("auto_check") \
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
            glossary_drama_id=opts["glossary_drama_id"],
            series_id=opts["series_id"], principal=None)
    except ConflictError:
        return "not started: another Live session is running"
    except MissingKeyError:
        return "not started: no key is saved for that engine"
    except DependencyUnavailableError as exc:
        # A missing engine, ffmpeg or yt-dlp, or a host that did not resolve
        # each say which; the service text is already redacted and URL-free.
        # Kept as written: it starts with a name (Ollama, DeepSeek) as often as not.
        reason = (exc.message or "").strip().split("\n", 1)[0].rstrip(".")
        return f"not started: {reason}" if reason else "not started: Live could not start"
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


# --- the threads --------------------------------------------------------------

def start_if_needed(tick: float = TICK_SECONDS) -> bool:
    """Starts the automatic-check scheduler when some channel has the toggle
    on and it is not running; True if this call started it."""
    global _thread
    if _halted():
        return False
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return False
        if not follow_store.any_auto_check():
            return False

        def loop():
            global _thread
            while not _halted():
                with _state_lock:
                    if not follow_store.any_auto_check():
                        _thread = None
                        return
                try:
                    run_due_once()
                except Exception as exc:
                    _log("followed-channel check failed: %s", type(exc).__name__)
                _wake.wait(tick)
                _wake.clear()

        _thread = _spawn(loop, "live-follow-poller")
        return True


def stop(timeout: float = 5.0) -> None:
    """For good in this process: no new check, and a running one's child is
    killed (its cancel is the same stop flag)."""
    global _thread, _worker
    _stop.set()
    _wake.set()
    with _state_lock:
        threads, _thread, _worker = (_thread, _worker), None, None
    for thread in threads:
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
