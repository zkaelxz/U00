"""Tests for Migration Slices 29-30: audiobook and burned-in video export jobs.
ffmpeg is never run: subprocess.run / shutil.which are patched."""

import os
import subprocess

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


import background_jobs
from jobs import job_table
import storage
import video_export
from core import Line
from services import artifact_service, export_service, media_export_service
from services.service_errors import NotFoundError
from tests.api_helpers import error_of
from tests.job_helpers import wait_for_job


class FakeFfmpeg:
    def __init__(self):
        self.calls = []
        self.fail = False


@pytest.fixture(autouse=True)
def fake_ffmpeg(monkeypatch):
    """ffmpeg 'installed'; subprocess.run records the command and writes the
    output file (the last argument, relative to cwd when cwd is given)."""
    fake = FakeFfmpeg()
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    # The frame-size probe runs ffprobe, which this fake would answer as ffmpeg.
    monkeypatch.setattr(export_service, "video_frame_aspect", lambda path: None)

    def fake_run(cmd, **kwargs):
        fake.calls.append((cmd, kwargs))
        if fake.fail:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"/secret/path")
        out = cmd[-1]
        if kwargs.get("cwd"):
            out = os.path.join(kwargs["cwd"], out)
        with open(out, "wb") as f:
            f.write(b"media")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(background_jobs, "run_cancellable",
                        lambda job_id, cmd, **kw: fake_run(cmd, **kw))
    return fake


@pytest.fixture(autouse=True)
def clean_jobs():
    with job_table._lock:
        job_table._jobs.clear()
    yield
    with job_table._lock:
        job_table._jobs.clear()


@pytest.fixture
def drama(isolated_db):
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [
        Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
        Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
    ])
    return did


def _touch(isolated_db, did, name):
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    with open(os.path.join(ddir, name), "wb") as f:
        f.write(b"RIFFxxxx")


def _no_artifact(did, kind):
    with pytest.raises(NotFoundError):
        artifact_service.get_artifact(did, kind)


def test_job_prefixes_registered():
    assert "audiobook_" in background_jobs.DRAMA_JOB_PREFIXES
    assert "burned_video_" in background_jobs.DRAMA_JOB_PREFIXES


# ---- Slice 29: audiobook ----------------------------------------------------

def test_audiobook_runs_and_writes_artifact(local_client, drama, isolated_db, fake_ffmpeg):
    _touch(isolated_db, drama, "narration_track.wav")
    r = local_client.post(f"/api/export/dramas/{drama}/audiobook")
    assert r.status_code == 200
    assert r.json() == {"job_id": f"audiobook_{drama}"}
    assert wait_for_job(f"audiobook_{drama}", timeout=6.0)["status"] == "done"
    assert artifact_service.get_artifact(drama, "audio")["name"] == f"audiobook_{drama}.m4b"
    cmd, kwargs = fake_ffmpeg.calls[0]
    assert cmd[0] == "ffmpeg" and "ipod" in cmd
    assert "shell" not in kwargs
    assert local_client.get(f"/api/artifacts/dramas/{drama}/audio").status_code == 200


def test_audiobook_unknown_drama_404(local_client):
    assert local_client.post("/api/export/dramas/999/audiobook").status_code == 404


def test_audiobook_no_lines_422(local_client, isolated_db):
    did = isolated_db.create_drama(title_en="Empty")
    r = local_client.post(f"/api/export/dramas/{did}/audiobook")
    assert r.status_code == 422
    assert error_of(r)["message"] == "This title has no lines to export."


def test_audiobook_no_narration_422(local_client, drama):
    r = local_client.post(f"/api/export/dramas/{drama}/audiobook")
    assert r.status_code == 422
    assert error_of(r)["message"] == "There is no narration yet. Create it with Listen in the Reader first."


def test_audiobook_ffmpeg_missing_503(local_client, drama, isolated_db, monkeypatch):
    _touch(isolated_db, drama, "narration_track.wav")
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: None)
    r = local_client.post(f"/api/export/dramas/{drama}/audiobook")
    assert r.status_code == 503
    assert "ffmpeg" in error_of(r)["message"]


def test_audiobook_duplicate_409(local_client, drama, isolated_db):
    _touch(isolated_db, drama, "narration_track.wav")
    with job_table._lock:
        job_table._jobs[f"audiobook_{drama}"] = {"status": "running"}
    assert local_client.post(f"/api/export/dramas/{drama}/audiobook").status_code == 409


def test_audiobook_failure_leaves_no_artifact_and_no_paths(local_client, drama, isolated_db, fake_ffmpeg):
    _touch(isolated_db, drama, "narration_track.wav")
    fake_ffmpeg.fail = True
    local_client.post(f"/api/export/dramas/{drama}/audiobook")
    st = wait_for_job(f"audiobook_{drama}", timeout=6.0)
    assert st["status"] == "error"
    assert "/secret" not in st["error"] and isolated_db.drama_dir(drama) not in st["error"]
    _no_artifact(drama, "audio")


# ---- Slice 30: burned-in video ----------------------------------------------

def _add_video(isolated_db, did, name="source.mp4"):
    _touch(isolated_db, did, name)
    isolated_db.update_drama(did, source_video_filename=name)


def test_burned_video_runs_and_writes_artifact(local_client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama)
    r = local_client.post(f"/api/export/dramas/{drama}/burned-video", json={"style": {"size": 30}})
    assert r.status_code == 200
    assert r.json() == {"job_id": f"burned_video_{drama}"}
    assert wait_for_job(f"burned_video_{drama}", timeout=6.0)["status"] == "done"
    assert artifact_service.get_artifact(drama, "video")["name"] == f"burned_video_{drama}.mp4"
    cmd, kwargs = fake_ffmpeg.calls[0]
    assert cmd[0] == "ffmpeg" and isinstance(cmd, list) and "shell" not in kwargs
    assert cmd[cmd.index("-vf") + 1] == "subtitles=subs.ass"
    assert kwargs["cwd"]
    assert local_client.get(f"/api/artifacts/dramas/{drama}/video").status_code == 200


def test_burned_video_keeps_overlapping_cues(local_client, drama, isolated_db, monkeypatch):
    isolated_db.save_lines(drama, [
        Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
        Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
    ])
    _add_video(isolated_db, drama)
    seen = {}

    def fake(job_id, cmd, cwd=None, **kw):
        with open(os.path.join(cwd, "subs.ass"), encoding="utf-8") as f:
            seen["ass"] = f.read()
        with open(os.path.join(cwd, cmd[-1]), "wb") as f:
            f.write(b"x")
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(background_jobs, "run_cancellable", fake)
    assert local_client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 200
    assert wait_for_job(f"burned_video_{drama}", timeout=6.0)["status"] == "done"
    assert "0:00:00.00,0:00:03.00" in seen["ass"] and "0:00:02.00,0:00:04.00" in seen["ass"]


@pytest.mark.parametrize("endpoint,body,job", [
    ("burned-video", {"field": "zh"}, "burned_video_"),
    ("softsub-video", {"field": "zh", "subtitle_format": "ass"}, "softsub_video_"),
])
def test_ass_video_exports_fit_long_cjk_rows_to_a_portrait_video(
        local_client, drama, isolated_db, monkeypatch, endpoint, body, job):
    import subtitle_formats
    text = "他说这件事我们明天再谈吧今天太晚了然后转身离开了房间"
    isolated_db.save_lines(drama, [Line(idx=0, start=0.0, end=3.0, zh=text, en="")])
    _add_video(isolated_db, drama, "source.mp4")
    probed = []
    monkeypatch.setattr(export_service, "video_frame_aspect",
                        lambda path: probed.append(path) or 9 / 16)
    seen = {}

    def fake(job_id, cmd, cwd=None, **kw):
        with open(os.path.join(cwd, "subs.ass"), encoding="utf-8") as f:
            seen["ass"] = f.read()
        with open(os.path.join(cwd, cmd[-1]), "wb") as f:
            f.write(b"x")
    monkeypatch.setattr(background_jobs, "run_cancellable", fake)
    assert local_client.post(f"/api/export/dramas/{drama}/{endpoint}", json=body).status_code == 200
    assert wait_for_job(f"{job}{drama}", timeout=6.0)["status"] == "done"
    assert probed and probed[0].endswith("source.mp4")
    rows = next(l for l in seen["ass"].splitlines() if l.startswith("Dialogue:")).split(",", 9)[9]
    limit = subtitle_formats.ass_cjk_row_limit(subtitle_formats.ASS_PRESETS["Clean"], 9 / 16)
    assert "".join(rows.split("\\N")) == text
    assert all(len(r) <= limit for r in rows.split("\\N"))


def test_burned_video_ffmpeg_has_a_timeout(local_client, drama, isolated_db, fake_ffmpeg):
    """B-05 leftover: the burned-video ffmpeg run is bounded like the others."""
    _add_video(isolated_db, drama)
    local_client.post(f"/api/export/dramas/{drama}/burned-video", json={})
    assert wait_for_job(f"burned_video_{drama}", timeout=6.0)["status"] == "done"
    assert fake_ffmpeg.calls[0][1]["timeout"] == media_export_service._VIDEO_TIMEOUT_S


def test_burned_video_body_optional(local_client, drama, isolated_db):
    _add_video(isolated_db, drama)
    assert local_client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 200
    wait_for_job(f"burned_video_{drama}", timeout=6.0)


@pytest.mark.parametrize("body", [
    {"style": {"font": "Ar\nial"}}, {"style": {"font": "A\x07"}}, {"style": {"size": 999}},
    {"preset": "Nope"}, {"style": {"primary": "red"}}])
def test_burned_video_bad_style_422(local_client, drama, isolated_db, body, fake_ffmpeg):
    _add_video(isolated_db, drama)
    assert local_client.post(f"/api/export/dramas/{drama}/burned-video", json=body).status_code == 422
    assert not fake_ffmpeg.calls


def test_burned_video_unknown_drama_404(local_client):
    assert local_client.post("/api/export/dramas/999/burned-video").status_code == 404


def test_burned_video_no_lines_422(local_client, isolated_db):
    did = isolated_db.create_drama(title_en="Empty")
    assert local_client.post(f"/api/export/dramas/{did}/burned-video").status_code == 422


def test_burned_video_no_source_video_422(local_client, drama):
    r = local_client.post(f"/api/export/dramas/{drama}/burned-video")
    assert r.status_code == 422
    assert error_of(r)["message"] == "No source video uploaded for this title."


def test_burned_video_traversal_filename_rejected(local_client, drama, isolated_db):
    isolated_db.update_drama(drama, source_video_filename="../evil.mp4")
    assert local_client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 422


def test_burned_video_ffmpeg_missing_503(local_client, drama, isolated_db, monkeypatch):
    _add_video(isolated_db, drama)
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: None)
    assert local_client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 503


def test_burned_video_duplicate_409(local_client, drama, isolated_db):
    _add_video(isolated_db, drama)
    with job_table._lock:
        job_table._jobs[f"burned_video_{drama}"] = {"status": "queued"}
    assert local_client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 409


def test_burned_video_failure_no_artifact_no_paths(local_client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama)
    fake_ffmpeg.fail = True
    local_client.post(f"/api/export/dramas/{drama}/burned-video")
    st = wait_for_job(f"burned_video_{drama}", timeout=6.0)
    assert st["status"] == "error"
    assert "/secret" not in st["error"] and isolated_db.drama_dir(drama) not in st["error"]
    _no_artifact(drama, "video")


# ---- B-05: cancel kills the ffmpeg run -------------------------------------

@pytest.fixture
def cancelled_ffmpeg(monkeypatch):
    def fake(job_id, cmd, **kw):
        raise background_jobs.JobCancelled(job_id)
    monkeypatch.setattr(background_jobs, "run_cancellable", fake)


def test_audiobook_cancel_ends_cancelled_no_artifact(local_client, drama, isolated_db,
                                                      fake_ffmpeg, cancelled_ffmpeg):
    _touch(isolated_db, drama, "narration_track.wav")
    local_client.post(f"/api/export/dramas/{drama}/audiobook")
    assert wait_for_job(f"audiobook_{drama}", timeout=6.0)["status"] == "cancelled"
    _no_artifact(drama, "audio")


def test_burned_video_cancel_ends_cancelled_no_artifact(local_client, drama, isolated_db,
                                                         fake_ffmpeg, cancelled_ffmpeg):
    _add_video(isolated_db, drama)
    local_client.post(f"/api/export/dramas/{drama}/burned-video")
    assert wait_for_job(f"burned_video_{drama}", timeout=6.0)["status"] == "cancelled"
    _no_artifact(drama, "video")


# ---- Parity E17: soft-subtitle video ----------------------------------------

def test_new_video_prefixes_registered():
    assert "softsub_video_" in background_jobs.DRAMA_JOB_PREFIXES
    assert "dubbed_video_" in background_jobs.DRAMA_JOB_PREFIXES


def test_softsub_runs_and_writes_artifact(local_client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama, "source.mkv")
    r = local_client.post(f"/api/export/dramas/{drama}/softsub-video", json={"field": "bilingual"})
    assert r.status_code == 200
    assert r.json() == {"job_id": f"softsub_video_{drama}"}
    assert wait_for_job(f"softsub_video_{drama}", timeout=6.0)["status"] == "done"
    assert artifact_service.get_artifact(drama, "softsub_video")["name"] == f"softsub_video_{drama}.mkv"
    cmd, kwargs = fake_ffmpeg.calls[0]
    assert cmd[0] == "ffmpeg" and "shell" not in kwargs and kwargs["cwd"]
    assert "subs.srt" in cmd and cmd[cmd.index("-c:s") + 1] == "srt"
    assert "language=und" in cmd


def test_softsub_other_container_becomes_mkv(local_client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama, "source.webm")
    assert local_client.post(f"/api/export/dramas/{drama}/softsub-video").status_code == 200
    assert wait_for_job(f"softsub_video_{drama}", timeout=6.0)["status"] == "done"
    assert artifact_service.get_artifact(drama, "softsub_video")["name"] == f"softsub_video_{drama}.mkv"
    cmd, _ = fake_ffmpeg.calls[0]
    assert cmd[-1].endswith(".mkv") and cmd[cmd.index("-c:s") + 1] == "srt" and "language=eng" in cmd


def test_softsub_source_track_is_tagged_with_the_title_language(local_client, drama, isolated_db,
                                                                fake_ffmpeg):
    isolated_db.update_drama(drama, source_language="ja")
    _add_video(isolated_db, drama, "source.mkv")
    assert local_client.post(f"/api/export/dramas/{drama}/softsub-video",
                             json={"field": "zh"}).status_code == 200
    assert wait_for_job(f"softsub_video_{drama}", timeout=6.0)["status"] == "done"
    cmd, _ = fake_ffmpeg.calls[0]
    assert "language=jpn" in cmd


def test_ass_exports_refuse_a_title_with_nothing_translated(local_client, isolated_db, fake_ffmpeg):
    raw = isolated_db.create_drama(title_en="Raw")
    isolated_db.save_lines(raw, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="")])
    _add_video(isolated_db, raw)
    r = local_client.post(f"/api/export/dramas/{raw}/ass", json={"field": "en"})
    assert r.status_code == 409 and "no translated lines" in r.json()["error"]["message"]
    r = local_client.post(f"/api/export/dramas/{raw}/burned-video", json={"field": "en"})
    assert r.status_code == 409
    r = local_client.post(f"/api/export/dramas/{raw}/softsub-video",
                          json={"field": "en", "subtitle_format": "ass"})
    assert r.status_code == 409
    assert fake_ffmpeg.calls == []
    assert local_client.post(f"/api/export/dramas/{raw}/ass", json={"field": "zh"}).status_code == 200


def test_softsub_srt_holds_the_lines(local_client, drama, isolated_db, monkeypatch):
    _add_video(isolated_db, drama)
    seen = {}

    def fake(job_id, cmd, cwd=None, **kw):
        with open(os.path.join(cwd, "subs.srt"), encoding="utf-8") as f:
            seen["srt"] = f.read()
        with open(os.path.join(cwd, cmd[-1]), "wb") as f:
            f.write(b"x")
        seen["timeout"] = kw.get("timeout")
    monkeypatch.setattr(background_jobs, "run_cancellable", fake)
    local_client.post(f"/api/export/dramas/{drama}/softsub-video", json={"field": "zh"})
    assert wait_for_job(f"softsub_video_{drama}", timeout=6.0)["status"] == "done"
    assert "你好" in seen["srt"] and "Hello" not in seen["srt"]
    assert seen["timeout"] and seen["timeout"] > 0


def test_softsub_ass_forces_mkv_and_keeps_overlapping_cues(local_client, drama, isolated_db, monkeypatch):
    isolated_db.save_lines(drama, [
        Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
        Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
    ])
    _add_video(isolated_db, drama, "source.mp4")
    seen = {}

    def fake(job_id, cmd, cwd=None, **kw):
        with open(os.path.join(cwd, "subs.ass"), encoding="utf-8") as f:
            seen["ass"] = f.read()
        seen["cmd"] = cmd
        with open(os.path.join(cwd, cmd[-1]), "wb") as f:
            f.write(b"x")
    monkeypatch.setattr(background_jobs, "run_cancellable", fake)
    r = local_client.post(f"/api/export/dramas/{drama}/softsub-video", json={"subtitle_format": "ass"})
    assert r.status_code == 200
    assert wait_for_job(f"softsub_video_{drama}", timeout=6.0)["status"] == "done"
    assert artifact_service.get_artifact(drama, "softsub_video")["name"] == f"softsub_video_{drama}.mkv"
    cmd = seen["cmd"]
    assert cmd[-1].endswith(".mkv") and cmd[cmd.index("-c:s") + 1] == "ass"
    # Line 0 still ends at 0:00:03.00, after line 1 starts at 0:00:02.00.
    assert "0:00:00.00,0:00:03.00" in seen["ass"] and "0:00:02.00,0:00:04.00" in seen["ass"]


def test_newer_softsub_export_removes_the_older_one_of_another_container(
        local_client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama)
    assert local_client.post(f"/api/export/dramas/{drama}/softsub-video").status_code == 200
    assert wait_for_job(f"softsub_video_{drama}", timeout=6.0)["status"] == "done"
    kind_dir = os.path.dirname(artifact_service.get_artifact(drama, "softsub_video")["path"])
    assert os.listdir(kind_dir).count(f"softsub_video_{drama}.mp4") == 1
    with job_table._lock:
        job_table._jobs.clear()
    r = local_client.post(f"/api/export/dramas/{drama}/softsub-video", json={"subtitle_format": "ass"})
    assert r.status_code == 200
    assert wait_for_job(f"softsub_video_{drama}", timeout=6.0)["status"] == "done"
    served = artifact_service.get_artifact(drama, "softsub_video")
    assert served["name"] == f"softsub_video_{drama}.mkv"
    assert [n for n in os.listdir(kind_dir) if not n.startswith(".")] == [served["name"]]
    assert served["language"] == "en"


@pytest.mark.parametrize("body", [{"field": "fr"}, {"field": "en", "extra": 1},
                                  {"include_notes": "yes"}, {"subtitle_format": "vtt"}])
def test_softsub_bad_body_422(local_client, drama, isolated_db, body, fake_ffmpeg):
    _add_video(isolated_db, drama)
    assert local_client.post(f"/api/export/dramas/{drama}/softsub-video", json=body).status_code == 422
    assert not fake_ffmpeg.calls


def test_softsub_guards(local_client, drama, isolated_db, monkeypatch):
    assert local_client.post("/api/export/dramas/999/softsub-video").status_code == 404
    r = local_client.post(f"/api/export/dramas/{drama}/softsub-video")
    assert r.status_code == 422 and error_of(r)["message"] == "No source video uploaded for this title."
    empty = isolated_db.create_drama(title_en="Empty")
    _add_video(isolated_db, empty)
    assert local_client.post(f"/api/export/dramas/{empty}/softsub-video").status_code == 422
    _add_video(isolated_db, drama)
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: None)
    assert local_client.post(f"/api/export/dramas/{drama}/softsub-video").status_code == 503


@pytest.mark.parametrize("running", ["burned_video_", "softsub_video_", "dubbed_video_"])
def test_video_jobs_one_at_a_time(local_client, drama, isolated_db, running):
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    with job_table._lock:
        job_table._jobs[f"{running}{drama}"] = {"status": "running"}
    for path in ("softsub-video", "dubbed-video", "burned-video"):
        assert local_client.post(f"/api/export/dramas/{drama}/{path}").status_code == 409, path


def test_softsub_failure_and_timeout_no_artifact_no_paths(local_client, drama, isolated_db, monkeypatch):
    _add_video(isolated_db, drama)

    def hang(job_id, cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(background_jobs, "run_cancellable", hang)
    local_client.post(f"/api/export/dramas/{drama}/softsub-video")
    st = wait_for_job(f"softsub_video_{drama}", timeout=6.0)
    assert st["status"] == "error" and "too long" in st["error"]
    assert isolated_db.drama_dir(drama) not in st["error"]
    _no_artifact(drama, "softsub_video")


def test_softsub_cancel(local_client, drama, isolated_db, fake_ffmpeg, cancelled_ffmpeg):
    _add_video(isolated_db, drama)
    local_client.post(f"/api/export/dramas/{drama}/softsub-video")
    assert wait_for_job(f"softsub_video_{drama}", timeout=6.0)["status"] == "cancelled"
    _no_artifact(drama, "softsub_video")


# ---- Parity E19: dubbed video ------------------------------------------------

def test_dubbed_replace_runs_and_writes_artifact(local_client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    r = local_client.post(f"/api/export/dramas/{drama}/dubbed-video")
    assert r.status_code == 200 and r.json() == {"job_id": f"dubbed_video_{drama}"}
    assert wait_for_job(f"dubbed_video_{drama}", timeout=6.0)["status"] == "done"
    assert artifact_service.get_artifact(drama, "dubbed_video")["name"] == f"dubbed_video_{drama}.mp4"
    cmd, kwargs = fake_ffmpeg.calls[0]
    assert "-filter_complex" not in cmd and "-shortest" in cmd and "shell" not in kwargs
    assert os.path.join(isolated_db.drama_dir(drama), "dub_track.wav") in cmd


def test_dubbed_mix_keeps_original_quietly(local_client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    local_client.post(f"/api/export/dramas/{drama}/dubbed-video", json={"keep_original": True})
    assert wait_for_job(f"dubbed_video_{drama}", timeout=6.0)["status"] == "done"
    cmd, _ = fake_ffmpeg.calls[0]
    assert "volume=-20.0dB" in cmd[cmd.index("-filter_complex") + 1]


def test_dubbed_guards(local_client, drama, isolated_db, monkeypatch, fake_ffmpeg):
    assert local_client.post("/api/export/dramas/999/dubbed-video").status_code == 404
    assert local_client.post(f"/api/export/dramas/{drama}/dubbed-video").status_code == 422
    _add_video(isolated_db, drama)
    r = local_client.post(f"/api/export/dramas/{drama}/dubbed-video")
    assert r.status_code == 422 and "dub" in error_of(r)["message"]
    _touch(isolated_db, drama, "dub_track.wav")
    assert local_client.post(f"/api/export/dramas/{drama}/dubbed-video",
                       json={"keep_original": 1}).status_code == 422
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: None)
    assert local_client.post(f"/api/export/dramas/{drama}/dubbed-video").status_code == 503
    assert not fake_ffmpeg.calls


def test_dubbed_failure_no_artifact_no_paths(local_client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    fake_ffmpeg.fail = True
    local_client.post(f"/api/export/dramas/{drama}/dubbed-video")
    st = wait_for_job(f"dubbed_video_{drama}", timeout=6.0)
    assert st["status"] == "error"
    assert "/secret" not in st["error"] and isolated_db.drama_dir(drama) not in st["error"]
    _no_artifact(drama, "dubbed_video")


# ---- Parity E22: mark as exported --------------------------------------------

def test_mark_exported_sets_status_only(local_client, drama, isolated_db):
    isolated_db.update_drama(drama, status="translated", title_en="Keep")
    r = local_client.post(f"/api/export/dramas/{drama}/mark-exported")
    assert r.status_code == 200 and r.json() == {"drama_id": drama, "status": "exported"}
    d = isolated_db.get_drama(drama)
    assert d["status"] == "exported" and d["title_en"] == "Keep"
    assert [ln.en for ln in isolated_db.load_line_objects(drama)] == ["Hello", "Bye"]


def test_mark_exported_unknown_drama_404(local_client):
    assert local_client.post("/api/export/dramas/999/mark-exported").status_code == 404


# ---- L2: a failed write or move stores fixed text, never a path --------------

@pytest.mark.parametrize("path,job", [("softsub-video", "softsub_video_"),
                                      ("dubbed-video", "dubbed_video_"),
                                      ("burned-video", "burned_video_"),
                                      ("audiobook", "audiobook_")])
def test_video_write_failure_stores_no_paths(local_client, drama, isolated_db, monkeypatch, path, job):
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    _touch(isolated_db, drama, "narration_track.wav")
    ddir = isolated_db.drama_dir(drama)

    def broken_replace(src, dst):
        raise PermissionError(13, "Permission denied", dst)
    monkeypatch.setattr(storage.os, "replace", broken_replace)
    assert local_client.post(f"/api/export/dramas/{drama}/{path}").status_code == 200
    st = wait_for_job(f"{job}{drama}", timeout=6.0)
    assert st["status"] == "error" and "Could not write the export file." in st["error"]
    assert ddir not in st["error"] and "Permission denied" not in st["error"]
    listed = local_client.get("/api/jobs").text
    assert ddir not in listed and "Permission denied" not in listed


def test_softsub_srt_write_failure_stores_no_paths(local_client, drama, isolated_db, monkeypatch):
    _add_video(isolated_db, drama)
    real_open = open

    def broken_open(file, mode="r", *a, **kw):
        if str(file).endswith("subs.srt"):
            raise OSError(28, "No space left on device", str(file))
        return real_open(file, mode, *a, **kw)
    monkeypatch.setattr("builtins.open", broken_open)
    local_client.post(f"/api/export/dramas/{drama}/softsub-video")
    st = wait_for_job(f"softsub_video_{drama}", timeout=6.0)
    monkeypatch.setattr("builtins.open", real_open)
    assert st["status"] == "error" and "No space" not in st["error"]
    assert "subs.srt" not in st["error"] and "tmp" not in st["error"]


# ---- L3: one video job at a time (atomically), each with its own download ---

def test_each_video_job_has_its_own_download(local_client, drama, isolated_db):
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    for path, job in (("burned-video", "burned_video_"), ("softsub-video", "softsub_video_"),
                      ("dubbed-video", "dubbed_video_")):
        assert local_client.post(f"/api/export/dramas/{drama}/{path}").status_code == 200
        assert wait_for_job(f"{job}{drama}", timeout=6.0)["status"] == "done"
    assert local_client.get(f"/api/artifacts/dramas/{drama}/video/info").json()["name"] == \
        f"burned_video_{drama}.mp4"
    assert local_client.get(f"/api/artifacts/dramas/{drama}/softsub_video/info").json()["name"] == \
        f"softsub_video_{drama}.mp4"
    assert local_client.get(f"/api/artifacts/dramas/{drama}/dubbed_video/info").json()["name"] == \
        f"dubbed_video_{drama}.mp4"
    assert local_client.get(f"/api/artifacts/dramas/{drama}/softsub_video").status_code == 200


def test_concurrent_video_starts_only_one_wins(local_client, drama, isolated_db, monkeypatch):
    import threading as _t
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    release = _t.Event()
    real_status = background_jobs.get_status
    in_check = _t.Barrier(2, timeout=2)

    def slow_status(job_id):
        # Both requests reach the check before either starts its job; without the
        # lock both would pass. With it the second waits (Barrier times out).
        try:
            in_check.wait()
        except _t.BrokenBarrierError:
            pass
        return real_status(job_id)
    monkeypatch.setattr(background_jobs, "get_status", slow_status)

    def hold(job_id, cmd, cwd=None, **kw):
        release.wait(5)
        with open(os.path.join(cwd, cmd[-1]), "wb") as f:
            f.write(b"media")
    monkeypatch.setattr(background_jobs, "run_cancellable", hold)
    codes = []
    threads = [_t.Thread(target=lambda p=p: codes.append(
        local_client.post(f"/api/export/dramas/{drama}/{p}").status_code))
        for p in ("softsub-video", "dubbed-video")]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    release.set()
    monkeypatch.setattr(background_jobs, "get_status", real_status)
    assert sorted(codes) == [200, 409], codes
    for job in ("softsub_video_", "dubbed_video_"):
        st = background_jobs.get_status(f"{job}{drama}")
        if st:
            wait_for_job(f"{job}{drama}", timeout=6.0)


# ---- L4: the dubbed video waits for the drama's dub job ----------------------

@pytest.mark.parametrize("status", ["running", "queued"])
def test_dubbed_refused_while_dub_job_runs(local_client, drama, isolated_db, fake_ffmpeg, status):
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    with job_table._lock:
        job_table._jobs[f"dub_{drama}"] = {"status": status}
    r = local_client.post(f"/api/export/dramas/{drama}/dubbed-video")
    assert r.status_code == 409 and "dub" in error_of(r)["message"]
    assert background_jobs.get_status(f"dubbed_video_{drama}") is None
    with job_table._lock:
        job_table._jobs[f"dub_{drama}"] = {"status": "done"}
    assert local_client.post(f"/api/export/dramas/{drama}/dubbed-video").status_code == 200
    assert wait_for_job(f"dubbed_video_{drama}", timeout=6.0)["status"] == "done"


# ---- hardening: ffmpeg may only open local files -----------------------------

@pytest.mark.parametrize("path,body", [("burned-video", None), ("softsub-video", None),
                                       ("softsub-video", {"subtitle_format": "ass"}),
                                       ("dubbed-video", None),
                                       ("dubbed-video", {"keep_original": True})])
def test_video_ffmpeg_inputs_are_file_only(local_client, drama, isolated_db, fake_ffmpeg, path, body):
    _add_video(isolated_db, drama)
    _touch(isolated_db, drama, "dub_track.wav")
    assert local_client.post(f"/api/export/dramas/{drama}/{path}", json=body).status_code == 200
    assert wait_for_job(f"{path.replace('-', '_')}_{drama}", timeout=6.0)["status"] == "done"
    cmd, _ = fake_ffmpeg.calls[0]
    inputs = [i for i, a in enumerate(cmd) if a == "-i"]
    assert inputs
    assert cmd[inputs[0] - 4:inputs[0]] == video_export.local_input(), cmd
    for i in inputs:
        assert cmd[i - 4:i - 1] == ["-protocol_whitelist", "file", "-format_whitelist"], cmd


def test_dubbed_video_without_a_dub_says_so_plainly(local_client, isolated_db, drama):
    _add_video(isolated_db, drama)
    r = local_client.post(f"/api/export/dramas/{drama}/dubbed-video")
    assert r.status_code == 422
    assert error_of(r)["message"] == "There is no dub yet. Create it in Dub first."


def test_export_is_moved_with_an_atomic_replace(local_client, drama, isolated_db, fake_ffmpeg,
                                                monkeypatch):
    _touch(isolated_db, drama, "narration_track.wav")
    replaced = []
    real_replace = storage.os.replace

    def spy(src, dst):
        replaced.append(dst)
        return real_replace(src, dst)
    monkeypatch.setattr(storage.os, "replace", spy)
    assert local_client.post(f"/api/export/dramas/{drama}/audiobook").status_code == 200
    assert wait_for_job(f"audiobook_{drama}", timeout=6.0)["status"] == "done"
    assert any(d.endswith(f"audiobook_{drama}.m4b") for d in replaced)


def test_audiobook_timeout_error_names_no_command_or_paths(local_client, drama, isolated_db,
                                                            monkeypatch):
    # TimeoutExpired's own text is the whole ffmpeg command line, absolute paths included.
    def hung(job_id, cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(background_jobs, "run_cancellable", hung)
    _touch(isolated_db, drama, "narration_track.wav")
    local_client.post(f"/api/export/dramas/{drama}/audiobook")
    st = wait_for_job(f"audiobook_{drama}", timeout=6.0)
    assert st["status"] == "error"
    assert st["error"].endswith("ffmpeg took too long and was stopped.")
    assert "narration_track" not in st["error"] and isolated_db.drama_dir(drama) not in st["error"]
    _no_artifact(drama, "audio")
