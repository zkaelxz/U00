"""The address-term routes: permissions, shapes, errors. Offline."""
import itertools

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service


def _title():
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="zh")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=t, en="") for i, t in enumerate(["姐姐好", "姐姐呢"])])
    return did


_emails = itertools.count()


def _session(*permissions):
    user = auth_service.add_user(f"kid{next(_emails)}@example.com")
    for gone in ("library.read", "lines.read", "lines.edit"):
        auth_service.revoke_permission(user["id"], gone)
    for p in permissions:
        auth_service.grant_permission(user["id"], p)
    s = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


@pytest.fixture
def remote(isolated_db):
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
    return TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)


def test_scan_needs_lines_read(remote):
    url = f"/api/glossary/dramas/{_title()}/address-terms"
    assert remote.get(url).status_code == 401
    assert remote.get(url, headers=_session("library.read")).status_code == 403
    body = remote.get(url, headers=_session("lines.read")).json()
    assert body["terms"][0]["term"] == "姐姐" and body["terms"][0]["count"] == 2
    assert set(body["terms"][0]) == {"term", "count", "examples"}


def test_save_needs_lines_edit_and_adds_the_term(remote):
    did = _title()
    url = f"/api/glossary/dramas/{did}/address-terms"
    payload = {"terms": [{"term": "姐姐", "translation": "big sister"}]}
    assert remote.post(url, json=payload, headers=_session("lines.read")).status_code == 403
    r = remote.post(url, json=payload, headers=_session("lines.edit"))
    assert r.status_code == 200 and r.json() == {"saved": ["姐姐"], "skipped_existing": []}
    assert db.list_glossary_terms(db.get_drama(did)["series_id"])[0]["category"] == "honorific"


def test_save_rejects_an_unknown_term(remote):
    url = f"/api/glossary/dramas/{_title()}/address-terms"
    r = remote.post(url, json={"terms": [{"term": "さん", "translation": "san"}]}, headers=_session("lines.edit"))
    assert r.status_code == 422
