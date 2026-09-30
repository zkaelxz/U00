"""GET /api/library/voice-bank/{entry_id}/audio (L19): media.stream, only an
audio file inside the voice-bank folder, generic name, nosniff."""
import os
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service

REMOTE = "https://baihe.example.com"


@pytest.fixture
def client(isolated_db):
    db.init_db()
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _entry(tmp_path, data=b"RIFFxxxxWAVE", ext=".wav"):
    src = tmp_path / f"clip{ext}"
    src.write_bytes(data)
    return db.save_voice_bank_entry("Voice", str(src), ref_text="hi")


def _set_clip(entry_id, name):
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("UPDATE voice_bank SET clip_filename = ? WHERE id = ?", (name, entry_id))
    conn.commit()
    conn.close()


def test_plays_clip_with_safe_headers(client, tmp_path):
    eid = _entry(tmp_path)
    r = client.get(f"/api/library/voice-bank/{eid}/audio")
    assert r.status_code == 200 and r.content == b"RIFFxxxxWAVE"
    assert r.headers["content-type"].startswith("audio/wav")
    assert r.headers["x-content-type-options"] == "nosniff"
    cd = r.headers["content-disposition"]
    assert cd.startswith("inline") and f"voice_{eid}.wav" in cd
    stored = db.get_voice_bank_entry(eid)["clip_filename"]
    assert stored not in cd and db.VOICE_BANK_DIR not in r.text
    r = client.get(f"/api/library/voice-bank/{eid}/audio", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"RIFF"
    assert client.head(f"/api/library/voice-bank/{eid}/audio").status_code == 200


def test_unknown_missing_and_non_audio(client, tmp_path):
    assert client.get("/api/library/voice-bank/999/audio").status_code == 404
    assert client.get("/api/library/voice-bank/0/audio").status_code == 422
    assert client.get("/api/library/voice-bank/abc/audio").status_code == 422
    eid = _entry(tmp_path)
    os.remove(os.path.join(db.VOICE_BANK_DIR, db.get_voice_bank_entry(eid)["clip_filename"]))
    assert client.get(f"/api/library/voice-bank/{eid}/audio").status_code == 404
    html = _entry(tmp_path, b"<script>alert(1)</script>", ext=".html")
    assert client.get(f"/api/library/voice-bank/{html}/audio").status_code == 404


def test_refuses_paths_escaping_the_folder(client, tmp_path):
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"SECRET")
    eid = _entry(tmp_path)
    for name in ("../secret.wav", str(outside), "..\\secret.wav", "sub/../../secret.wav"):
        _set_clip(eid, name)
        r = client.get(f"/api/library/voice-bank/{eid}/audio")
        assert r.status_code == 404 and b"SECRET" not in r.content, name


def test_refuses_symlinks(client, tmp_path):
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"SECRET")
    eid = _entry(tmp_path)
    link = os.path.join(db.VOICE_BANK_DIR, "link.wav")
    try:
        os.symlink(str(outside), link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    _set_clip(eid, "link.wav")
    r = client.get(f"/api/library/voice-bank/{eid}/audio")
    assert r.status_code == 404 and b"SECRET" not in r.content


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_needs_media_stream(isolated_db, tmp_path):
    db.init_db()
    eid = _entry(tmp_path)
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                   raise_server_exceptions=False)
    url = f"/api/library/voice-bank/{eid}/audio"
    assert c.get(url).status_code == 401
    u = auth_service.add_user("kid@example.com")
    kid = auth_service.create_session(u["id"])
    assert c.get(url, headers=_h(kid)).status_code == 403     # household default: no media
    auth_service.grant_permission(u["id"], "media.stream")
    assert c.get(url, headers=_h(kid)).status_code == 200


def test_oversized_id_is_422_not_500(client):
    assert client.get(f"/api/library/voice-bank/{2**31}/audio").status_code == 422
    assert client.get(f"/api/library/voice-bank/{2**63}/audio").status_code == 422
