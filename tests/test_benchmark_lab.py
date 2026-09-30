"""
Step 38: Benchmark Lab service (services/benchmark_lab_service.py).

Exit conditions covered here: a run persists a real row with engine/model/
prompt version/scores; "add as regression test", once accepted, is in the
next run; plus golden-set import, CER/WER scoring, the Model Arena view and
the spending-cap refusals. Mocked engines only (test_offline, or a fake
engine class patched into translate_engines.ENGINES).
"""
import time

import pytest

import background_jobs
import db
import translate_engines
from services import benchmark_lab_service as svc
from services import settings_service, translate_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)


def _wait(job_id=svc.JOB_ID, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


class EchoEngine:
    """Paid-looking engine: returns the reference-ish text from a table."""
    name = "claude"
    supports_reference = True
    answers = {}

    def __init__(self, api_key=None, model="claude-sonnet-5", **kw):
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context):
        self.last_usage = {"input_tokens": 1000, "output_tokens": 1000}
        return [self.answers.get(z, "") for z in zh_lines]


@pytest.fixture
def paid_engine(monkeypatch):
    monkeypatch.setitem(translate_engines.ENGINES, "claude", EchoEngine)
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: "sk-test" if name == "claude" else "offline")
    EchoEngine.answers = {}
    return EchoEngine


def _run(stage="translation", configs=None, **kw):
    started = svc.start_run(stage, configs or [{"engine": "test_offline"}], **kw)
    st = _wait()
    assert st["status"] == "done", st
    return started


class TestScoring:
    def test_cer_ignores_whitespace_and_counts_edits(self):
        assert svc.error_rate("你好 世界", "你好世界") == 0.0
        assert svc.error_rate("你好世", "你好世界") == pytest.approx(0.25)

    def test_wer_counts_words(self):
        assert svc.error_rate("the cat sat", "the cat sat down", unit="word") == pytest.approx(0.25)

    def test_metric_per_stage(self):
        assert svc.score_output("translation", "a", "a") == (1.0, "similarity")
        assert svc.score_output("ocr", "你好", "你好") == (1.0, "cer")
        assert svc.score_output("transcription", "hi", "hi", "zh")[1] == "cer"
        assert svc.score_output("transcription", "hi there", "hi there", "en")[1] == "wer"
        assert svc.score_output("ocr", "x", None) == (None, "cer")

    def test_score_is_never_negative(self):
        score, _ = svc.score_output("ocr", "完全不同的很长很长的文字", "对")
        assert score == 0.0


class TestCasesAndImport:
    def test_import_jsonl_and_skip_duplicates(self, isolated_db):
        text = ('{"source": "你好", "reference": "Hello"}\n'
                '# comment\n\n'
                '{"source": "谢谢", "reference": "Thanks", "id": "flores-2"}\n')
        out = svc.import_golden_set("flores-zho", text, "jsonl", "public", "zh")
        assert (out["added"], out["skipped"]) == (2, 0)
        again = svc.import_golden_set("flores-zho", text, "jsonl", "public", "zh")
        assert (again["added"], again["skipped"]) == (0, 2)
        cases = svc.list_cases(tier="public")["cases"]
        assert {c["label"] for c in cases} == {"flores-zho #1", "flores-2"}
        assert all(c["tier"] == "public" and c["set_name"] == "flores-zho" for c in cases)

    def test_import_tsv(self, isolated_db):
        out = svc.import_golden_set("mine", "你好\tHello\n再见\n", "tsv", "application", "zh")
        assert out["added"] == 2
        refs = {c["source_text"]: c["reference_text"] for c in svc.list_cases()["cases"]}
        assert refs == {"你好": "Hello", "再见": None}

    @pytest.mark.parametrize("text", ["", "not json\n", "[1]\n", '{"reference": "x"}\n'])
    def test_bad_import_rejected(self, isolated_db, text):
        with pytest.raises(InvalidInputError):
            svc.import_golden_set("s", text, "jsonl")

    def test_sets_summary(self, isolated_db):
        svc.import_golden_set("a", "你好\tHello\n再见\n", "tsv", "public")
        sets = svc.list_sets()["sets"]
        assert sets == [{"stage": "translation", "tier": "public", "set_name": "a",
                         "case_count": 2, "with_reference": 1}]

    def test_create_and_delete_case(self, isolated_db):
        c = svc.create_case("one", "你好", "Hello", set_name="s")
        with pytest.raises(ConflictError):
            svc.create_case("two", "你好", "Hi", set_name="s")
        assert svc.delete_case(c["id"]) == {"deleted": True, "id": c["id"]}
        with pytest.raises(NotFoundError):
            svc.delete_case(c["id"])

    def test_case_output_has_no_file_name(self, isolated_db):
        db.create_benchmark_case("clip", "transcription", "audio_drama", input_filename="case_1.wav")
        case = svc.list_cases()["cases"][0]
        assert case["has_input_file"] is True
        assert "case_1.wav" not in str(case)


class TestRunPersistence:
    def test_run_persists_row_with_model_prompt_and_scores(self, isolated_db, paid_engine):
        paid_engine.answers = {"你好": "Hello", "谢谢": "Nope"}
        svc.import_golden_set("g", "你好\tHello\n谢谢\tThanks\n", "tsv", "public")
        started = _run(configs=[{"engine": "claude", "model": "claude-sonnet-5"}],
                       label="baseline", prompt_version="v2")
        (sid,) = started["session_ids"]
        detail = svc.get_run(sid)
        run = detail["run"]
        assert (run["engine"], run["model"], run["prompt_version"], run["label"]) == \
            ("claude", "claude-sonnet-5", "v2", "baseline")
        assert run["status"] == "done"
        assert run["case_count"] == 2 and run["scored_count"] == 2
        assert run["passed_count"] == 1
        assert 0 < run["aggregate_score"] < 1
        assert run["total_cost_usd"] > 0
        assert run["avg_latency_seconds"] is not None
        by_label = {r["case_label"]: r for r in detail["results"]}
        assert by_label["g #1"]["score"] == 1.0 and by_label["g #1"]["passed"] is True
        assert by_label["g #1"]["metric"] == "similarity"

    def test_paid_run_spend_counts_toward_monthly_cap(self, isolated_db, paid_engine):
        paid_engine.answers = {"你好": "Hello"}
        svc.create_case("c", "你好", "Hello")
        before = db.get_month_spend()
        _run(configs=[{"engine": "claude"}])
        assert db.get_month_spend() > before

    def test_legacy_run_history_untouched(self, isolated_db):
        svc.create_case("c", "你好", "Hello")
        _run()
        case_id = svc.list_cases()["cases"][0]["id"]
        assert db.list_benchmark_runs(case_id) == []

    def test_engine_error_is_recorded_redacted(self, isolated_db, monkeypatch):
        class Boom(EchoEngine):
            def translate_batch(self, zh_lines, context):
                raise RuntimeError("bad key sk-ant-abcdefghijklmnopqrstuvwxyz123456")
        monkeypatch.setitem(translate_engines.ENGINES, "claude", Boom)
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda n, env_path=None: "k")
        svc.create_case("c", "你好", "Hello")
        (sid,) = _run(configs=[{"engine": "claude"}])["session_ids"]
        detail = svc.get_run(sid)
        assert detail["run"]["error_count"] == 1
        assert "abcdefghijklmnop" not in detail["results"][0]["error"]


class TestRegression:
    def _drama_with_line(self):
        did = db.create_drama(title_en="D", source_language="zh")
        db.save_lines(did, [__import__("core").Line(0, 0.0, 1.0, "你好", "Hi there")])
        line_id = db.load_lines(did)[0]["id"]
        return did, line_id

    def test_added_regression_is_in_next_run(self, isolated_db):
        did, line_id = self._drama_with_line()
        out = svc.add_regression_case(did, line_id)
        case = out["case"]
        assert (case["tier"], case["set_name"], case["reference_text"]) == \
            ("regression", "regressions", "Hi there")
        assert (case["origin_drama_id"], case["origin_line_id"]) == (did, line_id)
        (sid,) = _run(tier="regression")["session_ids"]
        assert [r["case_id"] for r in svc.get_run(sid)["results"]] == [case["id"]]

    def test_nothing_is_added_without_the_explicit_call(self, isolated_db):
        self._drama_with_line()
        assert svc.list_cases(tier="regression")["cases"] == []

    def test_adding_again_replaces_not_duplicates(self, isolated_db):
        did, line_id = self._drama_with_line()
        svc.add_regression_case(did, line_id)
        again = svc.add_regression_case(did, line_id)
        assert again["replaced"] is True
        assert len(svc.list_cases(tier="regression")["cases"]) == 1

    def test_unknown_line(self, isolated_db):
        did, _ = self._drama_with_line()
        with pytest.raises(NotFoundError):
            svc.add_regression_case(did, 999999)


class TestArena:
    def test_two_engines_side_by_side(self, isolated_db, paid_engine):
        paid_engine.answers = {"你好": "Hello"}
        svc.create_case("c", "你好", "Hello")
        started = _run(configs=[{"engine": "claude"}, {"engine": "test_offline"}])
        assert started["arena_group"]
        a, b = started["session_ids"]
        view = svc.arena([a, b])
        assert [r["engine"] for r in view["runs"]] == ["claude", "test_offline"]
        (row,) = view["rows"]
        assert row["results"][0]["output_text"] == "Hello"
        assert row["results"][1]["output_text"].startswith("[TEST]")
        assert view["runs"][0]["delta_vs_first"] == 0
        assert view["runs"][1]["delta_vs_first"] < 0

    def test_arena_needs_two_runs(self, isolated_db):
        with pytest.raises(InvalidInputError):
            svc.arena([1])

    def test_same_config_twice_rejected(self, isolated_db):
        svc.create_case("c", "你好", "Hello")
        with pytest.raises(InvalidInputError):
            svc.estimate("translation", [{"engine": "test_offline"}, {"engine": "test_offline"}])


class TestEstimateAndCap:
    def test_estimate_before_run(self, isolated_db, paid_engine):
        svc.create_case("c", "你好" * 50, "Hello")
        est = svc.estimate("translation", [{"engine": "claude"}, {"engine": "test_offline"}])
        assert est["case_count"] == 1
        claude, offline = est["configs"]
        assert claude["cap_applies"] and claude["estimated_cost_usd"] > 0
        assert not offline["cap_applies"] and offline["estimated_cost_usd"] == 0

    def test_refused_when_monthly_cap_used_up(self, isolated_db, paid_engine, monkeypatch):
        svc.create_case("c", "你好", "Hello")
        monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: 1.0)
        db.log_usage(None, "claude", "claude-sonnet-5", "translate", estimated_cost_usd=2.0)
        with pytest.raises(UnsupportedOperationError):
            svc.start_run("translation", [{"engine": "claude"}])
        assert db.list_benchmark_sessions() == []

    def test_refused_when_estimate_above_remaining(self, isolated_db, paid_engine, monkeypatch):
        svc.create_case("c", "你好" * 1000, "Hello")
        monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: 0.0001)
        with pytest.raises(UnsupportedOperationError):
            svc.start_run("translation", [{"engine": "claude"}])

    def test_free_engine_runs_with_cap_used_up(self, isolated_db, monkeypatch):
        svc.create_case("c", "你好", "Hello")
        monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: 1.0)
        db.log_usage(None, "claude", "claude-sonnet-5", "translate", estimated_cost_usd=2.0)
        _run()

    def test_unknown_model_rejected(self, isolated_db):
        svc.create_case("c", "你好", "Hello")
        with pytest.raises(InvalidInputError):
            svc.estimate("translation", [{"engine": "claude", "model": "not-a-model"}])

    def test_no_cases(self, isolated_db):
        with pytest.raises(UnsupportedOperationError):
            svc.estimate("translation", [{"engine": "test_offline"}])


class TestFileStages:
    def test_ocr_case_scored_by_cer(self, isolated_db, monkeypatch):
        import benchmark
        db.create_benchmark_case("page", "ocr", "manhua", input_filename="p.png",
                                 reference_text="你好世界")
        monkeypatch.setattr(benchmark, "run_ocr_case", lambda case, backend="tesseract": {
            "output_text": "你好世", "duration_seconds": 0.1, "error": None, "score": None})
        (sid,) = _run(stage="ocr", configs=[{"engine": "tesseract"}])["session_ids"]
        (res,) = svc.get_run(sid)["results"]
        assert res["metric"] == "cer"
        assert res["score"] == pytest.approx(0.75)

    def test_transcription_case_scored_by_cer(self, isolated_db, monkeypatch):
        import benchmark
        db.create_benchmark_case("clip", "transcription", "audio_drama", input_filename="c.wav",
                                 reference_text="你好")
        seen = {}

        def fake(case, whisper_size="medium", use_gpu=False):
            seen["size"] = whisper_size
            return {"output_text": "你好", "duration_seconds": 0.1, "error": None, "score": None}
        monkeypatch.setattr(benchmark, "run_transcription_case", fake)
        (sid,) = _run(stage="transcription", configs=[{"engine": "whisper", "model": "small"}])["session_ids"]
        assert seen["size"] == "small"
        (res,) = svc.get_run(sid)["results"]
        assert (res["metric"], res["score"]) == ("cer", 1.0)
