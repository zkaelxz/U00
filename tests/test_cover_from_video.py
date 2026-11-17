"""A title's cover made from a frame of its own video (cover_art_service.make_cover_from_video)."""
import io
import os
import shutil
import subprocess

import pytest

import db
from api.api_config import ApiSettings
from api.server import create_app
from fastapi.testclient import TestClient
from services import cover_art_service



@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False, headers={"X-Baihe-Local": "1"})


_needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                   reason="ffmpeg and ffprobe are not installed")


def _title_with_video(seconds=2):
    did = db.create_drama(title_en="D")
    path = os.path.join(db.drama_dir(did), "video.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=red:s=320x180:d={seconds}",
                    "-pix_fmt", "yuv420p", path], check=True, capture_output=True)
    db.update_drama(did, source_video_filename="video.mp4")
    return did


@_needs_ffmpeg
def test_frame_becomes_a_cover_through_the_route(client):
    pytest.importorskip("PIL")
    from PIL import Image
    did = _title_with_video()
    r = client.post(f"/api/dramas/{did}/cover/from-video")
    assert r.status_code == 200, r.text
    assert r.json()["has_cover_art"] is True
    got = client.get(f"/api/dramas/{did}/cover")
    assert got.status_code == 200
    assert Image.open(io.BytesIO(got.content)).size == (320, 180)


def test_a_title_without_video_is_refused(client):
    did = db.create_drama(title_en="D")
    assert client.post(f"/api/dramas/{did}/cover/from-video").status_code == 422
    assert db.get_drama(did)["cover_art_filename"] is None


def test_missing_ffmpeg_is_reported(client, monkeypatch):
    did = db.create_drama(title_en="D")
    open(os.path.join(db.drama_dir(did), "video.mp4"), "wb").close()
    db.update_drama(did, source_video_filename="video.mp4")
    monkeypatch.setattr(cover_art_service, "_frame_second", lambda path: 1.0)

    def no_ffmpeg(cmd, **kw):
        raise FileNotFoundError(2, "no such file", cmd[0])
    monkeypatch.setattr("lib.proc.run_captured", no_ffmpeg)
    assert client.post(f"/api/dramas/{did}/cover/from-video").status_code == 503


def test_make_cover_if_missing_never_raises_and_keeps_an_existing_cover(isolated_db, monkeypatch):
    did = db.create_drama(title_en="D")
    assert cover_art_service.make_cover_if_missing(did) is False  # no video: swallowed
    db.update_drama(did, cover_art_filename="cover.png")
    monkeypatch.setattr(cover_art_service, "make_cover_from_video",
                        lambda d: pytest.fail("a title with a cover is left alone"))
    assert cover_art_service.make_cover_if_missing(did) is False


def test_installing_a_video_makes_a_cover_and_a_failed_frame_does_not_fail_the_install(isolated_db, monkeypatch, tmp_path):
    from services import media_upload_service
    monkeypatch.undo()  # the suite-wide switch-off: this test wants the real hook
    did = db.create_drama(title_en="D")
    made = []
    monkeypatch.setattr(cover_art_service, "make_cover_if_missing", lambda d: made.append(d))
    src = tmp_path / "up.mp4"
    src.write_bytes(b"not really a video")
    names = media_upload_service.install_media(did, {"source_video_filename": (str(src), "source", ".mp4")})
    assert names == {"source_video_filename": "source.mp4"} and made == [did]
    # the real call on a file ffmpeg can't read: swallowed, title unchanged
    monkeypatch.undo()
    src2 = tmp_path / "up2.mp4"
    src2.write_bytes(b"still not a video")
    media_upload_service.install_media(did, {"source_video_filename": (str(src2), "source2", ".mp4")})
    assert db.get_drama(did)["cover_art_filename"] is None


def test_a_cover_set_by_hand_meanwhile_is_not_replaced_by_the_automatic_one(isolated_db):
    import io
    pytest.importorskip("PIL")
    from PIL import Image
    did = db.create_drama(title_en="D")
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG")
    cover_art_service.save_cover(did, io.BytesIO(buf.getvalue()))
    from lib.errors import ConflictError
    with pytest.raises(ConflictError):
        cover_art_service.save_cover(did, io.BytesIO(buf.getvalue()), only_if_missing=True)
    assert db.get_drama(did)["cover_art_filename"] == "cover.png"
