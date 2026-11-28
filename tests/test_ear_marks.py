"""Ear marks: per-line left/right balance from synthetic stereo PCM, the sidecar cache and its
stamp, windowed decoding, failures, busy checks and alerts, the Watch routes' permission and
ownership, and ASS placement. ffmpeg and ffprobe are mocked."""

import io
import json
import os
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
from services import (auth_service, drama_service, export_service, media_playback_service,
                      notification_service)
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
        assert [start for _, start in decoder.calls] == [0.0, 1.0, 2.0, 3.0]
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

    def test_an_unreadable_file_gets_no_marks_quietly(self, title, decoder, monkeypatch):
        self._failing(monkeypatch, subprocess.CalledProcessError(1, ["ffmpeg", "-i", "/x/source.flac"]))
        job = wait_for_job(ears.start_ear_marks(title)["job_id"])
        assert job["status"] == "done"
        got = ears.get_ear_marks(title)
        assert got["state"] == "ready" and got["marks"] == []
        assert _sidecar(title)["failed"] is True

    def test_a_file_ffprobe_cannot_read_gets_no_marks(self, title, decoder, monkeypatch):
        def probe(path):
            raise media_inspect.ProbeError("bad /x/source.flac", "unreadable")
        monkeypatch.setattr(ears, "_probe", probe)
        assert wait_for_job(ears.start_ear_marks(title)["job_id"])["status"] == "done"
        assert _sidecar(title)["failed"] is True

    @pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["ffmpeg", "/x/source.flac"], 600),
                                     media_inspect.ProbeError("slow", "timeout")])
    def test_a_timeout_stores_nothing_and_waits_before_trying_again(self, title, decoder, monkeypatch, exc):
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
        from services import delete_service
        job_id = ears.start_ear_marks(title)["job_id"]
        assert slow_decoder.wait(5)
        delete_service.remove_media(title, confirm=True)
        assert background_jobs.get_status(job_id)["status"] == "cancelled"
        assert not os.path.exists(os.path.join(db.drama_dir(title), "source.flac"))

    def test_a_job_that_will_not_stop_keeps_the_delete_off(self, title, slow_decoder, monkeypatch):
        from jobs import job_launch
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
