"""
Tests for api/routers/review_extras_routes.py + services/review_extras_service.py
(inventory R46, R37, R35, R03): merge short adjacent lines (preview/apply),
learn my style (mocked LLM), SenseVoice tagging (mocked model) and the
burned-subtitle preview clip (mocked ffmpeg). TestClient against an
`isolated_db` library -- no network, GPU, model or ffmpeg.
"""

import json
import os
import subprocess
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import adaptive_style
import background_jobs
import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import review_extras_service as svc

BASE = "/api/review-extras/dramas"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _error(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code
    return resp.json()["error"]["message"]


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _snapshot(did):
    return [(ln.id, ln.idx, ln.start, ln.end, ln.zh, ln.en) for ln in db.load_line_objects(did)]


# ---------------------------------------------------------------------------
# R46: merge short adjacent lines
# ---------------------------------------------------------------------------

def _mergeable():
    """Lines 0+1 merge (short, same speaker, close); line 2 stays."""
    did = db.create_drama(title_en="M", source_language="zh")
    db.save_lines(did, [Line(idx=0, start=0.0, end=0.5, zh="你", en="You"),
                        Line(idx=1, start=0.6, end=1.0, zh="好", en="good"),
                        Line(idx=2, start=5.0, end=9.0, zh="再见了朋友", en="Goodbye, friend")])
    return did, [ln.id for ln in db.load_line_objects(did)]


class TestMergeShort:
    def test_preview_is_read_only(self, client):
        did, ids = _mergeable()
        before = _snapshot(did)
        r = client.get(f"{BASE}/{did}/merge-short/preview")
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["source_line_ids"] == ids
        assert (p["line_count_before"], p["line_count_after"]) == (3, 2)
        assert p["groups"] == [[ids[0], ids[1]]]
        m = p["merges"][0]
        assert (m["line_id"], m["idx"], m["zh"], m["en"]) == (ids[0], 0, "你好", "You good")
        assert p["options"] == {"min_duration": 1.2, "max_gap": 0.5, "max_chars": 80}
        assert _snapshot(did) == before and db.list_line_history(did) == []

    def test_preview_does_not_mutate_loaded_lines(self, isolated_db, monkeypatch):
        did, _ = _mergeable()
        loaded = db.load_line_objects(did)
        monkeypatch.setattr(svc.db, "load_line_objects", lambda _d: loaded)
        svc.preview_merge_short(did)
        assert [(ln.zh, ln.idx, ln.end, ln.merged_ids) for ln in loaded] == [
            ("你", 0, 0.5, []), ("好", 1, 1.0, []), ("再见了朋友", 2, 9.0, [])]

    def test_options_change_the_plan_and_are_bounded(self, client):
        did, _ = _mergeable()
        p = client.get(f"{BASE}/{did}/merge-short/preview?max_gap=0.05").json()
        assert p["groups"] == [] and p["line_count_after"] == 3
        _error(client.get(f"{BASE}/{did}/merge-short/preview?max_chars=5"), 422, "validation_error")
        _error(client.get(f"{BASE}/{did}/merge-short/preview?min_duration=0"), 422,
               "validation_error")

    def test_apply_merges_snapshots_and_can_be_restored(self, client):
        did, ids = _mergeable()
        db.save_translation_notes(did, [{"line_idx": 1, "term": "T", "note_type": "cultural",
                                         "note": "on the second line"}])
        p = client.get(f"{BASE}/{did}/merge-short/preview").json()
        r = client.post(f"{BASE}/{did}/merge-short/apply",
                        json={"expected_line_ids": p["source_line_ids"],
                              "expected_groups": p["groups"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["line_ids"] == [ids[0], ids[2]] and body["merged_groups"] == 1
        assert [ln.zh for ln in db.load_line_objects(did)] == ["你好", "再见了朋友"]
        # the merged-away line's note moved onto the line it merged into
        assert [n["line_id"] for n in db.list_translation_notes(did)] == [ids[0]]
        hist = db.list_line_history(did)
        assert [h["label"] for h in hist] == ["before merge"]
        restored = client.post(f"/api/restructure/dramas/{did}/history/{hist[0]['id']}/restore",
                               json={"expected_line_ids": body["line_ids"]})
        assert restored.status_code == 200, restored.text
        assert [ln.zh for ln in db.load_line_objects(did)] == ["你", "好", "再见了朋友"]

    def test_apply_refuses_stale_line_ids(self, client):
        did, ids = _mergeable()
        p = client.get(f"{BASE}/{did}/merge-short/preview").json()
        lines = db.load_line_objects(did)
        lines.append(Line(idx=3, start=10.0, end=11.0, zh="新", en="new"))
        db.save_lines(did, lines)
        before = _snapshot(did)
        _error(client.post(f"{BASE}/{did}/merge-short/apply",
                           json={"expected_line_ids": p["source_line_ids"],
                                 "expected_groups": p["groups"]}), 409, "conflict")
        assert _snapshot(did) == before and db.list_line_history(did) == []

    def test_apply_refuses_when_the_plan_changed(self, client):
        did, ids = _mergeable()
        p = client.get(f"{BASE}/{did}/merge-short/preview").json()
        lines = db.load_line_objects(did)
        lines[1].start, lines[1].end = 3.0, 3.5   # same ids, no longer mergeable
        db.save_lines(did, lines)
        before = _snapshot(did)
        msg = _error(client.post(f"{BASE}/{did}/merge-short/apply",
                                 json={"expected_line_ids": p["source_line_ids"],
                                       "expected_groups": p["groups"]}), 409, "conflict")
        assert "preview again" in msg
        assert _snapshot(did) == before and db.list_line_history(did) == []

    def test_apply_refused_while_a_job_runs(self, client, monkeypatch):
        from services import drama_service
        did, ids = _mergeable()
        p = client.get(f"{BASE}/{did}/merge-short/preview").json()
        monkeypatch.setattr(drama_service, "job_running_for_drama", lambda _d: True)
        before = _snapshot(did)
        _error(client.post(f"{BASE}/{did}/merge-short/apply",
                           json={"expected_line_ids": ids, "expected_groups": p["groups"]}),
               409, "conflict")
        assert _snapshot(did) == before

    def test_apply_with_nothing_to_merge_is_refused(self, client):
        did, ids = _mergeable()
        _error(client.post(f"{BASE}/{did}/merge-short/apply",
                           json={"expected_line_ids": ids, "expected_groups": []}),
               422, "validation_error")

    def test_unknown_drama(self, client):
        _error(client.get(f"{BASE}/999/merge-short/preview"), 404, "not_found")


# ---------------------------------------------------------------------------
# R37: learn my style
# ---------------------------------------------------------------------------

class _FakeEngine:
    supports_reference = True
    model = "fake-model"


def _style_drama(samples=8):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="Style", series_id=sid, translation_engine="claude")
    for i in range(samples):
        db.record_edit_sample(did, f"中{i}", f"AI {i}", f"Mine {i}")
    return did, sid


@pytest.fixture
def fake_engine(monkeypatch):
    from services import translate_service
    import translate_engines
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda _n: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: _FakeEngine())


class TestLearnStyle:
    def test_empty_state(self, client):
        did, _ = _style_drama(samples=0)
        s = client.get(f"{BASE}/{did}/style").json()
        assert s["profile"] is None and s["scope"] == "series"
        assert (s["edit_count"], s["min_samples"]) == (0, adaptive_style.MIN_SAMPLES_TO_LEARN)

    def test_learn_needs_enough_edits(self, client, fake_engine):
        did, _ = _style_drama(samples=3)
        _error(client.post(f"{BASE}/{did}/style/learn", json={}), 400, "unsupported_operation")

    def test_learn_saves_profile_for_the_series(self, client, fake_engine, monkeypatch):
        did, sid = _style_drama()
        seen = {}

        def analyze(samples, engine, existing_profile=None, usage_cb=None):
            seen["n"] = len(samples)
            usage_cb(10, 5)
            return {"preferences": ["Keep it short"], "summary": "Terse", "confidence": "high"}
        monkeypatch.setattr(adaptive_style, "analyze_edit_patterns", analyze)
        r = client.post(f"{BASE}/{did}/style/learn", json={"engine": "claude"})
        assert r.status_code == 200, r.text
        s = r.json()
        assert seen["n"] == 8
        assert s["profile"]["preferences"] == ["Keep it short"]
        assert s["profile"]["applied"] is True and s["message"].startswith("Learned 1")
        assert db.get_style_profile(f"series:{sid}")["profile"]["summary"] == "Terse"

    def test_learn_without_pattern_saves_nothing(self, client, fake_engine, monkeypatch):
        did, sid = _style_drama()
        monkeypatch.setattr(adaptive_style, "analyze_edit_patterns",
                            lambda *a, **k: {"preferences": [], "summary": "No pattern."})
        s = client.post(f"{BASE}/{did}/style/learn", json={}).json()
        assert s["profile"] is None and s["message"] == "No pattern."
        assert db.get_style_profile(f"series:{sid}") is None

    def test_engine_error_is_redacted(self, client, fake_engine, monkeypatch):
        did, _ = _style_drama()
        key = "sk-ant-api03-" + "A" * 40

        def boom(*a, **k):
            raise RuntimeError(f"401 bad key {key}")
        monkeypatch.setattr(adaptive_style, "analyze_edit_patterns", boom)
        r = client.post(f"{BASE}/{did}/style/learn", json={})
        assert r.status_code == 500 and key not in r.text

    def test_translation_only_engine_refused(self, client, fake_engine):
        did, _ = _style_drama()
        _error(client.post(f"{BASE}/{did}/style/learn", json={"engine": "deepl"}), 400,
               "unsupported_operation")

    def test_apply_toggle_pauses_the_profile_everywhere(self, client, monkeypatch, fake_engine):
        did, sid = _style_drama()
        db.save_style_profile(f"series:{sid}", {"preferences": ["Keep it short"]}, 8)
        r = client.post(f"{BASE}/{did}/style/apply", json={"apply": False})
        assert r.status_code == 200 and r.json()["profile"]["applied"] is False
        stored = db.get_style_profile(f"series:{sid}")["profile"]
        # every translate path renders the block through this one function
        assert adaptive_style.profile_to_prompt_block(stored) == ""
        # a re-learn keeps the toggle off
        monkeypatch.setattr(adaptive_style, "analyze_edit_patterns",
                            lambda *a, **k: {"preferences": ["Use contractions"]})
        assert client.post(f"{BASE}/{did}/style/learn", json={}).json()["profile"]["applied"] is False
        r = client.post(f"{BASE}/{did}/style/apply", json={"apply": True})
        assert r.json()["profile"]["applied"] is True
        assert "Use contractions" in adaptive_style.profile_to_prompt_block(
            db.get_style_profile(f"series:{sid}")["profile"])

    def test_translate_run_skips_a_paused_profile(self, isolated_db):
        """translate_run_service, line_ai_service, workspace_job_service and the
        CLI all call profile_to_prompt_block on the stored profile."""
        import inspect
        from services import line_ai_service, translate_run_service, workspace_job_service
        import cli
        for mod in (translate_run_service, line_ai_service, workspace_job_service, cli):
            assert "profile_to_prompt_block" in inspect.getsource(mod)
        assert adaptive_style.profile_to_prompt_block(
            {"preferences": ["x"], "apply": False}) == ""

    def test_apply_without_profile_is_404(self, client):
        did, _ = _style_drama()
        _error(client.post(f"{BASE}/{did}/style/apply", json={"apply": True}), 404, "not_found")
        _error(client.post(f"{BASE}/{did}/style/apply", json={"apply": "no"}), 422,
               "validation_error")

    def test_reset_needs_confirm(self, client):
        did, sid = _style_drama()
        db.save_style_profile(f"series:{sid}", {"preferences": ["Keep it short"]}, 8)
        _error(client.post(f"{BASE}/{did}/style/reset", json={}), 422, "validation_error")
        assert db.get_style_profile(f"series:{sid}")["profile"]["preferences"]
        r = client.post(f"{BASE}/{did}/style/reset", json={"confirm": True})
        assert r.status_code == 200 and r.json()["profile"] is None
        assert db.get_style_profile(f"series:{sid}")["profile"] == {"preferences": []}


# ---------------------------------------------------------------------------
# R35: SenseVoice tagging
# ---------------------------------------------------------------------------

def _audio_drama():
    did = db.create_drama(title_en="A")
    with open(os.path.join(db.drama_dir(did), "a.mp3"), "wb") as f:
        f.write(b"ID3")
    db.update_drama(did, audio_filename="a.mp3")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="哈哈", en="Haha"),
                        Line(idx=1, start=1.0, end=2.0, zh="唉", en="Sigh")])
    return did, [ln.id for ln in db.load_line_objects(did)]


class TestSenseVoice:
    def test_missing_funasr_is_503(self, client, monkeypatch):
        did, _ = _audio_drama()
        monkeypatch.setattr(svc, "_sensevoice_installed", lambda: False)
        _error(client.post(f"{BASE}/{did}/sensevoice", json={}), 503, "dependency_unavailable")
        assert client.get(f"{BASE}/{did}/sensevoice").json()["installed"] is False

    def test_no_audio_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(svc, "_sensevoice_installed", lambda: True)
        did = db.create_drama(title_en="N")
        _error(client.post(f"{BASE}/{did}/sensevoice", json={}), 400, "unsupported_operation")

    def test_job_tags_by_line_id_and_rows_come_back(self, client, monkeypatch):
        import sensevoice_tags
        from services import workspace_job_service
        did, ids = _audio_drama()
        monkeypatch.setattr(svc, "_sensevoice_installed", lambda: True)
        monkeypatch.setattr(workspace_job_service.core_module, "release_gpu_models", lambda: None)
        seen = {}

        def tag_lines(audio_path, lines, use_gpu=False, progress_cb=None):
            seen["audio"] = os.path.basename(audio_path)
            progress_cb(1.0)
            return {ids[1]: {"emotion": "sad", "events": ["Cry", "Speech"]}}
        monkeypatch.setattr(sensevoice_tags, "tag_lines", tag_lines)
        db.save_emotions(did, {0: {"emotion": "happy", "intensity": "high", "note": ""},
                               1: {"emotion": "angry", "intensity": "low", "note": ""}})
        r = client.post(f"{BASE}/{did}/sensevoice", json={})
        assert r.status_code == 200, r.text
        job = _wait(r.json()["job_id"])
        assert job["status"] == "done", job
        assert seen["audio"] == "a.mp3"
        g = client.get(f"{BASE}/{did}/sensevoice").json()
        assert g["tagged"] == 1 and g["has_audio"] is True
        by_id = {row["line_id"]: row for row in g["rows"]}
        assert by_id[ids[1]]["audio_emotion"] == "sad"
        assert by_id[ids[1]]["audio_events"] == "crying"
        assert by_id[ids[0]]["audio_emotion"] == "" and by_id[ids[0]]["text_emotion"] == "happy"

    def test_no_tags_yet_returns_no_rows(self, client):
        did, _ = _audio_drama()
        g = client.get(f"{BASE}/{did}/sensevoice").json()
        assert g["rows"] == [] and g["tagged"] == 0 and "license" in g["license_note"].lower()


# ---------------------------------------------------------------------------
# R03: burned-subtitle preview clip
# ---------------------------------------------------------------------------

def _video_drama(long_line=False):
    did = db.create_drama(title_en="V")
    with open(os.path.join(db.drama_dir(did), "v.mp4"), "wb") as f:
        f.write(b"\0" * 64)
    db.update_drama(did, source_video_filename="v.mp4")
    db.save_lines(did, [Line(idx=0, start=1.0, end=100.0 if long_line else 3.0, zh="你好",
                             en="Hello")])
    return did, db.load_line_objects(did)[0].id


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    import video_export
    calls = []

    def render(video, ass, out, start, end):
        calls.append({"video": os.path.basename(video), "ass": ass, "start": start, "end": end})
        with open(out, "wb") as f:
            f.write(b"CLIPDATA" * 16)
        return out
    monkeypatch.setattr(svc, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(video_export, "render_preview_clip", render)
    return calls


class TestBurnPreview:
    def test_render_then_stream_with_range(self, client, fake_ffmpeg):
        did, lid = _video_drama()
        r = client.post(f"{BASE}/{did}/burn-preview", json={"line_id": lid, "pad_seconds": 1})
        assert r.status_code == 200, r.text
        assert (r.json()["start"], r.json()["end"]) == (0.0, 4.0)
        assert _wait(r.json()["job_id"])["status"] == "done"
        assert fake_ffmpeg[0]["video"] == "v.mp4" and "Hello" in fake_ffmpeg[0]["ass"]
        info = client.get(f"{BASE}/{did}/burn-preview/info").json()
        assert info["clip"]["line_id"] == lid and info["clip"]["preset"] == "Clean"
        full = client.get(f"{BASE}/{did}/burn-preview/clip")
        assert full.status_code == 200 and full.content == b"CLIPDATA" * 16
        assert full.headers["content-type"] == "video/mp4"
        part = client.get(f"{BASE}/{did}/burn-preview/clip", headers={"Range": "bytes=0-3"})
        assert part.status_code == 206 and part.content == b"CLIP"
        assert not os.path.exists(os.path.join(db.drama_dir(did), "_burn_preview.part.mp4"))

    def test_clip_length_is_capped(self, client, fake_ffmpeg):
        did, lid = _video_drama(long_line=True)
        r = client.post(f"{BASE}/{did}/burn-preview", json={"line_id": lid})
        body = r.json()
        assert body["end"] - body["start"] == svc.MAX_CLIP_SECONDS
        _wait(body["job_id"])
        assert fake_ffmpeg[0]["end"] - fake_ffmpeg[0]["start"] == svc.MAX_CLIP_SECONDS

    def test_bounds_and_validation(self, client, fake_ffmpeg):
        did, lid = _video_drama()
        _error(client.post(f"{BASE}/{did}/burn-preview", json={"line_id": 99999}), 404, "not_found")
        _error(client.post(f"{BASE}/{did}/burn-preview", json={"line_id": lid, "pad_seconds": 60}),
               422, "validation_error")
        _error(client.post(f"{BASE}/{did}/burn-preview", json={"line_id": lid, "preset": "Nope"}),
               422, "validation_error")
        _error(client.post(f"{BASE}/{did}/burn-preview",
                           json={"line_id": lid, "path": "/etc/passwd"}), 422, "validation_error")
        assert fake_ffmpeg == []

    def test_missing_ffmpeg_is_503(self, client, monkeypatch):
        did, lid = _video_drama()
        monkeypatch.setattr(svc, "_ffmpeg_available", lambda: False)
        _error(client.post(f"{BASE}/{did}/burn-preview", json={"line_id": lid}), 503,
               "dependency_unavailable")
        assert client.get(f"{BASE}/{did}/burn-preview/info").json()["ffmpeg_available"] is False

    def test_no_video_is_refused(self, client, fake_ffmpeg):
        did = db.create_drama(title_en="NV")
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="b")])
        lid = db.load_line_objects(did)[0].id
        _error(client.post(f"{BASE}/{did}/burn-preview", json={"line_id": lid}), 400,
               "unsupported_operation")

    def test_no_clip_yet_is_404(self, client):
        did, _ = _video_drama()
        _error(client.get(f"{BASE}/{did}/burn-preview/clip"), 404, "not_found")
        assert client.get(f"{BASE}/{did}/burn-preview/info").json()["clip"] is None

    def test_ffmpeg_failure_is_fixed_text(self, client, monkeypatch):
        import video_export
        did, lid = _video_drama()
        monkeypatch.setattr(svc, "_ffmpeg_available", lambda: True)

        def fail(video, ass, out, start, end):
            raise subprocess.CalledProcessError(1, ["ffmpeg", "-i", video])
        monkeypatch.setattr(video_export, "render_preview_clip", fail)
        job = _wait(client.post(f"{BASE}/{did}/burn-preview", json={"line_id": lid}).json()["job_id"])
        assert job["status"] == "error" and "v.mp4" not in (job["error"] or "")

    def test_symlinked_clip_is_not_served(self, client, tmp_path):
        did, _ = _video_drama()
        secret = tmp_path / "secret.mp4"
        secret.write_bytes(b"SECRET")
        os.symlink(secret, os.path.join(db.drama_dir(did), svc.BURN_PREVIEW_FILE))
        _error(client.get(f"{BASE}/{did}/burn-preview/clip"), 404, "not_found")

    def test_meta_file_is_not_trusted_for_paths(self, client, fake_ffmpeg):
        did, lid = _video_drama()
        _wait(client.post(f"{BASE}/{did}/burn-preview", json={"line_id": lid}).json()["job_id"])
        with open(os.path.join(db.drama_dir(did), svc.BURN_PREVIEW_META), "w") as f:
            json.dump({"line_id": lid, "path": "/etc/passwd"}, f)
        info = client.get(f"{BASE}/{did}/burn-preview/info").json()
        assert "path" not in json.dumps(info["clip"])


def test_burn_preview_job_blocks_drama_delete():
    import background_jobs
    assert "burnpreview_" in background_jobs.DRAMA_JOB_PREFIXES
