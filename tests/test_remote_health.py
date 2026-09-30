"""Remote-access health (services/remote_health_service.py, the
GET /api/diagnostics/remote-health route and the api/background.py monitor).
Sockets, DNS, HTTP and the clock are faked: no network."""
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
    monkeypatch.setattr(settings_service, "_default_env_path", lambda: str(path))
    for name in (rhs.IP_CHECK_ENV, ns.DISABLED_ENV, *ns.ENV_VARS["discord"], *ns.ENV_VARS["ntfy"]):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rhs, "_now", lambda: NOW)
    ns.reset_for_tests()
    yield path
    ns.reset_for_tests()


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


def test_monitor_runs_the_check_when_on(env, monkeypatch):
    ran = threading.Event()
    calls = []

    def fake_run(url, port, host):
        calls.append((url, port, host))
        ran.set()
    monkeypatch.setattr(rhs, "run_check", fake_run)
    settings = ApiSettings(public_url=PUBLIC_URL, household_port=PORT)
    try:
        assert background.start_remote_health_monitor(settings, interval=0.01, first=0.01)
        assert background.start_remote_health_monitor(settings) is False   # one at a time
        assert ran.wait(5)
    finally:
        background.stop_remote_health_monitor()
    assert background._remote_health_poller is None
    assert calls[0] == (PUBLIC_URL, PORT, "127.0.0.1")


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
    ({"2001:4860::1"}, "warn"),            # no IPv4 record for an IPv4 address
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
    from services import metadata_service, url_guard
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
    monkeypatch.setattr(metadata_service, "_pinned_get",
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
    rhs.run_check(PUBLIC_URL, PORT)   # the stored state is read back from app_settings
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
