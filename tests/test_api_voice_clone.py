"""
Voice-clone setup (parity blocker #7; inventory C01, C03, C05, C09, C13):
/api/characters/... routes in api/routers/voice_clone_routes.py over
services/voice_clone_service.py, plus the Dub config clone warning.
TestClient against an isolated library; ffprobe/ffmpeg are faked (no
binaries, no models, no network).
"""

import json
import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import media_inspect
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service
from services import voice_clone_service as vcs

BASE = "/api/characters/dramas"
REMOTE = "https://baihe.example.com"
LOCAL_HDR = {"X-Baihe-Local": "1"}


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def tools(monkeypatch):
    """ffprobe/ffmpeg on PATH; ffprobe reports `probe["duration"]` seconds
    of audio; ffmpeg 'cuts' by writing a small file to its output path."""
    probe = {"duration": 5.0, "streams": [{"codec_type": "audio"}], "calls": [], "cuts": []}
    monkeypatch.setattr(vcs.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_probe(path, timeout=30):
        probe["calls"].append(timeout)
        return {"format": {"duration": str(probe["duration"])}, "streams": probe["streams"]}

    def fake_run(job_id, cmd, cwd=None, timeout=None, **kw):
        assert timeout, "every ffmpeg run needs a timeout"
        probe["cuts"].append(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"RIFFcut")

    monkeypatch.setattr(media_inspect, "run_ffprobe", fake_probe)
    monkeypatch.setattr(background_jobs, "run_cancellable", fake_run)
    return probe


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


def _drama(speakers=("A", "B"), durations=None, audio=True, **fields):
    did = db.create_drama(title_en="Drama", **fields)
    durations = durations or [5.0] * len(speakers)
    t = 0.0
    lines = []
    for i, (s, d) in enumerate(zip(speakers, durations)):
        lines.append(Line(idx=i, start=t, end=t + d, zh=f"台词{i}", speaker=s))
        t += d + 1
    db.save_lines(did, lines)
    if audio:
        with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as f:
            f.write(b"RIFFaudio")
        db.update_drama(did, audio_filename="audio.wav")
    return did


def _upload(client, did, label="A", name="clip.wav", data=b"RIFFclip", **form):
    return client.post(f"{BASE}/{did}/reference-clip", headers=LOCAL_HDR,
                       files={"file": (name, data, "audio/wav")},
                       data={"speaker_label": label, **form})


def _char(did, label):
    return next(c for c in db.list_characters(did) if c["speaker_label"] == label)


def _wait(job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _no_leak(text, did):
    assert db.DRAMAS_DIR not in text and "voice_refs" not in text
    assert "clone_ref_" not in text and ".wav" not in text.replace("candidate_", "")


# ---- C09 upload / remove ---------------------------------------------------------

class TestUpload:
    def test_upload_sets_clip_and_transcript(self, client, isolated_db, tools):
        did = _drama()
        r = _upload(client, did, ref_text=" 你好 ")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_ref_audio"] is True and body["ref_text_present"] is True
        _no_leak(r.text, did)
        row = _char(did, "A")
        assert row["ref_text"] == "你好"
        assert row["ref_audio_filename"].startswith("voice_refs/clone_ref_")
        assert os.path.isfile(os.path.join(db.drama_dir(did), row["ref_audio_filename"]))
        assert tools["calls"] == [vcs.FFPROBE_TIMEOUT_SECONDS]

    def test_replace_deletes_old_owned_clip(self, client, isolated_db, tools):
        did = _drama()
        _upload(client, did)
        first = _char(did, "A")["ref_audio_filename"]
        assert _upload(client, did, name="b.mp3").status_code == 200
        second = _char(did, "A")["ref_audio_filename"]
        assert second != first and second.endswith(".mp3")
        assert not os.path.exists(os.path.join(db.drama_dir(did), first))

    def test_replace_keeps_a_clip_not_written_here(self, client, isolated_db, tools):
        did = _drama()
        legacy = os.path.join(db.drama_dir(did), "clone_ref_A.wav")
        open(legacy, "wb").write(b"RIFF")
        db.upsert_character(did, "A", ref_audio_filename="clone_ref_A.wav")
        assert _upload(client, did).status_code == 200
        assert os.path.exists(legacy)

    @pytest.mark.parametrize("name", ["x.exe", "clip", "../../x.wav\x00.txt", "a.mp4"])
    def test_bad_type_422(self, client, isolated_db, tools, name):
        did = _drama()
        r = _upload(client, did, name=name)
        assert r.status_code == 422
        assert _char_missing_or_no_clip(did, "A")

    def test_empty_and_too_large_422(self, client, isolated_db, tools, monkeypatch):
        did = _drama()
        assert _upload(client, did, data=b"").status_code == 422
        monkeypatch.setattr(vcs, "MAX_CLIP_BYTES", 10)
        r = _upload(client, did, data=b"x" * 11)
        assert r.status_code == 422 and "too large" in _error(r)["message"]
        refs = os.path.join(db.drama_dir(did), "voice_refs")
        assert [f for f in os.listdir(refs)] == []   # temp file removed

    @pytest.mark.parametrize("duration", [0.2, 45.0])
    def test_duration_cap_422(self, client, isolated_db, tools, duration):
        did = _drama()
        tools["duration"] = duration
        r = _upload(client, did)
        assert r.status_code == 422 and "seconds" in _error(r)["message"]
        assert _char_missing_or_no_clip(did, "A")

    def test_not_audio_or_video_422_without_path(self, client, isolated_db, tools, monkeypatch):
        did = _drama()
        tools["streams"] = [{"codec_type": "video"}, {"codec_type": "audio"}]
        assert _upload(client, did).status_code == 422

        def boom(path, timeout=30):
            raise media_inspect.ProbeError(f"ffprobe couldn't read this file: {path}")
        monkeypatch.setattr(media_inspect, "run_ffprobe", boom)
        r = _upload(client, did)
        assert r.status_code == 422
        _no_leak(r.text, did)

    def test_no_ffprobe_503(self, client, isolated_db, tools, monkeypatch):
        did = _drama()
        monkeypatch.setattr(vcs.shutil, "which", lambda name: None)
        assert _upload(client, did).status_code == 503

    def test_unknown_drama_or_speaker_404(self, client, isolated_db, tools):
        did = _drama()
        assert _upload(client, 999).status_code == 404
        assert _upload(client, did, label="Nobody").status_code == 404

    def test_remove_needs_confirm_and_deletes_owned_file(self, client, isolated_db, tools):
        did = _drama()
        _upload(client, did)
        rel = _char(did, "A")["ref_audio_filename"]
        url = f"{BASE}/{did}/reference-clip/remove"
        assert client.post(url, json={"speaker_label": "A"}).status_code == 422
        r = client.post(url, json={"speaker_label": "A", "confirm": True})
        assert r.status_code == 200 and r.json()["has_ref_audio"] is False
        assert not os.path.exists(os.path.join(db.drama_dir(did), rel))
        assert client.post(url, json={"speaker_label": "Z", "confirm": True}).status_code == 404

    def test_shared_clip_not_deleted(self, client, isolated_db, tools):
        did = _drama()
        _upload(client, did)
        rel = _char(did, "A")["ref_audio_filename"]
        db.upsert_character(did, "B", ref_audio_filename=rel)
        client.post(f"{BASE}/{did}/reference-clip/remove", json={"speaker_label": "A", "confirm": True})
        assert os.path.exists(os.path.join(db.drama_dir(did), rel))


def _char_missing_or_no_clip(did, label):
    rows = [c for c in db.list_characters(did) if c["speaker_label"] == label]
    return not rows or not rows[0].get("ref_audio_filename")


# ---- C01 extract candidates --------------------------------------------------------

class TestExtract:
    def _run(self, client, did, label="A", **body):
        r = client.post(f"{BASE}/{did}/reference-clips/extract", json={"speaker_label": label, **body})
        assert r.status_code == 200, r.text
        return _wait(r.json()["job_id"])

    def test_extract_from_lines_list_preview_choose(self, client, isolated_db, tools):
        did = _drama(speakers=("A", "A", "A", "B"), durations=[6.0, 2.0, 10.0, 5.0])
        job = self._run(client, did)
        assert job["status"] == "done", job
        assert job["result"] == {"candidate_count": 2}
        assert len(tools["cuts"]) == 2
        r = client.get(f"{BASE}/{did}/reference-clips/candidates")
        assert r.status_code == 200
        _no_leak(r.text, did)
        sp = r.json()["speakers"]
        assert [s["speaker_label"] for s in sp] == ["A"]
        cands = sp[0]["candidates"]
        assert [c["duration"] for c in cands] == [6.0, 10.0]   # closest to 6 s first
        assert cands[0]["ref_text"] == "台词0"
        audio = client.get(f"{BASE}/{did}/reference-clips/candidates/{cands[0]['id']}/audio")
        assert audio.status_code == 200 and audio.content == b"RIFFcut"
        assert "voice_refs" not in audio.headers.get("content-disposition", "")
        chosen = client.post(f"{BASE}/{did}/reference-clips/candidates/{cands[0]['id']}/choose",
                             headers=LOCAL_HDR)
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["has_ref_audio"] and chosen.json()["ref_text_present"]
        row = _char(did, "A")
        assert row["ref_text"] == "台词0" and row["ref_audio_filename"].startswith("voice_refs/clone_ref_")
        # B untouched: characters are matched by label, never by position.
        assert _char_missing_or_no_clip(did, "B")

    def test_uses_diarization_turns_when_present(self, client, isolated_db, tools):
        did = _drama(speakers=("A",), durations=[1.0])
        import diarize
        diarize.save_turns(db.drama_dir(did), [{"speaker": "A", "start": 0.0, "end": 7.0},
                                                {"speaker": "B", "start": 8.0, "end": 14.0}])
        job = self._run(client, did)
        assert job["result"] == {"candidate_count": 1}
        cmd = tools["cuts"][0]
        assert cmd[cmd.index("-t") + 1] == "7.000"

    def test_nothing_usable_reports_skip_reason(self, client, isolated_db, tools):
        did = _drama(speakers=("A", "A"), durations=[1.0, 2.0])
        job = self._run(client, did)
        assert job["status"] == "done" and job["result"] == {"candidate_count": 0}
        sp = client.get(f"{BASE}/{did}/reference-clips/candidates").json()["speakers"][0]
        assert sp["candidates"] == [] and sp["skip_reason"] == "too_short"
        assert sp["closest_duration"] == 2.0

    def test_rerun_replaces_old_candidates(self, client, isolated_db, tools):
        did = _drama(speakers=("A",), durations=[6.0])
        self._run(client, did)
        old = client.get(f"{BASE}/{did}/reference-clips/candidates").json()["speakers"][0]["candidates"]
        self._run(client, did)
        new = client.get(f"{BASE}/{did}/reference-clips/candidates").json()["speakers"][0]["candidates"]
        assert old[0]["id"] != new[0]["id"]
        assert client.get(f"{BASE}/{did}/reference-clips/candidates/{old[0]['id']}/audio").status_code == 404

    def test_cancel_leaves_no_files(self, client, isolated_db, tools, monkeypatch):
        did = _drama(speakers=("A", "A"), durations=[6.0, 7.0])
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: True)
        job = self._run(client, did)
        assert job["status"] == "cancelled"
        cdir = os.path.join(db.drama_dir(did), "voice_refs", "candidates")
        assert not [f for f in os.listdir(cdir) if f.endswith(".wav")]

    def test_cancel_during_a_cut_removes_the_partial_clip(self, client, isolated_db, tools,
                                                          monkeypatch):
        did = _drama(speakers=("A", "A"), durations=[6.0, 7.0])
        calls = []

        def cut_then_cancel(job_id, cmd, cwd=None, timeout=None, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"partial")
            calls.append(cmd)
            if len(calls) == 2:
                raise background_jobs.JobCancelled(job_id)
        monkeypatch.setattr(background_jobs, "run_cancellable", cut_then_cancel)
        job = self._run(client, did)
        assert job["status"] == "cancelled"
        cdir = os.path.join(db.drama_dir(did), "voice_refs", "candidates")
        assert not [f for f in os.listdir(cdir) if f.endswith(".wav")]

    def test_ffmpeg_failure_is_generic_job_error(self, client, isolated_db, tools, monkeypatch):
        import subprocess
        did = _drama(speakers=("A",), durations=[6.0])

        def fail(job_id, cmd, cwd=None, timeout=None, **kw):
            raise subprocess.CalledProcessError(1, cmd, stderr=b"/secret/path/audio.wav: bad")
        monkeypatch.setattr(background_jobs, "run_cancellable", fail)
        job = self._run(client, did)
        assert job["status"] == "error" and "/secret" not in job["error"]

    def test_errors(self, client, isolated_db, tools, monkeypatch):
        did = _drama()
        url = f"{BASE}/{did}/reference-clips/extract"
        assert client.post(f"{BASE}/999/reference-clips/extract", json={"speaker_label": "A"}).status_code == 404
        assert client.post(url, json={"speaker_label": "Z"}).status_code == 404
        assert client.post(url, json={"speaker_label": "A", "max_candidates": 9}).status_code == 422
        assert client.post(url, json={"speaker_label": "A", "extra": 1}).status_code == 422
        noaudio = _drama(audio=False)
        assert client.post(f"{BASE}/{noaudio}/reference-clips/extract",
                           json={"speaker_label": "A"}).status_code == 422
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
        assert client.post(url, json={"speaker_label": "A"}).status_code == 409
        monkeypatch.setattr(vcs.shutil, "which", lambda name: None)
        assert client.post(url, json={"speaker_label": "A"}).status_code == 503

    def test_bad_candidate_ids_404_or_422(self, client, isolated_db):
        did = _drama()
        good_shape = "a" * 32
        assert client.get(f"{BASE}/{did}/reference-clips/candidates/{good_shape}/audio").status_code == 404
        assert client.post(f"{BASE}/{did}/reference-clips/candidates/{good_shape}/choose",
                           headers=LOCAL_HDR).status_code == 404
        assert client.get(f"{BASE}/{did}/reference-clips/candidates/..%2F..%2Fx/audio").status_code in (404, 422)
        assert client.get(f"{BASE}/999/reference-clips/candidates").status_code == 404

    def test_manifest_tampering_cannot_escape(self, client, isolated_db, tools):
        did = _drama()
        cdir = os.path.join(db.drama_dir(did), "voice_refs", "candidates")
        os.makedirs(cdir)
        with open(os.path.join(cdir, "manifest.json"), "w") as f:
            json.dump({"A": {"candidates": [{"id": "../../../etc/passwd", "start": 0, "end": 5}]}}, f)
        body = client.get(f"{BASE}/{did}/reference-clips/candidates").json()
        assert body["speakers"][0]["candidates"] == []


# ---- C13 voice bank, C03 series link, C05 voice actor --------------------------

class TestBankLinkActor:
    def test_save_to_bank_then_apply_elsewhere(self, client, isolated_db, tools):
        did = _drama(source_language="ja")
        _upload(client, did, ref_text="こんにちは")
        db.upsert_character(did, "A", character_name="Aki", clone_engine="omnivoice")
        r = client.post(f"{BASE}/{did}/voice-bank/save",
                        json={"speaker_label": "A", "name": " Aki voice ", "notes": "calm"})
        assert r.status_code == 200, r.text
        _no_leak(r.text, did)
        e = r.json()
        assert e["name"] == "Aki voice" and e["clone_engine"] == "omnivoice"
        assert e["language"] == "ja" and e["ref_text_present"] is True
        stored = db.get_voice_bank_entry(e["id"])
        assert stored["source_drama"] == "Drama" and stored["source_speaker"] == "Aki"
        other = _drama(source_language="ja")
        applied = client.post(f"{BASE}/{other}/voice-bank/apply",
                              json={"speaker_label": "B", "voice_bank_id": e["id"]})
        assert applied.status_code == 200 and applied.json()["has_ref_audio"] is True

    def test_save_errors(self, client, isolated_db, tools):
        did = _drama()
        url = f"{BASE}/{did}/voice-bank/save"
        r = client.post(url, json={"speaker_label": "A", "name": "X"})
        assert r.status_code == 422 and "reference clip" in _error(r)["message"]
        assert client.post(url, json={"speaker_label": "A", "name": ""}).status_code == 422
        assert client.post(url, json={"speaker_label": "Z", "name": "X"}).status_code == 404
        assert client.post(f"{BASE}/999/voice-bank/save",
                           json={"speaker_label": "A", "name": "X"}).status_code == 404

    def test_series_link_and_unlink(self, client, isolated_db):
        sid = db.get_or_create_series("S")
        did = _drama(series_id=sid)
        db.upsert_series_character(sid, "Mei")
        mei = db.list_series_characters(sid)[0]["id"]
        url = f"{BASE}/{did}/series-link"
        r = client.post(url, json={"speaker_label": "A", "series_character_id": mei})
        assert r.status_code == 200, r.text
        assert r.json()["series_character_id"] == mei and r.json()["character_name"] == "Mei"
        r = client.post(url, json={"speaker_label": "A", "series_character_id": None})
        assert r.status_code == 200 and r.json()["series_character_id"] is None
        assert r.json()["character_name"] == "Mei"      # keeps its own copy of the name
        assert client.post(url, json={"speaker_label": "A"}).status_code == 422   # field required
        other_sid = db.get_or_create_series("T")
        db.upsert_series_character(other_sid, "Zed")
        zed = db.list_series_characters(other_sid)[0]["id"]
        assert client.post(url, json={"speaker_label": "A", "series_character_id": zed}).status_code == 404
        assert client.post(url, json={"speaker_label": "Z", "series_character_id": None}).status_code == 404

    def test_voice_actor_set_and_clear(self, client, isolated_db):
        did = _drama()
        url = f"{BASE}/{did}/character"
        r = client.post(url, json={"speaker_label": "A", "voice_actor": " 阿杰 "})
        assert r.status_code == 200 and r.json()["voice_actor"] == "阿杰"
        assert client.post(url, json={"speaker_label": "A", "voice_actor": ""}).json()["voice_actor"] == ""
        assert client.post(url, json={"speaker_label": "A", "voice_actor": "x" * 201}).status_code == 422
        listed = client.get(f"{BASE}/{did}").json()
        assert all("voice_actor" in c for c in listed)


# ---- dub clone warning --------------------------------------------------------------

class TestDubWarning:
    def test_warns_when_clone_engine_has_no_source(self, client, isolated_db, tools):
        did = _drama(speakers=("A", "B", "C", "D"))
        db.upsert_character(did, "A", clone_engine="f5tts")                 # no clip/design: falls back
        db.upsert_character(did, "B", clone_engine="omnivoice", voice_design="low, calm")
        db.upsert_character(did, "C", clone_engine="chatterbox")            # built-in voice, fine
        db.upsert_character(did, "D", clone_engine="f5tts", ref_audio_filename="gone.wav")
        body = client.get(f"/api/dub/dramas/{did}/config").json()
        by = {s["speaker_label"]: s for s in body["speakers"]}
        assert "plain TTS" in by["A"]["clone_warning"] and by["A"]["engine"] == "edge"
        assert by["B"]["clone_warning"] is None and by["C"]["clone_warning"] is None
        assert "missing" in by["D"]["clone_warning"] and "gone.wav" not in json.dumps(body)

    def test_no_warning_after_upload(self, client, isolated_db, tools):
        did = _drama()
        db.upsert_character(did, "A", clone_engine="f5tts")
        _upload(client, did)
        by = {s["speaker_label"]: s for s in client.get(f"/api/dub/dramas/{did}/config").json()["speakers"]}
        assert by["A"]["clone_warning"] is None and by["A"]["has_clone_ref"] is True


# ---- auth on ----------------------------------------------------------------------

def _remote():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                      raise_server_exceptions=False)


def _local():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False)


def _headers(session):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
            api_auth.CSRF_HEADER: session["csrf_token"]}


def _household(*extra, email="kid@example.com"):
    u = auth_service.add_user(email)
    for p in extra:
        auth_service.grant_permission(u["id"], p)
    return _headers(auth_service.create_session(u["id"], "pytest", "203.0.113.9"))


def _admin():
    u = auth_service.grant_admin_local("admin@example.com")
    return _headers(auth_service.create_session(u["id"], "pytest", "203.0.113.9"))


class TestAuthOn:
    def test_no_session_401(self, isolated_db):
        c = _remote()
        did = _drama()
        cid = "a" * 32
        for method, path, kw in [
                ("post", f"{BASE}/{did}/reference-clips/extract", {"json": {"speaker_label": "A"}}),
                ("get", f"{BASE}/{did}/reference-clips/candidates", {}),
                ("get", f"{BASE}/{did}/reference-clips/candidates/{cid}/audio", {}),
                ("post", f"{BASE}/{did}/reference-clips/candidates/{cid}/choose", {}),
                ("post", f"{BASE}/{did}/voice-bank/save", {"json": {"speaker_label": "A", "name": "x"}}),
                ("post", f"{BASE}/{did}/series-link",
                 {"json": {"speaker_label": "A", "series_character_id": None}})]:
            assert getattr(c, method)(path, **kw).status_code == 401, path

    def test_household_flow_and_limits(self, isolated_db, tools):
        c = _remote()
        did = _drama(speakers=("A",), durations=[6.0])
        h = _household()
        r = c.post(f"{BASE}/{did}/reference-clips/extract", headers=h, json={"speaker_label": "A"})
        assert r.status_code == 200, r.text
        _wait(r.json()["job_id"])
        cands = c.get(f"{BASE}/{did}/reference-clips/candidates", headers=h).json()["speakers"][0]["candidates"]
        cid = cands[0]["id"]
        # preview needs media.stream (off by default)
        assert c.get(f"{BASE}/{did}/reference-clips/candidates/{cid}/audio", headers=h).status_code == 403
        assert c.post(f"{BASE}/{did}/reference-clips/candidates/{cid}/choose", headers=h).status_code == 200
        assert c.post(f"{BASE}/{did}/series-link", headers=h,
                      json={"speaker_label": "A", "series_character_id": None}).status_code == 200
        # saving to the voice bank is admin.library
        assert c.post(f"{BASE}/{did}/voice-bank/save", headers=h,
                      json={"speaker_label": "A", "name": "x"}).status_code == 403
        assert db.list_voice_bank_entries() == []

    def test_media_stream_and_admin_grants(self, isolated_db, tools):
        c = _remote()
        did = _drama(speakers=("A",), durations=[6.0])
        h = _household("media.stream")
        _wait(c.post(f"{BASE}/{did}/reference-clips/extract", headers=h,
                     json={"speaker_label": "A"}).json()["job_id"])
        cid = c.get(f"{BASE}/{did}/reference-clips/candidates", headers=h).json()["speakers"][0]["candidates"][0]["id"]
        assert c.get(f"{BASE}/{did}/reference-clips/candidates/{cid}/audio", headers=h).status_code == 200
        c.post(f"{BASE}/{did}/reference-clips/candidates/{cid}/choose", headers=h)
        r = c.post(f"{BASE}/{did}/voice-bank/save", headers=_admin(),
                   json={"speaker_label": "A", "name": "Keep"})
        assert r.status_code == 200, r.text

    def test_household_bare_403_on_extract(self, isolated_db, tools):
        c = _remote()
        did = _drama()
        u = auth_service.add_user("bare@example.com")
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            auth_service.revoke_permission(u["id"], p)
        h = _headers(auth_service.create_session(u["id"]))
        assert c.post(f"{BASE}/{did}/reference-clips/extract", headers=h,
                      json={"speaker_label": "A"}).status_code == 403
        assert background_jobs.list_all_jobs() == {}

    def test_upload_and_remove_are_pc_only(self, isolated_db, tools):
        did = _drama()
        c = _remote()
        h = {**_admin(), **LOCAL_HDR}
        r = c.post(f"{BASE}/{did}/reference-clip", headers=h,
                   files={"file": ("a.wav", b"RIFF", "audio/wav")}, data={"speaker_label": "A"})
        assert r.status_code == 403
        assert c.post(f"{BASE}/{did}/reference-clip/remove", headers=h,
                      json={"speaker_label": "A", "confirm": True}).status_code == 403
        assert _char_missing_or_no_clip(did, "A")
        local = _local()
        r = local.post(f"{BASE}/{did}/reference-clip", headers=LOCAL_HDR,
                       files={"file": ("a.wav", b"RIFF", "audio/wav")}, data={"speaker_label": "A"})
        assert r.status_code == 200, r.text
        # multipart without the local header is refused even at the PC
        r = local.post(f"{BASE}/{did}/reference-clip",
                       files={"file": ("a.wav", b"RIFF", "audio/wav")}, data={"speaker_label": "A"})
        assert r.status_code == 403
