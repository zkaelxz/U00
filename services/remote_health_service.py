"""
services/remote_health_service.py -- is household (remote) access healthy?

UI-free. Three checks, run on a schedule by `api/background.py` only while
remote access is on (`BAIHE_PUBLIC_URL` set to an https address and
`BAIHE_API_HOUSEHOLD_PORT` set). With it off the schedule opens no socket,
resolves no name and reads no URL; the status is simply "off". The one
exception is the owner pressing Test in Settings (`run_ip_check_test`), which
reads the configured public-address check once even then.

- certificate: a TLS handshake with Caddy on this PC (127.0.0.1 at the public
  URL's port) asking for the public name (SNI), with the normal chain and
  host-name checks, reading only the certificate's expiry. Caddy renews about
  30 days before expiry, so under WARN_DAYS left means renewal is overdue
  (warn) and under CRITICAL_DAYS, expired or untrusted is critical.
- ddns: skipped ("not_configured") unless `BAIHE_REMOTE_IP_CHECK_URL` is set
  (Settings, PC only: `set_ip_check_url`; or by hand in .env): an https
  address that answers with this PC's public IP as plain text (e.g.
  https://api.ipify.org). It is read with a timeout, no redirects and a
  pinned public address (`url_guard`, its lookup bounded by RESOLVE_TIMEOUT),
  and the public name's DNS answer must contain that IP; with no address of
  the same family (IPv4/IPv6) to compare, it is "unknown". The address is
  kept in .env like the notification URLs (it may carry a token), never in
  app_settings, and never returned. Every cycle re-reads it, so a change
  applies without a restart.
- listener: the household listener accepts a TCP connection on this PC.

Alerts: the overall state is the worst check. On a change into warn or
critical (or from warn up to critical) and on the change back to ok,
`notification_service.notify_remote_access` is called once. The last result
is kept in app_settings (REMOTE_HEALTH_SETTING) so a restart does not repeat
an alert, and in memory (`_memory`) so a save that keeps failing does not
either. When the saved result can't be read, the cycle neither alerts nor
saves (the last known state stays). A cycle has an overall deadline
(CYCLE_DEADLINE) and gives up as soon as the monitor is stopped; a cycle that
gave up writes nothing and sends nothing.

Everything returned is states, whole days, times and fixed messages: never
the host name, an address, a URL, a path or exception text.
"""
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from services.service_errors import ConflictError, InvalidInputError, RateLimitedError

WARN_DAYS = 14
CRITICAL_DAYS = 5
CONNECT_TIMEOUT = 5.0
RESOLVE_TIMEOUT = 5.0
IP_CHECK_MAX_BYTES = 64
IP_CHECK_MAX_URL = 512
IP_CHECK_ENV = "BAIHE_REMOTE_IP_CHECK_URL"
REMOTE_HEALTH_SETTING = "remote_health"
CHECK_INTERVAL_SECONDS = 6 * 3600.0
FIRST_CHECK_SECONDS = 60.0
# One whole cycle: the IP read's own timeout is 20 s (metadata_service), each
# lookup RESOLVE_TIMEOUT and each connect CONNECT_TIMEOUT.
CYCLE_DEADLINE = 45.0
TEST_MIN_INTERVAL = 5.0
LOCAL_CADDY_HOST = "127.0.0.1"

_SEVERITY = {"ok": 0, "not_configured": 0, "unknown": 0, "warn": 1, "critical": 2}
_PROBLEM = ("warn", "critical")
_EXPIRED_VERIFY_CODE = 10   # X509_V_ERR_CERT_HAS_EXPIRED

OFF_MESSAGE = "Remote access is off."
NOT_CHECKED = "Not checked yet."
ALL_OK = "Remote access is working."
_BAD_IP_CHECK = "The address must be https:// with a public host name, like https://api.ipify.org"

_now = time.time
_lock = threading.Lock()
_READ_FAILED = object()
# {"state", "since"} of the last completed cycle in this process (None after
# a start): what alerts compare against, so a failing save can't re-alert.
_memory = None
_test_lock = threading.Lock()
_last_test = 0.0
# Test workers still running, including ones _bounded gave up on: the
# fetch's timeout is per read, so an abandoned one can outlive its request.
_test_workers = 0
_test_workers_lock = threading.Lock()


def _bounded(fn, timeout, stop=None):
    """fn() in a daemon thread. Raises TimeoutError at `timeout`, or as soon
    as `stop` is set; the thread is then abandoned (its own socket timeouts
    end it) and its result dropped."""
    box = {}
    done = threading.Event()

    def run():
        try:
            box["v"] = fn()
        except BaseException as exc:
            box["e"] = exc
        finally:
            done.set()

    threading.Thread(target=run, daemon=True, name="remote-health-worker").start()
    deadline = time.monotonic() + timeout
    while not done.wait(max(0.0, min(0.1, deadline - time.monotonic()))):
        if (stop is not None and stop.is_set()) or time.monotonic() >= deadline:
            raise TimeoutError("gave up")
    if "e" in box:
        raise box["e"]
    return box["v"]


def _stopped(stop) -> bool:
    return stop is not None and stop.is_set()


def public_target(public_url):
    """(host, port) from an https BAIHE_PUBLIC_URL, else None."""
    try:
        parts = urlsplit(public_url or "")
        port = parts.port or 443
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return parts.hostname, port


def remote_access_enabled(public_url, household_port) -> bool:
    try:
        port = int(household_port or 0)
    except (TypeError, ValueError):
        return False
    return public_target(public_url) is not None and port > 0


def _check(state, message, **extra):
    return {"state": state, "message": message, **extra}


# --- certificate -------------------------------------------------------------

def _peer_certificate(host, port) -> dict:
    """The verified certificate Caddy on this PC serves for `host`."""
    ctx = ssl.create_default_context()
    with socket.create_connection((LOCAL_CADDY_HOST, port), timeout=CONNECT_TIMEOUT) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            return tls.getpeercert()


def check_certificate(host, port) -> dict:
    try:
        cert = _peer_certificate(host, port)
    except ssl.SSLCertVerificationError as exc:
        if getattr(exc, "verify_code", None) == _EXPIRED_VERIFY_CODE:
            return _check("critical", "The certificate has expired. Check that Caddy is "
                          "running and can reach the certificate authority.", days_left=0)
        return _check("critical", "The certificate is not trusted for the public name. "
                      "Check the Caddy setup.", days_left=None)
    except (OSError, ssl.SSLError, ValueError):
        return _check("critical", "Caddy is not answering on this PC.", days_left=None)
    try:
        expires = ssl.cert_time_to_seconds(cert["notAfter"])
    except (KeyError, TypeError, ValueError):
        return _check("critical", "The certificate's expiry date could not be read.",
                      days_left=None)
    days = int((expires - _now()) // 86400)
    if days < 0:
        return _check("critical", "The certificate has expired. Check that Caddy is "
                      "running and can reach the certificate authority.", days_left=0)
    if days < CRITICAL_DAYS:
        return _check("critical", f"The certificate expires in {days} day{'s' if days != 1 else ''} "
                      "and has not been renewed. Check Caddy's log.", days_left=days)
    if days < WARN_DAYS:
        return _check("warn", f"The certificate expires in {days} days and should already "
                      "have been renewed. Check Caddy's log.", days_left=days)
    return _check("ok", f"The certificate is valid for {days} more days.", days_left=days)


# --- DDNS --------------------------------------------------------------------

def _ip_check_url(env_path=None):
    from services import settings_service
    return settings_service.resolve_env_names((IP_CHECK_ENV,), env_path)


def _current_public_ip(url):
    """This PC's public address, from the configured https check."""
    from services import metadata_service, url_guard
    if urlsplit(url).scheme != "https":
        raise ValueError("not https")
    ip = _bounded(lambda: url_guard.resolve_public(url), RESOLVE_TIMEOUT)
    resp = metadata_service.pinned_get(url, ip, {"Accept": "text/plain"})
    try:
        if resp.status_code != 200:
            raise ValueError("bad status")
        raw = resp.raw.read(IP_CHECK_MAX_BYTES + 1, decode_content=True)
    finally:
        resp.close()
    if len(raw) > IP_CHECK_MAX_BYTES:
        raise ValueError("too long")
    return ipaddress.ip_address(raw.decode("ascii").strip())


def _resolve(host):
    """Every address `host` resolves to; getaddrinfo has no timeout of its
    own, so it is bounded by RESOLVE_TIMEOUT."""
    try:
        return _bounded(lambda: {ipaddress.ip_address(i[4][0].split("%")[0])
                                 for i in socket.getaddrinfo(host, None,
                                                             type=socket.SOCK_STREAM)},
                        RESOLVE_TIMEOUT)
    except Exception:
        raise OSError("not resolved") from None


def check_ddns(host) -> dict:
    url = _ip_check_url()
    if not url:
        return _check("not_configured", "The public address check is not set up.",
                      configured=False)
    try:
        current = _current_public_ip(url)
    except Exception:
        return _check("unknown", "This PC's public address could not be read.",
                      configured=True)
    try:
        resolved = _resolve(host)
    except OSError:
        return _check("critical", "The public name does not resolve.", configured=True)
    if resolved and not any(a.is_global for a in resolved):
        return _check("unknown", "The public name resolves to a local address here, "
                      "so it can't be compared.", configured=True)
    same_family = {a for a in resolved if a.version == current.version}
    if not same_family:
        return _check("unknown", "The public name has no address of the same kind as this "
                      "PC's public address, so it can't be compared.", configured=True)
    if current in same_family:
        return _check("ok", "The public name points to this PC.", configured=True)
    return _check("warn", "The public name points to an old address. Check the dynamic "
                  "DNS updater.", configured=True)


# --- household listener -----------------------------------------------------

def check_listener(listener_host, household_port) -> dict:
    try:
        with socket.create_connection((listener_host, int(household_port)),
                                      timeout=CONNECT_TIMEOUT):
            pass
    except (OSError, ValueError):
        return _check("critical", "The household listener is not answering on this PC.")
    return _check("ok", "The household listener is answering.")


# --- snapshot, transitions ---------------------------------------------------

def _off_snapshot():
    return {"state": "off", "message": OFF_MESSAGE, "checked_at": None, "since": None,
            "certificate": _check("off", OFF_MESSAGE, days_left=None),
            "ddns": _check("off", OFF_MESSAGE, configured=bool(_ip_check_url())),
            "listener": _check("off", OFF_MESSAGE)}


def _not_checked_snapshot():
    return {"state": "unknown", "message": NOT_CHECKED, "checked_at": None, "since": None,
            "certificate": _check("unknown", NOT_CHECKED, days_left=None),
            "ddns": _check("unknown", NOT_CHECKED, configured=bool(_ip_check_url())),
            "listener": _check("unknown", NOT_CHECKED)}


def _load():
    """The saved result, None when there is none, _READ_FAILED on an error."""
    try:
        import db
        stored = db.get_app_setting(REMOTE_HEALTH_SETTING, None)
    except Exception as exc:
        _log("remote health: the last result could not be read (%s)", type(exc).__name__)
        return _READ_FAILED
    return stored if isinstance(stored, dict) and stored.get("state") else None


def _save(snapshot) -> bool:
    try:
        import db
        db.set_app_setting(REMOTE_HEALTH_SETTING, snapshot)
        return True
    except Exception as exc:
        _log("remote health: the result was not saved (%s)", type(exc).__name__)
        return False


def _should_notify(previous, current) -> bool:
    if current in _PROBLEM:
        return previous not in _PROBLEM or _SEVERITY[current] > _SEVERITY[previous]
    return current == "ok" and previous in _PROBLEM


def _alert_text(state, message) -> str:
    # No final full stop: a burst of several alerts is joined with ". ".
    if state == "ok":
        return "Remote access is working again"
    return f"Remote access problem: {message.rstrip('.')}"


def _gather(host, port, listener_host, household_port) -> dict:
    return {"certificate": check_certificate(host, port),
            "ddns": check_ddns(host),
            "listener": check_listener(listener_host, household_port)}


def run_check(public_url, household_port, listener_host="127.0.0.1", stop=None):
    """Runs the checks (or records "off" without any network call), saves
    the result and sends a transition alert. Returns the snapshot, or None
    when the cycle gave up (CYCLE_DEADLINE, or `stop` set): nothing is
    written or sent then."""
    global _memory
    from translate_engines import redact_secrets
    notify = False
    with _lock:
        if _stopped(stop):
            return None
        previous = _load()
        read_failed = previous is _READ_FAILED
        stored = None if read_failed else previous
        if not remote_access_enabled(public_url, household_port):
            snapshot = _off_snapshot()
            if not read_failed and (stored or {}).get("state") != "off":
                _save(snapshot)
            _memory = None
            return snapshot
        host, port = public_target(public_url)
        try:
            checks = _bounded(lambda: _gather(host, port, listener_host, household_port),
                              CYCLE_DEADLINE, stop)
        except TimeoutError:
            if not _stopped(stop):
                _log("remote health: the check took too long and was dropped")
            return None
        if _stopped(stop):
            return None
        for c in checks.values():
            c["message"] = redact_secrets(c["message"])
        worst = max(checks.values(), key=lambda c: _SEVERITY.get(c["state"], 0))
        state = worst["state"] if worst["state"] in _PROBLEM else "ok"
        message = worst["message"] if state in _PROBLEM else ALL_OK
        now = _now()
        base = _memory if _memory is not None else stored
        prev_state = (base or {}).get("state")
        since = (base or {}).get("since") if prev_state == state else None
        snapshot = {"state": state, "message": message, "checked_at": now,
                    "since": since if since is not None else now, **checks}
        if not read_failed:
            # A read error keeps the last known state: no alert, no save over it.
            if _stopped(stop):
                return None
            notify = _should_notify(prev_state, state)
            _save(snapshot)
            _memory = {"state": state, "since": snapshot["since"]}
    if notify and not _stopped(stop):
        try:
            from services import notification_service
            notification_service.notify_remote_access(_alert_text(state, message))
        except Exception as exc:
            _log("remote health: the alert was not sent (%s)", type(exc).__name__)
    return snapshot


def get_status(public_url, household_port) -> dict:
    """The last saved result; never checks anything itself."""
    if not remote_access_enabled(public_url, household_port):
        return _off_snapshot()
    stored = _load()
    if not stored or stored is _READ_FAILED or stored.get("state") == "off":
        return _not_checked_snapshot()
    return stored


# --- the IP-check address (Settings, PC only) --------------------------------

def ip_check_status(env_path=None) -> dict:
    return {"configured": bool(_ip_check_url(env_path))}


def validate_ip_check_url(value) -> str:
    """The cleaned value, or InvalidInputError with a fixed message (never
    the value): https, a host, no user info, at most IP_CHECK_MAX_URL
    characters, nothing that could break the .env line, every address
    public."""
    from services import notification_service, url_guard
    value = notification_service.clean_value(value)
    if len(value) > IP_CHECK_MAX_URL:
        raise InvalidInputError("The address is too long.")
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError:
        raise InvalidInputError(_BAD_IP_CHECK) from None
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None):
        raise InvalidInputError(_BAD_IP_CHECK)
    try:
        _bounded(lambda: url_guard.resolve_public(value), RESOLVE_TIMEOUT)
    except url_guard.UnsafeURLError:
        raise InvalidInputError(_BAD_IP_CHECK) from None
    except Exception:
        raise InvalidInputError("The address could not be resolved.") from None
    return value


def set_ip_check_url(value, env_path=None) -> dict:
    """Stores the address in .env under the fixed name. {configured} only."""
    from services import settings_service
    settings_service.write_env_var(IP_CHECK_ENV, validate_ip_check_url(value), env_path)
    return ip_check_status(env_path)


def clear_ip_check_url(env_path=None) -> dict:
    """Removes it from .env; one also set as a real environment variable
    stays configured, and `configured` says so."""
    from services import settings_service
    settings_service.remove_env_vars((IP_CHECK_ENV,), env_path)
    return ip_check_status(env_path)


def run_ip_check_test(public_url) -> dict:
    """Settings' "Test": one public-address check now. Saves nothing and
    alerts nobody. {configured, state, message}: no address or URL."""
    global _last_test, _test_workers
    url = _ip_check_url()
    if not url:
        raise InvalidInputError("No address check is set up.")
    if not _test_lock.acquire(blocking=False):
        raise ConflictError("A test is already running.")
    try:
        if time.monotonic() - _last_test < TEST_MIN_INTERVAL:
            raise RateLimitedError("Wait a few seconds before testing again.")
        with _test_workers_lock:
            if _test_workers:
                raise RateLimitedError("The last test is still finishing. Try again shortly.")
            _test_workers += 1
        _last_test = time.monotonic()
        target = public_target(public_url)

        def work():
            global _test_workers
            try:
                if target is None:
                    _current_public_ip(url)
                    return _check("ok", "The address check answered. Remote access is off, "
                                  "so there is no public name to compare with yet.")
                return check_ddns(target[0])
            finally:
                with _test_workers_lock:
                    _test_workers -= 1

        try:
            result = _bounded(work, CYCLE_DEADLINE)
        except Exception:
            result = _check("unknown", "This PC's public address could not be read.")
    finally:
        _test_lock.release()
    return {"configured": True, "state": result["state"], "message": result["message"]}


def reset_for_tests():
    global _memory, _last_test, _test_workers
    _memory = None
    _last_test = 0.0
    with _test_workers_lock:
        _test_workers = 0


def _log(fmt, *args):
    try:
        from applog import get_logger
        get_logger().warning(fmt, *args)
    except Exception:
        pass
