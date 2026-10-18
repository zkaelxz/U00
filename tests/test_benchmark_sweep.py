"""
The Benchmark Lab's settings sweep (services/benchmark_sweep_service.py): the split by title,
coordinate descent with the default repeated for noise, resuming by skipping finished passes,
the hold-out check and margin rule, and the owner's approve/reject. Mocked: no GPU, no models,
no network; the recogniser is a fake whose output depends on the model and options it is given.
"""
import json
import time

import pytest

import background_jobs
import db
import whisper_models
import whisper_transcribe
from jobs import job_launch
from services import benchmark_lab_service as lab
from services import benchmark_sweep_service as sweep
from services import model_readiness_service, translate_preset_service
from services.service_errors import ConflictError, DependencyUnavailableError, UnsupportedOperationError

REF = "你好世界今天天气很好"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda size: True)
    monkeypatch.setattr(model_readiness_service, "separation_ready", lambda *a, **k: True)
    yield
    background_jobs.clear_all_jobs()


@pytest.fixture
def recogniser(monkeypatch):
    """large-v3 gets two characters wrong, turbo one, turbo at beam 3 none; anything that errors
    (separation and levelling on a missing file) scores 0."""
    calls = []

    def fake(path, model_size=None, language=None, use_gpu=False, local_model_path=None, **kw):
        calls.append((model_size, kw.get("beam_size")))
        if model_size == "large-v3-turbo":
            text = REF if kw.get("beam_size") == 3 else "你好世界今天天气很坏"
        else:
            text = "你好世界今天天汽很坏"
        return [{"start": 0.0, "end": 1.0, "text": text}]
    monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", fake)
    return calls


def _cases(titles=("A", "B", "C", "D"), per_title=2, language="zh", set_name="s", tags=None):
    rows = []
    for t in titles:
        for i in range(per_title):
            rows.append({"label": f"{t}{i}", "stage": "transcription", "content_type": "audio_drama",
                         "source_language": language, "input_filename": f"{t}{i}.wav", "reference_text": REF,
                         "tier": "application", "set_name": set_name, "title": t,
                         "tags": json.dumps(tags or ["music"])})
    return db.create_benchmark_clip_cases(rows)


def _wait(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(sweep.JOB_ID)
        if st and st["status"] in ("done", "error", "cancelled"):
            return st
        time.sleep(0.05)
    raise AssertionError("sweep did not finish")


def _sweep(set_name="s", language="zh"):
    sweep.start(set_name, language, use_gpu=False)
    _wait()
    background_jobs.clear_all_jobs()
    return sweep.get_sweep()["sweep"]


class TestSplitByTitle:
    def test_no_title_is_in_both_halves_and_the_hold_out_is_near_30_percent(self, isolated_db):
        _cases(titles=("A", "B", "C", "D", "E"), per_title=4)
        tuning, holdout = sweep.split_by_title(sweep._sweep_cases("s", "zh"))
        assert {c["title"] for c in tuning}.isdisjoint({c["title"] for c in holdout})
        assert len(holdout) in (4, 8) and len(tuning) + len(holdout) == 20

    def test_the_split_is_the_same_every_time(self, isolated_db):
        _cases()
        cases = sweep._sweep_cases("s", "zh")
        first = {c["id"] for c in sweep.split_by_title(cases)[1]}
        assert first == {c["id"] for c in sweep.split_by_title(list(reversed(cases)))[1]}

    def test_one_title_cannot_be_split(self, isolated_db):
        _cases(titles=("A",))
        with pytest.raises(UnsupportedOperationError, match="at least 2 titles"):
            sweep.estimate("s", "zh", use_gpu=False)

    def test_only_the_sweeps_language_counts(self, isolated_db):
        _cases()
        _cases(titles=("J",), language="ja")
        assert {c["source_language"] for c in sweep._sweep_cases("s", "zh")} == {"zh"}


class TestSweep:
    def test_coordinate_descent_finds_the_winner_and_the_hold_out_confirms_it(self, isolated_db, recogniser):
        _cases()
        rec = _sweep()
        assert rec["status"] == "done"
        r = rec["recommendation"]
        assert r["state"] == "adopt"
        assert r["winner"]["model"] == "large-v3-turbo" and r["winner"]["changes"] == {"beam_size": 3}
        assert r["noise"] == 0.0 and r["margin"] == sweep.MIN_SCORE_MARGIN
        assert r["holdout"]["winner_score"] == 1.0 and r["holdout"]["default_score"] == pytest.approx(0.8)
        assert "2 CER points" in r["margin_rule"]
        # The default ran twice on the tuning titles, to measure the noise.
        noise = [s for s in r["steps"] if s["step"] == "noise"]
        assert len(noise) == 2 and noise[0]["run_id"] != noise[1]["run_id"]
        assert [s["kept"] for s in r["steps"] if s["step"] == "model"] == [True]

    def test_the_per_tag_breakdown_marks_a_small_group(self, isolated_db, recogniser):
        _cases()
        (tag,) = _sweep()["recommendation"]["tags"]
        assert tag["tag"] == "music" and tag["too_small"] and tag["delta"] == pytest.approx(0.2)

    def test_only_tuning_clips_pick_and_the_hold_out_only_confirms(self, isolated_db, recogniser):
        _cases()
        rec = _sweep()
        holdout_ids = {c["id"] for c in sweep.split_by_title(sweep._sweep_cases("s", "zh"))[1]}
        for step in rec["recommendation"]["steps"]:
            ids = {r["case_id"] for r in db.list_benchmark_results(step["run_id"])}
            assert (ids <= holdout_ids) == (step["half"] == "holdout")

    def test_after_the_model_step_every_pass_uses_one_model(self, isolated_db, recogniser):
        _cases()
        rec = _sweep()
        runs = [db.get_benchmark_session(s["run_id"]) for s in rec["recommendation"]["steps"]
                if s["half"] == "tuning" and s["step"] not in ("noise", "model")]
        assert {r["model"] for r in runs} == {"large-v3-turbo"}

    def test_nothing_better_keeps_the_default_and_runs_no_hold_out(self, isolated_db, monkeypatch):
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": REF}])
        _cases()
        r = _sweep()["recommendation"]
        assert r["state"] == "keep_default" and r["holdout"] is None
        assert not any(s["half"] == "holdout" for s in r["steps"])

    def test_a_winner_that_fails_on_the_hold_out_is_not_recommended(self, isolated_db, recogniser, monkeypatch):
        _cases()
        holdout_files = {c["input_filename"] for c in sweep.split_by_title(sweep._sweep_cases("s", "zh"))[1]}
        real = whisper_transcribe.transcribe_for_timing

        def worse_on_holdout(path, model_size=None, **kw):
            out = real(path, model_size=model_size, **kw)
            if model_size == "large-v3-turbo" and any(path.endswith(f) for f in holdout_files):
                return [{"start": 0.0, "end": 1.0, "text": "你好"}]
            return out
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", worse_on_holdout)
        r = _sweep()["recommendation"]
        assert r["state"] == "not_confirmed" and r["holdout"]["delta"] < 0

    def test_the_job_is_gpu_touching_and_runs_under_the_lab_job(self, isolated_db, monkeypatch):
        _cases()
        seen = {}
        monkeypatch.setattr(job_launch, "start_job", lambda job_id, *a, **k: seen.update(job_id=job_id, **k) or True)
        sweep.start("s", "zh", use_gpu=False)
        assert seen["job_id"] == lab.JOB_ID and seen["gpu_touching"] is True

    def test_gpu_models_are_released_when_it_ends(self, isolated_db, recogniser, monkeypatch):
        released = []
        monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: released.append(1))
        _cases()
        _sweep()
        assert released


class TestStopAndResume:
    def test_starting_again_reuses_every_finished_pass(self, isolated_db, recogniser):
        _cases()
        first = _sweep()
        assert first["passes_reused"] == 0
        calls = len(recogniser)
        again = _sweep()
        assert again["passes_run"] == 0 and again["passes_reused"] == first["passes_run"]
        assert len(recogniser) == calls
        assert again["recommendation"]["winner"] == first["recommendation"]["winner"]

    def test_a_cancelled_sweep_resumes_without_redoing_finished_passes(self, isolated_db, recogniser, monkeypatch):
        _cases()
        real = background_jobs.is_cancel_requested
        monkeypatch.setattr(background_jobs, "is_cancel_requested",
                            lambda job_id: len(db.list_benchmark_sessions(100, "transcription")) >= 3)
        stopped = _sweep()
        assert stopped["status"] == "cancelled" and stopped["passes_run"] == 2
        monkeypatch.setattr(background_jobs, "is_cancel_requested", real)
        resumed = _sweep()
        assert resumed["status"] == "done" and resumed["passes_reused"] == 2

    def test_an_edited_reference_is_not_skipped(self, isolated_db, recogniser):
        ids = _cases()
        first = _sweep()
        db.update_benchmark_case_texts(ids[0], None, REF + "啊")
        again = _sweep()
        assert again["passes_run"] > 0 and again["passes_run"] + again["passes_reused"] >= first["passes_run"]

    def test_the_estimate_counts_what_is_left(self, isolated_db, recogniser):
        _cases()
        before = sweep.estimate("s", "zh", use_gpu=False)
        assert before["passes_done"] == 0 and before["passes_to_run"] > 2
        assert before["estimated_cost_usd"] == 0.0 and before["tuning_cases"] + before["holdout_cases"] == 8
        _sweep()
        after = sweep.estimate("s", "zh", use_gpu=False)
        assert after["passes_to_run"] == 0 and after["estimated_seconds"] == 0


class TestModels:
    def test_a_model_not_on_the_pc_is_left_out_and_says_why(self, isolated_db, monkeypatch):
        monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda size: size == "large-v3")
        _cases()
        models = {m["model"]: m for m in sweep.estimate("s", "zh", use_gpu=False)["models"]}
        assert models["large-v3"]["included"]
        assert not models["large-v3-turbo"]["included"] and "never downloads" in models["large-v3-turbo"]["reason"]

    def test_the_default_model_must_be_on_the_pc(self, isolated_db, monkeypatch):
        monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda size: False)
        _cases()
        with pytest.raises(DependencyUnavailableError, match="never downloads"):
            sweep.estimate("s", "zh", use_gpu=False)

    def test_japanese_adds_kotoba_and_anime_whisper(self, isolated_db):
        _cases(language="ja")
        names = [m["label"] for m in sweep.estimate("s", "ja", use_gpu=False)["models"]]
        assert names == ["large-v3", "large-v3-turbo", "kotoba-v2", "anime-whisper"]

    def test_separation_is_left_out_until_its_model_is_downloaded(self, isolated_db, monkeypatch):
        monkeypatch.setattr(model_readiness_service, "separation_ready", lambda *a, **k: False)
        _cases()
        knobs = {k["key"]: k for k in sweep.estimate("s", "zh", use_gpu=False)["knobs"]}
        assert not knobs["separate_vocals_first"]["included"] and knobs["beam_size"]["included"]


class TestDecision:
    def test_approve_saves_a_profile_and_records_the_decision(self, isolated_db, recogniser):
        _cases()
        _sweep()
        out = sweep.decide("approve", "turbo reads better")["sweep"]["decision"]
        assert out["decision"] == "promoted"
        preset = next(p for p in db.list_presets() if p["id"] == out["profile"]["id"])
        options = json.loads(preset["options_json"])
        assert options == {"asr_backend_choice": "whisper", "whisper_size": "large-v3-turbo", "beam_size": 3}
        (d,) = db.list_model_decisions(sweep.DECISION_CAPABILITY)
        assert d["decision"] == "promoted" and d["reason"] == "turbo reads better"
        assert json.loads(d["scores"])["holdout"]["winner_score"] == 1.0
        # Model re-evaluation's own list stays its own.
        assert db.list_model_decisions("translation") == []
        assert sweep.list_decisions()["decisions"][0]["setup"] == "large-v3-turbo, Beam size 3"

    def test_the_card_is_told_exactly_what_applying_writes_and_approving_touches_no_title(
            self, isolated_db, recogniser):
        _cases()
        _sweep()
        did = db.create_drama(title_zh="D", source_language="zh", translation_engine="ollama")
        before = db.get_drama(did)
        applies = sweep.get_sweep()["sweep"]["applies"]
        by_key = {a["key"]: a["value"] for a in applies}
        assert by_key["whisper_size"] == "large-v3-turbo" and by_key["beam_size"] == "3"
        assert by_key["include_genre_notes"] == "on" and by_key["default_female_pronouns"] == "off"
        decided = sweep.decide("approve")["sweep"]
        assert decided["decision"]["applies"] == applies and "applies" not in sweep.get_sweep()["sweep"]
        assert db.get_drama(did) == before
        (d,) = db.list_model_decisions(sweep.DECISION_CAPABILITY)
        scores = json.loads(d["scores"])
        assert scores["decided_by"] == "owner at this PC" and scores["sweep_id"] == decided["id"]
        assert scores["holdout"]["winner_run_id"] and scores["applies"] == applies
        # Applying the saved profile writes those settings and no others.
        translate_preset_service.apply_translate_preset(did, decided["decision"]["profile"]["id"])
        after = db.get_drama(did)
        changed = {k for k in after if after[k] != before[k]} - {"translate_start_json", "updated_at"}
        assert changed <= set(by_key)

    def test_a_decision_is_made_once(self, isolated_db, recogniser):
        _cases()
        _sweep()
        sweep.decide("reject")
        with pytest.raises(ConflictError):
            sweep.decide("approve")

    def test_a_winner_that_did_not_hold_up_cannot_be_approved(self, isolated_db, recogniser):
        _cases()
        _sweep()
        record = sweep._record()
        record["recommendation"]["state"] = "not_confirmed"
        sweep._save_record(record)
        with pytest.raises(UnsupportedOperationError, match="did not hold up"):
            sweep.decide("approve")
        assert sweep.decide("reject")["sweep"]["decision"]["decision"] == "rejected"
        assert db.list_presets() == []

    def test_nothing_is_decided_before_a_sweep_finishes(self, isolated_db):
        with pytest.raises(UnsupportedOperationError):
            sweep.decide("approve")


def test_a_sweep_the_app_stopped_during_reads_interrupted(isolated_db):
    sweep._save_record({"status": "running", "set_name": "s", "language": "zh"})
    assert sweep.get_sweep()["sweep"]["status"] == "interrupted"
