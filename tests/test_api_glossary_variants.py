"""The two read-only glossary spelling routes: permission, shapes, errors. Offline."""
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
    did = db.create_drama(title_en="T", series_id=sid, source_language="ko")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="Yaloch said hi", en=""),
                        Line(idx=1, start=1, end=2, zh="then Yalou left", en="")])
    return did


_emails = itertools.count()


def _session(*permissions, keep=()):
    user = auth_service.add_user(f"kid{next(_emails)}@example.com")
    # New users start with the household defaults; the 403 cases need them gone.
    for gone in ("library.read", "lines.read"):
        if gone not in keep:
            auth_service.revoke_permission(user["id"], gone)
    for p in permissions:
        auth_service.grant_permission(user["id"], p)
    s = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}"}


@pytest.fixture
def remote(isolated_db):
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
    return TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)


def test_reading_the_lines_permission_is_enough(remote):
    did = _title()
    url = f"/api/glossary/dramas/{did}/variants?term=Yalo"
    assert remote.get(url).status_code == 401
    assert remote.get(url, headers=_session()).status_code == 403
    headers = _session("lines.read")
    body = remote.get(url, headers=headers).json()
    assert body["available"] is True
    assert {c["text"] for c in body["candidates"]} == {"Yaloch", "Yalou"}
    assert set(body["candidates"][0]) == {"text", "count", "score", "is_alias", "is_term", "examples"}
    clusters = remote.get(f"/api/glossary/dramas/{did}/name-clusters", headers=headers)
    assert clusters.status_code == 200 and set(clusters.json()) == {
        "available", "reason", "lines_scanned", "truncated", "clusters"}


def test_snippets_are_line_text_so_library_read_alone_is_refused(remote):
    # Both routes return source line text, which every other route gates with lines.read.
    did = _title()
    headers = _session(keep=("library.read",))
    assert remote.get(f"/api/glossary/dramas/{did}/variants?term=Yalo", headers=headers).status_code == 403
    assert remote.get(f"/api/glossary/dramas/{did}/name-clusters", headers=headers).status_code == 403


def test_clusters_need_the_same_permission(remote):
    did = _title()
    assert remote.get(f"/api/glossary/dramas/{did}/name-clusters", headers=_session()).status_code == 403


def test_errors_use_the_standard_shape(remote):
    headers = _session("lines.read")
    did = _title()
    assert remote.get("/api/glossary/dramas/9999/variants?term=Yalo", headers=headers).status_code == 404
    assert remote.get("/api/glossary/dramas/9999/name-clusters", headers=headers).status_code == 404
    assert remote.get(f"/api/glossary/dramas/{did}/variants", headers=headers).status_code == 422
    assert remote.get(f"/api/glossary/dramas/{did}/variants?term=", headers=headers).status_code == 422
    assert remote.get(f"/api/glossary/dramas/{did}/variants?term=!!!", headers=headers).status_code == 422
    assert remote.get(f"/api/glossary/dramas/{did}/variants?term={'x' * 201}",
                      headers=headers).status_code == 422
