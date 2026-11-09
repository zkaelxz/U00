"""services/inbox_service.py: the watch folder. Uploads are real (a tiny .mp3 body); the queue is
checked through its saved document, and time is passed in so no test sleeps."""
import os

import pytest

import db
from lib.errors import InvalidInputError
from services import inbox_service as inbox, queue_service as q


@pytest.fixture
def folder(isolated_db, tmp_path):
    root = tmp_path / "drop"
    root.mkdir()
    return root


def _on(folder, **kw):
    args = dict(on=True, path=str(folder), source_language="ja", series_id=None, move=False)
    args.update(kw)
    return inbox.configure(**args)


def _scan_until_started(now0=1000.0):
    """Two passes STABLE_SECONDS apart, then wait for the import thread."""
    inbox.scan(now0)
    inbox.scan(now0 + inbox.STABLE_SECONDS + 1)
    if inbox._worker is not None:
        inbox._worker.join(10)


def _drop(folder, name="ep1.mp3", body=b"abc" * 100):
    path = folder / name
    path.write_bytes(body)
    return path


def _titles():
    return db.list_dramas()


def test_off_by_default_imports_nothing(folder):
    _drop(folder)
    _scan_until_started()
    assert _titles() == []


def test_stable_file_becomes_title_and_a_transcribe_item(folder):
    _on(folder)
    _drop(folder)
    _scan_until_started()
    titles = _titles()
    assert [t["title_en"] for t in titles] == ["ep1"]
    items = q._load()["items"]
    assert [(i["drama_id"], i["step"]) for i in items] == [(titles[0]["id"], "transcribe")]
    assert (folder / "ep1.mp3").exists()   # the original is left alone


def test_file_still_changing_is_not_taken(folder):
    _on(folder)
    path = _drop(folder)
    inbox.scan(1000.0)
    path.write_bytes(b"abc" * 200)   # still being copied
    inbox.scan(1000.0 + inbox.STABLE_SECONDS + 1)
    assert _titles() == []


def test_unreadable_file_is_left_for_later(folder, monkeypatch):
    _on(folder)
    _drop(folder)
    monkeypatch.setattr(inbox, "_openable", lambda path: False)
    _scan_until_started()
    assert _titles() == []


def test_same_file_is_not_imported_twice(folder):
    _on(folder)
    _drop(folder)
    _scan_until_started()
    inbox.scan(5000.0)
    inbox.scan(5000.0 + inbox.STABLE_SECONDS + 1)
    assert len(_titles()) == 1


def test_symlink_hidden_and_other_files_are_ignored(folder, tmp_path):
    _on(folder)
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"x" * 50)
    try:
        os.symlink(outside, folder / "link.mp3")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    _drop(folder, ".hidden.mp3")
    _drop(folder, "notes.txt")
    _scan_until_started()
    assert _titles() == []


def test_subfolder_files_are_not_read(folder):
    _on(folder)
    sub = folder / "inner"
    sub.mkdir()
    _drop(sub)
    _scan_until_started()
    assert _titles() == []


def test_move_puts_the_original_in_done(folder):
    _on(folder, move=True)
    _drop(folder)
    _scan_until_started()
    assert not (folder / "ep1.mp3").exists()
    assert (folder / inbox.DONE_FOLDER / "ep1.mp3").exists()


def test_series_mode_adds_to_the_series_and_queues_nothing_itself(folder):
    sid = db.get_or_create_series("Show")
    _on(folder, series_id=sid)
    _drop(folder)
    _scan_until_started()
    assert [t["series_id"] for t in _titles()] == [sid]
    assert q._load()["items"] == []


def test_configure_refuses_bad_input(folder, tmp_path):
    with pytest.raises(InvalidInputError):
        _on(tmp_path / "missing")
    with pytest.raises(InvalidInputError):
        _on(folder, source_language="en")
    with pytest.raises(InvalidInputError):
        _on(folder, series_id=999)
    with pytest.raises(InvalidInputError):
        _on(db.LIBRARY_DIR)


def test_state_never_returns_the_path(folder):
    state = _on(folder)
    assert state["folder_set"] and state["folder_name"] == "drop"
    assert str(folder) not in repr(state)


def test_routes_are_pc_only_and_never_return_the_path(folder):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    from tests.api_helpers import loopback_client
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    c = loopback_client(app)
    body = {"on": True, "path": str(folder), "source_language": "ko", "series_id": None, "move": False}
    saved = c.post("/api/inbox", json=body).json()
    assert saved["on"] is True and saved["source_language"] == "ko" and saved["folder_name"] == "drop"
    assert str(folder) not in c.get("/api/inbox").text
    assert c.post("/api/inbox", json={**body, "path": "/nope/missing"}).status_code == 422
    remote = TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False)),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    assert remote.get("/api/inbox").status_code in (401, 403)
