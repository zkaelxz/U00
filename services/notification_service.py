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
there is deliberately no API route that turns it on. Every send connects
to the address that was validated (no second DNS lookup), with no
redirects, no proxy and a timeout.

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
ENV_VARS = {"discord": ("BAIHE_DISCORD_WEBHOOK_URL",), "ntfy": ("BAIHE_NTFY_TOPIC_URL",)}
ALLOW_LOCAL_NTFY_ENV = "BAIHE_NTFY_ALLOW_LOCAL"
DISABLED_ENV = "BAIHE_NOTIFY_DISABLED"

HTTP_TIMEOUT = (3.05, 5)
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


def get_status() -> dict:
    """Booleans only: never a URL, host or topic."""
    return {"discord_configured": bool(_channel_url("discord")),
            "ntfy_configured": bool(_channel_url("ntfy")),
            "ntfy_allow_local": allow_local_ntfy()}


def _is_local_ip(ip) -> bool:
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in _LOCAL_NETS)


def _clean_value(value) -> str:
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
    value = _clean_value(value)
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
    looks_local = (host == "localhost" or host.endswith(".localhost") or host.endswith(".local")
                   or (literal is not None and not literal.is_global))
    if not allow_local and (parts.scheme != "https" or looks_local):
        raise InvalidInputError(_NTFY_LOCAL_OFF)
    if literal is not None and (literal.is_multicast or literal.is_reserved
                                or literal.is_unspecified
                                or (not literal.is_global and not _is_local_ip(literal))):
        raise InvalidInputError(bad)   # link-local, reserved, multicast ...
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


def _drama_title(description):
    m = _DRAMA_REF.search(description or "")
    if not m:
        return None
    try:
        import db
        drama = db.get_drama(int(m.group(1)))
    except Exception:
        return None
    if not isinstance(drama, dict):
        return None
    title = drama.get("title_en") or drama.get("title_zh")
    return _tidy(title, _TITLE_MAX) if title else None


def build_message(description, status) -> str:
    """"Finished: Translation - <drama title>" / "Failed: ...". Only the
    job kind (description up to its first "("), the drama title and the
    outcome."""
    from translate_engines import redact_secrets
    outcome = "Finished" if status == "done" else "Failed"
    kind = _tidy(re.split(r"\s*\(", description or "", maxsplit=1)[0], _KIND_MAX)
    text = f"{outcome}: {kind or 'Background job'}"
    title = _drama_title(description)
    if title:
        text += f" - {title}"
    return redact_secrets(text)


def _summarize(events) -> str:
    if len(events) == 1:
        return events[0][1]
    done = sum(1 for status, _ in events if status == "done")
    return (f"{len(events)} background jobs ended: {done} finished, "
            f"{len(events) - done} failed.")


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
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port
                                   or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except (UnicodeError, OSError):
        raise OSError("resolve failed") from None
    first = None
    for info in infos:
        raw = info[4][0].split("%")[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise _Refused() from None
        if not _is_local_ip(ip):
            raise _Refused()   # a mix of local and other addresses is refused too
        first = first or raw
    if first is None:
        raise OSError("resolve failed")
    return first


def _pinned_post(url, ip, body: bytes, headers: dict):
    """POST to `url` connecting to the already-validated `ip` (no second DNS
    lookup), keeping SNI and the certificate check on the real host name."""
    import requests
    from requests.adapters import HTTPAdapter

    parts = urlsplit(url)
    host = parts.hostname

    class _PinnedAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            if parts.scheme == "https":
                kwargs["server_hostname"] = host
                kwargs["assert_hostname"] = host
            super().init_poolmanager(*args, **kwargs)

        def send(self, request, **kw):
            p = urlsplit(request.url)
            ip_host = f"[{ip}]" if ":" in ip else ip
            request.url = p._replace(netloc=ip_host + (f":{p.port}" if p.port else "")).geturl()
            request.headers["Host"] = p.netloc
            return super().send(request, **kw)

    session = requests.Session()
    session.trust_env = False   # a proxy would re-resolve the host name itself
    session.mount(f"{parts.scheme}://", _PinnedAdapter())
    try:
        resp = session.post(url, data=body, headers=headers, timeout=HTTP_TIMEOUT,
                            allow_redirects=False)
        status = resp.status_code
        resp.close()
        return status
    finally:
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


def notify_job_finished(description, status):
    """Queue a job-ended event. Never raises, never blocks on the network."""
    global _timer
    try:
        if status not in ("done", "error") or os.environ.get(DISABLED_ENV) == "1":
            return
        if not configured_channels():
            return
        message = build_message(description, status)
        with _lock:
            _pending.append((status, message))
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
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = None
        _pending.clear()
        _sent_at.clear()
