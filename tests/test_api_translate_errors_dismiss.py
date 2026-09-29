"""Parity X01: dismiss the last translate run's failed-batch notice
(services/translate_run_service.dismiss_translate_errors, route in
api/routers/translate_run_routes.py)."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import core
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, translate_run_service
from services.service_errors import NotFoundError

URL = "/api/translate-run/dramas/{}/errors/dismiss"
CONFIG = "/api/translate-run/dramas/{}/config"
ERRORS = [{"batch_index": 0, "lines": [0, 2], "error": "timeout"}]


def _drama_with_errors():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [core.Line(idx=i, start=i, end=i + 1, zh=f"z{i}", en="e" if i == 1 else "")
                        for i in range(3)])
    db.update_drama(did, last_translate_errors=json.dumps(ERRORS))
    return did


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})  # as the React client sends


def test_service_clears_only_the_errors(isolated_db, monkeypatch):
    monkeypatch.setattr(background_jobs, "start_job",
                        lambda *a, **k: pytest.fail("dismissing must not start a job"))
    did = _drama_with_errors()
    before = [dict(r) for r in db.load_lines(did)]
    assert translate_run_service.dismiss_translate_errors(did) == {"drama_id": did, "dismissed": True}
    assert db.get_drama(did)["last_translate_errors"] is None
    assert [dict(r) for r in db.load_lines(did)] == before
    # Nothing left: a second dismiss is a no-op.
    assert translate_run_service.dismiss_translate_errors(did) == {"drama_id": did, "dismissed": False}


def test_service_unknown_drama(isolated_db):
    with pytest.raises(NotFoundError):
        translate_run_service.dismiss_translate_errors(9999)


def test_api_dismiss_round_trip(client):
    did = _drama_with_errors()
    assert client.get(CONFIG.format(did)).json()["last_translate_errors"] == ERRORS
    r = client.post(URL.format(did))
    assert r.status_code == 200 and r.json() == {"drama_id": did, "dismissed": True}
    assert client.get(CONFIG.format(did)).json()["last_translate_errors"] is None


def test_api_dismiss_errors(client):
    assert client.post(URL.format(9999)).status_code == 404
    assert client.post(URL.format(0)).status_code == 422
    assert client.get(URL.format(1)).status_code == 405


def test_api_dismiss_needs_lines_edit(isolated_db):
    app = create_app(ApiSettings(auth_mode="on"))
    c = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
    did = _drama_with_errors()

    u = auth_service.add_user("kid@example.com")   # household defaults include lines.edit
    auth_service.revoke_permission(u["id"], "lines.edit")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}

    assert c.post(URL.format(did), headers=h).status_code == 403
    assert db.get_drama(did)["last_translate_errors"] is not None
    auth_service.grant_permission(u["id"], "lines.edit")
    assert c.post(URL.format(did), headers=h).status_code == 200
    assert db.get_drama(did)["last_translate_errors"] is None
