"""Compare transcription: services/compare_transcription_service.py and its
routes under /api/transcribe/dramas/{id}/compare-transcription. ffmpeg, ASR
and the translation engine are faked: no audio model, no network."""

import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import background_jobs
import core
import db
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import compare_transcription_service as svc, transcribe_service, translate_service
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError,
                                     UnsupportedOperationError)

SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRET"


class FakeEngine:
    model = "fake-model"
    last_usage = {"input_tokens": 1000, "output_tokens": 1000}

    def __init__(self):
        self.calls = []

    def translate_batch(self, texts, ctx=None):
        self.calls.append(list(texts))
        return [f"EN:{t}" for t in texts]


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(transcribe_service.diagnostics, "check_dependency", lambda name: True)
    engine = FakeEngine()
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)
    calls = {"slices": [], "transcribe": [], "text": "新的"}

    def fake_slice(audio_path, start, end, out_path, timeout=None):
        calls["slices"].append((start, end))
        with open(out_path, "wb") as f:
            f.write(b"x")

    def fake_transcribe(path, model_size="medium", **kw):
        calls["transcribe"].append({"model_size": model_size, **kw})
        return [{"start": 0.0, "end": 1.0, "text": f"{calls['text']}{len(calls['transcribe'])}"}]

    monkeypatch.setattr(core, "extract_audio_slice", fake_slice)
    monkeypatch.setattr(core, "transcribe_for_timing", fake_transcribe)
    monkeypatch.setattr(core, "release_gpu_models", lambda: None)
    calls["engine"] = engine
    yield calls
    background_jobs.clear_all_jobs()


ALL = {"kind": "range", "from_number": 1, "to_number": 3}


def _drama(n=3, **kw):
    did = db.create_drama(title_en="D", audio_filename="audio.wav", whisper_size="small",
                          source_language="zh", **kw)
    with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as f:
        f.write(b"x")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=f"原{i}",
                             en=f"en{i}" if i else "", speaker="A" if i % 2 else "B",
                             flag="check" if i == 2 else None) for i in range(n)])
    return did, [ln.id for ln in db.load_line_objects(did)]


def _wait(out):
    for _ in range(300):
        job = background_jobs.get_status(out["job_id"])
        if job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _run(did, **kw):
    kw.setdefault("selection", {"kind": "range", "from_number": 1, "to_number": 3})
    out = svc.start_compare(did, **kw)
    assert _wait(out)["status"] == "done"
    return out


def _item(p, **kw):
    return {"line_id": p["line_id"], "expected_base_zh": p["base_zh"],
            "expected_candidate_zh": p["candidate_zh"], **kw}


class TestSelection:
    def test_kinds(self):
        did, ids = _drama(4)
        pick = lambda sel: [ln.idx + 1 for ln in svc.select_lines(did, sel)]
        assert pick({"kind": "line_ids", "line_ids": [ids[3], ids[0]]}) == [1, 4]
        assert pick({"kind": "range", "from_number": 2, "to_number": 3}) == [2, 3]
        assert pick({"kind": "flagged"}) == [3]
        assert pick({"kind": "speaker", "speaker": "A"}) == [2, 4]
        assert pick({"kind": "time", "start_seconds": 1.5, "end_seconds": 2.5}) == [2, 3]

    @pytest.mark.parametrize("sel", [
        {"kind": "bogus"}, {"kind": "range", "from_number": 3, "to_number": 1},
        {"kind": "time", "start_seconds": 5, "end_seconds": 5}, {"kind": "speaker"},
        {"kind": "line_ids", "line_ids": []},
        {"kind": "range", "from_number": 50, "to_number": 60}])
    def test_bad_or_empty_selection(self, sel):
        did, _ = _drama(3)
        with pytest.raises(InvalidInputError):
            svc.select_lines(did, sel)

    def test_cap_has_a_clear_message(self, monkeypatch):
        did, _ = _drama(5)
        monkeypatch.setattr(svc, "MAX_LINES", 4)
        with pytest.raises(InvalidInputError, match="limited to 4"):
            svc.select_lines(did, {"kind": "range", "from_number": 1, "to_number": 5})
        assert len(svc.select_lines(did, {"kind": "range", "from_number": 1, "to_number": 4})) == 4


class TestLineLanguage:
    def _mixed(self):
        did, ids = _drama(3)
        lines = db.load_line_objects(did)
        lines[1].lang = "en"
        lines[2].lang = "zh"
        db.save_lines(did, lines, fields=("lang",))
        return did

    def test_whisper_hears_each_line_in_its_own_language(self, _env):
        did = self._mixed()
        _run(did)
        assert [t["language"] for t in _env["transcribe"]] == ["zh", "en", "zh"]

    def test_single_language_title_is_unchanged(self, _env):
        did, _ = _drama(3)
        _run(did)
        assert {t["language"] for t in _env["transcribe"]} == {"zh"}

    def test_qwen3_vad_detects_an_english_line(self, monkeypatch):
        did = self._mixed()
        heard = []

        class Fake:
            def transcribe(self, path, language, **kw):
                heard.append(language)
                return [{"start": 0.0, "end": 1.0, "text": "x"}]

        monkeypatch.setattr(svc, "_backend_problem", lambda c, l: None)
        monkeypatch.setattr(svc.asr_backend, "get_backend", lambda name: Fake())
        _run(did, asr_backend_choice="qwen3_asr_vad")
        assert heard == ["zh", None, "zh"]

    def test_plain_qwen3_skips_an_english_line_with_a_message(self, _env, monkeypatch):
        did = self._mixed()
        monkeypatch.setattr(svc, "_backend_problem", lambda c, l: None)
        monkeypatch.setattr(svc.asr_backend, "get_backend", lambda name: type(
            "F", (), {"transcribe": lambda self, p, lang, segs, **kw: segs})())
        out = _run(did, asr_backend_choice="qwen3_asr")
        result = background_jobs.get_status(out["job_id"])["result"]
        assert [t["language"] for t in _env["transcribe"]] == ["zh", "zh"]
        assert result["candidate_count"] == 2
        assert any("line 2" in e and "skipped" in e for e in result["errors"])


class TestRun:
    def test_candidate_settings_forwarded_and_saved_ones_are_defaults(self, _env):
        did, _ = _drama(2)
        _run(did)
        assert _env["transcribe"][0]["model_size"] == "small"
        _run(did, whisper_size="tiny")
        assert _env["transcribe"][-1]["model_size"] == "tiny"
        with pytest.raises(InvalidInputError):
            svc.start_compare(did, {"kind": "flagged"}, whisper_size="someone/evil-repo")

    def test_backend_unavailable_is_refused_with_reason(self, monkeypatch):
        did, _ = _drama(2)
        monkeypatch.setattr(svc, "_backend_problem", lambda c, l: "not installed" if c != "whisper" else None)
        opts = svc.get_options(did)
        assert {b["id"]: b["available"] for b in opts["backends"]}["qwen3_asr"] is False
        assert [b["reason"] for b in opts["backends"] if b["id"] == "qwen3_asr_vad"] == ["not installed"]
        with pytest.raises(Exception, match="not installed"):
            svc.start_compare(did, ALL, asr_backend_choice="qwen3_asr")

    def test_proposals_are_not_written(self):
        did, _ = _drama(3)
        before = [(l.zh, l.en) for l in db.load_line_objects(did)]
        out = _run(did)
        assert [(l.zh, l.en) for l in db.load_line_objects(did)] == before
        assert db.list_line_history(did) == []
        res = svc.get_compare_result(did)
        assert [p["candidate_zh"] for p in res["proposals"]] == ["新的1", "新的2", "新的3"]
        assert res["proposals"][0]["translated"] is False
        # GET /api/jobs shows counts only, never text.
        from services import jobs_service
        rec = jobs_service.get_job(out["job_id"])
        assert "新的" not in str(rec) and "原0" not in str(rec)

    def test_no_audio_is_refused(self):
        did = db.create_drama(title_en="D")
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="x")])
        with pytest.raises(UnsupportedOperationError, match="no stored audio"):
            svc.start_compare(did, ALL)
        assert svc.get_options(did)["has_audio"] is False

    def test_cancel_mid_run_keeps_earlier_proposals(self, monkeypatch):
        did, _ = _drama(3)
        seen = []

        def cancel_after_first(job_id):
            return len(seen) >= 1
        real = core.transcribe_for_timing

        def tracking(*a, **k):
            seen.append(1)
            return real(*a, **k)
        monkeypatch.setattr(core, "transcribe_for_timing", tracking)
        monkeypatch.setattr(background_jobs, "is_cancel_requested", cancel_after_first)
        _run(did)
        res = svc.get_compare_result(did)
        assert len(res["proposals"]) == 1 and res["partial"] is True

    def test_cancel_before_first_line_has_no_result(self, monkeypatch):
        did, _ = _drama(2)
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda j: True)
        out = _run(did)
        assert background_jobs.get_status(out["job_id"])["result"] == {"failed_reason": "cancelled"}
        with pytest.raises(NotFoundError):
            svc.get_compare_result(did)

    def test_model_download_failure_is_redacted(self, monkeypatch):
        def boom(*a, **k):
            raise core.ModelDownloadError(f"failed token={SECRET}")
        monkeypatch.setattr(core, "transcribe_for_timing", boom)
        did, _ = _drama(2)
        out = _run(did)
        result = background_jobs.get_status(out["job_id"])["result"]
        assert result["failed_reason"] == "model_download" and SECRET not in str(result)

    def test_one_job_per_title(self):
        did, _ = _drama(2)
        with background_jobs._lock:
            background_jobs._jobs[svc.compare_job_id(did)] = {
                "status": "running", "progress": 0.0, "message": "", "result": None,
                "error": None, "started_at": time.time(), "finished_at": None,
                "cancel_requested": False}
        with pytest.raises(ConflictError):
            svc.start_compare(did, ALL)

    def test_refused_while_another_job_changes_lines(self):
        did, _ = _drama(2)
        with background_jobs._lock:
            background_jobs._jobs[f"resegment_{did}"] = {
                "status": "queued", "progress": 0.0, "message": "", "result": None,
                "error": None, "started_at": time.time(), "finished_at": None,
                "cancel_requested": False}
        with pytest.raises(ConflictError, match="Another job is changing"):
            svc.start_compare(did, ALL)
        assert background_jobs.get_status(svc.compare_job_id(did)) is None

    def test_slice_and_hearing_errors_never_show_paths(self, _env, monkeypatch):
        secret_dir = "/home/someone/private/library/drama_42"
        real_slice = core.extract_audio_slice

        def failing_slice(audio_path, start, end, out_path, timeout=None):
            if start == 0.0:
                raise subprocess.CalledProcessError(
                    1, ["ffmpeg", "-i", f"{secret_dir}/audio.wav", f"{secret_dir}/out.wav"])
            return real_slice(audio_path, start, end, out_path, timeout=timeout)
        real_hear = core.transcribe_for_timing

        def failing_hear(path, *a, **k):
            if len(_env["transcribe"]) == 0:
                _env["transcribe"].append({})
                raise RuntimeError(f"Invalid data found when processing input: '{secret_dir}/x.wav'")
            return real_hear(path, *a, **k)
        monkeypatch.setattr(core, "extract_audio_slice", failing_slice)
        monkeypatch.setattr(core, "transcribe_for_timing", failing_hear)
        did, _ = _drama(3)
        client = TestClient(create_app(ApiSettings()))
        started = client.post(f"/api/transcribe/dramas/{did}/compare-transcription/run",
                              json={"selection": ALL})
        assert started.status_code == 200, started.text
        _wait(started.json())
        res = client.get(f"/api/transcribe/dramas/{did}/compare-transcription/result")
        assert res.status_code == 200, res.text
        errors = res.json()["errors"]
        assert len(errors) == 2 and "couldn't cut this line's audio" in errors[0]
        assert "private" not in res.text and "/home/" not in res.text
        jobs = client.get("/api/jobs")
        assert jobs.status_code == 200
        assert "private" not in jobs.text and "/home/" not in jobs.text


class TestTranslation:
    def test_translates_candidate_and_missing_current_only(self, _env, monkeypatch):
        did, _ = _drama(2)
        monkeypatch.setattr(translate_engines, "estimate_cost_for_engine", lambda *a, **k: 0.01)
        _run(did, translate=True, engine_name="claude",
             selection={"kind": "range", "from_number": 1, "to_number": 2})
        p0, p1 = svc.get_compare_result(did)["proposals"]
        # line 1 has no English: current + candidate in one call; line 2 keeps its English.
        assert _env["engine"].calls == [["原0", "新的1"], ["新的2"]]
        assert (p0["current_en"], p0["candidate_en"]) == ("EN:原0", "EN:新的1")
        assert (p1["current_en"], p1["candidate_en"]) == ("en1", "EN:新的2")
        assert db.get_month_spend() > 0

    def test_retranslate_current(self, _env):
        did, _ = _drama(2)
        _run(did, translate=True, retranslate_current=True, engine_name="claude",
             selection={"kind": "line_ids", "line_ids": [db.load_line_objects(did)[1].id]})
        assert _env["engine"].calls == [["原1", "新的1"]]
        assert svc.get_compare_result(did)["proposals"][0]["current_en"] == "EN:原1"

    def test_monthly_cap_spent_refuses_before_starting(self, monkeypatch, _env):
        did, _ = _drama(2)
        monkeypatch.setattr(svc.translate_run_service, "month_cap_usd", lambda: 1.0)
        monkeypatch.setattr(db, "get_month_spend", lambda *a: 1.0)
        with pytest.raises(UnsupportedOperationError):
            svc.start_compare(did, ALL, translate=True, engine_name="claude")
        assert _env["transcribe"] == [] and background_jobs.get_status(svc.compare_job_id(did)) is None

    def test_job_cost_cap_stops_translation_but_keeps_candidates(self, monkeypatch, _env):
        did, _ = _drama(3)
        monkeypatch.setattr(translate_engines, "estimate_cost_for_engine", lambda *a, **k: 0.5)
        _run(did, translate=True, engine_name="claude", job_cost_cap_usd=0.5)
        res = svc.get_compare_result(did)
        assert [p["translated"] for p in res["proposals"]] == [True, False, False]
        assert res["cap_reached"] is True and res["partial"] is True
        assert len(res["proposals"]) == 3

    def test_translation_error_is_redacted(self, monkeypatch, _env):
        def boom(texts, ctx=None):
            raise RuntimeError(f"401 key={SECRET}")
        _env["engine"].translate_batch = boom
        did, _ = _drama(2)
        _run(did, translate=True, engine_name="claude")
        res = svc.get_compare_result(did)
        assert res["errors"] and SECRET not in str(res)
        assert all(p["candidate_en"] == "" for p in res["proposals"])

    def test_translation_error_urls_are_scrubbed(self, monkeypatch, _env):
        def boom(texts, ctx=None):
            raise RuntimeError("failed calling https://internal.example/v1?x=1")
        _env["engine"].translate_batch = boom
        did, _ = _drama(2)
        _run(did, translate=True, engine_name="claude")
        res = svc.get_compare_result(did)
        assert res["errors"] and "internal.example" not in str(res)
        assert any("[URL]" in e for e in res["errors"])

    def test_estimate(self, monkeypatch):
        did, _ = _drama(3)
        monkeypatch.setattr(translate_engines, "estimate_translation_cost", lambda e, t: 0.1)
        assert svc.estimate_compare(did, {"kind": "flagged"})["estimated_usd"] is None
        est = svc.estimate_compare(did, {"kind": "range", "from_number": 1, "to_number": 3},
                                   translate=True, engine="claude")
        assert est["line_count"] == 3 and est["estimated_usd"] > 0 and est["cap_applies"]


class TestMissingPackage:
    def test_whisper_backend_is_unavailable_with_a_plain_reason(self, monkeypatch):
        monkeypatch.setattr(transcribe_service.diagnostics, "check_dependency", lambda name: False)
        did, _ = _drama()
        by_id = {b["id"]: b for b in svc.get_options(did)["backends"]}
        assert by_id["whisper"]["available"] is False
        assert by_id["whisper"]["reason"] == transcribe_service.MISSING_TRANSCRIPTION_MESSAGE
        with pytest.raises(DependencyUnavailableError, match="Open Diagnostics"):
            svc.start_compare(did, selection=ALL)

    def test_import_error_mid_run_fails_the_job_without_the_module_text(self, monkeypatch):
        did, _ = _drama()

        def boom(*a, **k):
            raise ModuleNotFoundError("No module named 'faster_whisper'")
        monkeypatch.setattr(core, "transcribe_for_timing", boom)
        out = svc.start_compare(did, selection=ALL)
        job = _wait(out)
        assert job["status"] == "error"
        assert job["result"]["failed_reason"] == "dependency_missing"
        assert "faster_whisper" not in job["error"] and "No module" not in job["error"]
        assert "Diagnostics" in job["error"]


class TestApply:
    def test_writes_only_zh_and_en_when_chosen_after_snapshot(self, _env):
        did, ids = _drama(3)
        db.save_lines(did, [ln for ln in db.load_line_objects(did)], fields=("flag",))
        out = _run(did, translate=True, engine_name="claude")
        proposals = svc.get_compare_result(did)["proposals"]
        before = {l.id: l for l in db.load_line_objects(did)}
        res = svc.apply_compare(did, out["job_id"], [
            _item(proposals[0]),
            _item(proposals[1], use_english=True,
                  expected_candidate_en=proposals[1]["candidate_en"])])
        assert res == {"applied": [ids[0], ids[1]], "skipped": []}
        now = {l.id: l for l in db.load_line_objects(did)}
        assert now[ids[0]].zh == "新的1" and now[ids[0]].en == before[ids[0]].en
        assert now[ids[1]].zh == "新的2" and now[ids[1]].en == "EN:新的2"
        assert now[ids[2]].zh == before[ids[2]].zh
        assert now[ids[2]].flag == before[ids[2]].flag
        assert now[ids[1]].speaker == before[ids[1]].speaker
        history = db.list_line_history(did)
        assert [h["label"] for h in history] == ["before compare-transcription apply"]

    def test_edited_line_is_skipped_not_overwritten(self):
        did, ids = _drama(3)
        out = _run(did)
        proposals = svc.get_compare_result(did)["proposals"]
        db.update_line_fields_if(did, ids[0], {"zh": "我改的"}, {})
        res = svc.apply_compare(did, out["job_id"], [_item(p) for p in proposals])
        assert res["skipped"] == [ids[0]] and res["applied"] == [ids[1], ids[2]]
        assert db.load_line_objects(did)[0].zh == "我改的"

    def test_retimed_line_is_skipped(self):
        did, ids = _drama(2)
        out = _run(did)
        p = svc.get_compare_result(did)["proposals"]
        db.update_line_fields_if(did, ids[1], {"end": 9.0}, {})
        res = svc.apply_compare(did, out["job_id"], [_item(x) for x in p])
        assert res["skipped"] == [ids[1]]

    def test_unknown_or_mismatched_proposal_writes_nothing(self):
        did, ids = _drama(2)
        out = _run(did)
        p = svc.get_compare_result(did)["proposals"]
        with pytest.raises(ConflictError):
            svc.apply_compare(did, out["job_id"], [_item(p[0]), {**_item(p[1]), "expected_candidate_zh": "别的"}])
        with pytest.raises(ConflictError):
            svc.apply_compare(did, out["job_id"], [_item(p[0], use_english=True)])
        assert [l.zh for l in db.load_line_objects(did)] == ["原0", "原1"]
        assert db.list_line_history(did) == []

    def test_job_id_and_duplicates_are_validated(self):
        did, _ = _drama(2)
        out = _run(did)
        p = svc.get_compare_result(did)["proposals"][0]
        with pytest.raises(InvalidInputError):
            svc.apply_compare(did, "comparetx_999999", [_item(p)])
        with pytest.raises(InvalidInputError):
            svc.apply_compare(did, out["job_id"], [_item(p), _item(p)])
        other, _ = _drama(1)
        with pytest.raises(NotFoundError):
            svc.get_compare_result(other)

    def test_all_skipped_apply_takes_no_snapshot(self):
        did, ids = _drama(2)
        out = _run(did)
        p = svc.get_compare_result(did)["proposals"]
        db.update_line_fields_if(did, ids[0], {"zh": "我改的"}, {})
        db.update_line_fields_if(did, ids[1], {"zh": "也改了"}, {})
        res = svc.apply_compare(did, out["job_id"], [_item(x) for x in p])
        assert res == {"applied": [], "skipped": [ids[0], ids[1]]}
        assert db.list_line_history(did) == []

    def test_one_click_per_row_keeps_older_undo_points(self):
        did, ids = _drama(10)
        for n in range(3):
            db.save_line_history_snapshot(did, db.load_line_objects(did), f"older {n}")
        out = _run(did, selection={"kind": "range", "from_number": 1, "to_number": 10})
        proposals = svc.get_compare_result(did)["proposals"]
        for p in proposals:
            assert svc.apply_compare(did, out["job_id"], [_item(p)])["applied"] == [p["line_id"]]
        labels = [h["label"] for h in db.list_line_history(did)]
        assert labels == ["before compare-transcription apply", "older 2", "older 1", "older 0"]
        # Another snapshot in between means the next apply takes its own.
        db.save_line_history_snapshot(did, db.load_line_objects(did), "someone else")
        db.update_line_fields_if(did, ids[0], {"zh": proposals[0]["base_zh"]}, {})
        svc.apply_compare(did, out["job_id"], [_item(proposals[0])])
        assert [h["label"] for h in db.list_line_history(did)][:2] == [
            "before compare-transcription apply", "someone else"]


class TestApi:
    def test_round_trip_and_registries(self):
        did, ids = _drama(2)
        client = TestClient(create_app(ApiSettings()))
        base = f"/api/transcribe/dramas/{did}/compare-transcription"
        assert client.get(f"{base}/options").json()["max_lines"] == svc.MAX_LINES
        sel = {"kind": "range", "from_number": 1, "to_number": 2}
        assert client.post(f"{base}/estimate", json={"selection": sel}).json()["line_count"] == 2
        bad = client.post(f"{base}/run", json={"selection": {"kind": "range", "from_number": 9, "to_number": 10}})
        assert bad.status_code == 422 or bad.status_code == 400
        started = client.post(f"{base}/run", json={"selection": sel})
        assert started.status_code == 200, started.text
        _wait(started.json())
        res = client.get(f"{base}/result").json()
        p = res["proposals"][0]
        applied = client.post(f"{base}/apply", json={"job_id": res["job_id"], "items": [
            {"line_id": p["line_id"], "expected_base_zh": p["base_zh"],
             "expected_candidate_zh": p["candidate_zh"]}]})
        assert applied.json() == {"applied": [p["line_id"]], "skipped": []}
        from services import jobs_service
        assert jobs_service.job_kind(res["job_id"]) == "transcribe"
        assert jobs_service.job_page(res["job_id"]) == "title"
        assert client.get(f"/api/transcribe/dramas/999999/compare-transcription/result").status_code == 404
