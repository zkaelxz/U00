"""
The Benchmark Lab's settings sweep (services/benchmark_sweep_service.py): the split by title,
coordinate descent with the default repeated for noise, resuming by skipping finished passes,
the hold-out check and margin rule, and the owner's approve/reject. Mocked: no GPU, no models,
no network; the recogniser is a fake whose output depends on the model and options it is given.
"""
import json

import pytest

import background_jobs
import db
import whisper_models
import whisper_transcribe
from jobs import job_launch
from services import benchmark_lab_service as lab
from services import benchmark_sweep_core as sweep_core
from services import benchmark_sweep_service as sweep
from services import model_readiness_service, translate_preset_service
from lib.errors import ConflictError, DependencyUnavailableError, UnsupportedOperationError
from tests.job_helpers import wait_for_job

pytestmark = pytest.mark.usefixtures("whisper_present")

REF = "你好世界今天天气很好"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    background_jobs.clear_all_jobs()
    sweep._store.active["id"] = None
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


def _sweep(set_name="s", language="zh"):
    sweep.start(set_name, language, use_gpu=False)
    wait_for_job(sweep.JOB_ID, timeout=30)
    background_jobs.clear_all_jobs()
    return sweep.get_sweep()["sweep"]


class TestSplitByTitle:
    def test_no_title_is_in_both_halves_and_the_hold_out_is_near_30_percent(self, isolated_db):
        _cases(titles=("A", "B", "C", "D", "E"), per_title=4)
        tuning, holdout = sweep_core.split_by_title(sweep._sweep_cases("s", "zh"))
        assert {c["title"] for c in tuning}.isdisjoint({c["title"] for c in holdout})
        assert len(holdout) in (4, 8) and len(tuning) + len(holdout) == 20

    def test_the_split_is_the_same_every_time(self, isolated_db):
        _cases()
        cases = sweep._sweep_cases("s", "zh")
        first = {c["id"] for c in sweep_core.split_by_title(cases)[1]}
        assert first == {c["id"] for c in sweep_core.split_by_title(list(reversed(cases)))[1]}

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
        holdout_ids = {c["id"] for c in sweep_core.split_by_title(sweep._sweep_cases("s", "zh"))[1]}
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
        holdout_files = {c["input_filename"] for c in sweep_core.split_by_title(sweep._sweep_cases("s", "zh"))[1]}
        real = whisper_transcribe.transcribe_for_timing

        def worse_on_holdout(path, model_size=None, **kw):
            out = real(path, model_size=model_size, **kw)
            if model_size == "large-v3-turbo" and any(path.endswith(f) for f in holdout_files):
                return [{"start": 0.0, "end": 1.0, "text": "你好"}]
            return out
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", worse_on_holdout)
        r = _sweep()["recommendation"]
        assert r["state"] == "not_confirmed" and r["holdout"]["delta"] < 0

    def test_failed_clips_in_the_defaults_hold_out_pass_stop_the_sweep(self, isolated_db, recogniser, monkeypatch):
        _cases()
        holdout_files = {c["input_filename"] for c in sweep_core.split_by_title(sweep._sweep_cases("s", "zh"))[1]}
        real = whisper_transcribe.transcribe_for_timing

        def default_fails_on_holdout(path, model_size=None, **kw):
            if model_size == "large-v3" and any(path.endswith(f) for f in holdout_files):
                raise RuntimeError("CUDA out of memory")
            return real(path, model_size=model_size, **kw)
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", default_fails_on_holdout)
        out = _sweep()
        # Scored 0, those clips would have made the winner look better than it is.
        assert out["status"] == "failed" and out["recommendation"] is None
        assert "(holdout) had" in out["note"] and "failed clips" in out["note"]

    def test_the_margin_rule_names_the_margin(self):
        rule = sweep._margin_rule(0.01, 0.02)
        assert rule == ("A change is recommended only when it beats the default on the held-out titles by "
                        "at least 2 CER points and at least twice the noise (1.0 points), so here by "
                        "2.0 points, with no more failed clips.")

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


    def test_the_gpu_is_freed_before_each_different_model_loads(self, isolated_db, recogniser, monkeypatch):
        events = []
        monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: events.append("release"))
        real = whisper_transcribe.transcribe_for_timing

        def tracked(path, model_size=None, **kw):
            events.append(model_size)
            return real(path, model_size=model_size, **kw)
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", tracked)
        _cases()
        _sweep()
        models = [e for e in events if e != "release"]
        assert {"large-v3", "large-v3-turbo"} <= set(models)
        last = None
        for i, e in enumerate(events):
            if e == "release":
                last = None
                continue
            assert last in (None, e), f"{e} loaded while {last} was still held (event {i})"
            last = e

    def test_a_failed_pass_is_a_failure_not_a_stop(self, isolated_db, recogniser, monkeypatch):
        _cases()

        def broken(job_id, stage, plan, *a, **k):
            for sid, _cfg, _x in plan:
                db.update_benchmark_session(sid, status="failed", note="CUDA out of memory")
        monkeypatch.setattr(lab, "_run_plan", broken)
        out = _sweep()
        assert out["status"] == "failed"
        assert "did not finish: CUDA out of memory" in out["note"] and "Stopped" not in out["note"]


class TestStopAndResume:
    @pytest.fixture(autouse=True)
    def _clean_passes(self, monkeypatch):
        # The fake fails every clip of the vocal-separation pass, and a pass with failed clips is
        # always run again, so these resume tests leave separation out to make every pass reusable.
        monkeypatch.setattr(model_readiness_service, "separation_ready", lambda *a, **k: False)

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

    def test_a_pass_with_failed_clips_is_run_again(self, isolated_db, recogniser):
        _cases()
        first = _sweep()
        i, step = next((i, st) for i, st in enumerate(first["recommendation"]["steps"])
                       if st["step"] not in ("noise", "holdout") and st["kept"] is False)
        db.update_benchmark_session(step["run_id"], error_count=3)
        assert sweep.estimate("s", "zh", use_gpu=False)["passes_to_run"] == 1
        again = _sweep()
        assert again["passes_run"] == 1 and again["recommendation"]["steps"][i]["run_id"] != step["run_id"]

    def test_a_different_model_build_or_app_version_is_run_again(self, isolated_db, recogniser, monkeypatch):
        _cases()
        _sweep()
        assert sweep.estimate("s", "zh", use_gpu=False)["passes_to_run"] == 0
        real_identity = sweep.model_identity
        monkeypatch.setattr(sweep, "model_identity", lambda stage, cfg: {**real_identity(stage, cfg), "revision": "new"})
        assert sweep.estimate("s", "zh", use_gpu=False)["passes_done"] == 0
        monkeypatch.setattr(sweep, "model_identity", real_identity)
        monkeypatch.setattr(lab, "app_version", lambda: "9.9.9")
        assert sweep.estimate("s", "zh", use_gpu=False)["passes_done"] == 0

    def test_a_refused_start_leaves_the_waiting_recommendation_alone(self, isolated_db, recogniser, monkeypatch):
        _cases()
        done = _sweep()
        _cases(titles=("E", "F"), set_name="other")
        monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            sweep.start("other", "zh", use_gpu=False)
        assert sweep.get_sweep()["sweep"]["set_name"] == "s"
        assert sweep._store.read()["recommendation"] == done["recommendation"]

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
        record = sweep._store.read()
        record["recommendation"]["state"] = "not_confirmed"
        sweep._store.save(record)
        with pytest.raises(UnsupportedOperationError, match="did not hold up"):
            sweep.decide("approve")
        assert sweep.decide("reject")["sweep"]["decision"]["decision"] == "rejected"
        assert db.list_presets() == []

    def test_nothing_is_decided_before_a_sweep_finishes(self, isolated_db):
        with pytest.raises(UnsupportedOperationError):
            sweep.decide("approve")


def test_a_sweep_the_app_stopped_during_reads_interrupted(isolated_db):
    sweep._store.save({"status": "running", "set_name": "s", "language": "zh"})
    assert sweep.get_sweep()["sweep"]["status"] == "interrupted"


def test_a_plain_lab_run_after_a_restart_does_not_make_an_old_sweep_look_live(isolated_db, monkeypatch):
    sweep._store.save({"id": "x", "status": "running", "set_name": "s", "language": "zh"})
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: {"status": "running"})
    out = sweep.get_sweep()["sweep"]
    assert out["status"] == "interrupted" and "app stopped" in out["note"]


def test_a_sweep_cancelled_while_queued_reads_cancelled(isolated_db, monkeypatch):
    sweep._store.save({"id": "x", "status": "queued", "set_name": "s", "language": "zh"})
    sweep._store.active["id"] = "x"
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: {"status": "cancelled"})
    out = sweep.get_sweep()["sweep"]
    assert out["status"] == "cancelled" and "app stopped" not in out["note"]


def test_a_stored_winner_the_profile_rules_no_longer_accept_shows_no_list(isolated_db, recogniser):
    _cases()
    _sweep()
    record = sweep._store.read()
    record["recommendation"]["winner"]["changes"]["gone_setting"] = 1
    sweep._store.save(record)
    assert sweep.get_sweep()["sweep"]["applies"] is None


def test_a_crash_marks_the_sweep_failed_and_its_unfinished_run_stopped(isolated_db, recogniser, monkeypatch):
    _cases()
    released = []
    monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: released.append(1))

    def crash(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(lab, "_run_plan", crash)
    sweep.start("s", "zh", use_gpu=False)
    st = wait_for_job(sweep.JOB_ID, timeout=30)
    assert st["status"] == "error"
    (sid,) = st["result"]["session_ids"]
    run = db.get_benchmark_session(sid)
    # The unfinished run keeps the reason it stopped, not only that it did.
    assert run["status"] == "failed" and run["note"] == "The run stopped: boom"
    rec = sweep.get_sweep()["sweep"]
    assert rec["status"] == "failed" and "boom" in rec["note"] and rec["finished_at"]
    assert sweep._store.active["id"] is None and released


def test_a_sweep_whose_job_ended_without_finishing_reads_interrupted(isolated_db, monkeypatch):
    sweep._store.save({"id": "x", "status": "running", "set_name": "s", "language": "zh"})
    sweep._store.active["id"] = "x"
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: {"status": "done"})
    out = sweep.get_sweep()["sweep"]
    assert out["status"] == "interrupted" and "job ended" in out["note"]


def test_the_transcription_and_translation_sweeps_keep_separate_records(isolated_db, monkeypatch):
    from services import benchmark_translate_sweep_service as tsweep
    sweep._store.save({"id": "x", "status": "running", "set_name": "s", "language": "zh"})
    tsweep._store.save({"id": "x", "status": "running", "set_name": "t", "language": "ja"})
    sweep._store.active["id"] = "x"
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: {"status": "running"})
    try:
        assert sweep.get_sweep()["sweep"]["status"] == "running"
        other = tsweep.get_sweep()["sweep"]
        assert other["status"] == "interrupted" and other["set_name"] == "t"
    finally:
        tsweep._store.active["id"] = None


@pytest.mark.parametrize("exc,shown", [(KeyError("beam_size"), "KeyError: 'beam_size'"),
                                       (RuntimeError(), "RuntimeError")])
def test_a_crash_with_no_readable_message_names_its_type(isolated_db, recogniser, monkeypatch, exc, shown):
    _cases()

    def crash(*a, **k):
        raise exc
    monkeypatch.setattr(lab, "_run_plan", crash)
    sweep.start("s", "zh", use_gpu=False)
    st = wait_for_job(sweep.JOB_ID, timeout=30)
    (sid,) = st["result"]["session_ids"]
    assert db.get_benchmark_session(sid)["note"] == f"The run stopped: {shown}"
    assert sweep.get_sweep()["sweep"]["note"] == shown
