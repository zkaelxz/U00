"""
services/remote_health_service.py -- is household (remote) access healthy?

UI-free. Three checks, run on a schedule by `api/background.py` only while
remote access is on (`BAIHE_PUBLIC_URL` set to an https address and
`BAIHE_API_HOUSEHOLD_PORT` set). With it off nothing here opens a socket,
resolves a name or reads a URL; the status is simply "off".

- certificate: a TLS handshake with Caddy on this PC (127.0.0.1 at the public
  URL's port) asking for the public name (SNI), with the normal chain and
  host-name checks, reading only the certificate's expiry. Caddy renews about
  30 days before expiry, so under WARN_DAYS left means renewal is overdue
  (warn) and under CRITICAL_DAYS, expired or untrusted is critical.
- ddns: skipped ("not_configured") unless `BAIHE_REMOTE_IP_CHECK_URL` is set in
  .env or the environment: an https address that answers with this PC's
  public IP as plain text (e.g. https://api.ipify.org). It is read with a
  timeout, no redirects and a pinned public address (`url_guard`), and the
  public name's DNS answer must contain that IP. The address is kept
  server-side like the notification URLs (it may carry a token); no token is
  stored in app_settings.
- listener: the household listener accepts a TCP connection on this PC.

Alerts: the overall state is the worst check. On a change into warn or
critical (or from warn up to critical) and on the change back to ok,
`notification_service.notify_remote_access` is called once; the last result
is kept in app_settings (REMOTE_HEALTH_SETTING) so a restart does not repeat
an alert. Everything returned is states, whole days, times and fixed
messages: never the host name, an address, a URL, a path or exception text.
"""
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

WARN_DAYS = 14
CRITICAL_DAYS = 5
CONNECT_TIMEOUT = 5.0
RESOLVE_TIMEOUT = 5.0
IP_CHECK_MAX_BYTES = 64
IP_CHECK_ENV = "BAIHE_REMOTE_IP_CHECK_URL"
REMOTE_HEALTH_SETTING = "remote_health"
CHECK_INTERVAL_SECONDS = 6 * 3600.0
FIRST_CHECK_SECONDS = 60.0
LOCAL_CADDY_HOST = "127.0.0.1"

_SEVERITY = {"ok": 0, "not_configured": 0, "unknown": 0, "warn": 1, "critical": 2}
_PROBLEM = ("warn", "critical")
_EXPIRED_VERIFY_CODE = 10   # X509_V_ERR_CERT_HAS_EXPIRED

OFF_MESSAGE = "Remote access is off."
NOT_CHECKED = "Not checked yet."
ALL_OK = "Remote access is working."

_now = time.time
_lock = threading.Lock()


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

def _ip_check_url():
    from services import settings_service
    return settings_service.resolve_env_names((IP_CHECK_ENV,))


def _current_public_ip(url):
    """This PC's public address, from the configured https check."""
    from services import metadata_service, url_guard
    if urlsplit(url).scheme != "https":
        raise ValueError("not https")
    ip = url_guard.resolve_public(url)
    resp = metadata_service._pinned_get(url, ip, {"Accept": "text/plain"})
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
    own, so it runs in a daemon thread that is abandoned at RESOLVE_TIMEOUT."""
    box = {}

    def run():
        try:
            box["v"] = {ipaddress.ip_address(i[4][0].split("%")[0])
                        for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        except Exception as exc:
            box["e"] = exc

    t = threading.Thread(target=run, daemon=True, name="remote-health-resolve")
    t.start()
    t.join(RESOLVE_TIMEOUT)
    if "v" not in box:
        raise OSError("not resolved")
    return box["v"]


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
            "ddns": _check("off", OFF_MESSAGE, configured=False),
            "listener": _check("off", OFF_MESSAGE)}


def _not_checked_snapshot():
    snap = {"state": "unknown", "message": NOT_CHECKED, "checked_at": None, "since": None,
            "certificate": _check("unknown", NOT_CHECKED, days_left=None),
            "ddns": _check("unknown", NOT_CHECKED, configured=bool(_ip_check_url())),
            "listener": _check("unknown", NOT_CHECKED)}
    return snap


def _load():
    try:
        import db
        stored = db.get_app_setting(REMOTE_HEALTH_SETTING, None)
    except Exception:
        return None
    return stored if isinstance(stored, dict) and stored.get("state") else None


def _save(snapshot):
    try:
        import db
        db.set_app_setting(REMOTE_HEALTH_SETTING, snapshot)
    except Exception as exc:
        _log("remote health: the result was not saved (%s)", type(exc).__name__)


def _should_notify(previous, current) -> bool:
    if current in _PROBLEM:
        return previous not in _PROBLEM or _SEVERITY[current] > _SEVERITY[previous]
    return current == "ok" and previous in _PROBLEM


def _alert_text(state, message) -> str:
    # No final full stop: a burst of several alerts is joined with ". ".
    if state == "ok":
        return "Remote access is working again"
    return f"Remote access problem: {message.rstrip('.')}"


def run_check(public_url, household_port, listener_host="127.0.0.1") -> dict:
    """Runs the checks (or records "off" without any network call), saves
    the result and sends a transition alert. Returns the public snapshot."""
    from translate_engines import redact_secrets
    with _lock:
        previous = _load()
        prev_state = (previous or {}).get("state")
        if not remote_access_enabled(public_url, household_port):
            snapshot = _off_snapshot()
            if prev_state != "off":
                _save(snapshot)
            return snapshot
        host, port = public_target(public_url)
        checks = {"certificate": check_certificate(host, port),
                  "ddns": check_ddns(host),
                  "listener": check_listener(listener_host, household_port)}
        for c in checks.values():
            c["message"] = redact_secrets(c["message"])
        worst = max(checks.values(), key=lambda c: _SEVERITY.get(c["state"], 0))
        state = worst["state"] if worst["state"] in _PROBLEM else "ok"
        message = worst["message"] if state in _PROBLEM else ALL_OK
        now = _now()
        since = (previous or {}).get("since") if prev_state == state else now
        snapshot = {"state": state, "message": message, "checked_at": now,
                    "since": since if since is not None else now, **checks}
        _save(snapshot)
    if _should_notify(prev_state, state):
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
    if not stored or stored.get("state") == "off":
        return _not_checked_snapshot()
    return stored


def _log(fmt, *args):
    try:
        from applog import get_logger
        get_logger().warning(fmt, *args)
    except Exception:
        pass
