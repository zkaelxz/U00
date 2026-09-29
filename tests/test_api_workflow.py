"""GET /api/workflow/dramas/{id}/progress (API batch 1). isolated_db, no network."""
import os

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


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _url(did):
    return f"/api/workflow/dramas/{did}/progress"


def _states(body):
    return {s["key"]: s["state"] for s in body["stages"]}


def _no_leak(r):
    assert db.LIBRARY_DIR not in r.text and "dub_track.wav" not in r.text
    assert "audio.wav" not in r.text


def test_empty_drama_source_current_rest_blocked(client):
    did = db.create_drama(title_en="D")
    r = client.get(_url(did))
    assert r.status_code == 200
    b = r.json()
    assert b["stage_index"] == 0 and b["stage"] == "source" and b["line_count"] == 0
    assert _states(b) == {"source": "current", "translate": "blocked", "review": "blocked",
                          "dub": "blocked", "export": "blocked"}
    assert not os.path.exists(os.path.join(db.DRAMAS_DIR, str(did)))  # read never creates


def test_has_audio_no_lines_is_transcript_step(client):
    did = db.create_drama(title_en="D", audio_filename="audio.wav")
    b = client.get(_url(did)).json()
    assert b["stage_index"] == 1 and b["stage"] == "source" and b["has_audio"] is True
    _no_leak(client.get(_url(did)))


def test_translate_current_with_counts(client):
    did = db.create_drama(title_en="D", audio_filename="audio.wav")
    lines = [Line(idx=i, start=i, end=i + 0.5, zh=f"中{i}", en="" if i < 2 else "x",
                  speaker="A") for i in range(5)]
    lines[4].flag = "uncertain"
    db.save_lines(did, lines)
    b = client.get(_url(did)).json()
    assert b["stage_index"] == 3 and b["stage"] == "translate"
    assert (b["line_count"], b["untranslated_count"], b["flagged_count"]) == (5, 2, 1)
    assert _states(b) == {"source": "done", "translate": "current", "review": "pending",
                          "dub": "optional", "export": "pending"}


def test_review_then_dub_track_then_exported(client):
    did = db.create_drama(title_en="D", audio_filename="audio.wav")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="中", en="x", speaker="A")])
    b = client.get(_url(did)).json()
    assert b["stage"] == "review"
    assert _states(b)["translate"] == "done" and _states(b)["review"] == "current"
    ddir = db.drama_dir(did)
    with open(os.path.join(ddir, "dub_track.wav"), "wb") as f:
        f.write(b"RIFF")
    r = client.get(_url(did))
    b = r.json()
    assert b["stage_index"] == 6 and b["stage"] == "export" and b["has_dub_track"] is True
    assert _states(b) == {"source": "done", "translate": "done", "review": "done",
                          "dub": "done", "export": "current"}
    _no_leak(r)
    db.update_drama(did, status="exported")
    b = client.get(_url(did)).json()
    assert b["exported"] is True and _states(b)["export"] == "done"


def test_404_and_422(client):
    r = client.get(_url(999))
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get(_url(0)).status_code == 422
    assert client.get(_url("abc")).status_code == 422
    assert client.get(_url(2**40)).status_code == 422


def test_auth_on(isolated_db):
    did = db.create_drama(title_en="D")
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)
    assert c.get(_url(did)).status_code == 401
    u = auth_service.add_user("kid@example.com")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}"}
    assert c.get(_url(did), headers=h).status_code == 200      # household default
    auth_service.revoke_permission(u["id"], "library.read")
    assert c.get(_url(did), headers=h).status_code == 403
