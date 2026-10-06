"""Tests for POST /api/media/dramas/{id}/upload (Migration Slice 31): fully
mocked, tiny fake bytes, isolated library."""

import os
import subprocess
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import background_jobs
import video_export
from api.api_config import ApiSettings
from api.server import create_app


def _fake_ffmpeg(job_id, cmd, cwd=None, **kw):
    with open(cmd[-1], "wb") as f:
        f.write(b"wav")


def _wait(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(background_jobs, "run_cancellable", _fake_ffmpeg)
    # X-Baihe-Local: what the React upload helper sends; local_only refuses
    # a multipart POST without it (api/auth.py _cross_site_safe)
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _up(client, did, name="clip.mp3", data=b"fakeaudio"):
    return client.post(f"/api/media/dramas/{did}/upload", files={"file": (name, data)})


def test_audio_upload(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    r = _up(client, did, "My Clip.MP3")
    assert r.status_code == 200
    assert r.json() == {"name": "source.mp3", "size": 9, "kind": "audio", "job_id": None}
    assert db.get_drama(did)["audio_filename"] == "source.mp3"
    assert os.listdir(db.drama_dir(did)) == ["source.mp3"]
    with open(os.path.join(db.drama_dir(did), "source.mp3"), "rb") as f:
        assert f.read() == b"fakeaudio"


def test_video_upload_extracts_audio(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mkv")
    assert r.status_code == 200
    # The job names the video when it puts it in place (source-2.mkv if source.mkv is taken).
    assert r.json() == {"name": None, "size": 9, "kind": "video",
                        "job_id": f"extract_audio_{did}"}
    assert _wait(f"extract_audio_{did}")["status"] == "done"
    d = db.get_drama(did)
    assert d["audio_filename"] == "audio.wav" and d["source_video_filename"] == "source.mkv"
    assert sorted(os.listdir(db.drama_dir(did))) == ["audio.wav", "source.mkv"]


def test_video_upload_returns_before_extraction_finishes(client, isolated_db, monkeypatch):
    import db
    gate = threading.Event()

    def slow_ffmpeg(job_id, cmd, cwd=None, **kw):
        assert kw.get("timeout")  # a stuck ffmpeg is bounded
        gate.wait(5)
        _fake_ffmpeg(job_id, cmd)
    monkeypatch.setattr(background_jobs, "run_cancellable", slow_ffmpeg)
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mp4")
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert background_jobs.get_status(job_id)["status"] == "running"
    assert not db.get_drama(did).get("audio_filename")
    # a second upload while extracting is refused and does not touch the file
    r2 = _up(client, did, "b.mp4", b"other")
    assert r2.status_code == 409
    gate.set()
    assert _wait(job_id)["status"] == "done"
    assert db.get_drama(did)["audio_filename"] == "audio.wav"
    with open(os.path.join(db.drama_dir(did), "source.mp4"), "rb") as f:
        assert f.read() == b"fakeaudio"


def _kept(did):
    import db
    kept = os.path.join(db.drama_dir(did), "kept_media")
    out = {}
    for name in sorted(os.listdir(kept)) if os.path.isdir(kept) else []:
        with open(os.path.join(kept, name), "rb") as f:
            out[name] = f.read()
    return out


def test_video_extract_failure_is_job_error_and_keeps_the_upload(client, isolated_db, monkeypatch):
    import db

    def boom(job_id, cmd, cwd=None, **kw):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"key sk-ant-abcdefghijklmnopqrstuvwxyz0123")
    monkeypatch.setattr(background_jobs, "run_cancellable", boom)
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mp4")
    assert r.status_code == 200
    job = _wait(r.json()["job_id"])
    assert job["status"] == "error"
    assert "Could not read audio from that video file." in job["error"]
    assert "kept" in job["error"]
    assert "sk-ant" not in job["error"] and "sk-ant" not in (job.get("traceback") or "")
    assert db.drama_dir(did) not in job["error"]
    assert os.listdir(db.drama_dir(did)) == ["kept_media"]
    kept = _kept(did)
    assert list(kept.values()) == [b"fakeaudio"]
    assert next(iter(kept)).startswith("failed-upload-") and next(iter(kept)).endswith(".mp4")
    assert not db.get_drama(did).get("audio_filename")


def test_extract_ffmpeg_opens_local_files_only(client, isolated_db, monkeypatch):
    # an uploaded "mp4" could really be an HLS playlist naming network URLs
    import db
    cmds = []

    def recording(job_id, cmd, cwd=None, **kw):
        cmds.append(cmd)
        _fake_ffmpeg(job_id, cmd)
    monkeypatch.setattr(background_jobs, "run_cancellable", recording)
    did = db.create_drama(title_en="D")
    assert _wait(_up(client, did, "a.mp4").json()["job_id"])["status"] == "done"
    i = cmds[0].index("-i")
    assert cmds[0][i - 4:i] == video_export.local_input()


def test_video_extract_cancel_is_cancelled_and_keeps_the_upload(client, isolated_db, monkeypatch):
    import db

    def cancelled(job_id, cmd, cwd=None, **kw):
        raise background_jobs.JobCancelled(job_id)
    monkeypatch.setattr(background_jobs, "run_cancellable", cancelled)
    did = db.create_drama(title_en="D")
    job = _wait(_up(client, did, "a.mp4").json()["job_id"])
    assert job["status"] == "cancelled"
    assert os.listdir(db.drama_dir(did)) == ["kept_media"]
    assert list(_kept(did).values()) == [b"fakeaudio"]


def test_run_cancellable_timeout_kills_process(isolated_db):
    import sys
    background_jobs.start_job("t_timeout", lambda: None)
    t0 = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        background_jobs.run_cancellable("t_timeout", [sys.executable, "-c", "import time; time.sleep(30)"],
                                        timeout=0.5)
    assert time.time() - t0 < 10


def test_bad_extension(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "evil.exe").status_code == 422
    assert _up(client, did, "noext").status_code == 422
    assert _up(client, did, "a.mp3\x00.exe").status_code == 422
    assert os.listdir(db.drama_dir(did)) == []


def test_traversal_name_is_ignored(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    r = _up(client, did, "../../../etc/passwd.wav")
    assert r.status_code == 200
    assert r.json()["name"] == "source.wav"
    assert "etc" not in r.text and "passwd" not in r.text
    assert not os.path.exists(os.path.join(os.path.dirname(db.drama_dir(did)), "source.wav"))


def test_oversize_rejected_and_partial_deleted(client, isolated_db, monkeypatch):
    import db
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "0.00001")  # ~10 bytes
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mp3", b"x" * 100)
    assert r.status_code == 422
    assert r.json()["error"]["message"] .startswith("That file is larger than the")
    assert os.listdir(db.drama_dir(did)) == []
    assert not db.get_drama(did).get("audio_filename")


def test_empty_upload_rejected(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"").status_code == 422
    assert os.listdir(db.drama_dir(did)) == []


def test_unknown_drama_404(client, isolated_db):
    assert _up(client, 9999).status_code == 404


def test_running_job_409(client, isolated_db, monkeypatch):
    import db
    did = db.create_drama(title_en="D")
    monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda d: True)
    r = _up(client, did)
    assert r.status_code == 409
    assert os.listdir(db.drama_dir(did)) == []


def test_missing_file_field_422(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    # multipart without the "file" field (a body-less POST is now refused
    # earlier by local_only's content-type check, see api/auth.py)
    r = client.post(f"/api/media/dramas/{did}/upload", files={"other": ("a.txt", b"x")})
    assert r.status_code == 422


def test_upload_rejected_for_novel_narration_drama(client, isolated_db):
    import db
    did = db.create_drama(title_en="N")
    db.update_drama(did, content_mode="novel_narration")
    r = _up(client, did)
    assert r.status_code == 422
    assert not any(n.startswith("source") for n in os.listdir(db.drama_dir(did)))
    assert db.get_drama(did)["audio_filename"] in (None, "")


def test_upload_allowed_for_streamer_vod(client, isolated_db):
    import db
    did = db.create_drama(title_en="V")
    db.update_drama(did, content_mode="streamer_vod")
    assert _up(client, did).status_code == 200


def test_concurrent_upload_loser_never_touches_winner_file(client, isolated_db):
    import db
    from services import media_upload_service
    did = db.create_drama(title_en="D")
    reading, release = threading.Event(), threading.Event()

    class SlowBody:
        def __init__(self):
            self.sent = False

        def read(self, n):
            if self.sent:
                return b""
            reading.set()
            release.wait(5)
            self.sent = True
            return b"winner"
    out = {}
    t = threading.Thread(target=lambda: out.update(
        r=media_upload_service.upload_media(did, "a.mp4", SlowBody())))
    t.start()
    assert reading.wait(5)
    r = _up(client, did, "b.mp4", b"loser")  # same source.mp4 target
    assert r.status_code == 409
    release.set()
    t.join(5)
    assert out["r"]["job_id"] == f"extract_audio_{did}"
    _wait(out["r"]["job_id"])
    with open(os.path.join(db.drama_dir(did), "source.mp4"), "rb") as f:
        assert f.read() == b"winner"
    assert not any(n.startswith(".upload_") for n in os.listdir(db.drama_dir(did)))


def _read(did, name):
    import db
    with open(os.path.join(db.drama_dir(did), name), "rb") as f:
        return f.read()


def test_replacing_audio_needs_confirm_and_touches_nothing_without_it(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200
    r = _up(client, did, "b.mp3", b"second")
    assert r.status_code == 422
    assert r.json()["error"]["details"]["reason"] == "confirm_replace_audio"
    assert os.listdir(db.drama_dir(did)) == ["source.mp3"]
    assert _read(did, "source.mp3") == b"first"


def test_confirmed_replace_keeps_the_old_original(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"second")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200, r.text
    # never written over the old name: the drama switches to a free one
    assert db.get_drama(did)["audio_filename"] == "source-2.mp3"
    assert _read(did, "source-2.mp3") == b"second"
    assert not os.path.exists(os.path.join(db.drama_dir(did), "source.mp3"))
    kept = _kept(did)
    assert list(kept.values()) == [b"first"]
    assert next(iter(kept)).startswith("replaced-") and next(iter(kept)).endswith(".mp3")
    assert "kept_media" not in r.text
    # a second replace in the same second gets its own kept name
    client.post(f"/api/media/dramas/{did}/upload", files={"file": ("c.mp3", b"third")},
                data={"confirm_replace_audio": "true"})
    assert _read(did, db.get_drama(did)["audio_filename"]) == b"third"
    assert sorted(_kept(did).values()) == [b"first", b"second"]


def test_existing_source_video_needs_confirm(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert _wait(_up(client, did, "a.mp4", b"vid1").json()["job_id"])["status"] == "done"
    os.remove(os.path.join(db.drama_dir(did), "audio.wav"))  # video alone still counts
    r = _up(client, did, "b.mp3", b"aud")
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "confirm_replace_audio"


def test_video_replace_keeps_old_until_extraction_succeeds(client, isolated_db, monkeypatch):
    import db
    did = db.create_drama(title_en="D")
    assert _wait(_up(client, did, "a.mp4", b"old").json()["job_id"])["status"] == "done"
    gate = threading.Event()
    seen = {}

    def slow_ffmpeg(job_id, cmd, cwd=None, **kw):
        seen["during"] = _read(did, "source.mp4")
        gate.wait(5)
        _fake_ffmpeg(job_id, cmd)
    monkeypatch.setattr(background_jobs, "run_cancellable", slow_ffmpeg)
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp4", b"new")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200, r.text
    gate.set()
    assert _wait(r.json()["job_id"])["status"] == "done"
    assert seen["during"] == b"old"
    after = db.get_drama(did)
    assert (after["source_video_filename"], after["audio_filename"]) == ("source-2.mp4", "audio-2.wav")
    assert _read(did, "source-2.mp4") == b"new"
    assert sorted(_kept(did).values()) == [b"old", b"wav"]
    assert not any(n.startswith(".") for n in os.listdir(db.drama_dir(did)))


def test_failed_extraction_on_replace_leaves_the_title_as_it_was(client, isolated_db, monkeypatch):
    import db
    did = db.create_drama(title_en="D")
    assert _wait(_up(client, did, "a.mp4", b"old").json()["job_id"])["status"] == "done"
    before = db.get_drama(did)

    def boom(job_id, cmd, cwd=None, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(background_jobs, "run_cancellable", boom)
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp4", b"new")},
                    data={"confirm_replace_audio": "true"})
    assert _wait(r.json()["job_id"])["status"] == "error"
    assert _read(did, "source.mp4") == b"old" and _read(did, "audio.wav") == b"wav"
    after = db.get_drama(did)
    assert (after["audio_filename"], after["source_video_filename"]) == (
        before["audio_filename"], before["source_video_filename"])
    kept = _kept(did)
    assert list(kept.values()) == [b"new"] and next(iter(kept)).startswith("failed-upload-")


def test_replace_without_hard_links_still_keeps_the_old_file(client, isolated_db, monkeypatch):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200

    def no_links(*a, **kw):
        raise OSError("hard links not supported")
    monkeypatch.setattr(os, "link", no_links)
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"second")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200, r.text
    assert _read(did, db.get_drama(did)["audio_filename"]) == b"second"
    assert list(_kept(did).values()) == [b"first"]


def test_confirm_flag_must_be_a_bool(isolated_db):
    import io
    import db
    from services import media_upload_service
    from services.service_errors import InvalidInputError
    did = db.create_drama(title_en="D")
    with pytest.raises(InvalidInputError):
        media_upload_service.upload_media(did, "a.mp3", io.BytesIO(b"x"), confirm_replace_audio="yes")


def test_kept_media_is_in_a_media_backup(client, isolated_db):
    import zipfile
    import db
    from services import library_admin_service as las
    did = db.create_drama(title_en="D")
    _up(client, did, "a.mp3", b"first")
    client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"second")},
                data={"confirm_replace_audio": "true"})
    dest = os.path.join(db.LIBRARY_DIR, "b.zip")
    las.write_backup_zip(dest, include_media=True)
    with zipfile.ZipFile(dest) as zf:
        names = [n for n in zf.namelist() if n.startswith(f"dramas/{did}/kept_media/replaced-")]
        assert len(names) == 1 and zf.read(names[0]) == b"first"


def _video_title(client, did):
    assert _wait(_up(client, did, "a.mp4", b"old").json()["job_id"])["status"] == "done"
    import db
    return db.get_drama(did)


def _replace_video(client, did):
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp4", b"new")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200, r.text
    return _wait(r.json()["job_id"])


def _assert_title_unchanged(did, before):
    import db
    after = db.get_drama(did)
    assert (after["audio_filename"], after["source_video_filename"]) == ("audio.wav", "source.mp4")
    assert (before["audio_filename"], before["source_video_filename"]) == ("audio.wav", "source.mp4")
    assert _read(did, "source.mp4") == b"old" and _read(did, "audio.wav") == b"wav"
    kept = _kept(did)
    assert list(kept.values()) == [b"new"] and next(iter(kept)).startswith("failed-upload-")
    # nothing half-installed or staged is left beside the title's files
    assert sorted(os.listdir(db.drama_dir(did))) == ["audio.wav", "kept_media", "source.mp4"]


def test_audio_that_cannot_be_put_in_place_leaves_the_title_consistent(client, isolated_db, monkeypatch):
    # e.g. Windows refusing a name in the folder: the extracted WAV can't be
    # moved in after the new video was. The old video and audio stay named.
    import db
    from services import media_upload_service
    did = db.create_drama(title_en="D")
    before = _video_title(client, did)
    real_link, real_rename = os.link, os.rename

    def refuse_audio(fn):
        def wrapper(src, dst, *a, **kw):
            if os.path.basename(dst).startswith("audio"):
                raise PermissionError(13, "in use")
            return fn(src, dst, *a, **kw)
        return wrapper
    monkeypatch.setattr(media_upload_service.os, "link", refuse_audio(real_link))
    monkeypatch.setattr(media_upload_service.os, "rename", refuse_audio(real_rename))
    job = _replace_video(client, did)
    assert job["status"] == "error"
    assert job["error"].endswith(media_upload_service._SAVE_FAILED)
    _assert_title_unchanged(did, before)


def test_a_failed_db_switch_leaves_the_title_consistent(client, isolated_db, monkeypatch):
    import sqlite3
    import db
    from services import media_upload_service
    did = db.create_drama(title_en="D")
    before = _video_title(client, did)

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "update_drama", locked)
    job = _replace_video(client, did)
    assert job["status"] == "error" and job["error"].endswith(media_upload_service._SAVE_FAILED)
    _assert_title_unchanged(did, before)


def test_old_files_in_use_never_block_the_switch(client, isolated_db, monkeypatch):
    # Windows: the old audio.wav is open (Review playing it), so it can be
    # neither replaced nor moved. The new files go in under fresh names, the
    # drama switches to them, and the old WAV just stays, unreferenced and
    # with one name only.
    import db
    from services import media_upload_service
    did = db.create_drama(title_en="D")
    _video_title(client, did)
    ddir = db.drama_dir(did)
    busy = os.path.join(ddir, "audio.wav")
    real_unlink, real_rename, real_replace = os.unlink, os.rename, os.replace

    def guard(fn):
        def wrapper(src, *a, **kw):
            if os.path.abspath(src) == busy:
                raise PermissionError(13, "in use")
            return fn(src, *a, **kw)
        return wrapper
    monkeypatch.setattr(media_upload_service.os, "unlink", guard(real_unlink))
    monkeypatch.setattr(media_upload_service.os, "rename", guard(real_rename))
    monkeypatch.setattr(media_upload_service.os, "replace", guard(real_replace))
    assert _replace_video(client, did)["status"] == "done"
    after = db.get_drama(did)
    assert (after["source_video_filename"], after["audio_filename"]) == ("source-2.mp4", "audio-2.wav")
    assert _read(did, "source-2.mp4") == b"new" and _read(did, "audio.wav") == b"wav"
    assert list(_kept(did).values()) == [b"old"]
    assert os.stat(busy).st_nlink == 1


def test_audio_upload_that_cannot_be_saved_is_kept_and_says_so(client, isolated_db, monkeypatch):
    import db
    from services import media_upload_service
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200
    # no free name can be claimed (each candidate refused)
    monkeypatch.setattr(media_upload_service, "_in_place_names", lambda *a: iter([]))
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"second")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 409 and r.json()["error"]["message"] == media_upload_service._SAVE_FAILED
    assert db.get_drama(did)["audio_filename"] == "source.mp3" and _read(did, "source.mp3") == b"first"
    kept = _kept(did)
    assert list(kept.values()) == [b"second"] and next(iter(kept)).startswith("failed-upload-")


def test_move_never_overwrites_a_taken_name(tmp_path, monkeypatch):
    from services import media_upload_service as m
    src, taken, free = tmp_path / "src", tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"new")
    taken.write_bytes(b"precious")
    assert m._move_no_clobber(str(src), [str(taken), str(free)]) == str(free)
    assert taken.read_bytes() == b"precious" and free.read_bytes() == b"new"

    # a name taken between the check and the link: FileExistsError moves on,
    # it never falls back to an overwriting rename
    src.write_bytes(b"new2")
    racer, last = tmp_path / "c", tmp_path / "d"
    real_link = os.link

    def racing_link(s, d, *a, **kw):
        if d == str(racer):
            racer.write_bytes(b"theirs")
            raise FileExistsError(17, "exists")
        return real_link(s, d, *a, **kw)
    renames = []
    monkeypatch.setattr(m.os, "link", racing_link)
    monkeypatch.setattr(m.os, "rename", lambda *a: renames.append(a))
    assert m._move_no_clobber(str(src), [str(racer), str(last)]) == str(last)
    assert racer.read_bytes() == b"theirs" and last.read_bytes() == b"new2" and renames == []


def test_move_without_hard_links_skips_taken_names(tmp_path, monkeypatch):
    from services import media_upload_service as m
    src, taken, free = tmp_path / "src", tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"new")
    taken.write_bytes(b"precious")

    def no_links(*a, **kw):
        raise OSError("hard links not supported")
    monkeypatch.setattr(m.os, "link", no_links)
    assert m._move_no_clobber(str(src), [str(taken), str(free)]) == str(free)
    assert taken.read_bytes() == b"precious" and free.read_bytes() == b"new"


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_a_symlinked_original_is_moved_as_a_link_not_followed(client, isolated_db, tmp_path):
    import db
    did = db.create_drama(title_en="D")
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"theirs")
    ddir = db.drama_dir(did)
    os.symlink(outside, os.path.join(ddir, "source.mp3"))
    db.update_drama(did, audio_filename="source.mp3")
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"new")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200, r.text
    kept_dir = os.path.join(ddir, "kept_media")
    (name,) = os.listdir(kept_dir)
    assert os.path.islink(os.path.join(kept_dir, name))
    assert outside.read_bytes() == b"theirs" and os.stat(outside).st_nlink == 1


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_a_linked_kept_media_folder_is_refused(client, isolated_db, tmp_path):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ddir = db.drama_dir(did)
    os.symlink(elsewhere, os.path.join(ddir, "kept_media"))
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"second")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200, r.text
    # the switch happened; the old original stays in the folder instead
    assert _read(did, db.get_drama(did)["audio_filename"]) == b"second"
    assert _read(did, "source.mp3") == b"first"
    assert os.listdir(elsewhere) == []


def test_start_job_raising_keeps_the_upload(client, isolated_db, monkeypatch):
    import db

    def broken(*a, **kw):
        raise RuntimeError("thread limit")
    monkeypatch.setattr(background_jobs, "start_job", broken)
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp4", b"vid").status_code == 500
    assert list(_kept(did).values()) == [b"vid"]
    assert not any(n.startswith(".") for n in os.listdir(db.drama_dir(did)))


def test_stale_staged_uploads_are_recovered_never_deleted(client, isolated_db, monkeypatch):
    import db
    from services import drama_service, media_upload_service as m
    did = db.create_drama(title_en="D")
    ddir = db.drama_dir(did)
    old_t = time.time() - m.EXTRACT_TIMEOUT_SECONDS - 60
    for name, data in ((".upload_old.mp4", b"crashed"), (".upload_new.mp4", b"live"),
                       (".upload_x.part", b"partial"), ("notes.mp4", b"mine")):
        with open(os.path.join(ddir, name), "wb") as f:
            f.write(data)
        if name != ".upload_new.mp4":
            os.utime(os.path.join(ddir, name), (old_t, old_t))
    monkeypatch.setattr(drama_service, "job_running_for_drama", lambda _d: True)
    assert m.recover_stale_uploads(did) == 0  # a job may still be reading it
    monkeypatch.undo()
    assert m.recover_all_stale_uploads() == 1
    assert list(_kept(did).values()) == [b"crashed"]
    assert sorted(os.listdir(ddir)) == [".upload_new.mp4", ".upload_x.part", "kept_media", "notes.mp4"]
    # also at the drama's next upload
    os.utime(os.path.join(ddir, ".upload_new.mp4"), (old_t, old_t))
    assert _up(client, did, "a.mp3", b"aud").status_code == 200
    assert sorted(_kept(did).values()) == [b"crashed", b"live"]


def test_status_reports_kept_media_as_numbers_only(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    _up(client, did, "a.mp3", b"first")
    client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"second!")},
                data={"confirm_replace_audio": "true"})
    body = client.get(f"/api/media/dramas/{did}/status").json()
    assert body["kept_media_files"] == 1 and body["kept_media_bytes"] == len(b"first")
    assert "replaced" not in str(body) and "kept_media/" not in str(body)


def test_a_title_naming_a_folder_never_has_the_folder_moved(client, isolated_db):
    # An imported title could name a folder ("pages") as its audio; a replace
    # must leave that folder where it is, contents and all.
    import db
    did = db.create_drama(title_en="D")
    ddir = db.drama_dir(did)
    os.makedirs(os.path.join(ddir, "pages"))
    with open(os.path.join(ddir, "pages", "001.png"), "wb") as f:
        f.write(b"page")
    db.update_drama(did, audio_filename="pages")
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("a.mp3", b"new")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200
    assert db.get_drama(did)["audio_filename"] == "source.mp3"
    assert _read(did, os.path.join("pages", "001.png")) == b"page"
    assert _kept(did) == {}


def test_import_drops_a_file_reference_that_names_a_folder(tmp_path):
    from services import auto_backup_service as abs_
    os.makedirs(tmp_path / "pages")
    (tmp_path / "a.mp3").write_bytes(b"a")
    assert abs_._import_file_ref(str(tmp_path), "pages", None) is None
    assert abs_._import_file_ref(str(tmp_path), "a.mp3", None) == "a.mp3"
    # unchanged: a name whose file wasn't in the backup is still kept
    assert abs_._import_file_ref(str(tmp_path), "missing.mp3", None) == "missing.mp3"


def _status(client, did):
    return client.get(f"/api/media/dramas/{did}/status").json()


def test_media_left_unnamed_by_a_crash_is_counted_then_kept(client, isolated_db, monkeypatch):
    # A crash after the new file was put in place but before the DB named it:
    # possibly the user's only copy, so it is counted and moved, never deleted.
    import db
    from services import drama_service, media_upload_service as m
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200
    ddir = db.drama_dir(did)
    for name, data in (("source-2.mp3", b"new upload"), ("audio-3.wav", b"new wav"),
                       ("source.txt", b"mine"), ("notes.mp3", b"mine too"), ("cover.wav", b"art")):
        with open(os.path.join(ddir, name), "wb") as f:
            f.write(data)
    os.makedirs(os.path.join(ddir, "audio-2.wav"))  # never a folder
    db.update_drama(did, cover_art_filename="cover.wav")
    body = _status(client, did)
    assert body["kept_media_files"] == 2
    assert body["kept_media_bytes"] == len(b"new upload") + len(b"new wav")

    monkeypatch.setattr(drama_service, "job_running_for_drama", lambda _d: True)
    assert m.recover_all_stale_uploads() == 0  # a job may be about to record them
    assert _status(client, did)["kept_media_files"] == 0  # nor counted while it runs
    monkeypatch.undo()
    monkeypatch.setattr(m, "UNNAMED_MIN_AGE_SECONDS", 0)  # the age rule has its own test
    assert m.recover_all_stale_uploads() == 2
    kept = _kept(did)
    assert sorted(kept.values()) == [b"new upload", b"new wav"]
    assert all(n.startswith("unreferenced-") for n in kept)
    assert sorted(os.listdir(ddir)) == ["audio-2.wav", "cover.wav", "kept_media", "notes.mp3",
                                        "source.mp3", "source.txt"]
    assert db.get_drama(did)["audio_filename"] == "source.mp3" and _read(did, "source.mp3") == b"first"
    assert _status(client, did)["kept_media_files"] == 2


def test_startup_recovery_skips_a_drama_with_an_upload_in_progress(isolated_db):
    import db
    from services import media_upload_service as m
    did = db.create_drama(title_en="D")
    with open(os.path.join(db.drama_dir(did), "source.mp3"), "wb") as f:
        f.write(b"being recorded")
    m.claimed.add(did)
    try:
        assert m.recover_all_stale_uploads() == 0
    finally:
        m.claimed.discard(did)
    assert os.listdir(db.drama_dir(did)) == ["source.mp3"]


def test_unnamed_media_is_only_recovered_once_it_is_old(client, isolated_db):
    # Another process may have just linked its upload into place and not yet
    # named it in the DB; its claim is invisible here and an audio upload
    # starts no job. A link keeps the old mtime, so ctime counts too.
    import db
    from services import media_upload_service as m
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200
    ddir = db.drama_dir(did)
    with open(os.path.join(ddir, "source-2.mp3"), "wb") as f:
        f.write(b"other process")
    old_t = time.time() - m.UNNAMED_MIN_AGE_SECONDS - 60
    os.utime(os.path.join(ddir, "source-2.mp3"), (old_t, old_t))
    assert m.recover_all_stale_uploads() == 0
    assert m.recover_stale_uploads(did) == 0
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"second")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200 and r.json()["name"] == "source-3.mp3"
    assert _read(did, "source-2.mp3") == b"other process"
    later = time.time() + m.UNNAMED_MIN_AGE_SECONDS + 1
    assert m.recover_stale_uploads(did, now=later) == 1
    assert sorted(_kept(did).values()) == [b"first", b"other process"]


def test_installed_media_counts_as_new_even_from_an_old_source(isolated_db, tmp_path):
    # On Windows a link or rename into place changes neither mtime nor ctime,
    # so until the DB names it the file would look old enough to recover.
    import db
    from services import media_upload_service as m
    did = db.create_drama(title_en="D")
    src = tmp_path / "download.mp3"
    src.write_bytes(b"media")
    old_t = time.time() - m.UNNAMED_MIN_AGE_SECONDS - 60
    os.utime(src, (old_t, old_t))
    before = time.time()
    names = m.install_media(did, {"audio_filename": (str(src), "source", ".mp3")})
    st = os.stat(os.path.join(db.drama_dir(did), names["audio_filename"]))
    assert st.st_mtime >= before - 1


def test_a_file_the_drama_names_by_another_spelling_is_never_moved(client, isolated_db,
                                                                    monkeypatch):
    # Windows and macOS resolve "Audio.wav" to "audio.wav". A hard link stands
    # in for that here, so the test doesn't depend on this filesystem's case
    # rules: the drama names one spelling, the in-place name is the same file.
    import db
    from services import media_upload_service as m
    monkeypatch.setattr(m, "UNNAMED_MIN_AGE_SECONDS", 0)
    did = db.create_drama(title_en="D")
    ddir = db.drama_dir(did)
    with open(os.path.join(ddir, "Live Audio.wav"), "wb") as f:
        f.write(b"live")
    try:
        os.link(os.path.join(ddir, "Live Audio.wav"), os.path.join(ddir, "audio.wav"))
    except OSError:
        pytest.skip("no hard links on this filesystem")
    db.update_drama(did, audio_filename="Live Audio.wav")
    assert _status(client, did)["kept_media_files"] == 0
    assert m.recover_all_stale_uploads() == 0
    assert _read(did, "audio.wav") == b"live" and _kept(did) == {}


def test_a_failed_rollback_leaves_the_upload_counted_and_recovered(client, isolated_db, monkeypatch):
    # The DB switch fails and moving the new file back fails too: it stays
    # in the folder under its in-place name, still kept as the error says.
    import sqlite3
    import db
    from services import media_upload_service as m
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    def no_rename(*a, **kw):
        raise PermissionError(13, "in use")
    monkeypatch.setattr(db, "update_drama", locked)
    monkeypatch.setattr(m.os, "rename", no_rename)
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp3", b"second")},
                    data={"confirm_replace_audio": "true"})
    monkeypatch.undo()
    monkeypatch.setattr(m, "UNNAMED_MIN_AGE_SECONDS", 0)
    assert r.status_code == 409 and r.json()["error"]["message"] == m._SAVE_FAILED
    assert db.get_drama(did)["audio_filename"] == "source.mp3"
    assert _read(did, "source-2.mp3") == b"second"
    body = _status(client, did)
    assert (body["kept_media_files"], body["kept_media_bytes"]) == (1, len(b"second"))
    # moved at the next upload, which still needs the confirm
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("c.mp3", b"third")},
                    data={"confirm_replace_audio": "true"})
    assert r.status_code == 200 and r.json()["name"] == "source-2.mp3"
    assert sorted(_kept(did).values()) == [b"first", b"second"]


def test_an_old_file_that_was_in_use_is_counted_then_kept(client, isolated_db, monkeypatch):
    import db
    from services import media_upload_service as m
    did = db.create_drama(title_en="D")
    _video_title(client, did)
    busy = os.path.join(db.drama_dir(did), "audio.wav")
    real_unlink, real_rename = os.unlink, os.rename

    def guard(fn):
        def wrapper(src, *a, **kw):
            if os.path.abspath(src) == busy:
                raise PermissionError(13, "in use")
            return fn(src, *a, **kw)
        return wrapper
    monkeypatch.setattr(m.os, "unlink", guard(real_unlink))
    monkeypatch.setattr(m.os, "rename", guard(real_rename))
    assert _replace_video(client, did)["status"] == "done"
    assert _status(client, did)["kept_media_files"] == 2  # old video moved, old audio still in place
    monkeypatch.undo()
    monkeypatch.setattr(m, "UNNAMED_MIN_AGE_SECONDS", 0)
    assert m.recover_stale_uploads(did) == 1
    assert sorted(_kept(did).values()) == [b"old", b"wav"]
    assert db.get_drama(did)["audio_filename"] == "audio-2.wav"


def test_new_media_is_fsynced_before_the_db_names_it(client, isolated_db, monkeypatch):
    import db
    from services import media_upload_service as m
    events = []
    real_fsync_file, real_update = m._fsync_file, db.update_drama

    def spy_fsync(path):
        events.append(("fsync", os.path.basename(path), os.path.exists(path)))
        real_fsync_file(path)

    def spy_update(*a, **kw):
        events.append(("db",))
        return real_update(*a, **kw)
    monkeypatch.setattr(m, "_fsync_file", spy_fsync)
    monkeypatch.setattr(db, "update_drama", spy_update)
    did = db.create_drama(title_en="D")
    assert _wait(_up(client, did, "a.mp4", b"vid").json()["job_id"])["status"] == "done"
    assert [e[0] for e in events] == ["fsync", "fsync", "db"]
    names = sorted(e[1] for e in events[:2])
    assert names[0] == ".audio.extract.wav" and names[1].startswith(".upload_")
    assert all(e[2] for e in events[:2])  # synced at its source path, before the move


def test_fsync_file_syncs_the_data_and_never_fails_the_install(tmp_path, monkeypatch):
    from services import media_upload_service as m
    synced = []
    real = os.fsync
    monkeypatch.setattr(m.os, "fsync", lambda fd: synced.append(fd) or real(fd))
    path = tmp_path / "a.wav"
    path.write_bytes(b"x")
    m._fsync_file(str(path))
    assert len(synced) == 1
    m._fsync_file(str(tmp_path / "gone.wav"))  # best effort: no error


def _replace_with_audio(client, did, data=b"new audio"):
    return client.post(f"/api/media/dramas/{did}/upload", files={"file": ("dub.mp3", data)},
                       data={"confirm_replace_audio": "true"})


def test_replacing_a_video_with_audio_sets_the_video_aside(client, isolated_db):
    # An old video left named beside new audio would play its picture over
    # sound that isn't its own, in Review and in every video export.
    import db
    did = db.create_drama(title_en="D")
    _video_title(client, did)
    r = _replace_with_audio(client, did)
    assert r.status_code == 200, r.text
    after = db.get_drama(did)
    assert (after["audio_filename"], after["source_video_filename"]) == ("source.mp3", None)
    assert sorted(os.listdir(db.drama_dir(did))) == ["kept_media", "source.mp3"]
    kept = _kept(did)
    assert sorted(kept.values()) == [b"old", b"wav"]
    assert all(n.startswith("replaced-") for n in kept)
    body = _status(client, did)
    assert (body["has_audio"], body["has_source_video"]) == (True, False)
    assert body["kept_media_files"] == 2
    source = client.get(f"/api/source/dramas/{did}/config").json()
    assert source["has_video_source"] is False


def test_video_exports_refuse_plainly_once_the_video_was_replaced_by_audio(client, isolated_db,
                                                                         monkeypatch):
    import db
    from core import Line
    did = db.create_drama(title_en="D")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    _video_title(client, did)
    assert _replace_with_audio(client, did).status_code == 200
    ran = []
    monkeypatch.setattr(background_jobs, "run_cancellable", lambda *a, **kw: ran.append(a))
    for path in ("burned-video", "softsub-video", "dubbed-video"):
        r = client.post(f"/api/export/dramas/{did}/{path}", json={})
        assert r.status_code == 422, (path, r.text)
        assert r.json()["error"]["message"] == "No source video uploaded for this drama."
    assert ran == []


def test_a_failed_db_switch_on_audio_replace_keeps_video_and_audio(client, isolated_db,
                                                                   monkeypatch):
    import sqlite3
    import db
    from services import media_upload_service
    did = db.create_drama(title_en="D")
    before = _video_title(client, did)

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "update_drama", locked)
    r = _replace_with_audio(client, did, b"new")
    assert r.status_code == 409 and r.json()["error"]["message"] == media_upload_service._SAVE_FAILED
    _assert_title_unchanged(did, before)
    assert _status(client, did)["has_source_video"] is True


def test_a_crash_before_the_old_video_was_set_aside_is_recovered(client, isolated_db,
                                                                 monkeypatch):
    # The DB write is the commit point and the old files are moved only
    # after it, so a crash in between leaves them in place but unnamed.
    import db
    from services import media_upload_service as m
    did = db.create_drama(title_en="D")
    _video_title(client, did)
    monkeypatch.setattr(m, "_retire", lambda *a, **kw: None)
    assert _replace_with_audio(client, did).status_code == 200
    monkeypatch.undo()
    assert db.get_drama(did)["source_video_filename"] is None
    ddir = db.drama_dir(did)
    assert sorted(os.listdir(ddir)) == ["audio.wav", "source.mp3", "source.mp4"]
    body = _status(client, did)
    assert body["has_source_video"] is False and body["kept_media_files"] == 2
    later = time.time() + m.UNNAMED_MIN_AGE_SECONDS + 1
    assert m.recover_stale_uploads(did, now=later) == 2
    kept = _kept(did)
    assert sorted(kept.values()) == [b"old", b"wav"]
    assert all(n.startswith("unreferenced-") for n in kept)
    assert sorted(os.listdir(ddir)) == ["kept_media", "source.mp3"]
    assert _read(did, "source.mp3") == b"new audio"


def test_replacing_audio_with_a_video_attaches_it_and_sets_the_old_audio_aside(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"first").status_code == 200
    r = client.post(f"/api/media/dramas/{did}/upload", files={"file": ("b.mp4", b"vid")},
                    data={"confirm_replace_audio": "true"})
    assert _wait(r.json()["job_id"])["status"] == "done"
    after = db.get_drama(did)
    assert (after["source_video_filename"], after["audio_filename"]) == ("source.mp4", "audio.wav")
    assert _status(client, did)["has_source_video"] is True
    assert list(_kept(did).values()) == [b"first"]
