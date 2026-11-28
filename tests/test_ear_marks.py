"""Ear marks: per-line left/right balance from synthetic stereo PCM, the sidecar cache and its
stamp, windowed decoding, failures, busy checks and alerts, the Watch routes' permission and
ownership, and ASS placement. ffmpeg and ffprobe are mocked, except in TestRealDecode."""

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import media_inspect
import subtitle_ass
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from jobs import job_launch
from lib.errors import ConflictError
from services import (auth_service, delete_service, drama_service, export_service,
                      media_playback_service, media_upload_service, notification_service)
from services import ear_marks_service as ears
from tests.job_helpers import wait_for_job

RATE = ears.SAMPLE_RATE
FRAME = ears.BYTES_PER_FRAME


def _tone(seconds, left, right, seed=0):
    """Noise-like speech stand-in with the given per-channel gains."""
    rng = np.random.default_rng(seed)
    voice = rng.uniform(-0.5, 0.5, int(seconds * RATE))
    return np.stack([voice * left, voice * right], axis=1)


def _pcm(*parts) -> bytes:
    frames = np.concatenate(parts)
    return (np.clip(frames, -1, 1) * 32767).astype("<i2").tobytes()


# One second each: left ear, right ear, centred, then silence.
_SCENE = _pcm(_tone(1, 1.0, 0.1, 1), _tone(1, 0.1, 1.0, 2), _tone(1, 0.8, 0.7, 3), np.zeros((RATE, 2)))
_TIMED = [(11, 0.0, 1.0), (12, 1.0, 2.0), (13, 2.0, 3.0), (14, 3.0, 4.0)]


def _measure(pcm, timed=_TIMED):
    return ears.classify(*ears.block_energies(io.BytesIO(pcm)), timed)


@pytest.fixture(autouse=True)
def _no_failures_remembered():
    ears._failed_at.clear()
    yield
    ears._failed_at.clear()


class TestClassify:
    def test_left_right_centred_and_silent_lines(self):
        found = _measure(_SCENE)
        assert found["stereo"] is True
        assert found["marks"] == {11: "left", 12: "right"}

    def test_mono_saved_as_stereo_gives_no_marks(self):
        # Identical channels, even with one line far louder than the rest.
        found = _measure(_pcm(_tone(1, 1.0, 1.0, 1), _tone(1, 0.1, 0.1, 2)), [(1, 0.0, 1.0), (2, 1.0, 2.0)])
        assert found == {"stereo": False, "side_db": found["side_db"], "marks": {}}

    def test_a_small_tilt_is_not_one_sided(self):
        # 4 dB louder on the left: under SIDE_DB, so centred.
        assert _measure(_pcm(_tone(1, 1.0, 10 ** (-4 / 20), 4)), [(1, 0.0, 1.0)])["marks"] == {}

    def test_reads_in_chunks_that_do_not_end_on_a_block(self, monkeypatch):
        monkeypatch.setattr(ears, "READ_BLOCKS", 7)
        assert _measure(_SCENE[:-6])["marks"] == {11: "left", 12: "right"}

    def test_lines_past_the_end_of_the_sound(self):
        assert _measure(_SCENE, [(1, 9.0, 10.0)])["marks"] == {}


@pytest.fixture
def title(isolated_db):
    did = db.create_drama(title_en="Binaural Example", source_language="ja")
    with open(os.path.join(db.drama_dir(did), "source.flac"), "wb") as f:
        f.write(b"fLaC")
    db.update_drama(did, audio_filename="source.flac")
    db.save_lines(did, [Line(idx=i, start=float(i), end=float(i + 1), zh=f"台词{i}", en=f"Line {i}")
                        for i in range(4)])
    return did


class _Decoder:
    """Stands in for ffmpeg: writes the asked-for window of _SCENE, and notes each call."""

    def __init__(self):
        self.calls = []     # (path, start)
        self.largest = 0    # bytes on disk at once

    def __call__(self, job_id, path, start, out_path):
        self.calls.append((path, start))
        lo = int(round(start * RATE)) * FRAME
        hi = lo + int(round(ears.WINDOW_SECONDS * RATE)) * FRAME
        with open(out_path, "wb") as f:
            f.write(_SCENE[lo:hi])
        self.largest = max(self.largest, os.path.getsize(out_path))


@pytest.fixture
def decoder(monkeypatch):
    fake = _Decoder()
    monkeypatch.setattr(ears, "_decode_window", fake)
    monkeypatch.setattr(ears, "_probe", lambda path: (True, len(_SCENE) / FRAME / RATE))
    monkeypatch.setattr(ears, "_tools_present", lambda: True)
    return fake


def _run(did):
    started = ears.start_ear_marks(did)
    if started["started"]:
        job = wait_for_job(started["job_id"])
        assert job["status"] == "done", job
    return started


def _sidecar(did):
    return ears.load_sidecar(db.drama_dir(did))


class TestCache:
    def test_sidecar_keyed_by_line_id_with_counts(self, title, decoder):
        ids = [ln.id for ln in db.load_line_objects(title)]
        assert ears.get_ear_marks(title)["state"] == "needed"
        _run(title)
        side = _sidecar(title)
        assert side["marks"] == {str(ids[0]): "left", str(ids[1]): "right"}
        assert (side["one_sided"], side["total_lines"], side["left"], side["right"]) == (2, 4, 1, 1)
        got = ears.get_ear_marks(title)
        assert got["state"] == "ready" and (got["one_sided"], got["total_lines"]) == (2, 4)
        assert got["marks"] == [{"line_id": ids[0], "side": "left"}, {"line_id": ids[1], "side": "right"}]

    def test_current_marks_are_not_worked_out_again(self, title, decoder):
        _run(title)
        assert _run(title)["started"] is False
        assert len(decoder.calls) == 1

    def test_a_timing_change_or_a_new_file_recomputes(self, title, decoder):
        _run(title)
        lines = db.load_line_objects(title)
        lines[2].end = 2.5
        db.save_lines(title, lines, fields=("end",))
        assert ears.get_ear_marks(title)["state"] == "needed"
        assert ears.fresh_sides(title) == {}
        _run(title)
        assert len(decoder.calls) == 2 and ears.get_ear_marks(title)["state"] == "ready"
        path = os.path.join(db.drama_dir(title), "source.flac")
        os.utime(path, ns=(1, 1))
        assert ears.get_ear_marks(title)["state"] == "needed"

    def test_marks_follow_ids_when_lines_are_reordered(self, title, decoder):
        _run(title)
        ids = [ln.id for ln in db.load_line_objects(title)]
        # Same timings, other order in the list: still the same marks per id.
        assert ears.fresh_sides(title, list(reversed(db.load_line_objects(title)))) == {
            ids[0]: "left", ids[1]: "right"}

    def test_a_mono_file_is_not_decoded(self, title, decoder, monkeypatch):
        monkeypatch.setattr(ears, "_probe", lambda path: (False, 4.0))
        _run(title)
        got = ears.get_ear_marks(title)
        assert got["state"] == "ready" and got["marks"] == [] and decoder.calls == []

    def test_a_video_title_reads_the_video_not_the_mono_extract(self, title, decoder):
        with open(os.path.join(db.drama_dir(title), "source.mp4"), "wb") as f:
            f.write(b"\0")
        db.update_drama(title, source_video_filename="source.mp4")
        _run(title)
        assert os.path.basename(decoder.calls[0][0]) == "source.mp4"

    def test_no_sound_file_is_unavailable(self, isolated_db):
        did = db.create_drama(title_en="Silent Example")
        assert ears.get_ear_marks(did)["state"] == "unavailable"

    def test_without_ffmpeg_nothing_starts(self, title, decoder, monkeypatch):
        monkeypatch.setattr(ears, "_tools_present", lambda: False)
        assert ears.start_ear_marks(title)["started"] is False
        assert ears.get_ear_marks(title)["state"] == "needed"


class TestWindows:
    def test_one_window_at_a_time_on_disk_with_the_same_marks(self, title, decoder, monkeypatch):
        monkeypatch.setattr(ears, "WINDOW_SECONDS", 1.0)
        _run(title)
        # The read that comes back empty, not the probed length, ends it.
        assert [start for _, start in decoder.calls] == [0.0, 1.0, 2.0, 3.0, 4.0]
        assert decoder.largest <= RATE * FRAME
        ids = [ln.id for ln in db.load_line_objects(title)]
        assert ears.fresh_sides(title) == {ids[0]: "left", ids[1]: "right"}

    def test_a_short_window_keeps_later_lines_at_their_time(self, title, decoder, monkeypatch):
        # ffmpeg giving a window a few samples short must not shift the next one.
        monkeypatch.setattr(ears, "WINDOW_SECONDS", 1.0)

        def short(job_id, path, start, out_path):
            decoder(job_id, path, start, out_path)
            with open(out_path, "r+b") as f:
                f.truncate(max(0, os.path.getsize(out_path) - 40 * FRAME))
        monkeypatch.setattr(ears, "_decode_window", short)
        _run(title)
        ids = [ln.id for ln in db.load_line_objects(title)]
        assert ears.fresh_sides(title) == {ids[0]: "left", ids[1]: "right"}

    def test_a_probed_length_shorter_than_the_sound_still_reads_to_the_end(self, title, decoder,
                                                                          monkeypatch):
        # ffprobe guesses an MP3 with no Xing header from its first frames.
        monkeypatch.setattr(ears, "WINDOW_SECONDS", 1.0)
        monkeypatch.setattr(ears, "_probe", lambda path: (True, 0.5))
        _run(title)
        ids = [ln.id for ln in db.load_line_objects(title)]
        assert ears.fresh_sides(title) == {ids[0]: "left", ids[1]: "right"}
        assert [start for _, start in decoder.calls][-1] == 4.0

    def test_an_unknown_length_ends_at_the_first_short_window(self, title, decoder, monkeypatch):
        monkeypatch.setattr(ears, "WINDOW_SECONDS", 1.5)
        monkeypatch.setattr(ears, "_probe", lambda path: (True, 0.0))
        _run(title)
        assert [start for _, start in decoder.calls] == [0.0, 1.5, 3.0]


class TestFailures:
    def _failing(self, monkeypatch, exc):
        def boom(job_id, path, start, out_path):
            raise exc
        monkeypatch.setattr(ears, "_decode_window", boom)

    def test_a_file_ffprobe_cannot_read_gets_no_marks(self, title, decoder, monkeypatch):
        def probe(path):
            raise media_inspect.ProbeError("bad /x/source.flac", "unreadable")
        monkeypatch.setattr(ears, "_probe", probe)
        assert wait_for_job(ears.start_ear_marks(title)["job_id"])["status"] == "done"
        assert _sidecar(title)["failed"] is True

    # A full disk makes ffmpeg exit non-zero too, so a failed decode is not stored as final.
    @pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["ffmpeg", "/x/source.flac"], 600),
                                     subprocess.CalledProcessError(1, ["ffmpeg", "-i", "/x/source.flac"]),
                                     OSError(28, "No space left on device", "/x/window.pcm"),
                                     media_inspect.ProbeError("slow", "timeout")])
    def test_a_failed_or_slow_read_stores_nothing_and_waits_before_trying_again(self, title, decoder, monkeypatch, exc):
        if isinstance(exc, media_inspect.ProbeError):
            def probe(path):
                raise exc
            monkeypatch.setattr(ears, "_probe", probe)
        else:
            self._failing(monkeypatch, exc)
        job = wait_for_job(ears.start_ear_marks(title)["job_id"])
        assert job["status"] == "error" and "/x/" not in job["error"]
        assert _sidecar(title) is None and ears.get_ear_marks(title)["state"] == "needed"
        assert ears.start_ear_marks(title)["started"] is False
        # Later, or with new timings, it is tried again.
        monkeypatch.setattr(ears, "RETRY_AFTER_SECONDS", 0)
        assert ears.start_ear_marks(title)["started"] is True
        wait_for_job(ears._job_id(title))

    def test_an_unexpected_error_is_not_retried_on_every_visit(self, title, decoder, monkeypatch):
        self._failing(monkeypatch, ValueError("surprise"))
        job = wait_for_job(ears.start_ear_marks(title)["job_id"])
        assert job["status"] == "error"
        assert ears.start_ear_marks(title)["started"] is False
        lines = db.load_line_objects(title)
        lines[0].end = 0.8
        db.save_lines(title, lines, fields=("end",))
        assert ears.start_ear_marks(title)["started"] is True
        wait_for_job(ears._job_id(title))


@pytest.fixture
def slow_decoder(decoder, monkeypatch):
    """A decode that runs until it is cancelled, as a long file's would."""
    running = threading.Event()

    def slow(job_id, path, start, out_path):
        running.set()
        while not background_jobs.is_cancel_requested(job_id):
            time.sleep(0.01)
        raise background_jobs.JobCancelled(job_id)
    monkeypatch.setattr(ears, "_decode_window", slow)
    return running


class TestBusy:
    def test_a_running_job_does_not_block_title_edits(self, title, slow_decoder):
        job_id = ears.start_ear_marks(title)["job_id"]
        assert slow_decoder.wait(5)
        try:
            assert not drama_service.job_running_for_drama(title)
            assert not background_jobs.any_job_running_for_drama(title)
        finally:
            background_jobs.request_cancel(job_id)
            wait_for_job(job_id)

    def test_deleting_the_title_stops_the_job_first(self, title, slow_decoder):
        job_id = ears.start_ear_marks(title)["job_id"]
        assert slow_decoder.wait(5)
        drama_service.delete_drama(title, confirm=True, confirm_text="DELETE")
        assert db.get_drama(title) is None
        assert background_jobs.get_status(job_id)["status"] == "cancelled"

    def test_removing_the_media_stops_the_job_first(self, title, slow_decoder):
        job_id = ears.start_ear_marks(title)["job_id"]
        assert slow_decoder.wait(5)
        delete_service.remove_media(title, confirm=True)
        assert background_jobs.get_status(job_id)["status"] == "cancelled"
        assert not os.path.exists(os.path.join(db.drama_dir(title), "source.flac"))

    def test_no_restart_while_the_media_is_being_removed(self, title, decoder, monkeypatch):
        refused = []
        real_remove = delete_service._remove_file

        def remove(path):
            if path:
                with pytest.raises(ConflictError):
                    ears.start_ear_marks(title)
                refused.append(path)
            return real_remove(path)
        monkeypatch.setattr(delete_service, "_remove_file", remove)
        delete_service.remove_media(title, confirm=True)
        assert refused and decoder.calls == []

    def test_replacing_the_audio_stops_the_job_and_starts_none_meanwhile(self, title, slow_decoder,
                                                                        monkeypatch):
        job_id = ears.start_ear_marks(title)["job_id"]
        assert slow_decoder.wait(5)
        seen = []
        real_install = media_upload_service.install_media

        def install(*a, **k):
            seen.append((background_jobs.get_status(job_id)["status"],
                         ears.start_ear_marks(title)["started"]))
            return real_install(*a, **k)
        monkeypatch.setattr(media_upload_service, "install_media", install)
        media_upload_service.upload_media(title, "new.flac", io.BytesIO(b"fLaC2"),
                                          confirm_replace_audio=True)
        assert seen == [("cancelled", False)]

    def _upload_video(self, title, monkeypatch):
        def extract(job_id, cmd, cwd=None, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"flac")
        monkeypatch.setattr(background_jobs, "run_cancellable", extract)
        r = media_upload_service.upload_media(title, "new.mp4", io.BytesIO(b"\0video"),
                                              confirm_replace_audio=True)
        return wait_for_job(r["job_id"])

    def test_a_video_upload_stops_the_job_before_its_audio_goes_in(self, title, slow_decoder,
                                                                  monkeypatch):
        job_id = ears.start_ear_marks(title)["job_id"]
        assert slow_decoder.wait(5)
        assert self._upload_video(title, monkeypatch)["status"] == "done"
        assert background_jobs.get_status(job_id)["status"] == "cancelled"
        assert db.get_drama(title)["source_video_filename"] == "source.mp4"

    def test_a_job_that_will_not_stop_keeps_the_title_as_it_was(self, title, decoder, monkeypatch):
        monkeypatch.setattr(background_jobs, "stop_side_jobs", lambda drama_id, **k: False)
        job = self._upload_video(title, monkeypatch)
        assert job["status"] == "error" and "would not stop" in job["error"]
        drama = db.get_drama(title)
        assert (drama["audio_filename"], drama["source_video_filename"]) == ("source.flac", None)

    def test_no_start_while_an_upload_or_another_job_may_replace_the_file(self, title, decoder):
        with media_upload_service.claims_lock:
            media_upload_service.claimed.add(title)
        try:
            assert ears.start_ear_marks(title)["started"] is False
        finally:
            with media_upload_service.claims_lock:
                media_upload_service.claimed.discard(title)
        gate = threading.Event()
        assert job_launch.start_job(f"extract_audio_{title}", gate.wait, 5, line_fields=())
        try:
            assert ears.start_ear_marks(title)["started"] is False
        finally:
            gate.set()
            wait_for_job(f"extract_audio_{title}")
        assert _run(title)["started"] is True

    def test_a_job_that_will_not_stop_keeps_the_delete_off(self, title, slow_decoder, monkeypatch):
        job_id = ears.start_ear_marks(title)["job_id"]
        assert slow_decoder.wait(5)
        monkeypatch.setattr(background_jobs, "request_cancel", lambda *a, **k: True)
        try:
            with pytest.raises(job_launch.TitleBusy):
                with job_launch.hold_title(title, "busy"):
                    pass
        finally:
            monkeypatch.undo()
            background_jobs.request_cancel(job_id)
            wait_for_job(job_id)

    def test_nothing_to_stop_is_at_once(self):
        assert background_jobs.stop_side_jobs(987654, wait_seconds=0) is True


_BURSTS = [3.0 + 10 * k for k in range(8)]   # left at 3, 23, 43, 63; right at 13, 33, 53, 73


def _vbr_mp3(path):
    """80 s VBR MP3 with no Xing header (so no seek table and a guessed length): quiet
    noise, plus a tone, a new pitch each time, in one ear for a second every 10 s."""
    burst = "lt(mod(t+7,10),1)*0.5*sin(2*PI*(300+50*floor(t/10))*t)"
    left = f"0.02*random(0)+{burst}*eq(mod(floor(t/10),2),0)"
    right = f"0.02*random(1)+{burst}*eq(mod(floor(t/10),2),1)"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"aevalsrc='{left}|{right}':s=16000:d=80",
                    "-c:a", "libmp3lame", "-q:a", "4", "-write_xing", "0", path],
                   check=True, timeout=60)


def _onsets(loud):
    return np.flatnonzero(loud[1:] & ~loud[:-1]) + 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="needs ffmpeg and ffprobe")
class TestRealDecode:
    def test_windows_of_a_vbr_mp3_line_up_with_one_pass(self, isolated_db, monkeypatch, tmp_path):
        # Input-side -ss on an MP3 with no seek table is ffmpeg's estimate; the windows join
        # up only if each starts within a block of where one pass puts it.
        did = db.create_drama(title_en="Binaural Example", source_language="ja")
        path = os.path.join(db.drama_dir(did), "source.mp3")
        try:
            _vbr_mp3(path)
        except subprocess.CalledProcessError:
            pytest.skip("this ffmpeg has no libmp3lame")
        db.update_drama(did, audio_filename="source.mp3")
        db.save_lines(did, [Line(idx=i, start=b + 0.1, end=b + 0.9, zh=f"台词{i}", en=f"Line {i}")
                            for i, b in enumerate(_BURSTS)])
        one_pass = str(tmp_path / "all.pcm")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-ac", "2",
                        "-ar", str(RATE), "-f", "s16le", one_pass], check=True, timeout=60)
        with open(one_pass, "rb") as f:
            l1, r1, _, _ = ears.block_energies(f)

        monkeypatch.setattr(ears, "WINDOW_SECONDS", 25.0)
        seen = {}
        real_classify = ears.classify

        def classify(left, right, side, mid, timed):
            seen["lr"] = (left, right)
            return real_classify(left, right, side, mid, timed)
        monkeypatch.setattr(ears, "classify", classify)
        _run(did)

        l2, r2 = (a[:len(l1)] for a in seen["lr"])
        for a1, b1, a2, b2 in ((l1, r1, l2, r2), (r1, l1, r2, l2)):
            want = _onsets((a1 > 10 * b1) & (a1 > 1e-3 * a1.max()))
            got = _onsets((a2 > 10 * b2) & (a2 > 1e-3 * a2.max()))
            assert len(want) == len(got) == 4
            assert np.abs(got - want).max() <= 1
        ids = [ln.id for ln in db.load_line_objects(did)]
        assert ears.fresh_sides(did) == {i: ("left" if n % 2 == 0 else "right")
                                         for n, i in enumerate(ids)}


class TestAlerts:
    def test_side_jobs_and_quiet_ones_are_the_same_list(self):
        assert notification_service.QUIET_DONE_JOB_PREFIXES == background_jobs.SIDE_JOB_PREFIXES
        assert ears.JOB_PREFIX in background_jobs.SIDE_JOB_PREFIXES

    def test_a_finished_run_is_not_announced_but_a_failed_one_is(self, isolated_db, monkeypatch):
        from jobs import job_settings
        notification_service.reset_for_tests()
        popups = []
        monkeypatch.setattr(job_settings, "get_notify_on_completion", lambda: True)
        monkeypatch.setattr(job_settings.desktop_session, "has_desktop", lambda: True)
        plyer = types.SimpleNamespace(notification=types.SimpleNamespace(notify=lambda **kw: popups.append(kw)))
        monkeypatch.setitem(sys.modules, "plyer", plyer)
        job_settings._notify_job_finished("Ear marks (title #3)", "done", job_id="earmarks_3")
        assert notification_service.list_recent(None) == [] and popups == []
        job_settings._notify_job_finished("Ear marks (title #3)", "error", job_id="earmarks_3",
                                          reason="RuntimeError: x")
        assert len(notification_service.list_recent(None)) == 1


class TestRoutes:
    def test_get_and_post(self, local_client, title, decoder):
        client = local_client
        url = f"/api/reader/dramas/{title}/ear-marks"
        assert client.get(url).json()["state"] == "needed"
        r = client.post(url)
        assert r.status_code == 200, r.text
        wait_for_job(r.json()["job_id"])
        body = client.get(url).json()
        assert body["state"] == "ready" and body["one_sided"] == 2
        assert "source" not in json.dumps(body)
        assert client.get("/api/reader/dramas/9999/ear-marks").status_code == 404

    def test_other_members_private_title_is_404_and_post_needs_jobs_start(self, isolated_db, decoder):
        def login(email):
            u = auth_service.add_user(email)
            s = auth_service.create_session(u["id"])
            return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                             api_auth.CSRF_HEADER: s["csrf_token"]}
        a_id, a = login("a@example.com")
        b_id, b = login("b@example.com")
        did = db.create_drama(title_en="Private Example", owner_user_id=a_id, is_private=1)
        app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
        c = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
        url = f"/api/reader/dramas/{did}/ear-marks"
        assert c.get(url, headers=b).status_code == 404
        assert c.post(url, headers=b).status_code == 404
        assert c.get(url, headers=a).status_code == 200
        auth_service.revoke_permission(a_id, "jobs.start")
        assert c.post(url, headers=a).status_code == 403


_HEADER = {"styles": [{"name": "Sign", "fontname": "Arial", "fontsize": "40", "alignment": "8"},
                      {"name": "Talk", "fontname": "Arial", "fontsize": "40", "alignment": "2"}],
           "script_info": {"playresx": "1920", "playresy": "1080"}}


class TestAssPlacement:
    def _lines(self):
        return [Line(idx=0, start=0.0, end=1.0, zh="左", en="Left ear", id=5),
                Line(idx=1, start=1.0, end=2.0, zh="右", en="Right ear", id=6),
                Line(idx=2, start=2.0, end=3.0, zh="中", en="Centre", id=7)]

    def _dialogues(self, ass):
        return [row.split(",", 9)[9] for row in ass.splitlines() if row.startswith("Dialogue:")]

    def test_one_sided_lines_go_bottom_left_and_right(self):
        ass = subtitle_ass.lines_to_ass(self._lines(), subtitle_ass.ASS_PRESETS["Clean"],
                                        ear_placement={5: "left", 6: "right"})
        assert self._dialogues(ass) == [r"{\an1}Left ear", r"{\an3}Right ear", "Centre"]

    def test_off_by_default(self):
        ass = subtitle_ass.lines_to_ass(self._lines(), subtitle_ass.ASS_PRESETS["Clean"])
        assert "\\an" not in "".join(self._dialogues(ass))

    def test_a_top_style_keeps_its_row(self):
        style = {**subtitle_ass.ASS_PRESETS["Clean"], "alignment": "top-center"}
        ass = subtitle_ass.lines_to_ass(self._lines(), style, ear_placement={5: "left", 6: "right"})
        assert self._dialogues(ass)[:2] == [r"{\an7}Left ear", r"{\an9}Right ear"]

    def test_a_sound_effect_follows_its_own_position(self):
        lines = self._lines()
        lines[1].sfx = True
        style = {**subtitle_ass.ASS_PRESETS["Clean"], "sfx_alignment": "top-right"}
        ass = subtitle_ass.lines_to_ass(lines, style, ear_placement={5: "left", 6: "right"})
        assert self._dialogues(ass)[:2] == [r"{\an1}Left ear", r"{\an9}[Right ear]"]

    def test_an_imported_line_that_places_itself_keeps_its_place(self):
        lines = self._lines()
        lines[0].ass_event = json.dumps({"style": "Sign", "lead": r"{\pos(960,100)}"})
        lines[1].ass_event = json.dumps({"style": "Talk", "lead": r"{\an8}"})
        lines[2].ass_event = json.dumps({"style": "Sign", "lead": r"{\blur2}"})
        ass = subtitle_ass.lines_to_ass(lines, subtitle_ass.ASS_PRESETS["Clean"], ass_header=_HEADER,
                                        ear_placement={5: "left", 6: "right", 7: "right"})
        # Its own \pos or \an stays the only placement; a plain imported top line keeps its row.
        assert self._dialogues(ass) == [r"{\pos(960,100)}Left ear", r"{\an8}Right ear", r"{\an9}{\blur2}Centre"]

    def test_export_uses_current_marks_only_when_asked(self, title, decoder):
        _run(title)
        assert r"{\an1}" not in export_service.generate_ass_text(title, frame_aspect=None)
        text = export_service.generate_ass_text(title, frame_aspect=None, ear_placement=True)
        assert r"{\an1}Line 0" in text and r"{\an3}Line 1" in text and "{\\an" not in text.split("Line 1")[1]
        assert export_service.get_export_readiness(title)["ear_marked_lines"] == 2

    def test_the_burn_preview_places_them_too(self, title, decoder):
        _run(title)
        lines = db.load_line_objects(title)
        _s, _e, ass = media_playback_service.burn_preview_ass(
            lines, lines[0], {"ear_placement": ears.fresh_sides(title, lines)})
        assert r"{\an1}Line 0" in ass

    def test_the_burn_preview_start_passes_the_option_on(self, title, decoder, monkeypatch):
        from services import review_extras_service as rx
        _run(title)
        seen = []

        def preview(lines, line, state, pad=2.0):
            seen.append(state.get("ear_placement"))
            return 0.0, 1.0, ""
        monkeypatch.setattr(rx.media_playback_service, "burn_preview_ass", preview)
        monkeypatch.setattr(rx, "_video_path", lambda did: "/x/source.mp4")
        monkeypatch.setattr(rx, "_ffmpeg_available", lambda: True)
        monkeypatch.setattr(rx.export_service, "video_frame_aspect", lambda path: None)
        monkeypatch.setattr(rx.job_launch, "start_job", lambda *a, **k: True)
        ids = [ln.id for ln in db.load_line_objects(title)]
        rx.start_burn_preview(title, ids[0])
        rx.start_burn_preview(title, ids[0], ear_placement=True)
        assert seen[0] is None and seen[1] == {ids[0]: "left", ids[1]: "right"}
