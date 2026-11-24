"""
tests/test_picture_subtitles.py -- reading a picture subtitle track (PGS) inside a title's video into
lines: a real ffmpeg draws a tiny hand-made PGS track over black, the OCR reading is stubbed.
"""
import os
import shutil
import struct
import subprocess
import time

import pytest

pytest.importorskip("cv2")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import background_jobs
import db
import hardsub_ocr
import media_inspect
from fastapi.testclient import TestClient
from api.api_config import ApiSettings
from api.server import create_app
from lib.errors import InvalidInputError
from services import picture_subtitle_service as svc

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg is not installed")

W, H = 640, 360


def _segment(pts, kind, payload):
    return b"PG" + struct.pack(">II", int(pts * 90000), 0) + bytes([kind]) + struct.pack(">H", len(payload)) + payload


def _caption(pts, comp):
    x, y, w, h = 100, 280, 200, 40
    pcs = struct.pack(">HHBHBBBB", W, H, 0x10, comp, 0x80, 0, 0, 1) + struct.pack(">HBBHH", 0, 0, 0, x, y)
    wds = bytes([1]) + struct.pack(">BHHHH", 0, x, y, w, h)
    pds = bytes([0, 0, 1, 235, 128, 128, 255])
    rle = (bytes([0, 0xC0 | (w >> 8), w & 255, 1, 0, 0])) * h
    ods = struct.pack(">HBB", 0, 0, 0xC0) + (len(rle) + 4).to_bytes(3, "big") + struct.pack(">HH", w, h) + rle
    return b"".join(_segment(pts, k, p) for k, p in ((0x16, pcs), (0x17, wds), (0x14, pds), (0x15, ods), (0x80, b"")))


def _clear(pts, comp):
    pcs = struct.pack(">HHBHBBBB", W, H, 0x10, comp, 0x80, 0, 0, 0)
    return _segment(pts, 0x16, pcs) + _segment(pts, 0x80, b"")


def _title_with_pgs(tmp_path):
    sup = tmp_path / "t.sup"
    # Two captions: 1-3 s and 5-7 s.
    sup.write_bytes(_caption(1.0, 0) + _clear(3.0, 1) + _caption(5.0, 2) + _clear(7.0, 3))
    did = db.create_drama(title_en="D", source_language="en")
    folder = os.path.join(db.DRAMAS_DIR, str(did))
    os.makedirs(folder, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:d=9:r=5",
                    "-itsoffset", "0", "-i", str(sup), "-c:v", "mpeg4", "-c:s", "copy", "-map", "0",
                    "-map", "1", os.path.join(folder, "v.mkv")], check=True)
    db.update_drama(did, source_video_filename="v.mkv")
    return did, os.path.join(folder, "v.mkv")


def _read_by_brightness(path, region, lang, backend, **_):
    import cv2
    return "CAPTION" if cv2.imread(path, 0).max() > 100 else ""


def test_track_is_drawn_over_black_so_only_the_caption_frames_are_lit(isolated_db, tmp_path):
    import cv2
    _, video = _title_with_pgs(tmp_path)
    info = media_inspect.picture_track_info(video, 1)
    assert info["size"] == (W, H)
    frames = hardsub_ocr.extract_picture_subtitle_frames(
        video, 1, str(tmp_path / "frames"), 0.5, info["size"], info["duration"])
    lit = [ts for ts, p in frames if cv2.imread(p, 0).max() > 100]
    assert lit and len(lit) < len(frames) / 2
    assert frames[1][0] == 0.5


def test_reading_the_track_writes_one_line_per_caption(isolated_db, tmp_path, monkeypatch):
    did, _ = _title_with_pgs(tmp_path)
    monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region", _read_by_brightness)
    job = svc.start_read(did, 1)
    for _ in range(200):
        if background_jobs.get_status(job["job_id"])["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.1)
    status = background_jobs.get_status(job["job_id"])
    assert status["status"] == "done", status
    lines = db.load_line_objects(did)
    assert len(lines) == 2
    assert [ln.zh or ln.en for ln in lines] == ["CAPTION", "CAPTION"]
    assert lines[0].start < lines[1].start


def test_a_text_track_or_a_video_stream_is_not_read_as_pictures(isolated_db, tmp_path):
    did, _ = _title_with_pgs(tmp_path)
    with pytest.raises(InvalidInputError, match="isn't a subtitle track"):
        svc.start_read(did, 0)


def test_existing_lines_need_confirmation_before_the_job_starts(isolated_db, tmp_path):
    from core import Line
    did, _ = _title_with_pgs(tmp_path)
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="old", en="")])
    with pytest.raises(InvalidInputError) as e:
        svc.start_read(did, 1)
    assert e.value.details["reason"] == "confirm_replace_lines"


def test_analysis_marks_the_picture_track(isolated_db, tmp_path):
    from services import metadata_service
    did, _ = _title_with_pgs(tmp_path)
    (track,) = metadata_service.analyze_media(did)["subtitle_tracks"]
    assert track["picture"] is True and track["importable"] is False


def test_route_starts_the_job_or_says_why_not(isolated_db, tmp_path, monkeypatch):
    did, _ = _title_with_pgs(tmp_path)
    monkeypatch.setattr(svc.job_launch, "start_job", lambda *a, **k: True)
    client = TestClient(create_app(ApiSettings(auth_mode="off", serve_frontend=False)))
    base = f"/api/subtitle-import/dramas/{did}/picture-track/read"
    assert client.post(base, json={"track_index": 1}).json() == {"job_id": f"picsub_{did}", "drama_id": did}
    assert client.post(base, json={"track_index": 0}).status_code == 422
