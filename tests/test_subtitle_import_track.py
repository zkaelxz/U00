"""
tests/test_subtitle_import_track.py -- importing a subtitle track that is
inside the title's own video file, using a real ffmpeg on a tiny fixture.
"""
import os
import shutil
import subprocess

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import media_inspect
from api.api_config import ApiSettings
from api.server import create_app
from lib.errors import InvalidInputError
from services import subtitle_import_service as svc

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg is not installed")

SRT = ("1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n"
       "2\n00:00:03,000 --> 00:00:04,000\nsecond\n").encode()


def _title_with_video(tmp_path, *, codec="srt"):
    sub = tmp_path / "in.srt"
    sub.write_bytes(SRT)
    did = db.create_drama(title_en="D", source_language="en")
    folder = os.path.join(db.DRAMAS_DIR, str(did))
    os.makedirs(folder, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=6:r=5",
                    "-i", str(sub), "-c:v", "mpeg4", "-c:s", codec, "-map", "0", "-map", "1",
                    os.path.join(folder, "v.mkv")], check=True)
    db.update_drama(did, source_video_filename="v.mkv")
    return did


def test_text_track_is_extracted_and_imported_as_lines(isolated_db, tmp_path):
    did = _title_with_video(tmp_path)
    preview = svc.preview_track(did, 1)
    assert preview["cue_count"] == 2
    result = svc.import_track(did, 1)
    assert result["lines_written"] == 2
    assert [round(ln.start) for ln in db.load_line_objects(did)] == [1, 3]


def test_replacing_lines_needs_confirmation_like_a_file_import(isolated_db, tmp_path):
    did = _title_with_video(tmp_path)
    svc.import_track(did, 1)
    with pytest.raises(InvalidInputError) as e:
        svc.import_track(did, 1)
    assert e.value.details["reason"] == "confirm_replace_lines"
    assert svc.import_track(did, 1, confirm_replace_lines=True)["lines_written"] == 2


def test_video_stream_is_refused_not_passed_to_ffmpeg(isolated_db, tmp_path):
    did = _title_with_video(tmp_path)
    with pytest.raises(InvalidInputError, match="isn't a subtitle track"):
        svc.preview_track(did, 0)


def test_picture_codec_is_refused_with_a_plain_sentence(monkeypatch):
    monkeypatch.setattr(media_inspect, "run_ffprobe", lambda p, timeout=30: {"streams": [
        {"index": 2, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"}]})
    with pytest.raises(media_inspect.SubtitleExtractError) as e:
        media_inspect.extract_text_subtitle("x.mkv", 2)
    assert e.value.reason == "image"


def test_analysis_marks_which_tracks_can_be_imported(isolated_db, tmp_path):
    from services import metadata_service
    did = _title_with_video(tmp_path)
    tracks = metadata_service.analyze_media(did)["subtitle_tracks"]
    assert [t["importable"] for t in tracks] == [True]


def test_route_previews_and_applies(isolated_db, tmp_path):
    did = _title_with_video(tmp_path)
    client = TestClient(create_app(ApiSettings(auth_mode="off", serve_frontend=False)))
    base = f"/api/subtitle-import/dramas/{did}/track"
    assert client.post(f"{base}/preview", json={"track_index": 1}).json()["cue_count"] == 2
    assert client.post(f"{base}/apply", json={"track_index": 1}).json()["lines_written"] == 2
    assert client.post(f"{base}/apply", json={"track_index": 9}).status_code == 422
