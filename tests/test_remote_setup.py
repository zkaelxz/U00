"""Remote-access setup status (services/remote_setup_service.py and
GET /api/diagnostics/remote-setup). Settings are built directly; the one
loopback connect is faked."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from api.api_config import ApiSettings
from api.server import create_app
from services import remote_health_service as rhs
from services import remote_setup_service as rss
from tests.api_helpers import loopback_client

SECRET = "GOCSPX-never-shown"
_REAL_FIREWALL_RULE_VISIBLE = rss._firewall_rule_visible


def _settings(**kw):
    base = dict(public_url="", household_port=0, google_client_id="", google_client_secret="")
    base.update(kw)
    return ApiSettings(**base)


def _states(status):
    return {s["id"]: s["state"] for s in status["steps"]}


@pytest.fixture(autouse=True)
def _network(monkeypatch):
    """The name resolves to a public address; no address check is set."""
    import ipaddress
    monkeypatch.setattr(rhs, "_resolve", lambda host, stop=None: {ipaddress.ip_address("93.184.216.34")})
    monkeypatch.setattr(rhs, "_ip_check_url", lambda env_path=None: "")
    monkeypatch.setattr(rss, "_firewall_rule_visible", lambda: None)


@pytest.fixture(autouse=True)
def _listener(monkeypatch):
    monkeypatch.setattr(rhs, "check_listener",
                        lambda host, port: {"state": "ok", "message": "The household listener is answering."})


def test_nothing_set_up_points_at_the_name_first(isolated_db):
    status = rss.get_setup_status(_settings())
    assert _states(status)["name"] == "todo"
    assert _states(status)["turn_on"] == "todo"
    assert status["next"] == "name"


def test_half_a_google_client_is_a_problem(isolated_db):
    status = rss.get_setup_status(_settings(google_client_id="id"))
    assert _states(status)["google"] == "problem"
    full = rss.get_setup_status(_settings(google_client_id="id", google_client_secret=SECRET))
    assert _states(full)["google"] == "done"


def test_invalid_public_url_is_a_problem_with_the_fixed_reason(isolated_db):
    status = rss.get_setup_status(_settings(public_url_error="BAIHE_PUBLIC_URL must be just https://your-domain."))
    step = next(s for s in status["steps"] if s["id"] == "name")
    assert step["state"] == "problem" and "just https://your-domain" in step["message"]


def test_people_step_counts_active_admins_and_members(isolated_db):
    assert _states(rss.get_setup_status(_settings()))["people"] == "todo"
    db.auth_create_user("owner@example.com", is_admin=True)
    solo = next(s for s in rss.get_setup_status(_settings())["steps"] if s["id"] == "people")
    assert solo["state"] == "done" and "only person" in solo["message"]
    db.auth_create_user("kid@example.com")
    both = next(s for s in rss.get_setup_status(_settings())["steps"] if s["id"] == "people")
    assert "1 member" in both["message"]


def test_listener_that_does_not_answer_is_a_problem(isolated_db, monkeypatch):
    monkeypatch.setattr(rhs, "check_listener",
                        lambda host, port: {"state": "critical", "message": "The household listener is not answering on this PC."})
    on = _settings(public_url="https://baihe.example.com", household_port=8610)
    assert _states(rss.get_setup_status(on))["turn_on"] == "problem"


def test_router_and_firewall_are_manual_and_everything_done_leaves_them_next(isolated_db):
    db.auth_create_user("owner@example.com", is_admin=True)
    on = _settings(public_url="https://baihe.example.com", household_port=8610,
                   google_client_id="id", google_client_secret=SECRET)
    status = rss.get_setup_status(on)
    assert _states(status)["firewall"] == "manual" and _states(status)["router"] == "manual"
    assert status["next"] == "firewall"


def test_route_is_pc_only_and_leaks_nothing(isolated_db):
    settings = _settings(public_url="https://baihe.example.com", household_port=8610,
                         google_client_id="client-id-123", google_client_secret=SECRET)
    client = loopback_client(create_app(settings))
    body = client.get("/api/diagnostics/remote-setup")
    assert body.status_code == 200
    text = body.text
    for private in (SECRET, "client-id-123", "baihe.example.com"):
        assert private not in text


# --- saving the sign-in settings -------------------------------------------

GOOD_ID = "123-abc.apps.googleusercontent.com"
GOOD_SECRET = "GOCSPX-abcdefghij123456"


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("KEEP=1\n")
    return str(path)


def test_save_writes_all_three_and_asks_for_a_restart(isolated_db, env_file):
    status = rss.save_sign_in(_settings(), "Baihe.Example.com", GOOD_ID, GOOD_SECRET, env_file)
    text = open(env_file).read()
    assert "BAIHE_PUBLIC_URL=https://baihe.example.com\n" in text
    assert f"BAIHE_GOOGLE_CLIENT_ID={GOOD_ID}\n" in text and "KEEP=1" in text
    assert status["restart_needed"] is True
    assert status["fields"] == {"public_url": True, "google_client_id": True, "google_client_secret": True}
    assert GOOD_SECRET not in str(status)


def test_no_restart_needed_once_the_values_are_loaded(isolated_db, env_file):
    rss.save_sign_in(_settings(), "baihe.example.com", GOOD_ID, GOOD_SECRET, env_file)
    loaded = _settings(public_url="https://baihe.example.com", google_client_id=GOOD_ID,
                       google_client_secret=GOOD_SECRET)
    assert rss.get_setup_status(loaded, env_file)["restart_needed"] is False


@pytest.mark.parametrize("name", ["192.168.1.5", "localhost", "https://baihe.example.com:8443",
                                  "https://baihe.example.com/path", "nodots"])
def test_bad_public_names_are_refused_and_nothing_is_written(isolated_db, env_file, name):
    with pytest.raises(rss.InvalidInputError):
        rss.save_sign_in(_settings(), name, GOOD_ID, GOOD_SECRET, env_file)
    assert open(env_file).read() == "KEEP=1\n"


def test_id_and_secret_in_the_wrong_boxes_are_refused_without_echo(isolated_db, env_file):
    with pytest.raises(rss.InvalidInputError) as swapped:
        rss.save_sign_in(_settings(), None, GOOD_SECRET, None, env_file)
    assert GOOD_SECRET not in str(swapped.value)
    with pytest.raises(rss.InvalidInputError) as other:
        rss.save_sign_in(_settings(), None, None, GOOD_ID, env_file)
    assert GOOD_ID not in str(other.value)
    with pytest.raises(rss.InvalidInputError):
        rss.save_sign_in(_settings(), None, None, "has space 12345678", env_file)


def test_one_bad_field_writes_none_of_them(isolated_db, env_file):
    with pytest.raises(rss.InvalidInputError):
        rss.save_sign_in(_settings(), "baihe.example.com", "wrong", GOOD_SECRET, env_file)
    assert open(env_file).read() == "KEEP=1\n"


def test_sign_in_route_needs_key_writes_and_confirm(isolated_db, tmp_path, monkeypatch):
    from services import settings_service
    path = tmp_path / ".env"
    path.write_text("")
    monkeypatch.setattr(settings_service, "default_env_path", lambda: str(path))
    body = {"public_url": "baihe.example.com", "google_client_id": GOOD_ID,
            "google_client_secret": GOOD_SECRET, "confirm": True}
    off = loopback_client(create_app(_settings()))
    assert off.post("/api/diagnostics/remote-setup/sign-in", json=body).status_code == 403
    client = loopback_client(create_app(_settings(allow_key_writes=True)))
    assert client.post("/api/diagnostics/remote-setup/sign-in", json={**body, "confirm": False}).status_code == 422
    ok = client.post("/api/diagnostics/remote-setup/sign-in", json=body)
    assert ok.status_code == 200 and GOOD_SECRET not in ok.text and GOOD_ID not in ok.text
    assert ok.json()["restart_needed"] is True
    bad = client.post("/api/diagnostics/remote-setup/sign-in", json={"public_url": "10.0.0.1", "confirm": True})
    assert bad.status_code == 422 and "10.0.0.1" not in bad.text


# --- DNS and firewall ---------------------------------------------------------

def _step_of(status, step_id):
    return next(s for s in status["steps"] if s["id"] == step_id)


def test_a_name_that_does_not_resolve_is_a_problem(isolated_db, monkeypatch):
    def fail(host, stop=None):
        raise OSError("not resolved")
    monkeypatch.setattr(rhs, "_resolve", fail)
    step = _step_of(rss.get_setup_status(_settings(public_url="https://baihe.example.com")), "name")
    assert step["state"] == "problem" and "doesn't resolve" in step["message"]
    assert "baihe.example.com" not in step["message"]


def test_a_name_that_resolves_to_a_private_address_is_a_problem(isolated_db, monkeypatch):
    import ipaddress
    monkeypatch.setattr(rhs, "_resolve", lambda host, stop=None: {ipaddress.ip_address("192.168.1.9")})
    step = _step_of(rss.get_setup_status(_settings(public_url="https://baihe.example.com")), "name")
    assert step["state"] == "problem" and "private address" in step["message"]


def test_a_resolving_name_is_done_and_a_stale_record_is_a_problem(isolated_db, monkeypatch):
    on = _settings(public_url="https://baihe.example.com")
    assert _step_of(rss.get_setup_status(on), "name")["state"] == "done"
    monkeypatch.setattr(rhs, "check_ddns", lambda host: {"state": "warn", "message": "The public name points to an old address. Check the dynamic DNS updater."})
    stale = _step_of(rss.get_setup_status(on), "name")
    assert stale["state"] == "problem" and "old address" in stale["message"]


@pytest.mark.parametrize("seen,state", [(True, "done"), (False, "todo"), (None, "manual")])
def test_firewall_step_follows_the_rule_once_remote_access_is_on(isolated_db, monkeypatch, seen, state):
    monkeypatch.setattr(rss, "_firewall_rule_visible", lambda: seen)
    on = _settings(public_url="https://baihe.example.com", household_port=8610)
    assert _step_of(rss.get_setup_status(on), "firewall")["state"] == state
    off = _settings(public_url="https://baihe.example.com")
    assert _step_of(rss.get_setup_status(off), "firewall")["state"] == "manual"


def test_firewall_rule_is_only_asked_for_with_a_fixed_read_only_command(monkeypatch):
    seen = {}

    class Run:
        returncode, timed_out = 1, False

    def fake(cmd, timeout):
        seen["cmd"] = cmd
        return Run()

    monkeypatch.setattr(rss.sys, "platform", "win32")
    monkeypatch.setattr(rss.proc_run, "run_captured", fake)
    assert _REAL_FIREWALL_RULE_VISIBLE() is False
    assert seen["cmd"][1:5] == ["advfirewall", "firewall", "show", "rule"]
    assert "add" not in seen["cmd"] and "delete" not in seen["cmd"]
    monkeypatch.setattr(rss.sys, "platform", "linux")
    assert _REAL_FIREWALL_RULE_VISIBLE() is None
