"""Raw-video cleaner: GET/POST /api/data-usage/raw-videos. Offers only old source videos of finished titles that are
not pinned, moves them to Trash with confirm, and leaves the subtitles and audio alone."""
import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line

BASE = "/api/data-usage/raw-videos"


@pytest.fixture
def c(isolated_db):
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _title(name="Show", translated=True, age_days=60, **extra):
    did = db.create_drama(title_en=name, audio_filename="audio.flac", source_video_filename="source.mp4", **extra)
    folder = db.drama_dir(did)
    for fname, size in (("audio.flac", 10), ("source.mp4", 500)):
        with open(os.path.join(folder, fname), "wb") as fh:
            fh.write(b"x" * size)
    old = time.time() - age_days * 86400
    os.utime(os.path.join(folder, "source.mp4"), (old, old))
    if translated:
        db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    return did


def test_offers_only_old_finished_unpinned_titles(c):
    ok = _title("Old done")
    _title("Too new", age_days=2)
    _title("No subs", translated=False)
    _title("Pinned", keep_original=1)
    _title("Hardsub", transcript_mode="hardsub_ocr")
    body = c.get(BASE).json()
    assert [v["title"] for v in body["videos"]] == ["Old done"]
    assert body["total_bytes"] == 500
    assert "source.mp4" not in c.get(BASE).text and str(ok) not in [v["id"] for v in body["videos"]]


def test_needs_confirm_then_moves_to_trash_and_keeps_subs(c):
    did = _title()
    video = c.get(BASE).json()["videos"][0]
    choice = {"id": video["id"], "expected_size_bytes": 500}
    assert c.post(BASE + "/to-trash", json={"videos": [choice]}).status_code == 422
    assert os.path.exists(os.path.join(db.drama_dir(did), "source.mp4"))
    done = c.post(BASE + "/to-trash", json={"videos": [choice], "confirm": True})
    assert done.status_code == 200 and done.json()["moved_count"] == 1
    assert not os.path.exists(os.path.join(db.drama_dir(did), "source.mp4"))
    row = db.get_drama(did)
    assert row["source_video_filename"] is None and row["audio_filename"] == "audio.flac"
    assert db.drama_has_translated_lines(did)
    assert c.get(BASE).json()["videos"] == []


def test_a_changed_size_is_skipped_not_moved(c):
    did = _title()
    video = c.get(BASE).json()["videos"][0]
    done = c.post(BASE + "/to-trash", json={"videos": [{"id": video["id"], "expected_size_bytes": 1}], "confirm": True})
    assert done.json()["moved_count"] == 0 and done.json()["skipped"][0]["reason"] == "changed"
    assert os.path.exists(os.path.join(db.drama_dir(did), "source.mp4"))


def test_a_pin_after_listing_is_skipped(c):
    did = _title()
    video = c.get(BASE).json()["videos"][0]
    db.update_drama(did, keep_original=1)
    done = c.post(BASE + "/to-trash", json={"videos": [{"id": video["id"], "expected_size_bytes": 500}], "confirm": True})
    assert done.json()["moved_count"] == 0
    assert os.path.exists(os.path.join(db.drama_dir(did), "source.mp4"))


def test_a_traversing_stored_name_is_never_offered(c):
    victim = _title("Victim", keep_original=1)
    did = _title("Hostile")
    db.update_drama(did, source_video_filename=f"../{victim}/source.mp4")
    assert [v["title"] for v in c.get(BASE).json()["videos"]] == []


def test_a_missing_audio_file_keeps_the_video_out(c):
    did = _title()
    os.remove(os.path.join(db.drama_dir(did), "audio.flac"))
    assert c.get(BASE).json()["videos"] == []


def test_restore_from_trash_puts_the_video_back_on_its_title(c):
    did = _title()
    video = c.get(BASE).json()["videos"][0]
    c.post(BASE + "/to-trash", json={"videos": [{"id": video["id"], "expected_size_bytes": 500}], "confirm": True})
    assert db.get_drama(did)["source_video_filename"] is None
    entry = c.get("/api/data-usage/trash").json()["items"][0]
    done = c.post("/api/data-usage/trash/restore", json={"id": entry["id"], "confirm": True})
    assert done.status_code == 200, done.text
    assert db.get_drama(did)["source_video_filename"] == "source.mp4"
    assert os.path.exists(os.path.join(db.drama_dir(did), "source.mp4"))
    assert [v["title"] for v in c.get(BASE).json()["videos"]] == ["Show"]  # offered again, as before


def test_restore_leaves_a_video_added_meanwhile_alone(c):
    did = _title()
    video = c.get(BASE).json()["videos"][0]
    c.post(BASE + "/to-trash", json={"videos": [{"id": video["id"], "expected_size_bytes": 500}], "confirm": True})
    db.update_drama(did, source_video_filename="newer.mp4")
    entry = c.get("/api/data-usage/trash").json()["items"][0]
    c.post("/api/data-usage/trash/restore", json={"id": entry["id"], "confirm": True})
    assert db.get_drama(did)["source_video_filename"] == "newer.mp4"
