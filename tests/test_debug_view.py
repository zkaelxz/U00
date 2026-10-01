"""
tests/test_debug_view.py -- Step 58's "what happened here?" view and bug
record-and-replay.

Confirms the view surfaces real, currently-recorded data (glossary
matches, flag reason, engine/model, translation notes, current
neighbors) rather than placeholders, and explicitly marks the fields
Step 41's not-yet-built reproducibility metadata would cover (the
historical context window, per-line prompt/model version, per-stage job
timing) as unavailable instead of fabricating them. Also confirms a
saved bug bundle actually reproduces (or, when the engine changes, fails
to reproduce) the same output on replay.
"""
import json

import core
import db
import debug_view
import background_jobs

Line = core.Line


def _make_drama(isolated_db, **overrides):
    fields = {"title_en": "Test Drama", "source_language": "zh",
              "translation_engine": "fake"}
    fields.update(overrides)
    return isolated_db.create_drama(**fields)


class TestExplainLine:
    def test_surfaces_real_glossary_match_flag_and_translation_notes(self, isolated_db):
        series_id = isolated_db.get_or_create_series("Test Series")
        drama_id = _make_drama(isolated_db, series_id=series_id)
        isolated_db.upsert_glossary_term(series_id, "沈清疑", "Shen Qingyi")
        isolated_db.upsert_glossary_term(series_id, "不相关", "Unrelated")

        lines = [Line(idx=0, start=0, end=1, zh="沈清疑来了。", en="Shen Qingyi is here.",
                       flag="uncertain_translation", flag_note="pronoun unclear")]
        isolated_db.save_lines(drama_id, lines)
        isolated_db.save_translation_notes(drama_id, [
            {"line_id": lines[0].id, "term": "沈清疑", "note_type": "name_meaning",
             "note": "A poetic given name."}])

        info = debug_view.explain_line(drama_id, lines[0], lines)

        assert [t["term_original"] for t in info["glossary_matches"]] == ["沈清疑"]
        assert info["flag"] == "uncertain_translation"
        assert "Possible mistranslation" in info["flag_reason"]
        assert info["flag_note"] == "pronoun unclear"
        assert info["translation_notes"][0]["note"] == "A poetic given name."
        # Real data, not placeholders -- and every "we don't record this
        # yet" note names the actual gap rather than staying silent.
        assert "Step 41" in info["glossary_matches_note"]
        assert "Step 41" in info["context_window_note"]
        assert "No per-line record" in info["prompt_version_note"]
        assert info["provenance"] is None
        assert info["context_window_used"] is None

    def test_engine_prefers_active_translation_version_over_drama_default(self, isolated_db):
        drama_id = _make_drama(isolated_db, translation_engine="claude")
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello")]
        isolated_db.save_lines(drama_id, lines)
        isolated_db.save_translation_version(drama_id, lines, "v1", engine="deepseek",
                                             model="deepseek-chat", make_active=True)

        info = debug_view.explain_line(drama_id, lines[0], lines)

        assert info["engine"] == "deepseek"
        assert info["model"] == "deepseek-chat"
        assert "active saved translation version" in info["engine_source"]

    def test_falls_back_to_drama_default_engine_with_a_caveat(self, isolated_db):
        drama_id = _make_drama(isolated_db, translation_engine="claude")
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello")]
        isolated_db.save_lines(drama_id, lines)

        info = debug_view.explain_line(drama_id, lines[0], lines)

        assert info["engine"] == "claude"
        assert info["model"] is None
        assert "not necessarily what" in info["engine_source"]

    def test_shows_current_neighboring_lines(self, isolated_db):
        drama_id = _make_drama(isolated_db)
        lines = [Line(idx=i, start=float(i), end=float(i + 1), zh=f"句{i}", en=f"line{i}")
                for i in range(5)]
        isolated_db.save_lines(drama_id, lines)

        info = debug_view.explain_line(drama_id, lines[2], lines, context_before=2, context_after=2)

        assert [n[0] for n in info["current_neighbors_before"]] == [0, 1]
        assert [n[0] for n in info["current_neighbors_after"]] == [3, 4]

    def test_no_all_lines_means_no_neighbors_not_fabricated_ones(self, isolated_db):
        drama_id = _make_drama(isolated_db)
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello")]
        isolated_db.save_lines(drama_id, lines)

        info = debug_view.explain_line(drama_id, lines[0], all_lines=None)

        assert info["current_neighbors_before"] == []
        assert info["current_neighbors_after"] == []


class TestExplainJob:
    def test_reports_real_total_duration_for_a_finished_job(self):
        job_id = "translate_999"
        background_jobs._jobs[job_id] = {
            "status": "done", "progress": 1.0, "message": "", "error": None,
            "started_at": 1000.0, "finished_at": 1042.5, "gpu_touching": False,
            "description": "translate", "kind": "thread",
        }
        try:
            info = debug_view.explain_job(job_id)
            assert info["found"] is True
            assert info["duration_seconds"] == 42.5
            assert info["per_stage_breakdown"] is None
            assert "Step 41" in info["per_stage_breakdown_note"]
        finally:
            background_jobs.clear_job(job_id)

    def test_unknown_job_reports_not_found_rather_than_guessing(self):
        info = debug_view.explain_job("translate_no_such_job")
        assert info == {"job_id": "translate_no_such_job", "found": False}


class _ContextAwareEngine:
    """A fake engine whose output depends on BOTH the source text and the
    context it was given -- unlike TestOfflineEngine (deterministic from
    zh alone), so a replay that silently dropped the recorded context
    would produce a different, detectably wrong result."""
    supports_reference = True
    name = "context_aware_fake"

    def __init__(self, api_key=None, model=None, mood="calm"):
        self.mood = mood

    def translate_batch(self, zh_lines, context):
        n_ctx = len(context.get("recent_context") or [])
        return [f"[{self.mood}|ctx={n_ctx}] {z}" for z in zh_lines]


class TestBugBundle:
    def test_save_and_replay_reproduces_the_same_output(self, isolated_db):
        drama_id = _make_drama(isolated_db)
        lines = [Line(idx=0, start=0, end=1, zh="第一句", en="translated first"),
                 Line(idx=1, start=1, end=2, zh="第二句", en="")]
        isolated_db.save_lines(drama_id, lines)
        engine = _ContextAwareEngine(mood="calm")
        lines[1].en = engine.translate_batch(
            [lines[1].zh], {"recent_context": [(lines[0].zh, lines[0].en)]})[0]
        assert lines[1].en == "[calm|ctx=1] 第二句"
        isolated_db.save_lines(drama_id, lines)

        report_id = debug_view.save_bug_bundle(
            drama_id, lines[1], lines, "context_aware_fake", None,
            glossary_terms=[], locale="en-US", context_window=6)

        result = debug_view.replay_bug_bundle(report_id, _ContextAwareEngine(mood="calm"))

        assert result["reproduced"] is True
        assert result["replay_output"] == "[calm|ctx=1] 第二句"
        assert result["original_output"] == result["replay_output"]

        stored = isolated_db.get_bug_report(report_id)
        assert stored["replayed"] == 1
        assert stored["reproduced"] == 1

    def test_replay_detects_when_the_engine_no_longer_reproduces_it(self, isolated_db):
        drama_id = _make_drama(isolated_db)
        lines = [Line(idx=0, start=0, end=1, zh="第一句", en="translated first"),
                 Line(idx=1, start=1, end=2, zh="第二句", en="[calm|ctx=1] 第二句")]
        isolated_db.save_lines(drama_id, lines)

        report_id = debug_view.save_bug_bundle(
            drama_id, lines[1], lines, "context_aware_fake", None,
            glossary_terms=[], locale="en-US", context_window=6)

        # A different engine "version" (different mood) stands in for
        # the failure having since been fixed/changed.
        result = debug_view.replay_bug_bundle(report_id, _ContextAwareEngine(mood="different"))

        assert result["reproduced"] is False
        assert result["original_output"] == "[calm|ctx=1] 第二句"
        assert result["replay_output"] == "[different|ctx=1] 第二句"

    def test_save_bundle_freezes_the_exact_context_window_used(self, isolated_db):
        drama_id = _make_drama(isolated_db)
        lines = [Line(idx=i, start=float(i), end=float(i + 1), zh=f"句{i}",
                      en=f"en{i}" if i < 3 else "") for i in range(5)]
        isolated_db.save_lines(drama_id, lines)

        report_id = debug_view.save_bug_bundle(
            drama_id, lines[3], lines, "context_aware_fake", None,
            glossary_terms=[], locale="en-US", context_window=2, context_window_ahead=1)

        snapshot = json.loads(isolated_db.get_bug_report(report_id)["input_json"])
        assert snapshot["recent_context"] == [["句1", "en1"], ["句2", "en2"]]
        assert snapshot["upcoming_lines"] == ["句4"]
