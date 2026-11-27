"""Preferences an account keeps on the server (`/api/auth/preferences`, services/account_prefs_service.py)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api import auth as api_auth
from services import account_prefs_service, auth_service
from tests.test_api_device_sessions import _app, _h, _remote

URL = "/api/auth/preferences"


def _session(email):
    user = auth_service.add_user(email)
    return user, auth_service.create_session(user["id"], "Mozilla/5.0", "203.0.113.77")


def test_routes_are_authenticated_only(isolated_db):
    decls = {(m, p): d for _r, p, ms, d in api_auth.iter_route_declarations(_app())
             for m in ms if p == URL}
    assert set(decls) == {("GET", URL), ("PUT", URL)}
    assert all(d == [("authenticated", None)] for d in decls.values())


def test_a_choice_follows_the_account_to_another_device_and_not_to_another_account(isolated_db):
    c = _remote(_app())
    _a, laptop = _session("a@example.com")
    assert c.get(URL, headers=_h(laptop)).json() == {}
    assert c.put(URL, json={"theme": "sepia"}, headers=_h(laptop)).json() == {"theme": "sepia"}
    # The same account on a second device reads the same choice.
    phone = auth_service.create_session(_a["id"], "Mozilla/5.0 (iPhone)", "203.0.113.78")
    assert c.get(URL, headers=_h(phone)).json() == {"theme": "sepia"}
    _b, other = _session("b@example.com")
    assert c.get(URL, headers=_h(other)).json() == {}


def test_a_value_the_app_does_not_know_is_refused_and_nothing_is_stored(isolated_db):
    c = _remote(_app())
    user, s = _session("a@example.com")
    assert c.put(URL, json={"theme": "neon"}, headers=_h(s)).status_code == 422
    assert c.put(URL, json={"language": "fr"}, headers=_h(s)).status_code == 422
    assert account_prefs_service.get(user["id"]) == {}


def test_anonymous_is_401_and_auth_off_is_404(isolated_db):
    assert _remote(_app()).get(URL).status_code == 401
    local = TestClient(_app("off"), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000), raise_server_exceptions=False)
    assert local.get(URL).status_code == 404
