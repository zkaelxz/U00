"""
services/notification_service.py -- push a short "job finished /
job failed" message to a Discord webhook and/or an ntfy topic.

UI-free. `job_settings._notify_job_finished` calls
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
The push leaves the title (and its series name) out for a drama a household
member couldn't see (private, or in a private series): the channel may be
shared. The switch "This channel is only mine" (`show_private_titles`, off by
default, `private_titles` in `db.app_settings["notify_categories"]`) lifts that for a channel
only the PC owner reads.
Never line text, keys or paths; the result also goes through
`engine_backends.shared.redact_secrets`.

Success pushes: a job that finished cleanly pushes only when it ran
SUCCESS_PUSH_MIN_SECONDS or longer (a short one stays in the bell). A failure,
a finish with errors, "Waiting for you" and every event (chapters, live,
remote access) always push. A job whose run time is unknown counts as long.

Grouping: a clean success waits GROUP_WINDOW seconds for repeats and goes
out with them as "14 \u00d7 Re-transcribe line \u00b7 Title \u00b7 all finished",
one sentence per (job kind, title) with the names kept. Anything else is sent
after BURST_WINDOW seconds, carrying the waiting successes with it; identical
failure or hold sentences are shown once with a count.

Discord cards: a message to Discord is an embed whose colour is its result
(green Finished, amber Finished with errors or Waiting for you, red Failed).
A single job's card adds its title and series, the counts its result already
holds (RESULT_COUNTS), one scrubbed sentence of the failure reason, how long it
ran, a link to the title only when BAIHE_PUBLIC_URL is an https address, and
who ran it only when more than one account exists (display name, never an
email). Never line text. ntfy keeps plain text.

Rate limit: events arriving within one wait are collapsed into one message, and at most
MAX_PER_MINUTE messages (tests included) go out in any 60 s. Only clean
successes are dropped (with a log line) over the cap: anything the person
must act on or learn about is sent anyway, and a failure is never folded
into the count, so its reason stays readable.

SSRF: Discord URLs must be https to discord.com / discordapp.com (or the
ptb./canary. hosts) under /api/webhooks/<id>/<token>. ntfy URLs must be
https://<host>/<topic>, and the host must resolve to public addresses only
(`url_guard.resolve_public`). A self-hosted ntfy on this PC or the LAN
(loopback or RFC 1918 / IPv6 ULA addresses, http or https; never
link-local, so no cloud metadata endpoint) is allowed only when
`BAIHE_NTFY_ALLOW_LOCAL=1` is set in `.env` or the environment by hand --
there is deliberately no API route that turns it on. Even then, a target
on this PC (loopback) may not use one of Baihe's own ports (the API 8600 or the configured BAIHE_API_PORT, the extension bridge 8756).
Every send connects to the address that was validated (no second DNS
lookup), with no redirects, no proxy, a per-socket timeout and an overall
SEND_DEADLINE; the reply body is never read (only the status code).

In-app list: every job that finishes or fails is
also kept in a short in-memory list (RECENT_KEEP events, at most RECENT_MAX
shown to one viewer, lost on a restart), whether or not a channel is configured, for the header bell
(`GET /api/notifications`). `list_recent(principal)` shows a job event only
to someone who can see that job (`ownership_service.can_see_job`); new-
chapter events are household-wide, like the Sources notifications list.

A followed Live channel (services/live_follow_poller.py) adds a "live"
entry once per stream, for the follow's owner; it goes to Discord/ntfy only
for the PC owner's own follows (`notify_live_follow`), with no category
switch of its own.

A run that stopped and waits for the person ("Waiting for you: ...") adds a
"waiting" entry (`notify_waiting`, or `notify_job_finished` with the job
result's waiting note): glossary names to review, a series auto-run hold,
the queue stopping on a failed item, a job stopped at a spending cap. It
rides the "jobs" category. Its typed `target` (a page name plus the one id
that page needs, `clean_target`) lets the bell open the place; the push
never carries a link, and leaves out a title or series a household member
couldn't see.

Categories: "jobs" (a job finished, failed or waits), "chapters" (a tracked-
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

import env_reader
from engine_backends.redaction import redact_secrets
from lib import url_guard
from lib.errors import InvalidInputError, RateLimitedError

CHANNELS = ("discord", "ntfy")
CATEGORIES = ("jobs", "chapters", "remote")
CATEGORY_SETTING = "notify_categories"
PC_NAME_KEY = "pc_name"                   # also inside the CATEGORY_SETTING blob; empty means use the Windows name
PC_NAME_MAX = 40
PRIVATE_TITLES_KEY = "private_titles"   # kept inside the CATEGORY_SETTING blob, off unless True
CHAPTER_CHECK_JOB_ID = "sources_chapter_check"   # services.chapter_check_service.CHECK_JOB_ID
RECENT_MAX = 50          # events returned to one viewer
RECENT_KEEP = 500        # events kept in memory for everyone
ENV_VARS = {"discord": ("BAIHE_DISCORD_WEBHOOK_URL",), "ntfy": ("BAIHE_NTFY_TOPIC_URL",)}
ALLOW_LOCAL_NTFY_ENV = "BAIHE_NTFY_ALLOW_LOCAL"
DISABLED_ENV = "BAIHE_NOTIFY_DISABLED"

HTTP_TIMEOUT = (3.05, 5)
SEND_DEADLINE = 10.0          # wall clock for one POST, however slowly the server replies
BURST_WINDOW = 5.0
SUCCESS_PUSH_MIN_SECONDS = 120.0
GROUP_WINDOW = 60.0           # a lone clean success waits this long for repeats
MAX_GROUPS_NAMED = 5
PUBLIC_URL_ENV = "BAIHE_PUBLIC_URL"
# Discord card colours: green finished, amber with errors or waiting, red failed.
COLOR_OK, COLOR_WARN, COLOR_FAIL, COLOR_INFO = 0x2ECC71, 0xF59E0B, 0xE74C3C, 0x5865F2
# Counts a job result already carries, shown as card fields: (result key, label).
RESULT_COUNTS = (("translated_count", "Lines translated"), ("skipped_count", "Skipped"),
                 ("failed_count", "Failed"), ("line_count", "Lines"), ("page_count", "Pages"),
                 ("new", "New chapters"))
MAX_PER_MINUTE = 5
MAX_URL_LEN = 512
_KIND_MAX = 60
_TITLE_MAX = 80
APP_NAME = "Baihe Studio"

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
    return env_reader.resolve_env_names(ENV_VARS[channel])


def allow_local_ntfy() -> bool:
    return (env_reader.resolve_env_names((ALLOW_LOCAL_NTFY_ENV,)) or "").strip() == "1"


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


def show_private_titles() -> bool:
    """On only when the PC owner said the channel is theirs alone; any read
    problem keeps the safe answer."""
    try:
        import db
        stored = db.get_app_setting(CATEGORY_SETTING, None)
        return isinstance(stored, dict) and stored.get(PRIVATE_TITLES_KEY) is True
    except Exception:
        return False


def saved_pc_name() -> str:
    """The name the PC owner gave this PC, or "" when none is set."""
    try:
        import db
        stored = db.get_app_setting(CATEGORY_SETTING, None)
        value = stored.get(PC_NAME_KEY) if isinstance(stored, dict) else None
        return _tidy(value, PC_NAME_MAX) if isinstance(value, str) else ""
    except Exception:
        return ""


def set_categories(jobs=None, chapters=None, remote=None, private_titles=None,
                   pc_name=None) -> dict:
    """Switches the external-push categories and the private-titles switch,
    and sets the PC name ("" clears it). None leaves one unchanged."""
    import db
    changes: dict = {name: bool(value) for name, value in
               (("jobs", jobs), ("chapters", chapters), ("remote", remote),
                (PRIVATE_TITLES_KEY, private_titles)) if value is not None}
    if pc_name is not None:
        changes[PC_NAME_KEY] = _tidy(pc_name, PC_NAME_MAX)

    def apply(stored):
        # Same normalising as get_categories: anything but False reads as on.
        stored = stored if isinstance(stored, dict) else {}
        return {**{c: stored.get(c) is not False for c in CATEGORIES},
                PRIVATE_TITLES_KEY: stored.get(PRIVATE_TITLES_KEY) is True,
                PC_NAME_KEY: stored.get(PC_NAME_KEY, ""), **changes}
    db.update_app_setting(CATEGORY_SETTING, apply)
    return get_status()


def get_status() -> dict:
    """Booleans only: never a URL, host or topic."""
    categories = get_categories()
    return {"discord_configured": bool(_channel_url("discord")),
            "ntfy_configured": bool(_channel_url("ntfy")),
            "ntfy_allow_local": allow_local_ntfy(),
            "send_jobs": categories["jobs"],
            "send_chapters": categories["chapters"],
            "send_remote": categories["remote"],
            "show_private_titles": show_private_titles(),
            "pc_name": saved_pc_name(),
            "pc_name_default": _windows_name()}


def _unmap(ip):
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped if mapped else ip


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
    return {"channel": channel, "configured": bool(env_reader.resolve_env_names(ENV_VARS[channel], env_path))}


def clear_channel(channel, env_path=None) -> dict:
    """Removes the channel's URL from .env. One still set as a real
    environment variable stays configured, and `configured` says so."""
    _check_channel(channel)
    s = _settings()
    s.remove_env_vars(ENV_VARS[channel], env_path)
    return {"channel": channel, "configured": bool(env_reader.resolve_env_names(ENV_VARS[channel], env_path))}


# --- message content -------------------------------------------------------

_DRAMA_REF = re.compile(r"\((?:title|drama) #?(\d+)\)")
_SPACES = re.compile(r"\s+")


def _tidy(text, limit):
    text = _SPACES.sub(" ", "".join(c if c.isprintable() else " " for c in str(text))).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# Anyone signed in with no special rights: sees only shared dramas.
_HOUSEHOLD = {"user_id": None, "is_admin": False, "is_local_owner": False}


def household_sees(kind, item_id) -> bool:
    """Could any household member see this drama or series? A push channel
    may be read by the whole household, so only then may it be named
    there. False when unsure, except that the owner's "only mine" switch
    makes it True."""
    if show_private_titles():
        return True
    try:
        from services import ownership_service
        return ownership_service.can_see(_HOUSEHOLD, kind, int(item_id))
    except Exception:
        return False


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
        if shared_only and isinstance(drama, dict) and not household_sees("drama", m.group(1)):
            return None
    except Exception:
        return None
    if not isinstance(drama, dict):
        return None
    title = drama.get("title_en") or drama.get("title_zh")
    if not title:
        return None
    title = _tidy(title, _TITLE_MAX)
    series = _series_name(drama.get("series_id"), shared_only)
    return f"{title} ({series})" if series else title


def _series_name(series_id, shared_only):
    """The series' name, or None; shared_only hides one a household member
    couldn't see."""
    if not series_id:
        return None
    try:
        import db
        name = next((r["name"] for r in db.list_series() if r["id"] == series_id), None)
        if name and shared_only and not household_sees("series", series_id):
            return None
    except Exception:
        return None
    return _tidy(name, _TITLE_MAX) if name else None


def _job_kind(description) -> str:
    """The job description up to its first "(", or "Background job"."""
    kind = _tidy(re.split(r"\s*\(", description or "", maxsplit=1)[0], _KIND_MAX)
    return kind or "Background job"


def build_message(description, status, shared_only=False, with_errors=False,
                  reason=None) -> str:
    """"Finished: Translation - <drama title>" / "Failed: ...". Only the
    job kind (description up to its first "("), the drama title and the
    outcome, plus the plain reason of a failure that has one."""
    outcome = ("Finished with errors" if with_errors else "Finished") if status == "done" \
        else "Failed"
    text = f"{outcome}: {_job_kind(description)}"
    title = _drama_title(description, shared_only)
    if title:
        text += f" - {title}"
    if reason:
        text += (f". {_tidy(reason, 160)}" if status == "done" else f": {_tidy(reason, 160)}")
    return redact_secrets(text)


def chapter_message(new_count) -> str:
    n = int(new_count)
    return f"{n} new chapter{'s' if n != 1 else ''} found"


def _folds_into_count(event) -> bool:
    """Only a clean success is folded into "N background jobs finished".
    Failures, finishes with errors, "waiting" and events keep their own
    sentence: each has something the person has to read or do."""
    return event[0] == "done" and (event[2] if len(event) > 2 else "jobs") == "jobs"


def _group_sentence(group, n) -> str:
    kind, title = group
    return f"{n} \u00d7 {kind}" + (f" \u00b7 {title}" if title else "") + " \u00b7 all finished"


def _summarize(events) -> str:
    """events: (status, message[, category[, group]]) tuples. Status
    "partial" is a job that finished with errors. A clean success's `group`
    is (job kind, title or None): repeats of one group become "14 \u00d7
    Re-transcribe line \u00b7 Title \u00b7 all finished", keeping their names. A
    message repeated word for word by a failure or a hold is shown once with
    its count."""
    if len(events) == 1:
        return events[0][1]
    folded = [e for e in events if _folds_into_count(e)]
    parts = []
    groups = collections.OrderedDict()
    for e in folded:
        groups.setdefault(e[3] if len(e) > 3 else None, []).append(e)
    named = list(groups.items())[:MAX_GROUPS_NAMED]
    for group, members in named:
        if len(members) == 1:
            parts.append(f"{members[0][1]}.")
        elif group is not None:
            parts.append(f"{_group_sentence(group, len(members))}.")
        else:
            parts.append(f"{len(members)} background jobs finished.")
    rest = sum(len(m) for g, m in list(groups.items())[MAX_GROUPS_NAMED:])
    if rest:
        parts.append(f"{rest} more finished.")
    repeats = collections.Counter(e[1] for e in events if e not in folded)
    for text, n in repeats.items():
        # A hold's text is already a sentence ("... press Continue to run the rest.").
        parts.append(f"{text.rstrip('.')}" + (f" (\u00d7{n})" if n > 1 else "") + ".")
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
        raw = str(info[4][0]).split("%")[0]
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
        try:
            resp = session.post(url, data=body, headers=headers, timeout=HTTP_TIMEOUT,
                                allow_redirects=False, stream=True)
        except Exception:
            if expired.is_set():
                # The watchdog's shutdown can surface as a connection reset
                # instead of a clean return; it is still the deadline.
                raise TimeoutError("send deadline passed") from None
            raise
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


def _windows_name() -> str:
    try:
        return _tidy(socket.gethostname(), PC_NAME_MAX)
    except Exception:
        return ""


def _pc_name() -> str:
    """Which PC sent a message, so one Discord channel shared by several PCs
    tells them apart: the owner's own name for it, else the Windows computer
    name. Empty when neither is available."""
    return saved_pc_name() or _windows_name()


def _payload(channel, text, embed=None):
    if channel == "discord":
        import json
        pc = _pc_name()
        # allowed_mentions: a drama title can never ping @everyone/@here.
        body: dict = {"allowed_mentions": {"parse": []}}
        if embed:
            body["embeds"] = [{**embed, "footer": {"text": f"PC: {pc}"}} if pc else embed]
        else:
            body["content"] = f"{APP_NAME}{f' ({pc})' if pc else ''}: {text}"
        return (json.dumps(body, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json"})
    return text.encode("utf-8"), {"Title": APP_NAME, "Content-Type": "text/plain; charset=utf-8"}


def _log():
    import applog
    return applog.get_logger()


def _send_one(channel, text, embed=None) -> str:
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
        body, headers = _payload(channel, text, embed)
        status = _pinned_post(url, ip, body, headers)
    except Exception as exc:
        _log().warning(f"notification: {channel} send failed ({type(exc).__name__})")
        return "failed"
    if 200 <= status < 300:
        return "sent"
    _log().warning(f"notification: {channel} send failed (HTTP {int(status)})")
    return "failed"


def _deliver(text, embed=None) -> dict:
    return {channel: _send_one(channel, text, embed) for channel in CHANNELS}


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


def _schedule_flush(delay=BURST_WINDOW):
    """Starts the burst timer; it calls flush() `delay` seconds later."""
    timer = threading.Timer(delay, flush)
    timer.daemon = True
    timer.name = "notify-flush"
    timer.start()
    return timer


_timer_is_short = False


def _enqueue(event) -> None:
    """Queues one event. A clean success waits GROUP_WINDOW so repeats of the
    same job can be grouped; anything else is sent after BURST_WINDOW, and
    takes the waiting successes with it rather than being held for them."""
    global _timer, _timer_is_short
    urgent = not _folds_into_count(event)
    with _lock:
        _pending.append(event)
        if _timer is None:
            _timer = _schedule_flush(BURST_WINDOW if urgent else GROUP_WINDOW)
            _timer_is_short = urgent
        elif urgent and not _timer_is_short:
            _timer.cancel()
            _timer = _schedule_flush(BURST_WINDOW)
            _timer_is_short = True


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


# Optional fields an in-app entry may carry for the bell (a followed channel's
# "Start Live" action and a scheduled start time, a waiting entry's target);
# never a URL or path.
_EXTRA_KEYS = ("follow_id", "action", "starts_at", "target")

# A waiting entry's target: the page the bell's Open goes to, and the one id
# that page needs (None: the page needs none). The frontend builds the link.
TARGET_PAGES = {"glossary": "drama_id", "review": "drama_id", "series": "series_id",
                "jobs": None}
WAITING_PREFIX = "Waiting for you: "
_WAITING_MAX = 300


def clean_target(target):
    """{"page": ..., plus its id} for a known page with a positive int id,
    else None. Builds a new dict, so no other key can ride along."""
    if not isinstance(target, dict):
        return None
    page = target.get("page")
    if not isinstance(page, str) or page not in TARGET_PAGES:
        return None
    id_key = TARGET_PAGES[page]
    if id_key is None:
        return {"page": page}
    value = target.get(id_key)
    if type(value) is not int or value <= 0:
        return None
    return {"page": page, id_key: value}


def _waiting_text(text) -> str:
    """The prefix, then the text with every link that starts with a scheme
    ("https://...") taken out and secrets redacted, cut to length last: a
    key cut in half by the limit would no longer match the redaction."""
    from services import jobs_service
    text = redact_secrets(jobs_service.URL_PATTERN.sub("[link removed]", str(text)))
    return WAITING_PREFIX + _tidy(text, _WAITING_MAX)


def _record(kind, text, job_id, owner_user_id, extra=None, names_drama_id=None):
    """The id is the time in milliseconds (bumped to stay increasing), not a
    counter: a gap between two ids a viewer sees says nothing about how
    many events they were not shown. `names_drama_id` is kept for
    _sees_target only: it is not one of _EXTRA_KEYS, so no viewer gets it."""
    global _last_id
    now = time.time()
    with _recent_lock:
        _last_id = max(_last_id + 1, int(now * 1000))
        _recent.append({"id": _last_id, "at": now, "kind": kind, "text": text,
                        "job_id": job_id, "owner_user_id": owner_user_id,
                        **{k: v for k, v in (extra or {}).items() if k in _EXTRA_KEYS},
                        **({"names_drama_id": names_drama_id} if names_drama_id else {})})
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


def _sees_target(principal, event) -> bool:
    """A waiting entry names the title or series its target points at, and
    a hold's entry may also name a title in that series (`names_drama_id`).
    A hold's entry is kept for its owner alone (no job id): once any of
    these is no longer visible to them (a deleted title counts), neither is
    the entry."""
    from services import ownership_service
    target = event.get("target") or {}
    if event["kind"] != "waiting" or ownership_service.sees_every_job(principal):
        return True
    named = event.get("names_drama_id")
    if named is not None and not ownership_service.can_see(principal, "drama", named):
        return False
    if "drama_id" in target:
        return ownership_service.can_see(principal, "drama", target["drama_id"])
    if "series_id" in target:
        return ownership_service.can_see(principal, "series", target["series_id"])
    return True


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
                                                        e["owner_user_id"]) \
                    and _sees_target(principal, e)
            except Exception:
                visible = False
            if not visible:
                continue
        out.append({k: e[k] for k in ("id", "at", "kind", "text", *_EXTRA_KEYS) if k in e})
        if len(out) >= limit:
            break
    return out


def _one_sentence(text):
    """The first sentence of `text`, scrubbed of keys, paths and addresses."""
    if not text:
        return None
    from services import jobs_service
    clean = redact_secrets(jobs_service.scrub_text(str(text)))
    first = re.split(r"(?<=[.!?])\s", _tidy(clean, 400), maxsplit=1)[0]
    return _tidy(first, 160) or None


def _duration_text(seconds) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s"
    return f"{minutes // 60} h {minutes % 60} min"


def _title_link(description):
    """The title's address on the remote host, only when a public https
    address is set up; None otherwise."""
    m = _DRAMA_REF.search(description or "")
    if not m:
        return None
    try:
        from services import remote_health_service
        public = env_reader.resolve_env_names((PUBLIC_URL_ENV,))
        if remote_health_service.public_target(public) is None:
            return None
        return f"https://{urlsplit(public).netloc}/#/drama/{int(m.group(1))}"
    except Exception:
        return None


def _run_by(owner_user_id):
    """The runner's display name, only when more than one account exists."""
    if not owner_user_id:
        return None
    try:
        import db
        users = [u for u in db.auth_list_users() if u.get("is_active", 1)]
        if len(users) < 2:
            return None
        mine = next((u for u in users if u.get("id") == owner_user_id), None)
        name = _tidy((mine or {}).get("display_name") or "", 40)
        return name or None
    except Exception:
        return None


def _job_embed(description, status, with_errors, reason, job_id, owner_user_id):
    """The Discord card for one ended job (see the module note). `status` is
    "done" or "error". Every string has been through redact_secrets."""
    failed = status == "error"
    outcome = "Failed" if failed else ("Finished with errors" if with_errors else "Finished")
    job = {}
    try:
        import background_jobs
        job = background_jobs.get_status(job_id) or {}
    except Exception:
        pass
    embed = {"title": _tidy(f"{outcome} \u00b7 {_job_kind(description)}", 200),
             "color": COLOR_FAIL if failed else COLOR_WARN if with_errors else COLOR_OK}
    title = _drama_title(description, True)
    sentence = _one_sentence(reason or (job.get("error") if failed else None))
    lines = [x for x in (title, sentence) if x]
    if lines:
        embed["description"] = "\n".join(lines)
    fields = []
    started, finished = job.get("started_at"), job.get("finished_at")
    if started is not None and finished is not None and finished >= started:
        fields.append(("Duration", _duration_text(finished - started)))
    result = job.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    for key, label in RESULT_COUNTS:
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            fields.append((label, str(value)))
    who = _run_by(owner_user_id)
    if who:
        fields.append(("Run by", who))
    if fields:
        embed["fields"] = [{"name": n, "value": redact_secrets(v), "inline": True}
                           for n, v in fields[:10]]
    link = _title_link(description) if title else None
    if link:
        embed["url"] = link
    return embed


def _embed_for(events, text):
    """The card for one flushed message, or None for plain text. A single
    job keeps its own card; several events share one coloured card holding
    the summary. Events that are not jobs alone stay plain text."""
    if len(events) == 1:
        return events[0][4] if len(events[0]) > 4 else None
    if not any((e[2] if len(e) > 2 else "jobs") == "jobs" for e in events):
        return None
    statuses = {e[0] for e in events}
    color = (COLOR_FAIL if "error" in statuses else
             COLOR_WARN if statuses & {"partial", "waiting"} else
             COLOR_OK if statuses <= {"done"} else COLOR_INFO)
    return {"description": redact_secrets(_tidy(text, 2000)), "color": color}


def _ran_long(job_id) -> bool:
    """Did the job run SUCCESS_PUSH_MIN_SECONDS or more? True when its times
    are unknown: a missing record must not silence a long job."""
    try:
        import background_jobs
        job = background_jobs.get_status(job_id) or {}
        started, finished = job.get("started_at"), job.get("finished_at")
        if started is None or finished is None:
            return True
        return finished - started >= SUCCESS_PUSH_MIN_SECONDS
    except Exception:
        return True


def _chapter_count(job_id):
    try:
        import background_jobs
        result = (background_jobs.get_status(job_id) or {}).get("result")
        return int((result or {}).get("new") or 0) if isinstance(result, dict) else 0
    except Exception:
        return 0


def _waiting_job_text(description, note, shared_only) -> str:
    """"12 glossary names to review - <title>", "Translation stopped at the
    spending cap - <title>" or "Translation stopped - <title>. <reason>",
    without the title when there is none to name. `shared_only` (the push):
    the title only when the household may see it, and the reason only when
    the note marks it as one of Baihe's own sentences (`own_reason`), since
    any other stop sentence may quote a provider's error."""
    what = note.get("text") if isinstance(note.get("text"), str) else ""
    if not what.strip():
        kind = _job_kind(description)
        what = f"{kind} stopped at the spending cap" if note.get("cap") is True \
            else f"{kind} stopped"
    text = _tidy(what, 120)
    title = _drama_title(description, shared_only)
    if title:
        text += f" - {title}"
    reason = note.get("reason")
    if isinstance(reason, str) and reason.strip() \
            and (not shared_only or note.get("own_reason") is True):
        text += f". {_tidy(reason, 160)}"
    return text


def waiting_job_message(description, note) -> str:
    """The bell's sentence for a done job's waiting note, for the desktop
    pop-up on the PC itself (job_settings._notify_job_finished); on the
    Guest profile, the push's sentence instead."""
    from services import pc_profile_service
    try:
        guest = pc_profile_service.guest_active()
    except Exception:
        guest = True
    # Guest hides private titles from the screen, and the pop-up is on that screen.
    return _waiting_text(_waiting_job_text(description, note, guest))


def notify_job_finished(description, status, job_id=None, owner_user_id=None,
                        with_errors=False, reason=None, waiting=None):
    """Record a job-ended event in the in-app list and queue a push for the
    configured channels. `waiting`: the waiting note a done job's result
    left (jobs_service.waiting_note); the job then says "Waiting for you"
    instead of "Finished". Never raises, never blocks on the network."""
    try:
        if status not in ("done", "error"):
            return
        if status == "done" and isinstance(waiting, dict) and job_id != CHAPTER_CHECK_JOB_ID:
            notify_waiting(_waiting_job_text(description, waiting, False),
                           push_text=_waiting_job_text(description, waiting, True),
                           owner_user_id=owner_user_id, job_id=job_id,
                           target=waiting.get("target"))
            return
        if job_id == CHAPTER_CHECK_JOB_ID and status == "done":
            new = _chapter_count(job_id)
            if new <= 0:
                return   # a routine scheduled check that found nothing
            category, kind, message = "chapters", "chapters", chapter_message(new)
        else:
            category = "jobs"
            kind = "job_done" if status == "done" else "job_failed"
            message = build_message(description, status, with_errors=with_errors, reason=reason)
        _record(kind, message, job_id, owner_user_id)
        if category == "jobs":
            if status == "done" and not with_errors and not _ran_long(job_id):
                return   # a quick success stays in the bell
            # The in-app list is filtered per viewer; a push channel is not.
            message = build_message(description, status, shared_only=True,
                                    with_errors=with_errors, reason=reason)
            group = None
            if status == "done" and not with_errors:
                group = (_job_kind(description), _drama_title(description, True))
            if with_errors:
                status = "partial"
        if os.environ.get(DISABLED_ENV) == "1" or not get_categories()[category]:
            return
        if not configured_channels():
            return
        card = _job_embed(description, "error" if status == "error" else "done", with_errors,
                          reason, job_id, owner_user_id) if category == "jobs" else None
        _enqueue((status, message, category, group, card) if category == "jobs"
                 else (status, message, category))
    except Exception as exc:
        try:
            _log().warning(f"notification: could not queue ({type(exc).__name__})")
        except Exception:
            pass


def notify_remote_access(text) -> None:
    """A remote-access health change (remote_health_service, once per
    change): the in-app list, and Discord/ntfy when the "remote" category is
    on. Never raises, never blocks on the network."""
    try:
        message = redact_secrets(_tidy(text, 200))
        _record("remote", message, None, None)
        if os.environ.get(DISABLED_ENV) == "1" or not get_categories()["remote"]:
            return
        if not configured_channels():
            return
        _enqueue(("remote", message, "remote"))
    except Exception as exc:
        try:
            _log().warning(f"notification: could not queue ({type(exc).__name__})")
        except Exception:
            pass


def notify_resource_alert(text, recovered=False) -> None:
    """A resource alert or recovery (resource_monitor_service): the in-app
    list (PC owner and admins, like remote access) and, whenever a Discord or
    ntfy channel is set up, a push. It has no category switch: a hot or full
    PC is a warning, and warnings always push. Never raises, never blocks on
    the network."""
    try:
        message = redact_secrets(_tidy(f"This PC: {text}", 200))
        _record("resource", message, None, None)
        if os.environ.get(DISABLED_ENV) == "1" or not configured_channels():
            return
        card = {"description": message, "color": COLOR_OK if recovered else COLOR_WARN}
        _enqueue(("resource", message, "resource", None, card))
    except Exception as exc:
        try:
            _log().warning(f"notification: could not queue ({type(exc).__name__})")
        except Exception:
            pass


def notify_waiting(text, *, push_text, owner_user_id=None, job_id=None, target=None,
                   names_drama_id=None) -> None:
    """A run stopped and waits for the person: "Waiting for you: <text>" in
    the in-app list, seen like a job event (`job_id`, `owner_user_id`;
    neither: the PC owner and admins only), and Discord/ntfy when the
    "jobs" category is on. `push_text` is required because the push is not
    filtered per viewer: a caller whose text names a title or series a
    household member couldn't see (household_sees) passes a sentence
    without that name. `target`: where the bell's Open goes (clean_target;
    anything else is dropped). `names_drama_id`: the title `text` names,
    which matters where the target points at its series instead: the entry
    then also needs that title visible (_sees_target). Only a positive int
    is kept.
    Never raises, never blocks on the network."""
    try:
        clean = clean_target(target)
        named = names_drama_id if type(names_drama_id) is int and names_drama_id > 0 else None
        _record("waiting", _waiting_text(text), job_id, owner_user_id,
                {"target": clean} if clean else None, names_drama_id=named)
        if os.environ.get(DISABLED_ENV) == "1" or not get_categories()["jobs"]:
            return
        if not configured_channels():
            return
        _enqueue(("waiting", _waiting_text(push_text), "jobs"))
    except Exception as exc:
        try:
            _log().warning(f"notification: could not queue ({type(exc).__name__})")
        except Exception:
            pass


def notify_live_follow(text, owner_user_id, follow_id, starts_at=None, start_action=False,
                       push=False, push_text=None) -> None:
    """A followed channel went live or scheduled a stream
    (services/live_follow_poller.py, once per stream). The in-app entry is
    seen by the follow's owner (and, like a job of theirs, the PC owner and
    admins). `push`: also Discord/ntfy, which the poller passes only for
    the PC owner's own follows, so a member's channel is never named on a
    channel the household may share; `push_text` replaces the text there
    (a push has no viewer's clock to show a start time in). Never raises,
    never blocks."""
    try:
        message = redact_secrets(_tidy(text, 200))
        extra: dict = {"follow_id": int(follow_id)}
        if start_action:
            extra["action"] = "start_live"
        if starts_at is not None:
            extra["starts_at"] = float(starts_at)
        _record("live", message, None, owner_user_id, extra)
        if not push or os.environ.get(DISABLED_ENV) == "1" or not configured_channels():
            return
        _enqueue(("live", redact_secrets(_tidy(push_text or text, 200)), "live"))
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
                urgent = [e for e in events if not _folds_into_count(e)]
                _log().warning(f"notification: rate limit reached, "
                               f"{len(events) - len(urgent)} finished job(s) not sent")
                if not urgent:
                    return {}
                events = urgent
        text = _summarize(events)
        return _deliver(text, _embed_for(events, text))
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
