"""Ear marks: per-line left/right balance from synthetic stereo PCM, the sidecar cache and its
stamp, the Watch routes' permission and ownership, and ASS placement. ffmpeg and ffprobe are mocked."""

import io
import json
import os

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import subtitle_ass
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service, export_service
from services import ear_marks_service as ears
from tests.job_helpers import wait_for_job

RATE = ears.SAMPLE_RATE


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


@pytest.fixture
def decoder(monkeypatch):
    calls = []

    def decode(job_id, path, out_path):
        calls.append(path)
        with open(out_path, "wb") as f:
            f.write(_SCENE)

    monkeypatch.setattr(ears, "_decode", decode)
    monkeypatch.setattr(ears, "_has_stereo_track", lambda path: True)
    monkeypatch.setattr(ears, "_tools_present", lambda: True)
    return calls


def _run(did):
    started = ears.start_ear_marks(did)
    if started["started"]:
        job = wait_for_job(started["job_id"])
        assert job["status"] == "done", job
    return started


class TestCache:
    def test_sidecar_keyed_by_line_id_with_counts(self, title, decoder):
        ids = [ln.id for ln in db.load_line_objects(title)]
        assert ears.get_ear_marks(title)["state"] == "needed"
        _run(title)
        with open(os.path.join(db.drama_dir(title), ears.EAR_MARKS_FILE), encoding="utf-8") as f:
            side = json.load(f)
        assert side["marks"] == {str(ids[0]): "left", str(ids[1]): "right"}
        assert (side["one_sided"], side["total_lines"], side["left"], side["right"]) == (2, 4, 1, 1)
        got = ears.get_ear_marks(title)
        assert got["state"] == "ready" and (got["one_sided"], got["total_lines"]) == (2, 4)
        assert got["marks"] == [{"line_id": ids[0], "side": "left"}, {"line_id": ids[1], "side": "right"}]

    def test_current_marks_are_not_worked_out_again(self, title, decoder):
        _run(title)
        assert _run(title)["started"] is False
        assert len(decoder) == 1

    def test_a_timing_change_or_a_new_file_recomputes(self, title, decoder):
        _run(title)
        lines = db.load_line_objects(title)
        lines[2].end = 2.5
        db.save_lines(title, lines, fields=("end",))
        assert ears.get_ear_marks(title)["state"] == "needed"
        assert ears.fresh_sides(title) == {}
        _run(title)
        assert len(decoder) == 2 and ears.get_ear_marks(title)["state"] == "ready"
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
        monkeypatch.setattr(ears, "_has_stereo_track", lambda path: False)
        _run(title)
        got = ears.get_ear_marks(title)
        assert got["state"] == "ready" and got["marks"] == [] and decoder == []

    def test_a_video_title_reads_the_video_not_the_mono_extract(self, title, decoder):
        with open(os.path.join(db.drama_dir(title), "source.mp4"), "wb") as f:
            f.write(b"\0")
        db.update_drama(title, source_video_filename="source.mp4")
        _run(title)
        assert os.path.basename(decoder[0]) == "source.mp4"

    def test_no_sound_file_is_unavailable(self, isolated_db):
        did = db.create_drama(title_en="Silent Example")
        assert ears.get_ear_marks(did)["state"] == "unavailable"

    def test_an_unreadable_file_gets_no_marks_quietly(self, title, decoder, monkeypatch):
        import subprocess

        def boom(job_id, path, out_path):
            raise subprocess.CalledProcessError(1, ["ffmpeg", "-i", path])

        monkeypatch.setattr(ears, "_decode", boom)
        job = wait_for_job(ears.start_ear_marks(title)["job_id"])
        assert job["status"] == "done"
        got = ears.get_ear_marks(title)
        assert got["state"] == "ready" and got["marks"] == []
        assert ears.load_sidecar(db.drama_dir(title))["failed"] is True

    def test_without_ffmpeg_nothing_starts(self, title, decoder, monkeypatch):
        monkeypatch.setattr(ears, "_tools_present", lambda: False)
        assert ears.start_ear_marks(title)["started"] is False
        assert ears.get_ear_marks(title)["state"] == "needed"


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

    def test_export_uses_current_marks_only_when_asked(self, title, decoder):
        _run(title)
        assert r"{\an1}" not in export_service.generate_ass_text(title, frame_aspect=None)
        text = export_service.generate_ass_text(title, frame_aspect=None, ear_placement=True)
        assert r"{\an1}Line 0" in text and r"{\an3}Line 1" in text and "{\\an" not in text.split("Line 1")[1]
        assert export_service.get_export_readiness(title)["ear_marked_lines"] == 2
