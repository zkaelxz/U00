"""Remote-access health (services/remote_health_service.py, the
GET /api/diagnostics/remote-health route and the api/background.py monitor).
Sockets, DNS, HTTP and the clock are faked: no network."""
from lib import http
import ipaddress
import socket
import ssl
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api import background
from api.api_config import ApiSettings
from api.server import create_app
from services import notification_service as ns
from services import remote_health_service as rhs
from services import settings_service

PUBLIC_URL = "https://baihe.example.com"
PORT = 8610
NOW = 1_800_000_000.0
DAY = 86400
CHECK_URL = "https://ip.example.net/?token=SECRET-DDNS-TOKEN"


@pytest.fixture
def env(tmp_path, monkeypatch, isolated_db):
    path = tmp_path / ".env"
    path.write_text("")
    monkeypatch.setattr(settings_service, "default_env_path", lambda: str(path))
    for name in (rhs.IP_CHECK_ENV, ns.DISABLED_ENV, *ns.ENV_VARS["discord"], *ns.ENV_VARS["ntfy"]):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rhs, "_now", lambda: NOW)
    ns.reset_for_tests()
    rhs.reset_for_tests()
    yield path
    ns.reset_for_tests()
    rhs.reset_for_tests()


@pytest.fixture
def no_network(monkeypatch):
    """Any socket, DNS or HTTP use fails the test."""
    import requests

    def boom(*a, **k):
        raise AssertionError("no outbound call expected")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(requests.Session, "get", boom)
    monkeypatch.setattr(requests.Session, "request", boom)


def _cert(days_left):
    stamp = time.strftime("%b %d %H:%M:%S %Y GMT", time.gmtime(NOW + days_left * DAY + 60))
    return {"notAfter": stamp}


def _verify_error(code):
    exc = ssl.SSLCertVerificationError(1, "certificate verify failed")
    exc.verify_code = code
    return exc


@pytest.fixture
def listener_up(monkeypatch):
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls = []

    def fake(addr, timeout=None):
        assert timeout == rhs.CONNECT_TIMEOUT
        calls.append(addr)
        return _Conn()

    monkeypatch.setattr(socket, "create_connection", fake)
    return calls


# --- certificate thresholds -------------------------------------------------

@pytest.mark.parametrize("days,state", [(60, "ok"), (14, "ok"), (13, "warn"), (5, "warn"),
                                        (4, "critical"), (0, "critical")])
def test_certificate_thresholds(env, monkeypatch, days, state):
    monkeypatch.setattr(rhs, "_peer_certificate", lambda host, port: _cert(days))
    result = rhs.check_certificate("baihe.example.com", 443)
    assert result["state"] == state
    assert result["days_left"] == days


def test_certificate_past_expiry_is_critical(env, monkeypatch):
    monkeypatch.setattr(rhs, "_peer_certificate", lambda host, port: _cert(-3))
    result = rhs.check_certificate("baihe.example.com", 443)
    assert result == {"state": "critical", "days_left": 0, "message": result["message"]}
    assert "expired" in result["message"]


def test_expired_certificate_refused_by_verification_is_critical(env, monkeypatch):
    def refuse(host, port):
        raise _verify_error(10)
    monkeypatch.setattr(rhs, "_peer_certificate", refuse)
    result = rhs.check_certificate("baihe.example.com", 443)
    assert result["state"] == "critical" and result["days_left"] == 0
    assert "expired" in result["message"]


def test_untrusted_certificate_is_critical(env, monkeypatch):
    def refuse(host, port):
        raise _verify_error(62)   # host name mismatch
    monkeypatch.setattr(rhs, "_peer_certificate", refuse)
    result = rhs.check_certificate("baihe.example.com", 443)
    assert result["state"] == "critical" and result["days_left"] is None
    assert "not trusted" in result["message"]


def test_certificate_read_connects_to_local_caddy_with_sni_and_verification(env, monkeypatch):
    seen = {}

    class FakeRaw:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeTls(FakeRaw):
        def getpeercert(self):
            return _cert(40)

    class FakeCtx:
        def wrap_socket(self, raw, server_hostname=None):
            seen["sni"] = server_hostname
            return FakeTls()

    def fake_conn(addr, timeout=None):
        seen["addr"], seen["timeout"] = addr, timeout
        return FakeRaw()

    real_ctx = ssl.create_default_context()
    assert real_ctx.verify_mode == ssl.CERT_REQUIRED and real_ctx.check_hostname
    monkeypatch.setattr(ssl, "create_default_context", lambda: FakeCtx())
    monkeypatch.setattr(socket, "create_connection", fake_conn)
    assert rhs.check_certificate("baihe.example.com", 443)["state"] == "ok"
    assert seen == {"addr": ("127.0.0.1", 443), "timeout": rhs.CONNECT_TIMEOUT,
                    "sni": "baihe.example.com"}


def test_unreachable_caddy_and_listener_are_critical(env, monkeypatch):
    def refused(addr, timeout=None):
        assert timeout == rhs.CONNECT_TIMEOUT
        raise ConnectionRefusedError("[Errno 111] Connection refused")
    monkeypatch.setattr(socket, "create_connection", refused)
    snap = rhs.run_check(PUBLIC_URL, PORT)
    assert snap["certificate"]["state"] == "critical"
    assert snap["listener"]["state"] == "critical"
    assert snap["state"] == "critical"
    assert "Errno" not in str(snap)


def test_timeout_is_critical_not_a_crash(env, monkeypatch):
    def slow(addr, timeout=None):
        raise socket.timeout("timed out")
    monkeypatch.setattr(socket, "create_connection", slow)
    assert rhs.check_listener("127.0.0.1", PORT)["state"] == "critical"
    assert rhs.check_certificate("baihe.example.com", 443)["state"] == "critical"


# --- remote access off ------------------------------------------------------

@pytest.mark.parametrize("url,port", [("", 0), ("", PORT), (PUBLIC_URL, 0),
                                      ("http://baihe.example.com", PORT)])
def test_remote_access_off_makes_no_outbound_call(env, no_network, url, port):
    assert rhs.remote_access_enabled(url, port) is False
    snap = rhs.run_check(url, port)
    assert snap["state"] == "off" and snap["message"] == rhs.OFF_MESSAGE
    assert rhs.get_status(url, port)["state"] == "off"


def test_monitor_not_started_when_remote_access_is_off(env, no_network):
    assert background.start_remote_health_monitor(ApiSettings()) is False
    assert background._remote_health_poller is None


def test_monitor_not_started_without_sign_in_settings(env, no_network):
    settings = ApiSettings(public_url=PUBLIC_URL, household_port=PORT)
    assert background.start_remote_health_monitor(settings) is False
    assert background._remote_health_poller is None


def test_monitor_runs_the_check_when_on(env, monkeypatch):
    ran = threading.Event()
    calls = []

    def fake_run(url, port, host, stop=None):
        calls.append((url, port, host, isinstance(stop, threading.Event)))
        ran.set()
    monkeypatch.setattr(rhs, "run_check", fake_run)
    settings = ApiSettings(public_url=PUBLIC_URL, household_port=PORT,
                           google_client_id="cid", google_client_secret="s")
    try:
        assert background.start_remote_health_monitor(settings, interval=0.01, first=0.01)
        assert background.start_remote_health_monitor(settings) is False   # one at a time
        assert ran.wait(5)
    finally:
        background.stop_remote_health_monitor()
    assert background._remote_health_poller is None
    assert calls[0] == (PUBLIC_URL, PORT, "127.0.0.1", True)


# --- DDNS ---------------------------------------------------------------------

def test_ddns_not_configured_resolves_nothing(env, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("resolved"))
    result = rhs.check_ddns("baihe.example.com")
    assert result == {"state": "not_configured", "configured": False,
                      "message": result["message"]}


@pytest.mark.parametrize("resolved,state", [
    ({"93.184.216.34"}, "ok"),
    ({"93.184.216.34", "2606:4700::1111"}, "ok"),
    ({"1.1.1.1"}, "warn"),
    ({"2001:4860::1"}, "unknown"),         # no IPv4 record to compare an IPv4 address with
    ({"192.168.1.20"}, "unknown"),         # split-horizon DNS on the LAN
])
def test_ddns_compares_public_ip_with_the_name(env, monkeypatch, resolved, state):
    env.write_text(f"{rhs.IP_CHECK_ENV}={CHECK_URL}\n")
    monkeypatch.setattr(rhs, "_current_public_ip", lambda url: ipaddress.ip_address("93.184.216.34"))
    monkeypatch.setattr(rhs, "_resolve", lambda host: {ipaddress.ip_address(a) for a in resolved})
    result = rhs.check_ddns("baihe.example.com")
    assert result["state"] == state and result["configured"] is True


def test_ddns_name_not_resolving_is_critical(env, monkeypatch):
    env.write_text(f"{rhs.IP_CHECK_ENV}={CHECK_URL}\n")
    monkeypatch.setattr(rhs, "_current_public_ip", lambda url: ipaddress.ip_address("93.184.216.34"))
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("no such host")))
    assert rhs.check_ddns("baihe.example.com")["state"] == "critical"


def test_ddns_check_url_failure_is_unknown(env, monkeypatch):
    env.write_text(f"{rhs.IP_CHECK_ENV}=http://ip.example.net/\n")   # not https
    monkeypatch.setattr(rhs, "_resolve", lambda host: pytest.fail("resolved"))
    assert rhs.check_ddns("baihe.example.com")["state"] == "unknown"


def test_public_ip_read_is_pinned_capped_and_timed(env, monkeypatch):
    from services import metadata_service
    from lib import url_guard
    seen = {}

    class Raw:
        def read(self, n, decode_content=True):
            seen["n"] = n
            return b"93.184.216.34\n"

    class Resp:
        status_code = 200
        raw = Raw()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(url_guard, "resolve_public", lambda url: "8.8.8.8")
    monkeypatch.setattr(http, "pinned_get",
                        lambda url, ip, headers: seen.update(ip=ip) or Resp())
    assert rhs._current_public_ip(CHECK_URL) == ipaddress.ip_address("93.184.216.34")
    assert seen == {"ip": "8.8.8.8", "n": rhs.IP_CHECK_MAX_BYTES + 1, "closed": True}


# --- transitions and notifications ------------------------------------------

def test_alerts_once_per_transition_and_survive_a_restart(env, monkeypatch, listener_up):
    days = {"n": 40}
    monkeypatch.setattr(rhs, "_peer_certificate", lambda host, port: _cert(days["n"]))
    sent = []
    monkeypatch.setattr(ns, "notify_remote_access", lambda text: sent.append(text))

    def step(n):
        days["n"] = n
        return rhs.run_check(PUBLIC_URL, PORT)["state"]

    assert step(40) == "ok" and sent == []          # first result: nothing to report
    assert step(39) == "ok" and sent == []
    assert step(12) == "warn" and len(sent) == 1
    assert sent[0].startswith("Remote access problem: The certificate expires in 12 days")
    assert step(11) == "warn" and len(sent) == 1    # still the same problem
    assert step(3) == "critical" and len(sent) == 2  # got worse
    assert step(8) == "warn" and len(sent) == 2     # better, but not fixed
    assert step(8) == "warn" and len(sent) == 2
    assert step(80) == "ok" and len(sent) == 3
    assert sent[2] == "Remote access is working again"
    assert step(80) == "ok" and len(sent) == 3


def test_problem_saved_before_a_restart_is_not_alerted_again(env, monkeypatch, listener_up):
    monkeypatch.setattr(rhs, "_peer_certificate", lambda host, port: _cert(2))
    sent = []
    monkeypatch.setattr(ns, "notify_remote_access", lambda text: sent.append(text))
    rhs.run_check(PUBLIC_URL, PORT)
    first = rhs.get_status(PUBLIC_URL, PORT)
    monkeypatch.setattr(rhs, "_now", lambda: NOW + 3600)
    rhs.reset_for_tests()             # a restart: only app_settings remembers
    rhs.run_check(PUBLIC_URL, PORT)
    assert len(sent) == 1
    assert rhs.get_status(PUBLIC_URL, PORT)["since"] == first["since"] == NOW
    assert rhs.get_status(PUBLIC_URL, PORT)["checked_at"] == NOW + 3600


def test_notify_remote_access_goes_to_the_bell_and_the_remote_category(env, monkeypatch):
    env.write_text("BAIHE_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/"
                   "123456789012345678/SECRETtokenABCDEFGHIJKLMNOPQRSTUVWXYZ_-0123\n")
    timers = []

    class T:
        def cancel(self):
            pass
    monkeypatch.setattr(ns, "_schedule_flush", lambda: timers.append(1) or T())
    ns.notify_remote_access("Remote access problem: The certificate has expired")
    assert ns._pending == [("remote", "Remote access problem: The certificate has expired",
                            "remote")]
    assert [e["kind"] for e in ns.list_recent(None)] == ["remote"]
    member = {"user_id": 7, "is_admin": False, "is_local_owner": False}
    assert ns.list_recent(member) == []   # PC owner and admins only
    ns.reset_for_tests()
    ns.set_categories(remote=False)
    assert ns.get_status()["send_remote"] is False
    ns.notify_remote_access("Remote access is working again")
    assert ns._pending == [] and len(ns.list_recent(None)) == 1


# --- the route ----------------------------------------------------------------

def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def test_route_off_and_not_checked(env, no_network):
    off = _local(create_app(ApiSettings(serve_frontend=False))).get("/api/diagnostics/remote-health")
    assert off.status_code == 200 and off.json()["state"] == "off"
    app = create_app(ApiSettings(serve_frontend=False, public_url=PUBLIC_URL, household_port=PORT))
    r = _local(app).get("/api/diagnostics/remote-health")
    assert r.status_code == 200
    assert r.json()["state"] == "unknown" and r.json()["checked_at"] is None


def test_route_says_why_when_the_public_url_was_ignored(env, no_network):
    from api.api_config import load_settings
    settings = load_settings({"BAIHE_PUBLIC_URL": "http://bad.example/x",
                              "BAIHE_API_HOUSEHOLD_PORT": str(PORT)})
    assert settings.public_url == "" and "BAIHE_PUBLIC_URL" in settings.public_url_error
    r = _local(create_app(settings)).get("/api/diagnostics/remote-health")
    assert r.json()["state"] == "off"
    assert "BAIHE_PUBLIC_URL" in r.json()["message"] and "bad.example" not in r.text


def test_route_returns_states_without_secrets_hosts_or_paths(env, monkeypatch, listener_up):
    env.write_text(f"{rhs.IP_CHECK_ENV}={CHECK_URL}\n")
    monkeypatch.setattr(rhs, "_peer_certificate", lambda host, port: _cert(9))
    monkeypatch.setattr(rhs, "_current_public_ip", lambda url: ipaddress.ip_address("93.184.216.34"))
    monkeypatch.setattr(rhs, "_resolve", lambda host: {ipaddress.ip_address("1.1.1.1")})
    monkeypatch.setattr(ns, "notify_remote_access", lambda text: None)
    rhs.run_check(PUBLIC_URL, PORT)
    app = create_app(ApiSettings(serve_frontend=False, public_url=PUBLIC_URL, household_port=PORT))
    r = _local(app).get("/api/diagnostics/remote-health")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "warn"
    assert body["certificate"] == {"state": "warn", "days_left": 9,
                                   "message": body["certificate"]["message"]}
    assert body["ddns"]["state"] == "warn" and body["ddns"]["configured"] is True
    assert body["listener"]["state"] == "ok"
    assert body["checked_at"] == NOW and body["since"] == NOW
    for bit in ("SECRET-DDNS-TOKEN", "ip.example.net", "baihe.example.com", "93.184.216.34",
                "1.1.1.1", "http", str(env), "8610", "\\", "/"):
        assert bit not in r.text


def test_route_needs_admin_diagnostics_with_auth_on(env, no_network):
    from services import auth_service
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False, public_url=PUBLIC_URL,
                                 household_port=PORT))
    client = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
    assert client.get("/api/diagnostics/remote-health").status_code == 401
    user = auth_service.add_user("kid@example.com")
    session = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
    from api import auth as api_auth
    r = client.get("/api/diagnostics/remote-health",
                   headers={"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}"})
    assert r.status_code == 403


# --- bounded lookups, stop, unreadable or unsaveable state -----------------

def test_ip_check_host_lookup_is_bounded(env, monkeypatch):
    from lib import url_guard
    release = threading.Event()
    monkeypatch.setattr(rhs, "RESOLVE_TIMEOUT", 0.2)
    monkeypatch.setattr(url_guard, "resolve_public", lambda url: release.wait(10) and "8.8.8.8")
    env.write_text(f"{rhs.IP_CHECK_ENV}={CHECK_URL}\n")
    started = time.monotonic()
    try:
        assert rhs.check_ddns("baihe.example.com")["state"] == "unknown"
    finally:
        release.set()
    assert time.monotonic() - started < 3


def test_public_name_lookup_is_bounded(env, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(rhs, "RESOLVE_TIMEOUT", 0.2)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: release.wait(10) and [])
    started = time.monotonic()
    try:
        with pytest.raises(OSError):
            rhs._resolve("baihe.example.com")
    finally:
        release.set()
    assert time.monotonic() - started < 3


def _blocking_gather(monkeypatch):
    release = threading.Event()

    def gather(*a):
        release.wait(10)
        return {"certificate": rhs._check("critical", "x", days_left=None),
                "ddns": rhs._check("not_configured", "x", configured=False),
                "listener": rhs._check("critical", "x")}
    monkeypatch.setattr(rhs, "_gather", gather)
    return release


@pytest.fixture
def spies(monkeypatch):
    import db
    s = {"saved": [], "sent": []}
    real_set = db.set_app_setting

    def set_setting(key, value):
        if key == rhs.REMOTE_HEALTH_SETTING:
            s["saved"].append(value)
        return real_set(key, value)
    monkeypatch.setattr(db, "set_app_setting", set_setting)
    monkeypatch.setattr(ns, "notify_remote_access", lambda text: s["sent"].append(text))
    return s


def test_a_stopped_cycle_writes_and_sends_nothing(env, monkeypatch, spies):
    release = _blocking_gather(monkeypatch)
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    started = time.monotonic()
    try:
        assert rhs.run_check(PUBLIC_URL, PORT, stop=stop) is None
    finally:
        release.set()
    assert time.monotonic() - started < 3
    assert spies == {"saved": [], "sent": []}
    assert rhs.run_check(PUBLIC_URL, PORT, stop=stop) is None   # already stopped
    assert spies == {"saved": [], "sent": []}


def test_a_cycle_past_its_deadline_writes_and_sends_nothing(env, monkeypatch, spies):
    release = _blocking_gather(monkeypatch)
    monkeypatch.setattr(rhs, "CYCLE_DEADLINE", 0.2)
    try:
        assert rhs.run_check(PUBLIC_URL, PORT) is None
    finally:
        release.set()
    assert spies == {"saved": [], "sent": []}


def test_monitor_stop_ends_a_running_cycle_within_the_join(env, monkeypatch, spies):
    release = _blocking_gather(monkeypatch)
    entered = threading.Event()
    real_bounded = rhs._bounded

    def bounded(fn, timeout, stop=None):
        entered.set()
        return real_bounded(fn, timeout, stop)
    monkeypatch.setattr(rhs, "_bounded", bounded)
    settings = ApiSettings(public_url=PUBLIC_URL, household_port=PORT,
                           google_client_id="cid", google_client_secret="s")
    try:
        assert background.start_remote_health_monitor(settings, interval=0.01, first=0.01)
        thread = background._remote_health_poller[0]
        assert entered.wait(5)
        background.stop_remote_health_monitor(timeout=2)
        assert not thread.is_alive()
    finally:
        release.set()
        background.stop_remote_health_monitor()
    assert spies == {"saved": [], "sent": []}


def test_unreadable_saved_state_skips_the_alert_and_keeps_it(env, monkeypatch, listener_up, spies):
    import db
    monkeypatch.setattr(rhs, "_peer_certificate", lambda host, port: _cert(2))
    real_get = db.get_app_setting

    def broken(key, default=None):
        if key == rhs.REMOTE_HEALTH_SETTING:
            raise RuntimeError("database is locked")
        return real_get(key, default)
    monkeypatch.setattr(db, "get_app_setting", broken)
    snap = rhs.run_check(PUBLIC_URL, PORT)
    assert snap["state"] == "critical"
    assert spies == {"saved": [], "sent": []}
    monkeypatch.setattr(db, "get_app_setting", real_get)
    rhs.run_check(PUBLIC_URL, PORT)       # readable again: now it is a real change
    assert len(spies["sent"]) == 1 and len(spies["saved"]) == 1


def test_a_failing_save_does_not_realert_every_cycle(env, monkeypatch, listener_up):
    import db
    monkeypatch.setattr(rhs, "_peer_certificate", lambda host, port: _cert(9))
    sent = []
    monkeypatch.setattr(ns, "notify_remote_access", lambda text: sent.append(text))
    real_set = db.set_app_setting

    def broken(key, value):
        if key == rhs.REMOTE_HEALTH_SETTING:
            raise RuntimeError("disk full")
        return real_set(key, value)
    monkeypatch.setattr(db, "set_app_setting", broken)
    for _ in range(3):
        assert rhs.run_check(PUBLIC_URL, PORT)["state"] == "warn"
    assert len(sent) == 1


# --- the public-address check setting (Settings, PC only) ------------------

@pytest.fixture
def public_dns(monkeypatch):
    from lib import url_guard

    def fake(url):
        host = url.split("/")[2].split(":")[0]
        if host in ("ip.example.net", "api.ipify.org"):
            return "8.8.8.8"
        if host == "router.lan":
            raise url_guard.UnsafeURLError(url_guard.NOT_PUBLIC)
        raise url_guard.URLResolveError(url_guard.RESOLVE_FAILED)
    monkeypatch.setattr(url_guard, "resolve_public", fake)


@pytest.mark.parametrize("value", [
    "http://ip.example.net/", "ftp://ip.example.net/", "https://user:pw@ip.example.net/",
    "https://ip.example.net/" + "a" * 600, "https://ip.example.net/ x", 'https://ip.example.net/"',
    "https://router.lan/", "https://nowhere.invalid/", "https://ip.example.net:99999/", "",
    "https:///nohost", 42,
])
def test_ip_check_address_validation(env, public_dns, value):
    from services.service_errors import InvalidInputError
    with pytest.raises(InvalidInputError) as exc:
        rhs.set_ip_check_url(value)
    assert "ip.example" not in str(exc.value) and "router" not in str(exc.value)
    assert rhs.IP_CHECK_ENV not in env.read_text()


def test_ip_check_set_and_clear_round_trip_and_monitor_picks_it_up(env, public_dns, monkeypatch):
    assert rhs.ip_check_status() == {"configured": False}
    assert rhs.set_ip_check_url(f"  {CHECK_URL} ") == {"configured": True}
    assert env.read_text() == f"{rhs.IP_CHECK_ENV}={CHECK_URL}\n"
    # The next cycle uses it, no restart.
    monkeypatch.setattr(rhs, "_current_public_ip", lambda url: ipaddress.ip_address("93.184.216.34"))
    monkeypatch.setattr(rhs, "_resolve", lambda host: {ipaddress.ip_address("93.184.216.34")})
    assert rhs.check_ddns("baihe.example.com")["state"] == "ok"
    assert rhs.clear_ip_check_url() == {"configured": False}
    assert rhs.check_ddns("baihe.example.com")["state"] == "not_configured"


def _write_client(**kw):
    return _local(create_app(ApiSettings(serve_frontend=False, allow_key_writes=True, **kw)))


def test_ip_check_routes_never_return_the_address(env, public_dns, monkeypatch):
    c = _write_client(public_url=PUBLIC_URL, household_port=PORT)
    r = c.post("/api/diagnostics/remote-health/ip-check", json={"value": CHECK_URL, "confirm": True})
    assert r.status_code == 200 and r.json() == {"configured": True}
    assert c.get("/api/diagnostics/remote-health/ip-check").json() == {"configured": True}
    monkeypatch.setattr(rhs, "_current_public_ip", lambda url: ipaddress.ip_address("93.184.216.34"))
    monkeypatch.setattr(rhs, "_resolve", lambda host: {ipaddress.ip_address("1.1.1.1")})
    r = c.post("/api/diagnostics/remote-health/ip-check/test", json={})
    assert r.status_code == 200
    assert r.json()["state"] == "warn" and r.json()["configured"] is True
    for text in (r.text, c.get("/api/diagnostics/remote-health").text):
        for bit in ("SECRET-DDNS-TOKEN", "ip.example.net", "93.184.216.34", "1.1.1.1",
                    "baihe.example.com", "http"):
            assert bit not in text
    # Test saves nothing and alerts nobody.
    assert rhs.get_status(PUBLIC_URL, PORT)["checked_at"] is None
    assert c.post("/api/diagnostics/remote-health/ip-check/test", json={}).status_code == 429
    r = c.post("/api/diagnostics/remote-health/ip-check/clear", json={"confirm": True})
    assert r.json() == {"configured": False}


def test_ip_check_test_with_remote_access_off_reads_the_address_only(env, public_dns, monkeypatch):
    c = _write_client()
    assert c.post("/api/diagnostics/remote-health/ip-check/test", json={}).status_code == 422
    env.write_text(f"{rhs.IP_CHECK_ENV}={CHECK_URL}\n")
    monkeypatch.setattr(rhs, "_current_public_ip", lambda url: ipaddress.ip_address("93.184.216.34"))
    monkeypatch.setattr(rhs, "_resolve", lambda host: pytest.fail("no public name to resolve"))
    r = c.post("/api/diagnostics/remote-health/ip-check/test", json={})
    assert r.status_code == 200 and r.json()["state"] == "ok"
    rhs.reset_for_tests()
    monkeypatch.setattr(rhs, "_current_public_ip",
                        lambda url: (_ for _ in ()).throw(OSError("unreachable " + url)))
    r = c.post("/api/diagnostics/remote-health/ip-check/test", json={})
    assert r.json()["state"] == "unknown" and "ip.example" not in r.text


def test_ip_check_writes_need_confirm_and_key_writes(env, public_dns):
    c = _write_client()
    r = c.post("/api/diagnostics/remote-health/ip-check", json={"value": CHECK_URL})
    assert r.status_code == 422 and "ip.example" not in r.text
    off = _local(create_app(ApiSettings(serve_frontend=False)))
    assert off.post("/api/diagnostics/remote-health/ip-check",
                    json={"value": CHECK_URL, "confirm": True}).status_code == 403
    assert off.post("/api/diagnostics/remote-health/ip-check/clear",
                    json={"confirm": True}).status_code == 403
    r = c.post("/api/diagnostics/remote-health/ip-check",
               json={"value": "https://router.lan/?k=SECRET", "confirm": True})
    assert r.status_code == 422 and "SECRET" not in r.text and "router" not in r.text
    assert rhs.IP_CHECK_ENV not in env.read_text()


def test_ip_check_routes_are_pc_only(env, public_dns):
    from services import auth_service
    from api import auth as api_auth
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False, allow_key_writes=True))
    remote = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
    admin = auth_service.grant_admin_local("admin@example.com")
    session = auth_service.create_session(admin["id"], "pytest", "203.0.113.9")
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
         api_auth.CSRF_HEADER: session["csrf_token"]}
    assert remote.get("/api/diagnostics/remote-health/ip-check", headers=h).status_code == 403
    for path, body in (("", {"value": CHECK_URL, "confirm": True}), ("/clear", {"confirm": True}),
                       ("/test", {})):
        r = remote.post(f"/api/diagnostics/remote-health/ip-check{path}", json=body, headers=h)
        assert r.status_code == 403, path
    assert rhs.IP_CHECK_ENV not in env.read_text()


def test_ip_check_set_and_clear_run_off_the_event_loop(env, monkeypatch):
    import asyncio
    seen = []

    def on_loop():
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    monkeypatch.setattr(rhs, "set_ip_check_url",
                        lambda value: seen.append(on_loop()) or {"configured": True})
    monkeypatch.setattr(rhs, "clear_ip_check_url",
                        lambda: seen.append(on_loop()) or {"configured": False})
    c = _write_client()
    assert c.post("/api/diagnostics/remote-health/ip-check",
                  json={"value": CHECK_URL, "confirm": True}).status_code == 200
    assert c.post("/api/diagnostics/remote-health/ip-check/clear",
                  json={"confirm": True}).status_code == 200
    assert seen == [False, False]


def test_a_stuck_test_worker_refuses_the_next_test_until_it_ends(env, monkeypatch):
    from services.service_errors import RateLimitedError
    env.write_text(f"{rhs.IP_CHECK_ENV}={CHECK_URL}\n")
    release = threading.Event()
    ended = threading.Event()

    def stuck(url):
        try:
            release.wait(10)
            return ipaddress.ip_address("93.184.216.34")
        finally:
            ended.set()
    monkeypatch.setattr(rhs, "_current_public_ip", stuck)
    monkeypatch.setattr(rhs, "CYCLE_DEADLINE", 0.2)
    monkeypatch.setattr(rhs, "TEST_MIN_INTERVAL", 0.0)
    try:
        assert rhs.run_ip_check_test("")["state"] == "unknown"   # gave up; worker still alive
        with pytest.raises(RateLimitedError):
            rhs.run_ip_check_test("")
    finally:
        release.set()
    assert ended.wait(5)
    for _ in range(50):          # the worker's finally runs right after `ended`
        if rhs._test_workers == 0:
            break
        time.sleep(0.02)
    assert rhs.run_ip_check_test("")["state"] == "ok"


def test_stop_set_just_before_the_save_skips_it(env, monkeypatch, listener_up, spies):
    monkeypatch.setattr(rhs, "_peer_certificate", lambda host, port: _cert(2))
    stop = threading.Event()
    real_now = rhs._now

    def now_then_stop():
        stop.set()               # requested after the checks, before the save
        return real_now()
    monkeypatch.setattr(rhs, "_now", now_then_stop)
    assert rhs.run_check(PUBLIC_URL, PORT, stop=stop) is None
    assert spies == {"saved": [], "sent": []}
