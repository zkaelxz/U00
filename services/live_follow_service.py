"""
services/live_follow_service.py -- the Live page's "Followed channels": a
user follows a YouTube channel and is told when it goes live; the PC owner
may also let a stream start Live on its own. The checks themselves run in
services/live_follow_poller.py; rows live in sources.db
(sources/live_follow_store.py). No FastAPI import.

Who sees what: a follow is owned like a job that is not tied to a title
(`ownership_service.can_see_job`): its owner, and the local owner and
admins, see it; changing it needs the admin override (an admin away from
the PC changes only their own). Another user's follow is a 404.

Checks are manual unless the channel's "check automatically every hour"
toggle (`auto_check`, off by default) is on; see live_follow_poller.py.

"PC owner" here is the rule queue_service uses for the queue's controls:
auth off, the local owner, or an admin with the admin override (at the PC).
Only they may turn on "Start Live automatically", and only on their own
follows: it spends the owner's GPU and paid key. It needs the automatic
check on, since only an automatic check can notice the stream. Not local_only(): with
auth on, the owner's rows carry their user id, which local_only()'s
anonymous principal would lose.

Channel input is reduced to one server-built URL,
https://www.youtube.com/<@handle | channel/UC... | c/name>/live; nothing
the caller typed is fetched as given. Responses carry the short channel
label (for example "@name"), never that URL.
"""

import json
import re
import threading
import time
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

import translate_engines
from core import SOURCE_LANGUAGES
from lib import url_guard
from services import live_service, ownership_service
from services.service_errors import (ConflictError, DependencyUnavailableError, ForbiddenError,
                                     InvalidInputError, NotFoundError, RateLimitedError)
from sources import live_follow_store as follow_store

MAX_PER_MEMBER = 5
MAX_TOTAL = 20
DEFAULT_POLL_MINUTES = 60
MIN_POLL_MINUTES = 30
MAX_BACKOFF_MINUTES = 6 * 60
MODES = ("notify", "auto")
# Hosts a channel link may name. A code constant so another site (Twitch)
# can be added with its own URL forms later.
CHANNEL_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
CANONICAL_BASE = "https://www.youtube.com/"
MAX_INPUT_LEN = 300
MAX_NAME_LEN = 100
MAX_STATUS_LEN = 120
MAX_OPTIONS_BYTES = 1000
# A manual "Check now" this soon after the last check is refused, so the
# button can't be used to hammer YouTube.
MANUAL_CHECK_GAP_SECONDS = 60
# Follows a member may add in an hour, so remove-and-re-add can't be used to
# churn checks against YouTube.
MAX_ADDS_PER_HOUR = 10
_HANDLE = re.compile(r"[\w.\-]{3,30}")
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_CUSTOM = re.compile(r"[\w.\-]{1,100}")
_STREAM_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_NOT_FOUND = "No such followed channel."
_BAD_CHANNEL = ("Paste a YouTube channel link (https://www.youtube.com/@name, /channel/UC... "
                "or /c/name) or an @handle.")
# Count and insert under one hold, so two adds at once can't pass a limit together.
_add_lock = threading.Lock()
_adds = {}          # owner user id -> times of their recent adds (memory only)
INVALID_STATUS = "This channel's address is not valid any more. Remove it and follow it again."
_OPTION_DEFAULTS = {"source_language": "zh", "whisper_size": "small", "engine": None,
                    "model": None, "max_minutes": live_service.DEFAULT_MAX_MINUTES,
                    "use_gpu": False, "glossary_drama_id": None}


def is_pc_owner(principal) -> bool:
    return principal is None or bool(principal.get("is_local_owner")) \
        or bool(principal.get("is_admin") and principal.get("admin_override"))


def _can_see(principal, row, writing=False) -> bool:
    return ownership_service.can_see_job(principal, "", row.get("owner_user_id"),
                                         writing=writing)


def _require(principal, follow_id, writing=False) -> dict:
    try:
        follow_id = int(follow_id)
    except (TypeError, ValueError):
        raise NotFoundError(_NOT_FOUND) from None
    row = follow_store.get(follow_id) if 0 < follow_id < 2 ** 63 else None
    if row is None or not _can_see(principal, row):
        raise NotFoundError(_NOT_FOUND)
    if writing and not _can_see(principal, row, writing=True):
        raise ForbiddenError("An admin can change other people's followed channels only at the PC.")
    return row


# --- channel input ----------------------------------------------------------

def _name_ok(pattern, name: str) -> bool:
    # A name of dots and dashes alone ("..") would read as a path step.
    return bool(pattern.fullmatch(name)) and any(c.isalnum() for c in name)


def _form(path: str) -> Optional[str]:
    """`@handle`, `channel/UC...` or `c/name` from a channel page path, else None."""
    parts = [unquote(p) for p in path.split("/") if p]
    if not parts:
        return None
    if parts[0].startswith("@") and _name_ok(_HANDLE, parts[0][1:]):
        return parts[0]
    if len(parts) >= 2 and parts[0] == "channel" and _CHANNEL_ID.fullmatch(parts[1]):
        return f"channel/{parts[1]}"
    if len(parts) >= 2 and parts[0] == "c" and _name_ok(_CUSTOM, parts[1]):
        return f"c/{parts[1]}"
    return None


def normalize_channel(text) -> tuple:
    """(canonical live URL, label) for a channel link or a bare @handle.
    InvalidInputError for anything else: another host, userinfo, a port,
    a scheme other than http(s), or a path that is not a channel page."""
    if not isinstance(text, str) or not text.strip():
        raise InvalidInputError("Paste a channel link or an @handle.")
    text = text.strip()
    if len(text) > MAX_INPUT_LEN:
        raise InvalidInputError("That channel link is too long.")
    if text.startswith("@"):
        form = text if _name_ok(_HANDLE, text[1:]) else None
    else:
        if "://" not in text and text.lower().split("/", 1)[0] in CHANNEL_HOSTS:
            text = "https://" + text
        try:
            parts = urlsplit(text)
            port = parts.port
            host = (parts.hostname or "").lower()
        except ValueError:
            raise InvalidInputError(_BAD_CHANNEL) from None
        if (parts.scheme.lower() not in ("http", "https") or host not in CHANNEL_HOSTS
                or port is not None or parts.username is not None or parts.password is not None
                or "@" in parts.netloc or "\\" in text):
            raise InvalidInputError(_BAD_CHANNEL)
        form = _form(parts.path)
    if form is None:
        raise InvalidInputError(_BAD_CHANNEL)
    return f"{CANONICAL_BASE}{quote(form, safe='@/')}/live", form


def reset_for_tests() -> None:
    with _add_lock:
        _adds.clear()


def valid_url(row) -> bool:
    """Whether the stored URL is still exactly the server-built form. A row
    can come from a restored backup, so it is re-checked on every use and a
    row that fails is never fetched or shown."""
    url = row.get("channel_url")
    try:
        return isinstance(url, str) and normalize_channel(url)[0] == url
    except InvalidInputError:
        return False


def _require_valid(row) -> None:
    if not valid_url(row):
        raise InvalidInputError(INVALID_STATUS)


def _label(channel_url: str) -> str:
    return unquote(channel_url[len(CANONICAL_BASE):].rsplit("/live", 1)[0])


def owner_still_pc_owner(row) -> bool:
    """For a row made by the PC owner: is its owner still an active admin
    (auth off and the local owner have no user id and always are)?"""
    owner = row.get("owner_user_id")
    if owner is None:
        return True
    import db
    user = db.auth_get_user(owner) or {}
    return bool(user.get("is_admin") and user.get("is_active"))


def drop_owner_powers(row) -> dict:
    """The row's owner is not the PC owner any more: no automatic start, no
    owner cookies and no Discord/ntfy push for it from now on."""
    follow_store.update(row["id"], mode="notify", pc_owner=0)
    return dict(row, mode="notify", pc_owner=0)


def _require_public(url: str) -> None:
    try:
        url_guard.resolve_public(url)
    except url_guard.URLResolveError:
        raise DependencyUnavailableError(
            "YouTube could not be reached to check that link. Try again later.") from None
    except url_guard.UnsafeURLError:
        raise InvalidInputError(_BAD_CHANNEL) from None


# --- options -----------------------------------------------------------------

def clean_options(options, principal=None) -> dict:
    """The Live options saved with a follow, bounded like a Live start: the
    same choices start_session accepts, numbers clamped to its ranges."""
    if options is None:
        options = {}
    if not isinstance(options, dict) or set(options) - set(_OPTION_DEFAULTS):
        raise InvalidInputError("Unknown Live option.")
    out = dict(_OPTION_DEFAULTS, **{k: v for k, v in options.items() if v is not None})
    if out["source_language"] not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"source_language must be one of {', '.join(SOURCE_LANGUAGES)}.")
    if out["whisper_size"] not in live_service.WHISPER_SIZES:
        raise InvalidInputError(
            f"whisper_size must be one of {', '.join(live_service.WHISPER_SIZES)}.")
    if out["engine"] is not None and out["engine"] not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(out["engine"]))
    if out["model"] is not None and (not isinstance(out["model"], str) or len(out["model"]) > 100):
        raise InvalidInputError("model must be a short string.")
    out["max_minutes"] = int(live_service._num("max_minutes", out["max_minutes"],
                                               *live_service.MAX_MINUTES_RANGE))
    if not isinstance(out["use_gpu"], bool):
        raise InvalidInputError("use_gpu must be true or false.")
    title = out["glossary_drama_id"]
    if title is not None:
        if isinstance(title, bool) or not isinstance(title, int):
            raise InvalidInputError("glossary_drama_id must be a title id.")
        ownership_service.require_visible(principal, "drama", title)
    return out


def _options(row, strict=False) -> dict:
    """The saved options, re-checked. `strict` (anything that starts Live):
    an option no longer valid (an engine removed since) is an
    InvalidInputError, never a silent switch to the defaults."""
    try:
        stored = json.loads(row.get("options") or "{}")
        try:
            return clean_options(stored, None)
        except NotFoundError:       # the glossary title was deleted since
            return clean_options(dict(stored, glossary_drama_id=None), None)
    except Exception:
        if strict:
            raise InvalidInputError("A saved Live option for this channel is no longer valid. "
                                    "Change the channel's options and try again.") from None
        return dict(_OPTION_DEFAULTS)


# --- views -------------------------------------------------------------------

def view(row, principal=None) -> dict:
    """What a client may see: no URL, path or raw error text."""
    if not valid_url(row):
        row = dict(row, last_state="error", last_status=INVALID_STATUS, stream_title="",
                   starts_at=None, channel_name="")
    return {"id": row["id"],
            "channel": _label(row["channel_url"]) if valid_url(row) else "invalid",
            "channel_name": row.get("channel_name") or "",
            "mode": row["mode"] if row["mode"] in MODES else "notify",
            "auto_check": bool(row.get("auto_check")),
            "options": _options(row), "poll_minutes": int(row["poll_minutes"]),
            "last_checked_at": row.get("last_checked_at"),
            "state": row.get("last_state") or "", "last_status": row.get("last_status") or "",
            "starts_at": row.get("starts_at"), "live_now": row.get("last_state") == "live",
            "stream_title": row.get("stream_title") or "",
            "checking": _poller().is_checking(row["id"]),
            "mine": ownership_service.user_id(principal) == row.get("owner_user_id"),
            "created_at": row["created_at"]}


def list_follows(principal=None) -> dict:
    rows = [r for r in follow_store.list_all() if _can_see(principal, r)]
    return {"items": [view(r, principal) for r in rows], "can_auto_start": is_pc_owner(principal),
            "max_per_member": MAX_PER_MEMBER, "max_total": MAX_TOTAL}


# --- changes -----------------------------------------------------------------

def _poller():
    from services import live_follow_poller
    return live_follow_poller


def _check_mode(mode, principal, owner_user_id, auto_check) -> str:
    if mode not in MODES:
        raise InvalidInputError("mode must be notify or auto.")
    if mode == "auto" and not (is_pc_owner(principal)
                               and ownership_service.user_id(principal) == owner_user_id):
        raise ForbiddenError("Only the PC owner can let their own channels start Live "
                             "automatically. Choose Notify me instead.")
    if mode == "auto" and not auto_check:
        raise InvalidInputError("Start Live automatically needs the hourly check on: only an "
                                "automatic check can notice the stream.")
    return mode


def _dump(options: dict) -> str:
    text = json.dumps(options, sort_keys=True)
    if len(text.encode("utf-8")) > MAX_OPTIONS_BYTES:
        raise InvalidInputError("The Live options are too long.")
    return text


def add_follow(channel, mode="notify", options=None, auto_check=False, principal=None) -> dict:
    url, label = normalize_channel(channel)
    owner = ownership_service.user_id(principal)
    auto_check = bool(auto_check)
    mode = _check_mode(mode, principal, owner, auto_check)
    opts = _dump(clean_options(options, principal))
    _require_public(url)
    now = time.time()
    with _add_lock:
        recent = [t for t in _adds.get(owner, ()) if now - t < 3600]
        if not is_pc_owner(principal) and len(recent) >= MAX_ADDS_PER_HOUR:
            raise RateLimitedError("Too many channels added in the last hour. Try again later.")
        if follow_store.find(owner, url) is not None:
            raise ConflictError("You already follow that channel.")
        if not is_pc_owner(principal) and follow_store.count(owner) >= MAX_PER_MEMBER:
            raise ConflictError(
                f"You can follow at most {MAX_PER_MEMBER} channels. Remove one first.")
        if follow_store.count(any_owner=True) >= MAX_TOTAL:
            raise ConflictError(f"This PC already follows {MAX_TOTAL} channels, the most it "
                                "checks. Remove one first.")
        follow_id = follow_store.add(owner, is_pc_owner(principal), url, label[:MAX_NAME_LEN],
                                     mode, opts, DEFAULT_POLL_MINUTES, auto_check=auto_check)
        _adds[owner] = recent + [now]
    if auto_check:
        _poller().start_if_needed()
    return view(follow_store.get(follow_id), principal)


def update_follow(follow_id, mode=None, options=None, auto_check=None, principal=None) -> dict:
    """Turning the automatic check off also turns an automatic start back
    into Notify me, which can't work without it. An automatic start's
    options are the PC owner's to change; its own owner acting away from the
    PC loses the owner powers on it."""
    row = _require(principal, follow_id, writing=True)
    fields = {}
    if row.get("mode") == "auto" and not is_pc_owner(principal):
        if options is not None:
            raise ForbiddenError("Only the PC owner can change the options of a channel that "
                                 "starts Live automatically.")
        if ownership_service.user_id(principal) == row.get("owner_user_id"):
            row = drop_owner_powers(row)
    checking = bool(row.get("auto_check")) if auto_check is None else bool(auto_check)
    if auto_check is not None:
        fields["auto_check"] = int(checking)
        if not checking and mode is None and row.get("mode") == "auto":
            fields["mode"] = "notify"
    if mode is not None:
        fields["mode"] = _check_mode(mode, principal, row.get("owner_user_id"), checking)
        if mode == "auto":
            fields["pc_owner"] = 1
    if options is not None:
        fields["options"] = _dump(clean_options(options, principal))
    follow_store.update(row["id"], **fields)
    if checking:
        _poller().start_if_needed()
    else:
        _poller().forget(row["id"])
    return view(follow_store.get(row["id"]), principal)


def remove_follow(follow_id, principal=None) -> dict:
    row = _require(principal, follow_id, writing=True)
    follow_store.delete(row["id"])
    _poller().forget(row["id"])
    return {"id": row["id"], "removed": True}


def _cooling(row, now) -> bool:
    """Keyed by the channel too, server-wide, so removing and re-adding a
    follow, or another member following the same channel, doesn't reset it."""
    last = max(row.get("last_checked_at") or 0, _poller().last_manual_check(row["channel_url"]))
    return bool(last) and now - last < MANUAL_CHECK_GAP_SECONDS


def check_now(follow_id, principal=None, now=None) -> dict:
    """Queues one manual check of this channel (one check runs at a time,
    server-wide). A channel already queued or being checked is left as it
    is; one checked less than a minute ago is refused."""
    row = _require(principal, follow_id, writing=True)
    _require_valid(row)
    now = time.time() if now is None else now
    if _poller().is_checking(row["id"]):
        return {"id": row["id"], "queued": True}
    if _cooling(row, now):
        raise RateLimitedError("This channel was checked less than a minute ago.")
    if not _poller().request_check(row["id"], channel_url=row["channel_url"], now=now):
        raise ConflictError("Baihe is stopping, so no check can start.")
    return {"id": row["id"], "queued": True}


def check_all(principal=None, now=None) -> dict:
    """Queues a manual check of every channel the caller may change (a
    member: their own; the PC owner: all), checked one after another.
    Channels checked in the last minute or already queued are skipped."""
    now = time.time() if now is None else now
    queued = skipped = 0
    for row in follow_store.list_all():
        if not _can_see(principal, row, writing=True):
            continue
        if not valid_url(row) or _poller().is_checking(row["id"]) or _cooling(row, now) \
                or not _poller().request_check(row["id"], channel_url=row["channel_url"], now=now):
            skipped += 1
        else:
            queued += 1
    return {"queued": queued, "skipped": skipped}


# --- starting Live -----------------------------------------------------------

def stream_url(row) -> str:
    """The stream's own watch page when the last check saw one, else the
    channel's /live page; both built here from validated parts."""
    _require_valid(row)
    stream_id = row.get("last_stream_id") or ""
    if row.get("last_state") == "live" and _STREAM_ID.fullmatch(stream_id):
        return f"{CANONICAL_BASE}watch?v={stream_id}"
    return row["channel_url"]


def follow_engine(follow_id, principal=None) -> tuple:
    """(engine, model) a Start Live from this follow would use, for the
    router's engine permission checks."""
    opts = _options(_require(principal, follow_id, writing=True), strict=True)
    return opts["engine"], opts["model"]


def start_from_follow(follow_id, principal=None, use_saved_cookies=False) -> dict:
    """The notification's "Start Live": a normal Live start (same checks,
    one session at a time) with the follow's saved options."""
    row = _require(principal, follow_id, writing=True)
    opts = _options(row, strict=True)
    return live_service.start_session(
        stream_url(row), source_language=opts["source_language"],
        whisper_size=opts["whisper_size"], engine=opts["engine"], model=opts["model"],
        max_minutes=opts["max_minutes"], use_gpu=opts["use_gpu"],
        use_saved_cookies=use_saved_cookies, glossary_drama_id=opts["glossary_drama_id"],
        principal=principal)
