"""
services/notification_service.py -- Step 44: push a short "job finished /
job failed" message to a Discord webhook and/or an ntfy topic.

UI-free. `background_jobs._notify_job_finished` calls
`notify_job_finished(description, status)` right after the desktop toast;
it never raises and never blocks the job: events are queued and sent from a
daemon timer thread.

Secrets: the webhook URL and the ntfy topic URL are stored like engine keys
(`.env`, `BAIHE_DISCORD_WEBHOOK_URL` / `BAIHE_NTFY_TOPIC_URL`, written
through settings_service). Nothing here returns, logs or puts either one in
an error message; callers only ever see `configured` booleans and per-channel
outcome words ("sent", "failed", "refused", "not_configured").

Message content: the job kind (the job description up to its first "("),
the drama title (looked up from a "(drama #N)" reference) and the outcome.
The push leaves the title out for a drama a household member couldn't see
(private, or in a private series): the channel may be shared.
Never line text, keys or paths; the result also goes through
`translate_engines.redact_secrets`.

Rate limit: events arriving within BURST_WINDOW seconds of the first one
are collapsed into one message ("3 background jobs ended: 2 finished,
1 failed."), and at most MAX_PER_MINUTE messages (tests included) go out
in any 60 s; extra bursts are dropped with a log line.

SSRF: Discord URLs must be https to discord.com / discordapp.com (or the
ptb./canary. hosts) under /api/webhooks/<id>/<token>. ntfy URLs must be
https://<host>/<topic>, and the host must resolve to public addresses only
(`url_guard.resolve_public`). A self-hosted ntfy on this PC or the LAN
(loopback or RFC 1918 / IPv6 ULA addresses, http or https; never
link-local, so no cloud metadata endpoint) is allowed only when
`BAIHE_NTFY_ALLOW_LOCAL=1` is set in `.env` or the environment by hand --
there is deliberately no API route that turns it on. Even then, a target
on this PC (loopback) may not use one of Baihe's own ports (Streamlit 8501,
the API 8600 or the configured BAIHE_API_PORT, the extension bridge 8756).
Every send connects to the address that was validated (no second DNS
lookup), with no redirects, no proxy, a per-socket timeout and an overall
SEND_DEADLINE; the reply body is never read (only the status code).

In-app list (roadmap Step 44 item 5): every job that finishes or fails is
also kept in a short in-memory list (RECENT_KEEP events, at most RECENT_MAX
shown to one viewer, lost on a restart), whether or not a channel is configured, for the header bell
(`GET /api/notifications`). `list_recent(principal)` shows a job event only
to someone who can see that job (`ownership_service.can_see_job`); new-
chapter events are household-wide, like the Sources notifications list.

Categories: "jobs" (a job finished or failed), "chapters" (a tracked-
series check found new chapters) and "remote" (remote access broke or
recovered, `services/remote_health_service.py`) can each be switched off for
Discord and ntfy (`db.app_settings["notify_categories"]`, all on by default);
the in-app list always gets every one. A remote-access entry is shown only to
the PC owner and admins, like `record_event`. The chapter check itself is a background job
that runs on a schedule, so it never sends "Finished: ..."; it sends one
"N new chapters found" when a check finds any (a failed check is still a
failed job).

`BAIHE_NOTIFY_DISABLED=1` turns automatic sends off (tests/conftest.py sets
it so the suite never posts to a real webhook configured on the machine).
"""
import collections
import ipaddress
import os
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from services import url_guard
from services.service_errors import InvalidInputError, RateLimitedError

CHANNELS = ("discord", "ntfy")
CATEGORIES = ("jobs", "chapters", "remote")
CATEGORY_SETTING = "notify_categories"
CHAPTER_CHECK_JOB_ID = "sources_chapter_check"   # sources.chapter_check.CHECK_JOB_ID
RECENT_MAX = 50          # events returned to one viewer
RECENT_KEEP = 500        # events kept in memory for everyone
ENV_VARS = {"discord": ("BAIHE_DISCORD_WEBHOOK_URL",), "ntfy": ("BAIHE_NTFY_TOPIC_URL",)}
ALLOW_LOCAL_NTFY_ENV = "BAIHE_NTFY_ALLOW_LOCAL"
DISABLED_ENV = "BAIHE_NOTIFY_DISABLED"

HTTP_TIMEOUT = (3.05, 5)
SEND_DEADLINE = 10.0          # wall clock for one POST, however slowly the server replies
BURST_WINDOW = 5.0
MAX_PER_MINUTE = 5
MAX_URL_LEN = 512
_KIND_MAX = 60
_TITLE_MAX = 80
APP_NAME = "Baihe Subtitler"

DISCORD_HOSTS = frozenset({"discord.com", "discordapp.com", "ptb.discord.com",
                           "canary.discord.com"})
_DISCORD_PATH = re.compile(r"^/api/(?:v\d{1,2}/)?webhooks/\d{5,25}/[A-Za-z0-9_-]{20,200}/?$")
_NTFY_PATH = re.compile(r"^/[A-Za-z0-9_-]{1,64}$")
_LOCAL_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7"))

_BAD_CHANNEL = "Unknown notification channel."
_BAD_DISCORD = ("That is not a Discord webhook address. It should look like "
                "https://discord.com/api/webhooks/...")
_BAD_NTFY = "That is not an ntfy topic address. It should look like https://ntfy.sh/your-topic"
_NTFY_OWN_PORT = ("That port belongs to Baihe itself. A local ntfy server must use a "
                  "different port.")
_NTFY_LOCAL_OFF = ("A local ntfy server needs BAIHE_NTFY_ALLOW_LOCAL=1 in .env on the Baihe PC; "
                   "otherwise use an https:// address on the internet.")


class _Refused(Exception):
    """The URL failed the SSRF rules at send time (fixed, generic)."""


# --- settings --------------------------------------------------------------

def _settings():
    from services import settings_service
    return settings_service


def _check_channel(channel):
    if channel not in CHANNELS:
        raise InvalidInputError(_BAD_CHANNEL)


def _channel_url(channel):
    """Server-side only: the stored URL for `channel`, or None."""
    return _settings().resolve_env_names(ENV_VARS[channel])


def allow_local_ntfy() -> bool:
    return (_settings().resolve_env_names((ALLOW_LOCAL_NTFY_ENV,)) or "").strip() == "1"


def configured_channels():
    return [c for c in CHANNELS if _channel_url(c)]


def get_categories() -> dict:
    """{"jobs": bool, "chapters": bool, "remote": bool}; each on unless
    switched off."""
    try:
        import db
        stored = db.get_app_setting(CATEGORY_SETTING, None)
    except Exception:
        stored = None
    stored = stored if isinstance(stored, dict) else {}
    return {c: stored.get(c) is not False for c in CATEGORIES}


def set_categories(jobs=None, chapters=None, remote=None) -> dict:
    """Switches the external-push categories. None leaves one unchanged."""
    import db
    current = get_categories()
    for name, value in (("jobs", jobs), ("chapters", chapters), ("remote", remote)):
        if value is not None:
            current[name] = bool(value)
    db.set_app_setting(CATEGORY_SETTING, current)
    return get_status()


def get_status() -> dict:
    """Booleans only: never a URL, host or topic."""
    categories = get_categories()
    return {"discord_configured": bool(_channel_url("discord")),
            "ntfy_configured": bool(_channel_url("ntfy")),
            "ntfy_allow_local": allow_local_ntfy(),
            "send_jobs": categories["jobs"],
            "send_chapters": categories["chapters"],
            "send_remote": categories["remote"]}


def _unmap(ip):
    return ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip


def _is_local_ip(ip) -> bool:
    ip = _unmap(ip)
    return any(ip.version == n.version and ip in n for n in _LOCAL_NETS)


def _effective_port(parts) -> int:
    return parts.port or (443 if parts.scheme == "https" else 80)


def clean_value(value) -> str:
    """Raises with a fixed message (never the value): the text can't carry
    another .env line, quotes or whitespace."""
    if not isinstance(value, str):
        raise InvalidInputError("The address must be text.")
    value = value.strip()
    if not value:
        raise InvalidInputError("The address is empty.")
    if len(value) > MAX_URL_LEN:
        raise InvalidInputError("The address is too long.")
    if any(ord(c) < 33 or ord(c) == 127 or c in "\"'\\" or c.isspace() for c in value):
        raise InvalidInputError("The address contains characters that are not allowed.")
    return value


def validate_url(channel, value, allow_local=None) -> str:
    """Shape check for a channel URL (no DNS). Returns the cleaned value or
    raises InvalidInputError with a fixed message."""
    _check_channel(channel)
    value = clean_value(value)
    bad = _BAD_DISCORD if channel == "discord" else _BAD_NTFY
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise InvalidInputError(bad) from None
    if parts.username or parts.password or "@" in parts.netloc or parts.query or parts.fragment \
            or not host:
        raise InvalidInputError(bad)
    if channel == "discord":
        if (parts.scheme != "https" or host not in DISCORD_HOSTS or port not in (None, 443)
                or not _DISCORD_PATH.match(parts.path)):
            raise InvalidInputError(bad)
        return value
    if parts.scheme not in ("http", "https") or not _NTFY_PATH.match(parts.path):
        raise InvalidInputError(bad)
    if allow_local is None:
        allow_local = allow_local_ntfy()
    literal = None
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    loopback_name = host == "localhost" or host.endswith(".localhost")
    looks_local = (loopback_name or host.endswith(".local")
                   or (literal is not None and not literal.is_global))
    if not allow_local and (parts.scheme != "https" or looks_local):
        raise InvalidInputError(_NTFY_LOCAL_OFF)
    # Loopback / LAN first: IPv6 ::1 sits inside the reserved ::/8 block.
    if literal is not None and not _is_local_ip(literal) and (
            literal.is_multicast or literal.is_reserved or literal.is_unspecified
            or not literal.is_global):
        raise InvalidInputError(bad)   # link-local, reserved, multicast ...
    if ((loopback_name or (literal is not None and _unmap(literal).is_loopback))
            and _effective_port(parts) in _settings().baihe_own_ports()):
        raise InvalidInputError(_NTFY_OWN_PORT)
    return value


def set_channel(channel, value, env_path=None) -> dict:
    """Stores the channel's URL in .env. Returns {channel, configured} only."""
    _check_channel(channel)
    value = validate_url(channel, value)
    s = _settings()
    s.write_env_var(ENV_VARS[channel][0], value, env_path)
    return {"channel": channel, "configured": bool(s.resolve_env_names(ENV_VARS[channel],
                                                                       env_path))}


def clear_channel(channel, env_path=None) -> dict:
    """Removes the channel's URL from .env. One still set as a real
    environment variable stays configured, and `configured` says so."""
    _check_channel(channel)
    s = _settings()
    s.remove_env_vars(ENV_VARS[channel], env_path)
    return {"channel": channel, "configured": bool(s.resolve_env_names(ENV_VARS[channel],
                                                                       env_path))}


# --- message content -------------------------------------------------------

_DRAMA_REF = re.compile(r"\(drama #?(\d+)\)")
_SPACES = re.compile(r"\s+")


def _tidy(text, limit):
    text = _SPACES.sub(" ", "".join(c if c.isprintable() else " " for c in str(text))).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# Anyone signed in with no special rights: sees only shared dramas.
_HOUSEHOLD = {"user_id": None, "is_admin": False, "is_local_owner": False}


def _drama_title(description, shared_only=False):
    """The drama's title, or None. shared_only: None for a drama a
    household member couldn't see (private, or in a private series), since
    a Discord/ntfy channel may be read by the whole household."""
    m = _DRAMA_REF.search(description or "")
    if not m:
        return None
    try:
        import db
        drama = db.get_drama(int(m.group(1)))
        if shared_only and isinstance(drama, dict):
            from services import ownership_service
            if not ownership_service.can_see_drama(_HOUSEHOLD, int(m.group(1))):
                return None
    except Exception:
        return None
    if not isinstance(drama, dict):
        return None
    title = drama.get("title_en") or drama.get("title_zh")
    return _tidy(title, _TITLE_MAX) if title else None


def build_message(description, status, shared_only=False) -> str:
    """"Finished: Translation - <drama title>" / "Failed: ...". Only the
    job kind (description up to its first "("), the drama title and the
    outcome."""
    from translate_engines import redact_secrets
    outcome = "Finished" if status == "done" else "Failed"
    kind = _tidy(re.split(r"\s*\(", description or "", maxsplit=1)[0], _KIND_MAX)
    text = f"{outcome}: {kind or 'Background job'}"
    title = _drama_title(description, shared_only)
    if title:
        text += f" - {title}"
    return redact_secrets(text)


def chapter_message(new_count) -> str:
    n = int(new_count)
    return f"{n} new chapter{'s' if n != 1 else ''} found"


def _summarize(events) -> str:
    """events: (status, message) or (status, message, category) tuples."""
    if len(events) == 1:
        return events[0][1]
    jobs = [e for e in events if (e[2] if len(e) > 2 else "jobs") == "jobs"]
    parts = []
    if len(jobs) == 1:
        parts.append(f"{jobs[0][1]}.")
    elif jobs:
        done = sum(1 for e in jobs if e[0] == "done")
        parts.append(f"{len(jobs)} background jobs ended: {done} finished, "
                     f"{len(jobs) - done} failed.")
    parts.extend(f"{e[1]}." for e in events if e not in jobs)
    return " ".join(parts)


# --- delivery --------------------------------------------------------------

def _resolve_target(channel, url) -> str:
    """The validated address to connect to, or raises _Refused / OSError."""
    parts = urlsplit(url)
    try:
        ip = url_guard.resolve_public(url)
    except url_guard.UnsafeURLError as e:
        if channel == "ntfy" and str(e) == url_guard.NOT_PUBLIC and allow_local_ntfy():
            return _resolve_local(parts)
        raise _Refused() from None
    if parts.scheme != "https":
        raise _Refused()
    return ip


def _resolve_local(parts) -> str:
    port = _effective_port(parts)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (UnicodeError, OSError):
        raise OSError("resolve failed") from None
    first = None
    own_ports = None
    for info in infos:
        raw = info[4][0].split("%")[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise _Refused() from None
        if not _is_local_ip(ip):
            raise _Refused()   # a mix of local and other addresses is refused too
        if _unmap(ip).is_loopback:
            own_ports = _settings().baihe_own_ports() if own_ports is None else own_ports
            if port in own_ports:
                raise _Refused()   # never one of Baihe's own servers on this PC
        first = first or raw
    if first is None:
        raise OSError("resolve failed")
    return first


def _pinned_post(url, ip, body: bytes, headers: dict):
    """POST to `url` connecting to the already-validated `ip` (no second DNS
    lookup), keeping SNI and the certificate check on the real host name.

    Returns the status code only: the reply is streamed and its body is
    never read. HTTP_TIMEOUT bounds each socket wait; SEND_DEADLINE bounds
    the whole call (a server dripping headers one byte at a time is cut off
    by shutting its socket down from a timer)."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

    parts = urlsplit(url)
    host = parts.hostname
    opened = []
    opened_lock = threading.Lock()
    expired = threading.Event()

    def _track(conn):
        with opened_lock:
            opened.append(conn)
        return conn

    class _TrackedHTTPPool(HTTPConnectionPool):
        def _new_conn(self):
            return _track(super()._new_conn())

    class _TrackedHTTPSPool(HTTPSConnectionPool):
        def _new_conn(self):
            return _track(super()._new_conn())

    class _PinnedAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            if parts.scheme == "https":
                kwargs["server_hostname"] = host
                kwargs["assert_hostname"] = host
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = {"http": _TrackedHTTPPool,
                                                       "https": _TrackedHTTPSPool}

        def send(self, request, **kw):
            p = urlsplit(request.url)
            ip_host = f"[{ip}]" if ":" in ip else ip
            request.url = p._replace(netloc=ip_host + (f":{p.port}" if p.port else "")).geturl()
            request.headers["Host"] = p.netloc
            return super().send(request, **kw)

    def _expire():
        expired.set()
        with opened_lock:
            conns = list(opened)
        for conn in conns:
            sock = getattr(conn, "sock", None)
            try:
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)   # wakes a blocked recv/send
            except OSError:
                pass

    session = requests.Session()
    session.trust_env = False   # a proxy would re-resolve the host name itself
    session.mount(f"{parts.scheme}://", _PinnedAdapter())
    watchdog = threading.Timer(SEND_DEADLINE, _expire)
    watchdog.daemon = True
    watchdog.name = "notify-deadline"
    watchdog.start()
    try:
        resp = session.post(url, data=body, headers=headers, timeout=HTTP_TIMEOUT,
                            allow_redirects=False, stream=True)
        try:
            status = resp.status_code
        finally:
            resp.close()   # stream=True: the body is never read
        if expired.is_set():
            # The shutdown can end the headers early and still yield a status.
            raise TimeoutError("send deadline passed")
        return status
    finally:
        watchdog.cancel()
        session.close()


def _payload(channel, text):
    if channel == "discord":
        import json
        # allowed_mentions: a drama title can never ping @everyone/@here.
        return (json.dumps({"content": f"{APP_NAME}: {text}", "allowed_mentions": {"parse": []}},
                           ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json"})
    return text.encode("utf-8"), {"Title": APP_NAME, "Content-Type": "text/plain; charset=utf-8"}


def _log():
    import applog
    return applog.get_logger()


def _send_one(channel, text) -> str:
    """"sent" / "failed" / "refused" / "not_configured". Never raises; logs
    only the channel name and a status code or exception type."""
    url = _channel_url(channel)
    if not url:
        return "not_configured"
    try:
        validate_url(channel, url)
        ip = _resolve_target(channel, url)
    except (InvalidInputError, _Refused):
        _log().warning(f"notification: {channel} address refused by the safety rules")
        return "refused"
    except Exception as exc:
        _log().warning(f"notification: {channel} address could not be resolved "
                       f"({type(exc).__name__})")
        return "failed"
    try:
        body, headers = _payload(channel, text)
        status = _pinned_post(url, ip, body, headers)
    except Exception as exc:
        _log().warning(f"notification: {channel} send failed ({type(exc).__name__})")
        return "failed"
    if 200 <= status < 300:
        return "sent"
    _log().warning(f"notification: {channel} send failed (HTTP {int(status)})")
    return "failed"


def _deliver(text) -> dict:
    return {channel: _send_one(channel, text) for channel in CHANNELS}


# --- rate limit + burst collapse -------------------------------------------

_lock = threading.Lock()
_pending = []
_timer = None
_sent_at = collections.deque()


def _take_slot_locked(now=None) -> bool:
    now = time.monotonic() if now is None else now
    while _sent_at and now - _sent_at[0] >= 60.0:
        _sent_at.popleft()
    if len(_sent_at) >= MAX_PER_MINUTE:
        return False
    _sent_at.append(now)
    return True


def _schedule_flush():
    """Starts the burst timer; it calls flush() BURST_WINDOW seconds later."""
    timer = threading.Timer(BURST_WINDOW, flush)
    timer.daemon = True
    timer.name = "notify-flush"
    timer.start()
    return timer


# --- in-app list ------------------------------------------------------------

_recent = collections.deque(maxlen=RECENT_KEEP)
_recent_lock = threading.Lock()
_last_id = 0
# Push hook (SSE, services/event_stream_service.py): called with no
# arguments after an event is recorded; listeners re-read list_recent for
# their own principal. One that raises is ignored.
_listeners = []


def add_listener(fn) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn) -> None:
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


def _record(kind, text, job_id, owner_user_id):
    """The id is the time in milliseconds (bumped to stay increasing), not a
    counter: a gap between two ids a viewer sees says nothing about how
    many events they were not shown."""
    global _last_id
    now = time.time()
    with _recent_lock:
        _last_id = max(_last_id + 1, int(now * 1000))
        _recent.append({"id": _last_id, "at": now, "kind": kind, "text": text,
                        "job_id": job_id, "owner_user_id": owner_user_id})
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def record_event(kind, text) -> None:
    """An in-app entry not tied to a job, seen only by the PC owner and
    admins. Never raises."""
    try:
        _record(kind, text, None, None)
    except Exception:
        pass


def list_recent(principal=None, limit=RECENT_MAX) -> list:
    """Newest first: {id, at, kind, text}. Job events only for a caller who
    can see that job; new-chapter events for everyone (household-wide)."""
    from services import ownership_service
    with _recent_lock:
        events = list(_recent)
    out = []
    for e in reversed(events):
        if e["kind"] != "chapters":
            try:
                visible = ownership_service.can_see_job(principal, e["job_id"] or "",
                                                        e["owner_user_id"])
            except Exception:
                visible = False
            if not visible:
                continue
        out.append({k: e[k] for k in ("id", "at", "kind", "text")})
        if len(out) >= limit:
            break
    return out


def _chapter_count(job_id):
    try:
        import background_jobs
        result = (background_jobs.get_status(job_id) or {}).get("result")
        return int((result or {}).get("new") or 0) if isinstance(result, dict) else 0
    except Exception:
        return 0


def notify_job_finished(description, status, job_id=None, owner_user_id=None):
    """Record a job-ended event in the in-app list and queue a push for the
    configured channels. Never raises, never blocks on the network."""
    global _timer
    try:
        if status not in ("done", "error"):
            return
        if job_id == CHAPTER_CHECK_JOB_ID and status == "done":
            new = _chapter_count(job_id)
            if new <= 0:
                return   # a routine scheduled check that found nothing
            category, kind, message = "chapters", "chapters", chapter_message(new)
        else:
            category = "jobs"
            kind = "job_done" if status == "done" else "job_failed"
            message = build_message(description, status)
        _record(kind, message, job_id, owner_user_id)
        if category == "jobs":
            # The in-app list is filtered per viewer; a push channel is not.
            message = build_message(description, status, shared_only=True)
        if os.environ.get(DISABLED_ENV) == "1" or not get_categories()[category]:
            return
        if not configured_channels():
            return
        with _lock:
            _pending.append((status, message, category))
            if _timer is None:
                _timer = _schedule_flush()
    except Exception as exc:
        try:
            _log().warning(f"notification: could not queue ({type(exc).__name__})")
        except Exception:
            pass


def notify_remote_access(text) -> None:
    """A remote-access health change (remote_health_service, once per
    change): the in-app list, and Discord/ntfy when the "remote" category is
    on. Never raises, never blocks on the network."""
    global _timer
    try:
        from translate_engines import redact_secrets
        message = redact_secrets(_tidy(text, 200))
        _record("remote", message, None, None)
        if os.environ.get(DISABLED_ENV) == "1" or not get_categories()["remote"]:
            return
        if not configured_channels():
            return
        with _lock:
            _pending.append(("remote", message, "remote"))
            if _timer is None:
                _timer = _schedule_flush()
    except Exception as exc:
        try:
            _log().warning(f"notification: could not queue ({type(exc).__name__})")
        except Exception:
            pass


def flush() -> dict:
    """Send everything queued as one message (the timer calls this). Returns
    the per-channel outcome, {} when nothing was sent. Never raises."""
    global _timer
    try:
        with _lock:
            events = list(_pending)
            _pending.clear()
            _timer = None
            if not events:
                return {}
            if not _take_slot_locked():
                _log().warning(f"notification: rate limit reached, {len(events)} event(s) "
                               "not sent")
                return {}
        return _deliver(_summarize(events))
    except Exception as exc:
        try:
            _log().warning(f"notification: flush failed ({type(exc).__name__})")
        except Exception:
            pass
        return {}


def send_test() -> dict:
    """PC-only "Send test" (counts toward the per-minute cap). Returns
    {"results": {channel: outcome}}."""
    if not configured_channels():
        raise InvalidInputError("No notification channel is configured.")
    with _lock:
        if not _take_slot_locked():
            raise RateLimitedError("Too many notifications in the last minute. "
                                   "Wait a moment and try again.")
    return {"results": _deliver("Test notification. Job alerts will look like this.")}


def reset_for_tests():
    global _timer, _last_id
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = None
        _pending.clear()
        _sent_at.clear()
    with _recent_lock:
        _recent.clear()
        _last_id = 0
