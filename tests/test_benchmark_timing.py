"""
Line timing in the Benchmark Lab (services/asr_timing_service.py, the Lab's timing options
and the Arena's timing verdict) and the "include all lines" way of adding a title's lines.
Mocked: no GPU, no models, no network.
"""
import json
import os

import pytest

import db
import whisper_transcribe
from core import Line
from services import asr_timing_service as timing
from services import benchmark_lab_service as svc
from services import benchmark_reference_service as refs
from services import benchmark_runs_service as runs
from services import model_readiness_service
from services import transcribe_options_service as opts
from lib.errors import InvalidInputError
from tests.test_benchmark_references import _title, fake_ffmpeg  # noqa: F401  (fixture re-used)
from tests.job_helpers import wait_for_job

pytestmark = pytest.mark.usefixtures("whisper_present")


def _line(start, end, text="あ"):
    return {"start": start, "end": end, "text": text}


class TestCaseTiming:
    def test_edge_errors_come_from_the_best_covering_line(self):
        t = timing.case_timing([_line(0.35, 2.4)], 0.25, 2.25)
        assert (t["start_error_s"], t["end_error_s"]) == (0.1, 0.15)
        assert not (t["missed"] or t["split"] or t["merged"])

    def test_nothing_covering_is_a_missed_line(self):
        t = timing.case_timing([_line(5.0, 6.0)], 0.25, 2.25)
        assert t["missed"] and t["start_error_s"] is None

    def test_two_covering_lines_are_a_split(self):
        t = timing.case_timing([_line(0.25, 1.2), _line(1.3, 2.25)], 0.25, 2.25)
        assert t["split"] and not t["missed"]

    def test_a_line_running_past_both_edges_is_a_merge(self):
        assert timing.case_timing([_line(0.0, 2.5)], 0.25, 2.25)["merged"]
        assert not timing.case_timing([_line(0.1, 2.4)], 0.25, 2.25)["merged"]

    def test_silence_is_counted_only_with_speech_spans(self):
        assert timing.case_timing([_line(0.25, 2.25)], 0.25, 2.25)["silence"] is None
        assert timing.case_timing([_line(0.25, 2.25)], 0.25, 2.25, [(10.0, 11.0)])["silence"] == 1
        assert timing.case_timing([_line(0.25, 2.25)], 0.25, 2.25, [(0.0, 3.0)])["silence"] == 0


class TestSummary:
    def test_medians_share_within_and_counts(self):
        cases = [timing.case_timing([_line(0.25 + d, 2.25 + d)], 0.25, 2.25) for d in (0.0, 0.1, 0.5)]
        cases.append(timing.case_timing([_line(9.0, 10.0)], 0.25, 2.25))
        s = timing.summarise(cases + [None])
        assert s["cases"] == 4 and s["missed"] == 1
        assert s["start_median_ms"] == 100 and s["end_median_ms"] == 100
        assert s["within"] == 0.5

    def test_no_timed_cases(self):
        assert timing.summarise([None, None]) == {"cases": 0}


class TestSharedWithTheBenchTool:
    def test_the_bench_uses_the_same_matching_and_silence_code(self):
        from tools import asr_bench
        assert asr_bench.match is timing.match
        assert asr_bench.count_in_silence is timing.count_in_silence
        assert asr_bench.speech_spans is timing.speech_spans


class TestTimingOptions:
    def test_defaults_equal_what_a_title_runs_with_today(self):
        resolved = opts.resolve_lab_options(None)
        assert resolved["speech_pad_ms"] == 400            # faster-whisper's own default
        assert resolved["split_max_seconds"] == 8.0 and resolved["split_max_chars"] == 40
        assert resolved["refine_timing"] is False
        assert resolved == opts.lab_option_defaults()

    def test_values_are_checked(self):
        assert opts.resolve_lab_options({"speech_pad_ms": 100, "refine_timing": True,
                                         "split_max_seconds": 6, "split_max_chars": 30})["split_max_seconds"] == 6.0
        for bad in ({"speech_pad_ms": -1}, {"speech_pad_ms": "100"}, {"split_max_seconds": 3},
                    {"split_max_seconds": 21}, {"split_max_chars": 19}, {"split_max_chars": 81}, {"refine_timing": 1}, {"speech_pad_ms": True}):
            with pytest.raises(InvalidInputError):
                opts.resolve_lab_options(bad)

    def test_split_defaults_follow_settings_and_ranges_are_the_settings_ranges(self, monkeypatch):
        from tests.saved_settings import patch_setting
        patch_setting(monkeypatch, "split_max_seconds", 12.0)
        patch_setting(monkeypatch, "split_max_cjk_chars", 55)
        resolved = opts.resolve_lab_options(None)
        assert (resolved["split_max_seconds"], resolved["split_max_chars"]) == (12.0, 55)
        assert opts.LAB_TIMING_RANGES["split_max_seconds"] == (4.0, 20.0)
        assert opts.LAB_TIMING_RANGES["split_max_chars"] == (20, 80)

    def test_a_title_is_unaffected(self):
        assert "speech_pad_ms" not in opts.check_transcribe_fields({"min_silence_ms": 500}, None)

    def test_the_speech_pad_reaches_faster_whisper_only_when_set(self, monkeypatch):
        import whisper_models

        seen = {}

        class Model:
            def transcribe(self, path, **kw):
                seen.update(kw)
                return iter([]), type("Info", (), {"duration": 1})()

        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: Model())
        whisper_transcribe.transcribe_for_timing("a.wav", speech_pad_ms=250)
        assert seen["vad_parameters"]["speech_pad_ms"] == 250
        whisper_transcribe.transcribe_for_timing("a.wav")
        assert "speech_pad_ms" not in seen["vad_parameters"]


@pytest.fixture(autouse=True)
def separation_downloaded(monkeypatch):
    monkeypatch.setattr(model_readiness_service, "separation_ready", lambda *a, **k: True)


def _timed_case(name="clip", ref=(0.25, 2.25), text="こんにちは"):
    (cid,) = db.create_benchmark_clip_cases([{
        "label": name, "stage": "transcription", "content_type": "audio_drama", "source_language": "ja",
        "input_filename": f"{name}.wav", "reference_text": text, "tier": "application", "set_name": "t",
        "ref_start": ref[0], "ref_end": ref[1]}])
    return cid


def _run(configs):
    started = svc.start_run("transcription", configs)
    assert wait_for_job(svc.JOB_ID, timeout=15)["status"] == "done"
    return started["session_ids"]


class TestLabRunScoresTiming:
    @pytest.fixture
    def segments(self, monkeypatch):
        out = {"segments": [{"start": 0.35, "end": 2.4, "text": "こんにちは"}], "calls": []}

        def fake(path, model_size=None, language=None, use_gpu=False, local_model_path=None, **kw):
            out["calls"].append(kw)
            return out["segments"]
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", fake)
        return out

    def test_a_case_with_reference_times_stores_its_timing(self, isolated_db, segments):
        _timed_case()
        (sid,) = _run([{"engine": "whisper", "model": "large-v3"}])
        detail = runs.get_run(sid)
        (res,) = detail["results"]
        assert (res["timing"]["start_error_s"], res["timing"]["end_error_s"]) == (0.1, 0.15)
        assert detail["run"]["timing"]["cases"] == 1 and detail["run"]["timing"]["within"] == 1.0

    def test_a_case_without_times_has_no_timing(self, isolated_db, segments):
        db.create_benchmark_case("c", "transcription", "audio_drama", input_filename="c.wav",
                                 reference_text="こんにちは", source_language="ja")
        (sid,) = _run([{"engine": "whisper", "model": "large-v3"}])
        (res,) = runs.get_run(sid)["results"]
        assert res["timing"] is None and runs.get_run(sid)["run"]["timing"] == {"cases": 0}

    def test_the_speech_pad_is_passed_and_recorded(self, isolated_db, segments):
        _timed_case()
        (sid,) = _run([{"engine": "whisper", "model": "large-v3", "options": {"speech_pad_ms": 150}}])
        assert segments["calls"][0]["speech_pad_ms"] == 150
        ctx = runs.get_run(sid)["run"]["context_settings"]
        assert ctx["transcribe_options"]["speech_pad_ms"] == 150
        assert ctx["transcribe_changes"] == {"speech_pad_ms": 150}

    def test_split_limits_cut_the_lines_before_they_are_timed(self, isolated_db, segments):
        _timed_case(ref=(0.25, 9.25))
        segments["segments"] = [{"start": 0.25, "end": 9.25, "text": "こんにちは。さようなら。"}]
        (sid,) = _run([{"engine": "whisper", "model": "large-v3", "options": {"split_max_seconds": 4}}])
        (res,) = runs.get_run(sid)["results"]
        assert res["timing"]["lines"] >= 2 and res["timing"]["split"] is True

    def test_a_timing_failure_keeps_the_transcript(self, isolated_db, segments, monkeypatch):
        from services import benchmark_case_service as cases
        monkeypatch.setattr(cases.asr_timing_service, "case_timing", lambda *a, **k: 1 / 0)
        _timed_case()
        (sid,) = _run([{"engine": "whisper", "model": "large-v3"}])
        (res,) = runs.get_run(sid)["results"]
        assert res["error"] is None and res["timing"] is None and res["score"] == 1.0

    def test_whisper_ignores_refine_timing_so_it_cannot_make_two_arms_differ(self, isolated_db, segments):
        _timed_case()
        with pytest.raises(InvalidInputError, match="twice"):
            svc.start_run("transcription", [{"engine": "whisper", "model": "large-v3"},
                                            {"engine": "whisper", "model": "large-v3",
                                             "options": {"refine_timing": True}}])


class TestTimingVerdict:
    def _arena(self, isolated_db, specs, errors=None, gpu=None):
        """Runs that all scored the same single case, with the given (within, score, options);
        `errors` and `gpu` give a run's error text or use_gpu by position."""
        case = _timed_case()
        ids = []
        for i, (within, score, options) in enumerate(specs):
            sid = db.create_benchmark_session({
                "label": "", "stage": "transcription", "engine": "whisper", "model": "large-v3",
                "context_settings": json.dumps({"transcribe_options": options,
                                                "use_gpu": (gpu or {}).get(i, False)}), "status": "done",
                "case_count": 1, "aggregate_score": score})
            err = 0.1 if within else 0.9
            db.save_benchmark_result(sid, {"id": case, "label": "x", "ref_version": 1}, {
                "output_text": "a", "score": score, "metric": "cer", "passed": True,
                "error": (errors or {}).get(i),
                "timing": {"lines": 1, "missed": False, "split": False, "merged": False,
                           "start_error_s": err, "end_error_s": err, "silence": None}})
            ids.append(sid)
        return runs.arena(ids)

    def test_without_a_repeat_of_the_default_there_is_no_margin(self, isolated_db):
        arena = self._arena(isolated_db, [(False, 0.9, {}), (True, 0.9, {"speech_pad_ms": 100})])
        assert arena["runs"][1]["timing_verdict"]["state"] == "no_margin"
        assert arena["runs"][0]["timing_verdict"]["state"] == "baseline"

    def test_a_gain_over_the_noise_with_the_text_no_worse_is_accepted(self, isolated_db):
        arena = self._arena(isolated_db, [(False, 0.9, {}), (False, 0.9, {}), (True, 0.9, {"speech_pad_ms": 100})])
        assert [r["timing_verdict"]["state"] for r in arena["runs"]] == ["baseline", "repeat", "gain"]

    def test_a_timing_gain_is_rejected_when_the_text_gets_worse(self, isolated_db):
        arena = self._arena(isolated_db, [(False, 0.9, {}), (False, 0.9, {}), (True, 0.8, {"speech_pad_ms": 100})])
        verdict = arena["runs"][2]["timing_verdict"]
        assert verdict["state"] == "cer_worse" and "never traded" in verdict["text"]

    def test_a_text_drop_inside_the_noise_is_still_not_accepted(self, isolated_db):
        # The repeat differs by 0.02, so 0.01 is inside the noise, and still counts against the arm.
        arena = self._arena(isolated_db, [(False, 0.90, {}), (False, 0.92, {}), (True, 0.89, {"speech_pad_ms": 100})])
        verdict = arena["runs"][2]["timing_verdict"]
        assert verdict["state"] == "cer_worse" and "within noise" in verdict["text"]

    def test_an_arm_with_more_failed_cases_is_never_a_gain(self, isolated_db):
        arena = self._arena(isolated_db, [(False, 0.9, {}), (False, 0.9, {}), (True, 0.9, {"speech_pad_ms": 100})],
                            errors={2: "boom"})
        assert arena["runs"][2]["timing_verdict"]["state"] == "cer_worse"

    def test_a_repeat_must_also_share_the_gpu_setting(self, isolated_db):
        arena = self._arena(isolated_db, [(False, 0.9, {}), (False, 0.9, {}), (True, 0.9, {"speech_pad_ms": 100})],
                            gpu={1: True})
        # Run 2 used the GPU, so it is not a repeat of run 1 and there is no noise measurement.
        assert arena["runs"][2]["timing_verdict"]["state"] == "no_margin"

    def test_a_difference_inside_the_noise_is_no_gain(self, isolated_db):
        # The repeat of the default differs from it by the whole share, so nothing can beat that.
        arena = self._arena(isolated_db, [(False, 0.9, {}), (True, 0.9, {}), (True, 0.9, {"speech_pad_ms": 100})])
        assert arena["runs"][2]["timing_verdict"]["state"] == "no_gain"

    def test_no_verdict_when_nothing_was_timed(self, isolated_db):
        a = db.create_benchmark_session({"label": "", "stage": "transcription", "engine": "whisper", "status": "done"})
        b = db.create_benchmark_session({"label": "", "stage": "transcription", "engine": "whisper", "status": "done"})
        assert [r["timing_verdict"] for r in runs.arena([a, b])["runs"]] == [None, None]


class TestIncludeAllLines:
    def test_every_spoken_line_is_added_with_its_current_text(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        res = refs.add_title_lines(None, did, "timed", include_all=True)
        assert (res["added"], res["unedited_skipped"], res["include_all"]) == (5, 0, True)
        cases = db.list_benchmark_cases("transcription")
        assert [c["reference_text"] for c in cases] == ["你好吗", "我们走吧", "今天天气很好", "再见", "他到底说了什么"]
        # The first line starts at 0 s, so its clip cannot open earlier; the second starts at 3 s.
        assert [(c["ref_start"], c["ref_end"]) for c in cases[:2]] == [(0.0, 2.0), (0.25, 2.25)]
        assert {c["origin_drama_id"] for c in cases} == {did}

    def test_it_works_on_a_title_with_no_original_transcription(self, isolated_db, fake_ffmpeg):
        did = isolated_db.create_drama(title_en="Timed", source_language="zh", audio_filename="audio.wav")
        os.makedirs(db.drama_dir(did), exist_ok=True)
        open(os.path.join(db.drama_dir(did), "audio.wav"), "wb").write(b"RIFF")
        db.save_lines(did, [Line(0, 1.0, 3.0, "你好")])
        assert refs.add_title_lines(None, did, "timed", include_all=True)["added"] == 1

    def test_flagged_and_sound_effect_lines_are_still_left_out(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        db.save_lines(did, [Line(r["idx"], r["start"], r["end"], r["zh"], flag="unsure" if r["idx"] == 0 else None,
                                 sfx=r["idx"] == 1, id=r["id"]) for r in db.load_lines(did)])
        assert refs.add_title_lines(None, did, "timed", include_all=True)["added"] == 3

    def test_lines_already_added_as_corrections_are_not_added_twice(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        assert refs.add_title_lines(None, did, "timed")["added"] == 2
        res = refs.add_title_lines(None, did, "timed", include_all=True)
        assert (res["added"], res["skipped"]) == (3, 2)
        assert len(db.list_benchmark_cases("transcription")) == 5

    def test_a_preview_counts_and_writes_nothing(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        res = refs.add_title_lines(None, did, "timed", dry_run=True, include_all=True)
        assert (res["case_count"], res["added"]) == (5, 0)
        assert db.list_benchmark_cases("transcription") == [] and fake_ffmpeg == []

    def test_the_default_still_skips_unedited_lines(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        res = refs.add_title_lines(None, did, "timed")
        assert (res["added"], res["unedited_skipped"], res["include_all"]) == (2, 3, False)

    def test_a_title_with_no_spoken_lines_is_refused(self, isolated_db, fake_ffmpeg):
        did = isolated_db.create_drama(title_en="Silent", source_language="zh", audio_filename="audio.wav")
        with pytest.raises(InvalidInputError, match="no spoken lines"):
            refs.add_title_lines(None, did, "timed", include_all=True)
