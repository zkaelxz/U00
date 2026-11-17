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

# Taken at import, before the suite-wide fixture swaps the module's function for a no-op.
_REAL_MAKE_IF_MISSING = cover_art_service.make_cover_if_missing



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
    monkeypatch.setattr(cover_art_service, "make_cover_if_missing", _REAL_MAKE_IF_MISSING)  # the suite-wide switch-off
    did = db.create_drama(title_en="D")
    assert cover_art_service.make_cover_if_missing(did) is False  # no video: swallowed
    db.update_drama(did, cover_art_filename="cover.png")
    monkeypatch.setattr(cover_art_service, "make_cover_from_video",
                        lambda d: pytest.fail("a title with a cover is left alone"))
    assert cover_art_service.make_cover_if_missing(did) is False


def test_installing_a_video_makes_a_cover_and_a_failed_frame_does_not_fail_the_install(isolated_db, monkeypatch, tmp_path):
    from services import media_upload_service
    did = db.create_drama(title_en="D")
    made = []
    monkeypatch.setattr(cover_art_service, "make_cover_if_missing", lambda d: made.append(d))
    src = tmp_path / "up.mp4"
    src.write_bytes(b"not really a video")
    names = media_upload_service.install_media(did, {"source_video_filename": (str(src), "source", ".mp4")})
    assert names == {"source_video_filename": "source.mp4"} and made == [did]
    # the real call on a file ffmpeg can't read: swallowed, title unchanged
    monkeypatch.setattr(cover_art_service, "make_cover_if_missing", _REAL_MAKE_IF_MISSING)
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


def _png(color=(9, 9, 9)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (6, 4), color).save(buf, "PNG")
    return buf.getvalue()


def test_a_downloaded_site_thumbnail_becomes_the_cover(isolated_db, tmp_path):
    pytest.importorskip("PIL")
    did = db.create_drama(title_en="D")
    path = tmp_path / "site_thumbnail.bin"
    path.write_bytes(_png())  # raw bytes: the picture's type comes from the bytes, not the name
    assert cover_art_service.use_site_thumbnail(did, str(path)) is True
    assert db.get_drama(did)["cover_art_filename"] == "cover.png"


def test_a_site_thumbnail_never_replaces_a_cover_and_junk_is_ignored(isolated_db, tmp_path):
    pytest.importorskip("PIL")
    did = db.create_drama(title_en="D")
    path = tmp_path / "site_thumbnail.bin"
    path.write_bytes(b"<html>not an image</html>")
    assert cover_art_service.use_site_thumbnail(did, str(path)) is False
    assert db.get_drama(did)["cover_art_filename"] is None
    assert cover_art_service.use_site_thumbnail(did, str(tmp_path / "missing.bin")) is False
    path.write_bytes(_png((1, 1, 1)))
    cover_art_service.save_cover(did, io.BytesIO(_png((200, 0, 0))))
    assert cover_art_service.use_site_thumbnail(did, str(path)) is False
    assert db.get_drama(did)["cover_art_filename"] == "cover.png"


def test_remove_cover_deletes_the_file_and_clears_the_field(isolated_db):
    pytest.importorskip("PIL")
    did = db.create_drama(title_en="D")
    cover_art_service.save_cover(did, io.BytesIO(_png()))
    stored = os.path.join(db.drama_dir(did), "cover.png")
    assert os.path.exists(stored)
    cover_art_service.remove_cover(did)
    assert not os.path.exists(stored)
    assert db.get_drama(did)["cover_art_filename"] is None
    cover_art_service.remove_cover(did)  # nothing left: no error
    cover_art_service.remove_cover(99999)  # no such title: no error


class _Response:
    def __init__(self, chunks):
        self.chunks, self.closed = list(chunks), False

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


class _Ydl:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.urls = response, error, []

    def urlopen(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


def test_save_thumbnail_writes_what_the_page_names(tmp_path):
    import video_download
    out = tmp_path / "t.bin"
    ydl = _Ydl(_Response([b"ab", b"cd"]))
    assert video_download.save_thumbnail(ydl, {"thumbnail": "https://img.example/t.jpg"}, str(out)) is True
    assert out.read_bytes() == b"abcd" and ydl.urls == ["https://img.example/t.jpg"] and ydl.response.closed


def test_save_thumbnail_refuses_what_is_not_an_http_picture_url(tmp_path):
    import video_download
    out = tmp_path / "t.bin"
    for info in ({}, {"thumbnail": None}, {"thumbnail": "file:///etc/passwd"}, {"thumbnail": "ftp://x/y"}, None):
        ydl = _Ydl(_Response([b"x"]))
        assert video_download.save_thumbnail(ydl, info, str(out)) is False
        assert ydl.urls == []
    assert not out.exists()


def test_save_thumbnail_stops_at_the_byte_cap_and_keeps_nothing(tmp_path):
    import video_download
    out = tmp_path / "t.bin"
    endless = _Response([b"x" * 1000] * 10_000)
    assert video_download.save_thumbnail(_Ydl(endless), {"thumbnail": "https://i.example/t"}, str(out),
                                         max_bytes=5000) is False
    assert not out.exists() and not os.path.exists(str(out) + ".part")
    assert len(endless.chunks) > 9_000  # it stopped reading; it did not drain the stream


def test_save_thumbnail_gives_up_on_a_stalled_read_and_on_errors(tmp_path):
    import threading
    import video_download
    out = tmp_path / "t.bin"
    release = threading.Event()

    class Stalled(_Response):
        def read(self, n):
            release.wait(5)
            return b""
    try:
        assert video_download.save_thumbnail(_Ydl(Stalled([])), {"thumbnail": "https://i.example/t"}, str(out),
                                             seconds=0.2) is False
    finally:
        release.set()
    assert video_download.save_thumbnail(_Ydl(error=OSError("refused")), {"thumbnail": "https://i.example/t"},
                                         str(out)) is False
    assert not out.exists()
