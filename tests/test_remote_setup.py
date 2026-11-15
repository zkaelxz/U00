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


def _settings(**kw):
    base = dict(public_url="", household_port=0, google_client_id="", google_client_secret="")
    base.update(kw)
    return ApiSettings(**base)


def _states(status):
    return {s["id"]: s["state"] for s in status["steps"]}


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
