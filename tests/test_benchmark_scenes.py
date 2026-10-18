"""
Benchmark Lab translation arms that run a title's cases as batches in order
(services/benchmark_scene_service.py): batch size, context window, style and glossary reach the
engine; results are matched back by case id; the older one-case run is unchanged; a
paid arm honours the estimate and the cap. Mocked engines only.
"""
import json
import time

import pytest

import background_jobs
import db
import translate_engines
from services import benchmark_lab_service as lab
from services import benchmark_scene_service as scene
from services import translate_service
from services.service_errors import InvalidInputError, UnsupportedOperationError


class SceneEngine:
    """Paid-looking engine that records every batch (lines and the context around them) and
    answers `EN <source>`, so a test sees exactly what a batch carried."""
    name = "claude"
    supports_reference = True
    batches = []
    reverse = False

    def __init__(self, api_key=None, model="claude-sonnet-5", **kw):
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context):
        type(self).batches.append({"lines": list(zh_lines), "context": context})
        self.last_usage = {"input_tokens": 1000, "output_tokens": 1000}
        out = [f"EN {z}" for z in zh_lines]
        return out[::-1] if self.reverse and len(out) > 1 else out


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setitem(translate_engines.ENGINES, "claude", SceneEngine)
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: "sk-test" if name == "claude" else "offline")
    SceneEngine.batches, SceneEngine.reverse = [], False
    background_jobs.clear_all_jobs()
    yield SceneEngine
    background_jobs.clear_all_jobs()


def _scene(title="T", n=5, set_name="s", language="zh", start=1):
    rows = [{"label": f"{title} #{i}", "stage": "translation", "content_type": "audio_drama",
             "source_language": language, "source_text": f"{title}{i}", "reference_text": f"EN {title}{i}",
             "tier": "application", "set_name": set_name, "title": title, "scene_order": i}
            for i in range(start, start + n)]
    return db.create_benchmark_clip_cases(rows)


def _wait(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(lab.JOB_ID)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _run(options, **kw):
    started = lab.start_run("translation", [{"engine": "claude", "options": options}], **kw)
    _wait()
    return started["session_ids"][0]


class TestOptions:
    def test_missing_options_take_the_engines_defaults(self, isolated_db):
        cfg = lab.check_config("translation", {"engine": "claude", "options": {"batch_size": 7}})
        assert cfg["options"] == {"batch_size": 7, "context_window": 10, "context_window_ahead": 6,
                                  "reflect": False, "style_preset": "audio_drama", "series_id": None}

    def test_no_options_key_keeps_the_one_case_run(self, isolated_db):
        assert "options" not in lab.check_config("translation", {"engine": "claude"})

    @pytest.mark.parametrize("raw", [{"batch_size": 0}, {"batch_size": True}, {"context_window": -1},
                                     {"reflect": "yes"}, {"style_preset": "nope"}, {"series_id": 999},
                                     {"temperature": 1}, "text"])
    def test_bad_options_are_refused(self, isolated_db, raw):
        with pytest.raises(InvalidInputError):
            lab.check_config("translation", {"engine": "claude", "options": raw})

    def test_ocr_takes_no_options(self, isolated_db):
        with pytest.raises(InvalidInputError):
            lab.check_config("ocr", {"engine": "tesseract", "options": {"batch_size": 5}})


class TestSceneRun:
    def test_a_title_runs_in_batches_in_order_with_context(self, isolated_db, engine):
        _scene("T", 5)
        sid = _run({"batch_size": 2, "context_window": 2, "context_window_ahead": 1})
        assert [b["lines"] for b in engine.batches] == [["T1", "T2"], ["T3", "T4"], ["T5"]]
        run = lab.get_run(sid)
        assert run["run"]["status"] == "done" and run["run"]["aggregate_score"] == 1.0
        assert run["run"]["context_settings"]["translate_options"]["batch_size"] == 2
        assert run["run"]["context_settings"]["translate_changes"] == {
            "batch_size": 2, "context_window": 2, "context_window_ahead": 1}

    def test_scenes_are_ordered_by_scene_order_not_case_id(self, isolated_db, engine):
        ids = _scene("T", 3)
        with db.get_conn() as conn:
            conn.execute("UPDATE benchmark_cases SET scene_order = 10 - id WHERE id IN (?, ?, ?)", tuple(ids))
            conn.commit()
        _run({"batch_size": 5})
        assert engine.batches[0]["lines"] == ["T3", "T2", "T1"]

    def test_results_follow_the_case_not_the_order_the_run_lists_them(self, isolated_db, engine):
        ids = _scene("T", 4)
        sid = _run({"batch_size": 2}, case_ids=list(reversed(ids)))
        assert engine.batches[0]["lines"] == ["T1", "T2"]
        assert all(r["score"] == 1.0 for r in lab.get_run(sid)["results"])

    def test_cases_of_two_titles_never_share_a_batch(self, isolated_db, engine):
        _scene("A", 2)
        _scene("B", 2)
        _run({"batch_size": 10})
        assert sorted(b["lines"] for b in engine.batches) == [["A1", "A2"], ["B1", "B2"]]

    def test_a_case_without_a_title_is_translated_alone(self, isolated_db, engine):
        lab.create_case("c1", "你好", "EN 你好")
        lab.create_case("c2", "再见", "EN 再见")
        _run({"batch_size": 10})
        assert [b["lines"] for b in engine.batches] == [["你好"], ["再见"]]

    def test_the_series_glossary_and_style_reach_the_engine(self, isolated_db, engine):
        series_id = db.create_series("Saga")
        db.upsert_glossary_term(series_id, "T1", "Tee One")
        _scene("T", 2)
        _run({"series_id": series_id, "style_preset": "novel"})
        ctx = engine.batches[0]["context"]
        assert [t["term_original"] for t in ctx["glossary_terms"]] == ["T1"]
        assert "Tee One" in ctx["style_guidelines"]
        assert ctx["source_language"] == "zh"

    def test_without_a_series_the_run_has_no_glossary(self, isolated_db, engine):
        _scene("T", 2)
        _run({})
        assert not engine.batches[0]["context"]["glossary_terms"]

    def test_the_older_run_is_unchanged_without_options(self, isolated_db, engine):
        _scene("T", 3)
        lab.start_run("translation", [{"engine": "claude"}])
        _wait()
        assert [b["lines"] for b in engine.batches] == [["T1"], ["T2"], ["T3"]]

    def test_each_case_is_scored_and_spend_is_logged_once(self, isolated_db, engine):
        _scene("T", 4)
        before = db.get_month_spend()
        sid = _run({"batch_size": 2})
        results = lab.get_run(sid)["results"]
        assert len(results) == 4 and all(r["score"] == 1.0 for r in results)
        run = lab.get_run(sid)["run"]
        assert run["total_cost_usd"] == pytest.approx(sum(r["cost_usd"] for r in results))
        assert db.get_month_spend() - before == pytest.approx(run["total_cost_usd"])

    def test_a_scene_the_engine_cannot_translate_is_a_failed_case(self, isolated_db, engine, monkeypatch):
        class Down(SceneEngine):
            def translate_batch(self, zh_lines, context):
                raise RuntimeError("boom abcd-secret-1234")
        monkeypatch.setitem(translate_engines.ENGINES, "claude", Down)
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "abcd-secret-1234")
        _scene("T", 2)
        sid = _run({})
        results = lab.get_run(sid)["results"]
        assert all(r["score"] == 0.0 and r["error"] for r in results)
        assert "abcd-secret-1234" not in json.dumps(results)

    def test_estimate_grows_with_context_and_reflect(self, isolated_db, engine):
        _scene("T", 6)
        cost = lambda o: lab.estimate("translation", [{"engine": "claude", "options": o}])["estimated_cost_usd"]
        plain = cost({"context_window": 0, "context_window_ahead": 0})
        assert cost({"context_window": 20, "context_window_ahead": 10}) > plain
        assert cost({"context_window": 0, "context_window_ahead": 0, "reflect": True}) == pytest.approx(plain * 3)
        assert cost({"batch_size": 1, "context_window": 0, "context_window_ahead": 0}) > plain

    def test_refused_when_the_estimate_is_above_the_cap(self, isolated_db, engine, monkeypatch):
        _scene("T", 6)
        monkeypatch.setattr(lab.settings_service, "get_monthly_cap_usd", lambda: 0.0000001)
        with pytest.raises(UnsupportedOperationError):
            lab.start_run("translation", [{"engine": "claude", "options": {}}])

    def test_the_run_stops_at_the_cap_mid_scene(self, isolated_db, engine, monkeypatch):
        _scene("T", 6)
        monkeypatch.setattr(lab.settings_service, "get_monthly_cap_usd", lambda: 100.0)
        monkeypatch.setattr(lab, "estimate", lambda *a, **k: {"monthly_refusal": None, "estimate_above_cap": False,
                                                              "estimated_cost_usd": 0})
        started = lab.start_run("translation", [{"engine": "claude", "options": {"batch_size": 1}}],
                                max_cost_usd=0.0001)
        _wait()
        run = lab.get_run(started["session_ids"][0])["run"]
        assert run["status"] == "stopped_cap"
        assert len(engine.batches) < 6
