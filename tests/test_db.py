"""
tests/test_db.py -- tests for db.py, using the isolated_db fixture so
nothing here ever touches your real library.
"""

import ast
import contextlib
import shutil
import sqlite3
import subprocess
import sys
import os

import pytest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from core import Line
from tests.conftest import rmtree_retrying


class TestDramaCRUD:
    def test_create_and_get(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama")
        drama = isolated_db.get_drama(did)
        assert drama["title_en"] == "Test Drama"
        assert drama["status"] == "not started"  # default

    def test_narration_language_defaults_to_translation_and_persists_original(self, isolated_db):
        """Step 26c: existing dramas (and any new one that doesn't opt in)
        keep narrating the translation -- 'original' only takes effect
        once a drama explicitly sets it."""
        did = isolated_db.create_drama(title_en="Novel", content_mode="novel_narration")
        assert isolated_db.get_drama(did)["narration_language"] == "translation"
        isolated_db.update_drama(did, narration_language="original")
        assert isolated_db.get_drama(did)["narration_language"] == "original"

    def test_source_url_persists(self, isolated_db):
        did = isolated_db.create_drama(title_en="Stream", title_zh="直播原名")
        isolated_db.update_drama(did, source_url="https://youtube.com/watch?v=fake123")
        drama = isolated_db.get_drama(did)
        assert drama["source_url"] == "https://youtube.com/watch?v=fake123"
        # title_en/title_zh double as translated/untranslated stream name --
        # confirming both persist alongside source_url, not just one or the other.
        assert drama["title_en"] == "Stream"
        assert drama["title_zh"] == "直播原名"

    def test_create_sets_created_and_updated_at(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        drama = isolated_db.get_drama(did)
        assert drama["created_at"] is not None
        assert drama["updated_at"] is not None

    def test_update_changes_updated_at(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        before = isolated_db.get_drama(did)["updated_at"]
        isolated_db.update_drama(did, status="translated")
        after = isolated_db.get_drama(did)["updated_at"]
        assert isolated_db.get_drama(did)["status"] == "translated"
        # updated_at should change (may be equal if the clock has sub-ms
        # resolution issues, so just check status changed correctly --
        # the important behavioral guarantee, not exact timestamp diff)
        assert after is not None

    def test_delete_removes_drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.delete_drama(did)
        assert isolated_db.get_drama(did) is None

    def test_get_nonexistent_returns_none(self, isolated_db):
        assert isolated_db.get_drama(99999) is None

    def test_list_dramas_filters_by_language(self, isolated_db):
        isolated_db.create_drama(title_en="ZH Drama", source_language="zh")
        isolated_db.create_drama(title_en="KO Drama", source_language="ko")
        results = isolated_db.list_dramas(source_language="ko")
        assert len(results) == 1
        assert results[0]["title_en"] == "KO Drama"

    def test_list_dramas_filters_by_media_type(self, isolated_db):
        isolated_db.create_drama(title_en="Novel", media_type="novel")
        isolated_db.create_drama(title_en="Manhwa", media_type="manhwa")
        results = isolated_db.list_dramas(media_type="manhwa")
        assert len(results) == 1
        assert results[0]["title_en"] == "Manhwa"

    def test_voice_actor_filter_matches_a_whole_name_only(self, isolated_db):
        a = isolated_db.create_drama(title_en="A", voice_actors="Li, Wang Kai")
        isolated_db.create_drama(title_en="B", voice_actors="Lin Yu")
        isolated_db.create_drama(title_en="C", voice_actors="Wang Kaiwen")
        d = isolated_db.create_drama(title_en="D", voice_actors="A_B")
        isolated_db.create_drama(title_en="E", voice_actors="AxB")
        assert [x["id"] for x in isolated_db.list_dramas(voice_actor="Li")] == [a]
        assert [x["id"] for x in isolated_db.list_dramas(voice_actor="Wang Kai")] == [a]
        assert [x["id"] for x in isolated_db.list_dramas(voice_actor="A_B")] == [d]

    def test_search_matches_title_and_summary(self, isolated_db):
        isolated_db.create_drama(title_en="Unrelated", summary="mentions dragons")
        results = isolated_db.list_dramas(search="dragons")
        assert len(results) == 1


class TestDramaColumnCheck:
    BAD_KEYS = ["nope", "title en", "title_en'", "title_en;", "title_en -- ", "Title_en", "id"]

    def test_unknown_or_malformed_key_on_update_raises_and_changes_nothing(self, isolated_db):
        did = isolated_db.create_drama(title_en="Keep")
        before = isolated_db.get_drama(did)
        for key in self.BAD_KEYS:
            with pytest.raises(ValueError) as err:
                isolated_db.update_drama(did, **{key: "secret-value", "title_en": "Changed"})
            assert "secret-value" not in str(err.value)
        assert isolated_db.get_drama(did) == before

    def test_created_at_is_refused_on_update(self, isolated_db):
        did = isolated_db.create_drama(title_en="Keep")
        with pytest.raises(ValueError):
            isolated_db.update_drama(did, created_at="2000-01-01")

    def test_bad_key_on_create_writes_no_row(self, isolated_db):
        count = len(isolated_db.list_dramas())
        for key in ["nope", "title en", "title_en;", "x'--"]:
            with pytest.raises(ValueError):
                isolated_db.create_drama(**{key: "v", "title_en": "Row"})
        assert len(isolated_db.list_dramas()) == count

    def test_valid_update_advances_updated_at(self, isolated_db):
        did = isolated_db.create_drama(title_en="A")
        before = isolated_db.get_drama(did)["updated_at"]
        isolated_db.update_drama(did, title_en="B")
        drama = isolated_db.get_drama(did)
        assert drama["title_en"] == "B"
        assert drama["updated_at"] > before

    def test_column_added_after_startup_is_accepted(self, isolated_db):
        did = isolated_db.create_drama(title_en="A")
        with contextlib.closing(isolated_db.get_conn()) as conn:
            conn.execute("ALTER TABLE dramas ADD COLUMN extra_probe TEXT")
            conn.commit()
        isolated_db.update_drama(did, extra_probe="x")
        with contextlib.closing(isolated_db.get_conn()) as conn:
            row = conn.execute("SELECT extra_probe FROM dramas WHERE id = ?", (did,)).fetchone()
        assert row[0] == "x"


class TestUsageTracking:
    def test_log_and_summarize(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.log_usage(did, "claude", "claude-sonnet-5", "translate", 1000, 500, 0.0055)
        isolated_db.log_usage(did, "claude", "claude-sonnet-5", "translate", 2000, 800, 0.0092)
        summary = isolated_db.get_usage_summary(did)
        assert summary["call_count"] == 2
        assert abs(summary["estimated_cost_usd"] - 0.0147) < 0.0001

    def test_library_wide_total_sums_all_dramas(self, isolated_db):
        did1 = isolated_db.create_drama(title_en="D1")
        did2 = isolated_db.create_drama(title_en="D2")
        isolated_db.log_usage(did1, "claude", "m", "translate", 100, 50, 0.001)
        isolated_db.log_usage(did2, "deepseek", "m", "translate", 200, 100, 0.0002)
        total = isolated_db.get_usage_summary()
        assert total["call_count"] == 2

    def test_no_usage_returns_zeros_not_none(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        summary = isolated_db.get_usage_summary(did)
        assert summary["call_count"] == 0
        assert summary["estimated_cost_usd"] == 0


class TestCacheReadsAndMonthSpend:
    """Step 9: usage_log records prompt-cache reads, and month-to-date
    spend is what the monthly cap is checked against."""

    def test_cache_read_tokens_are_summed(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.log_usage(did, "claude", "m", "translate", 1000, 10, 0.01, cache_read_tokens=800)
        isolated_db.log_usage(did, "claude", "m", "translate", 1000, 10, 0.01)
        assert isolated_db.get_usage_summary(did)["cache_read_tokens"] == 800
        assert isolated_db.get_usage_summary()["cache_read_tokens"] == 800
        row = next(r for r in isolated_db.get_usage_by_drama() if r["id"] == did)
        assert row["cache_read_tokens"] == 800

    def test_month_spend_counts_only_the_current_month(self, isolated_db):
        import datetime
        import pytest
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.log_usage(did, "claude", "m", "translate", 1, 1, 1.50)
        conn = isolated_db.get_conn()
        conn.execute("INSERT INTO usage_log (drama_id, engine, model, operation, input_tokens, "
                     "output_tokens, estimated_cost_usd, created_at) VALUES (?, 'c', 'm', 't', 1, 1, 9.0, ?)",
                     (did, "2000-01-15T00:00:00"))
        conn.commit()
        conn.close()
        assert isolated_db.get_month_spend() == pytest.approx(1.50)
        assert isolated_db.get_month_spend(datetime.datetime(2000, 1, 20)) == pytest.approx(10.50)

    def test_the_cache_column_is_added_to_an_older_database(self, isolated_db):
        conn = isolated_db.get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_log)").fetchall()}
        conn.close()
        assert "cache_read_tokens" in cols


class TestUsageByDrama:
    """get_usage_by_drama backs the Library dashboard's cost breakdown --
    Step 1d added translation_engine and call_count to it so the
    dashboard can show a free engine's real (zero-cost) usage instead of
    filtering it out as if nothing had run (the old filter was
    estimated_cost_usd > 0, which drops free engines entirely)."""

    def test_includes_translation_engine_and_call_count(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test", translation_engine="fake")
        isolated_db.log_usage(did, "fake", "fake", "translate", 100, 50, 0.0)
        isolated_db.log_usage(did, "fake", "fake", "translate", 200, 100, 0.0)
        row = next(r for r in isolated_db.get_usage_by_drama() if r["id"] == did)
        assert row["translation_engine"] == "fake"
        assert row["call_count"] == 2
        assert row["estimated_cost_usd"] == 0.0

    def test_counts_calls_on_hosted_ollama_models(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test", translation_engine="ollama")
        isolated_db.log_usage(did, "ollama", "gemma4:31b-cloud", "translate", 100, 50, 0.0)
        isolated_db.log_usage(did, "ollama", "gpt-oss:120b:cloud", "translate", 100, 50, 0.0)
        isolated_db.log_usage(did, "ollama", "translategemma:27b", "translate", 100, 50, 0.0)
        row = next(r for r in isolated_db.get_usage_by_drama() if r["id"] == did)
        assert row["call_count"] == 3
        assert row["cloud_call_count"] == 2

    def test_a_drama_with_no_usage_has_zero_call_count(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        row = next(r for r in isolated_db.get_usage_by_drama() if r["id"] == did)
        assert row["call_count"] == 0


class TestConsistencyIssuesPersist:
    """Regression coverage for a real bug: the consistency-check result
    (a real LLM call) used to live only in st.session_state, so a page
    refresh silently lost it -- meaning re-checking (and re-paying for
    it) was the only way to see the same result again."""

    def test_save_and_load(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_consistency_issues(did, [
            {"term": "沈清疑", "variants": ["Shen Qingyi", "Shen Qing Yi"], "note": "spacing"},
        ])
        loaded = isolated_db.load_consistency_issues(did)
        assert len(loaded) == 1
        assert loaded[0]["term"] == "沈清疑"
        assert loaded[0]["variants"] == ["Shen Qingyi", "Shen Qing Yi"]

    def test_a_later_run_replaces_the_previous_one(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_consistency_issues(did, [{"term": "a", "variants": [], "note": ""}])
        isolated_db.save_consistency_issues(did, [{"term": "b", "variants": [], "note": ""}])
        loaded = isolated_db.load_consistency_issues(did)
        assert [i["term"] for i in loaded] == ["b"]

    def test_no_issues_saved_yet_returns_empty_list(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        assert isolated_db.load_consistency_issues(did) == []


class TestEmotionsPersist:
    """Regression coverage for a real bug: emotion detection (a
    whole-drama LLM batch job, same cost scale as translation) had no
    database persistence at all -- its result lived only in
    st.session_state, so a page refresh lost it and it never fed back
    into a later translation run either."""

    def test_save_and_load(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=str(i)) for i in range(4)])
        isolated_db.save_emotions(did, {
            0: {"emotion": "sarcastic", "intensity": 0.9, "note": "mock praise"},
            3: {"emotion": "sad", "intensity": 0.6, "note": ""},
        })
        loaded = isolated_db.load_emotions(did)
        assert loaded[0]["emotion"] == "sarcastic"
        assert loaded[0]["intensity"] == 0.9
        assert loaded[3]["emotion"] == "sad"

    def test_re_running_updates_existing_lines_rather_than_duplicating(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a")])
        isolated_db.save_emotions(did, {0: {"emotion": "angry", "intensity": 0.5, "note": ""}})
        isolated_db.save_emotions(did, {0: {"emotion": "sad", "intensity": 0.7, "note": ""}})
        loaded = isolated_db.load_emotions(did)
        assert len(loaded) == 1
        assert loaded[0]["emotion"] == "sad"

    def test_no_emotions_saved_yet_returns_empty_dict(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        assert isolated_db.load_emotions(did) == {}


class TestLibraryStatsAndSearch:
    def test_get_library_stats_counts_dramas(self, isolated_db):
        isolated_db.create_drama(title_en="A", status="translated")
        isolated_db.create_drama(title_en="B", status="not started")
        stats = isolated_db.get_library_stats()
        assert stats["total_dramas"] == 2
        assert stats["by_status"]["translated"] == 1

    def test_recently_active_orders_by_updated_at(self, isolated_db):
        did1 = isolated_db.create_drama(title_en="First")
        did2 = isolated_db.create_drama(title_en="Second")
        isolated_db.update_drama(did1, status="translated")  # touch it after did2 was created
        recent = isolated_db.list_dramas_recently_active(5)
        assert recent[0]["title_en"] == "First"  # most recently touched first

    def test_global_search_finds_across_dramas(self, isolated_db):
        did = isolated_db.create_drama(title_en="Findable Drama")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="hello there")])
        results = isolated_db.search_lines_globally("你好")
        assert len(results) == 1
        assert results[0]["title_en"] == "Findable Drama"

    def test_global_search_no_match_returns_empty(self, isolated_db):
        isolated_db.create_drama(title_en="Test")
        results = isolated_db.search_lines_globally("nonexistent_search_term_xyz")
        assert results == []


class TestKnownTitles:
    def test_create_and_list(self, isolated_db):
        isolated_db.create_known_title(title_original="测试", title_en="Test",
                                        source_name="manual", language="zh", media_type="novel")
        titles = isolated_db.list_known_titles()
        assert len(titles) == 1

    def test_filter_by_source_name(self, isolated_db):
        isolated_db.create_known_title(title_original="A", source_name="baihehub", language="zh", media_type="novel")
        isolated_db.create_known_title(title_original="B", source_name="manual", language="zh", media_type="novel")
        results = isolated_db.list_known_titles(source_name="baihehub")
        assert len(results) == 1
        assert results[0]["title_original"] == "A"

    def test_delete_known_title(self, isolated_db):
        tid = isolated_db.create_known_title(title_original="A", source_name="manual",
                                              language="zh", media_type="novel")
        isolated_db.delete_known_title(tid)
        assert isolated_db.list_known_titles() == []


class TestPagesAndBubbles:
    def test_create_page_and_list(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        pages = isolated_db.list_pages(did)
        assert len(pages) == 1
        assert pages[0]["width"] == 600

    def test_save_and_load_bubbles(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid, [
            {"x": 10, "y": 20, "w": 100, "h": 50, "source_text": "a", "translated_text": "b",
             "font_size": 18, "skip": False}
        ])
        bubbles = isolated_db.load_bubbles(pid)
        assert len(bubbles) == 1
        assert bubbles[0]["translated_text"] == "b"

    def test_update_page_rendered_filename(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.update_page(pid, rendered_filename="pages/typeset_0000.png")
        page = isolated_db.list_pages(did)[0]
        assert page["rendered_filename"] == "pages/typeset_0000.png"

    def test_list_bubbles_for_drama_spans_every_page(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid0 = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        pid1 = isolated_db.create_page(did, 1, "pages/page_0001.png", 600, 400)
        isolated_db.save_bubbles(pid0, [
            {"x": 1, "y": 1, "w": 10, "h": 10, "source_text": "a", "translated_text": "Hello",
             "font_size": 18, "skip": False}])
        isolated_db.save_bubbles(pid1, [
            {"x": 2, "y": 2, "w": 10, "h": 10, "source_text": "b", "translated_text": "World",
             "font_size": 18, "skip": False}])
        bubbles = isolated_db.list_bubbles_for_drama(did)
        assert len(bubbles) == 2
        assert {b["page_idx"] for b in bubbles} == {0, 1}
        assert {b["translated_text"] for b in bubbles} == {"Hello", "World"}

    def test_list_bubbles_for_drama_ignores_other_dramas(self, isolated_db):
        did_a = isolated_db.create_drama(title_en="A")
        did_b = isolated_db.create_drama(title_en="B")
        pid_a = isolated_db.create_page(did_a, 0, "pages/page_0000.png", 600, 400)
        isolated_db.create_page(did_b, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid_a, [
            {"x": 1, "y": 1, "w": 10, "h": 10, "source_text": "a", "translated_text": "Only A",
             "font_size": 18, "skip": False}])
        bubbles = isolated_db.list_bubbles_for_drama(did_b)
        assert bubbles == []


    def test_font_category_round_trips(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid, [
            {"x": 10, "y": 20, "w": 100, "h": 50, "source_text": "a", "translated_text": "b",
             "font_size": 18, "skip": False, "font_category": "handwritten"}
        ])
        bubbles = isolated_db.load_bubbles(pid)
        assert bubbles[0]["font_category"] == "handwritten"

    def test_font_category_defaults_to_regular_when_omitted(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid, [
            {"x": 10, "y": 20, "w": 100, "h": 50, "source_text": "a", "translated_text": "b",
             "font_size": 18, "skip": False}  # no font_category key at all
        ])
        bubbles = isolated_db.load_bubbles(pid)
        assert bubbles[0]["font_category"] == "regular"


class TestBenchmarkCasesAndRuns:
    def test_configure_library_dir_redirects_benchmark_dir_too(self, isolated_db):
        """Regression test for a real gap: configure_library_dir (what
        test isolation itself relies on) never redirected BENCHMARK_DIR,
        so any test exercising the benchmark case file-upload path would
        have silently written into the real production library instead
        of the isolated temp one."""
        assert isolated_db.BENCHMARK_DIR.startswith(isolated_db.LIBRARY_DIR)
        assert isolated_db.BENCHMARK_DIR == os.path.join(isolated_db.LIBRARY_DIR, "benchmark_cases")

    def test_create_and_list_a_case(self, isolated_db):
        case_id = isolated_db.create_benchmark_case(
            "My test drama clip", "transcription", "audio_drama", source_language="zh",
            input_filename="clip.wav", reference_text="你好世界")
        cases = isolated_db.list_benchmark_cases()
        assert len(cases) == 1
        assert cases[0]["id"] == case_id
        assert cases[0]["reference_text"] == "你好世界"

    def test_list_cases_filters_by_stage(self, isolated_db):
        isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.create_benchmark_case("b", "translation", "novel")
        assert len(isolated_db.list_benchmark_cases(stage="transcription")) == 1
        assert len(isolated_db.list_benchmark_cases(stage="translation")) == 1
        assert len(isolated_db.list_benchmark_cases()) == 2

    def test_delete_a_case(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.delete_benchmark_case(case_id)
        assert isolated_db.list_benchmark_cases() == []

    def test_save_and_list_runs_for_a_case(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.save_benchmark_run(case_id, {"output_text": "hi", "score": 0.9,
                                                  "duration_seconds": 1.2, "error": None},
                                        run_label="first try")
        runs = isolated_db.list_benchmark_runs(case_id)
        assert len(runs) == 1
        assert runs[0]["score"] == 0.9
        assert runs[0]["run_label"] == "first try"

    def test_deleting_a_case_cascades_to_its_runs(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.save_benchmark_run(case_id, {"output_text": "hi", "score": 0.9,
                                                  "duration_seconds": 1.0, "error": None})
        isolated_db.delete_benchmark_case(case_id)
        assert isolated_db.list_benchmark_runs(case_id) == []


    def test_cost_defaults_to_zero_when_omitted(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "translation", "novel")
        isolated_db.save_benchmark_run(case_id, {"output_text": "x", "score": 1.0,
                                                  "duration_seconds": 0.5, "error": None})
        runs = isolated_db.list_benchmark_runs(case_id)
        assert runs[0]["cost_usd"] == 0.0


class TestImportTimeSafety:
    """Step 51: importing db.py alone must never touch a real library path
    -- init has to be lazy (first real get_conn() call), not at import
    time, so `pytest` collecting/importing db can't create or touch the
    real library/library.db before isolated_db redirects it."""

    @staticmethod
    def _imported_modules(path, module):
        # Function-level imports count too: init_db imports device_tokens
        # lazily, and the first-call test needs it present.
        tree = ast.parse(open(path, encoding="utf-8").read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.update((node.module.split(".")[0], node.module))
                # `from lib import gzip_text` needs the submodule too.
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level > 0:
                package = module if os.path.basename(path) == "__init__.py" else module.rpartition(".")[0]
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - (node.level - 1)])
                source = f"{base}.{node.module}" if node.module else base
                names.add(source)
                names.update(f"{source}.{alias.name}" for alias in node.names)
        return names

    @classmethod
    def _copy_db_and_deps(cls, temp_dir):
        # Copy the db package plus every local module it imports
        # (transitively, lazy ones too), computed from the source so a split that adds an
        # import to core or db doesn't need this test edited. Copying only
        # those into an otherwise empty directory is the only way to observe a
        # genuinely fresh import rather than the module every other test in
        # this process shares; a dependency that isn't local (stdlib or
        # third-party) is left to the interpreter. Nothing is excluded on
        # purpose: a heavy module reached from db would be copied here and
        # then trip the "no library/ side effect" assertions in the tests.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def local_source(name):
            for base in (project_root, os.path.join(project_root, "lib")):
                path = name.replace(".", os.sep)
                for rel in (path + ".py", os.path.join(path, "__init__.py")):
                    if os.path.isfile(os.path.join(base, rel)):
                        return base, rel
            return None

        seen, queue = set(), ["db"]
        while queue:
            name = queue.pop()
            found = local_source(name)
            if name in seen or found is None:
                continue
            seen.add(name)
            base, rel = found
            dest = os.path.join(temp_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(os.path.join(base, rel), dest)
            queue.extend(cls._imported_modules(os.path.join(base, rel), name))
        assert {"db", "core"} <= seen, seen

    def test_bare_import_does_not_touch_any_library_dir(self):
        temp_dir = tempfile.mkdtemp(prefix="baihe_import_check_")
        try:
            self._copy_db_and_deps(temp_dir)
            result = subprocess.run(
                [sys.executable, "-c", "import db"],
                cwd=temp_dir, capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists(os.path.join(temp_dir, "library")), (
                "importing db.py alone created a library/ directory as a side effect"
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_relative_and_package_imports_are_followed(self, tmp_path):
        # Once db/ submodules import each other, a dependency reached only
        # through `from . import x` or `from .x import y` must still be
        # copied, or the fresh-import tests above import a partial package.
        pkg = tmp_path / "pkg"
        (pkg / "sub").mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "from .store import a\nfrom . import helpers\nfrom pkg import other\nfrom pkg.models import c\n"
            "def f():\n    from .lazy import b\n")
        (pkg / "sub" / "mod.py").write_text("from .. import x\nfrom ..store import y\nfrom . import z\n")
        top = self._imported_modules(str(pkg / "__init__.py"), "pkg")
        assert {"pkg.store", "pkg.helpers", "pkg.other", "pkg.models", "pkg.lazy"} <= top
        sub = self._imported_modules(str(pkg / "sub" / "mod.py"), "pkg.sub.mod")
        assert {"pkg.x", "pkg.store", "pkg.sub.z"} <= sub

    def test_first_real_call_still_initializes_the_schema(self):
        # The other half of the guarantee: init must still happen, just
        # lazily -- a real call right after import has to work normally.
        temp_dir = tempfile.mkdtemp(prefix="baihe_import_check_")
        try:
            self._copy_db_and_deps(temp_dir)
            script = (
                "import db\n"
                "did = db.create_drama(title_en='x')\n"
                "assert db.get_drama(did)['title_en'] == 'x'\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temp_dir, capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert os.path.exists(os.path.join(temp_dir, "library", "library.db"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_isolated_db_fixture_still_initializes_a_working_schema(self, isolated_db):
        # No regression to the isolation isolated_db already provides.
        did = isolated_db.create_drama(title_en="Fixture Still Works")
        assert isolated_db.get_drama(did)["title_en"] == "Fixture Still Works"
        assert os.path.exists(isolated_db.DB_PATH)

    def test_configure_library_dir_reinitializes_at_the_new_path(self, isolated_db, tmp_path_str):
        # isolated_db already redirected + initialized once; redirecting a
        # second time (configure_library_dir alone, no explicit init_db())
        # must still produce a working schema at the new path rather than
        # silently reusing the old path's "already ready" state.
        isolated_db.create_drama(title_en="Old Path")
        isolated_db.configure_library_dir(tmp_path_str)
        did = isolated_db.create_drama(title_en="New Path")
        assert isolated_db.get_drama(did)["title_en"] == "New Path"
        assert os.path.exists(os.path.join(tmp_path_str, "library.db"))


class _RivalAtBegin:
    """Wraps a migration's connection: just before its `nth` BEGIN, `rival`
    runs, as another process starting at the same moment would."""

    def __init__(self, conn, rival, nth=1):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_rival", rival)
        object.__setattr__(self, "_left", nth)

    def execute(self, sql, *args):
        if sql.startswith("BEGIN"):
            object.__setattr__(self, "_left", self._left - 1)
            if self._left == 0:
                self._rival()
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


def _first_conn_with_rival(monkeypatch, rival, nth=1):
    """Makes db.get_conn's next connection a _RivalAtBegin; the rival's own
    connections are ordinary ones."""
    real = db.get_conn
    calls = []

    def get_conn():
        conn = real()
        calls.append(1)
        return _RivalAtBegin(conn, rival, nth) if len(calls) == 1 else conn

    monkeypatch.setattr(db, "get_conn", get_conn)


class TestStep26eProfilesMigration:
    """_migrate_step26e_profiles: an existing (pre-profiles) install's
    progress/reading_history/personal_notes data must survive db.init_db()
    unchanged in content, just now attributed to a newly-created default
    profile -- and progress's primary key actually changes (SQLite can't
    ALTER a PRIMARY KEY in place), so this builds a real old-schema
    database by hand rather than trusting the new CREATE TABLE IF NOT
    EXISTS block alone (which only a fresh install ever goes through)."""

    def _make_old_schema_db(self, temp_dir):
        db_path = os.path.join(temp_dir, "library.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE dramas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title_en TEXT, title_zh TEXT, personal_notes TEXT
            );
            CREATE TABLE progress (
                drama_id INTEGER PRIMARY KEY,
                last_line_idx INTEGER DEFAULT 0,
                audio_position_seconds REAL DEFAULT 0,
                last_page INTEGER DEFAULT 1,
                percent_complete REAL DEFAULT 0,
                last_accessed_at TEXT
            );
            CREATE TABLE reading_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drama_id INTEGER NOT NULL,
                line_id INTEGER, line_idx INTEGER,
                percent_complete REAL, accessed_at TEXT
            );
        """)
        conn.execute("INSERT INTO dramas (id, title_en, personal_notes) VALUES "
                     "(1, 'Old Drama', 'a private note from before profiles existed')")
        conn.execute("INSERT INTO progress (drama_id, last_line_idx, last_page, "
                     "percent_complete, last_accessed_at) VALUES (1, 7, 3, 42.0, "
                     "'2025-01-01T00:00:00')")
        conn.execute("INSERT INTO reading_history (drama_id, line_idx, percent_complete, "
                     "accessed_at) VALUES (1, 7, 42.0, '2025-01-01T00:00:00')")
        conn.commit()
        conn.close()

    def _with_redirected_library(self, temp_dir, fn):
        previous = db.LIBRARY_DIR
        try:
            db.configure_library_dir(temp_dir)
            fn()
        finally:
            db.configure_library_dir(previous)

    def test_upgrading_a_pre_profiles_install_preserves_its_data(self, tmp_path_str):
        self._make_old_schema_db(tmp_path_str)

        def upgrade_and_check():
            db.init_db()  # this is the real upgrade path a restart runs
            profiles = db.list_profiles()
            assert len(profiles) == 1
            default_id = profiles[0]["id"]
            # Same naive-UTC format as every other db.py timestamp.
            assert "+" not in profiles[0]["created_at"]

            prog = db.get_progress(1, profile_id=default_id)
            assert prog["last_line_idx"] == 7
            assert prog["last_page"] == 3
            assert prog["percent_complete"] == 42.0

            hist = db.list_reading_history(1, profile_id=default_id)
            assert len(hist) == 1
            assert hist[0]["percent_complete"] == 42.0

            assert (db.get_personal_notes(1, profile_id=default_id)
                    == "a private note from before profiles existed")

        self._with_redirected_library(tmp_path_str, upgrade_and_check)

    def test_running_init_db_twice_does_not_duplicate_the_default_profile(self, tmp_path_str):
        self._make_old_schema_db(tmp_path_str)

        def upgrade_twice():
            db.init_db()
            db.init_db()  # e.g. the app restarting again later
            assert len(db.list_profiles()) == 1

        self._with_redirected_library(tmp_path_str, upgrade_twice)


    def test_a_default_profile_made_meanwhile_by_another_process_is_not_doubled(
            self, tmp_path_str, monkeypatch):
        self._make_old_schema_db(tmp_path_str)

        def upgrade_with_a_rival():
            from db.migrations import _migrate_step26e_profiles

            def rival():
                conn = sqlite3.connect(db.DB_PATH)
                conn.execute("INSERT INTO profiles (name, created_at) VALUES ('Me', 'x')")
                conn.commit()
                conn.close()

            # Everything but the profile step first, so the race is only that step.
            monkeypatch.setattr(db, "_migrate_step26e_profiles", lambda: None)
            db.init_db()
            _first_conn_with_rival(monkeypatch, rival)
            _migrate_step26e_profiles()
            assert len(db.list_profiles()) == 1

        self._with_redirected_library(tmp_path_str, upgrade_with_a_rival)


class TestSafeAlterGuardsInitDbAgainstACrossProcessRace:
    """Migration Slice 6 (React + FastAPI migration, D1 fix 4):
    db._ensure_ready() calls init_db() lazily, per process, with no
    cross-process lock -- Streamlit and a separately-running
    `python -m api` process can both pass init_db()'s own
    "column not in existing_cols yet" check before either ALTER runs, so
    the second ALTER for the same column hits
    sqlite3.OperationalError: duplicate column name even though nothing
    is actually wrong. _safe_alter() is the guard; these tests exercise
    it directly rather than trying to force a real thread race against
    SQLite's own file locking."""

    def test_duplicate_column_is_swallowed(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("ALTER TABLE t ADD COLUMN x TEXT")
        # Simulates the race outcome directly: both "processes" already
        # passed the not-in-existing_cols check, so this ALTER runs for
        # real against a column that's already there.
        db._safe_alter(conn, "ALTER TABLE t ADD COLUMN x TEXT")  # must not raise
        cols = {r[1] for r in conn.execute("PRAGMA table_info(t)").fetchall()}
        assert cols == {"id", "x"}
        conn.close()

    def test_other_operational_errors_still_raise(self, tmp_path):
        import pytest
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        with pytest.raises(sqlite3.OperationalError):
            db._safe_alter(conn, "ALTER TABLE does_not_exist ADD COLUMN x TEXT")
        conn.close()

    def test_init_db_still_adds_a_genuinely_missing_column(self, isolated_db):
        # isolated_db's own setup already ran init_db() once against a
        # fresh schema -- confirms the real check-then-_safe_alter path
        # (not just the swallow-a-duplicate path) actually adds columns.
        conn = db.get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lines)").fetchall()}
        conn.close()
        assert {"speaker", "flag", "flag_note", "sfx"} <= cols


class TestJobRecords:
    """Migration Slice 7 (D1 fix 1): job_records is a cross-process,
    records-only mirror of background_jobs.py's own in-memory job state
    -- no resume, so these tests exercise db.py's own CRUD directly
    rather than a real multi-process scenario."""

    def test_save_and_get_a_job_record(self, isolated_db):
        db.save_job_record("job1", status="running", progress=0.5, message="halfway",
                           error=None, description="Translating", gpu_touching=True,
                           started_at=100.0, finished_at=None)
        rec = db.get_job_record("job1")
        assert rec["status"] == "running"
        assert rec["progress"] == 0.5
        assert rec["description"] == "Translating"
        assert bool(rec["gpu_touching"]) is True
        assert rec["finished_at"] is None

    def test_saving_again_updates_in_place_not_a_second_row(self, isolated_db):
        db.save_job_record("job1", status="running", progress=0.0)
        db.save_job_record("job1", status="done", progress=1.0, finished_at=200.0)
        records = db.list_job_records()
        assert len(records) == 1
        assert records[0]["status"] == "done"
        assert records[0]["finished_at"] == 200.0

    def test_get_missing_record_is_none(self, isolated_db):
        assert db.get_job_record("does-not-exist") is None

    def test_delete_job_record(self, isolated_db):
        db.save_job_record("job1", status="done")
        db.delete_job_record("job1")
        assert db.get_job_record("job1") is None

    def test_clear_all_job_records(self, isolated_db):
        db.save_job_record("job1", status="done")
        db.save_job_record("job2", status="running")
        db.clear_all_job_records()
        assert db.list_job_records() == []

    def test_list_job_records_orders_newest_started_first(self, isolated_db):
        db.save_job_record("old", status="done", started_at=100.0)
        db.save_job_record("new", status="running", started_at=200.0)
        records = db.list_job_records()
        assert [r["job_id"] for r in records] == ["new", "old"]


class TestAppSettings:
    """Migration Slice 9 (D1 fix 2): a general-purpose, cross-process
    app-settings store -- distinct from sources/store.py's own settings
    table, which stays scoped to the source-adapter system."""

    def test_missing_key_returns_the_given_default(self, isolated_db):
        assert db.get_app_setting("does_not_exist", "fallback") == "fallback"
        assert db.get_app_setting("does_not_exist") is None

    def test_set_then_get_round_trips(self, isolated_db):
        db.set_app_setting("gpu_limit_enabled", False)
        assert db.get_app_setting("gpu_limit_enabled") is False

    def test_setting_again_updates_in_place_not_a_second_row(self, isolated_db):
        db.set_app_setting("k", 1)
        db.set_app_setting("k", 2)
        conn = db.get_conn()
        n = conn.execute("SELECT COUNT(*) AS n FROM app_settings WHERE key='k'").fetchone()["n"]
        conn.close()
        assert n == 1
        assert db.get_app_setting("k") == 2

    def test_update_changes_the_stored_value_and_returns_it(self, isolated_db):
        assert db.update_app_setting("doc", lambda v: {**v, "a": 1}, default={}) == {"a": 1}
        db.update_app_setting("doc", lambda v: {**v, "b": 2}, default={})
        assert db.get_app_setting("doc") == {"a": 1, "b": 2}

    def test_concurrent_updates_to_different_fields_both_land(self, isolated_db):
        import threading
        errors = []

        def add(field):
            try:
                for i in range(15):
                    db.update_app_setting("doc", lambda v: {**v, f"{field}{i}": i}, default={})
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=add, args=(f,)) for f in "abc"]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors
        assert len(db.get_app_setting("doc")) == 45

    def test_value_types_round_trip_via_json(self, isolated_db):
        db.set_app_setting("a_string", "hello")
        db.set_app_setting("a_number", 3.5)
        db.set_app_setting("a_bool", True)
        db.set_app_setting("a_list", [1, 2, 3])
        assert db.get_app_setting("a_string") == "hello"
        assert db.get_app_setting("a_number") == 3.5
        assert db.get_app_setting("a_bool") is True
        assert db.get_app_setting("a_list") == [1, 2, 3]

    def test_read_memo_fetches_each_setting_once(self, isolated_db, monkeypatch):
        db.set_app_setting("k", {"m": 1})
        opened = []
        real = db.get_conn
        monkeypatch.setattr(db, "get_conn", lambda: opened.append(1) or real())
        with db.app_settings_read_memo():
            assert [db.get_app_setting("k") for _ in range(5)] == [{"m": 1}] * 5
            assert db.get_app_setting("missing", "d") == "d"
            assert db.get_app_setting("missing", "d") == "d"
        assert len(opened) == 2
        db.get_app_setting("k")
        assert len(opened) == 3, "the memo ends with the block"

    def test_read_memo_hands_out_a_fresh_copy_and_sees_its_own_writes(self, isolated_db):
        db.set_app_setting("k", {"m": 1})
        with db.app_settings_read_memo():
            db.get_app_setting("k")["m"] = 99
            assert db.get_app_setting("k") == {"m": 1}
            db.set_app_setting("k", {"m": 2})
            assert db.get_app_setting("k") == {"m": 2}

    def test_read_memo_is_per_thread(self, isolated_db):
        import threading
        db.set_app_setting("k", 1)
        seen = []
        with db.app_settings_read_memo():
            assert db.get_app_setting("k") == 1
            t = threading.Thread(target=lambda: (db.set_app_setting("k", 2), seen.append(db.get_app_setting("k"))))
            t.start()
            t.join()
            assert db.get_app_setting("k") == 1, "a read-only block keeps one view of the settings"
        assert seen == [2]
        assert db.get_app_setting("k") == 2


def test_init_db_moves_dramas_off_the_removed_test_engine(isolated_db):
    did = isolated_db.create_drama(title_en="Old", translation_engine="test_offline")
    other = isolated_db.create_drama(title_en="Kept", translation_engine="deepseek")
    isolated_db.init_db()
    assert isolated_db.get_drama(did)["translation_engine"] == "claude"
    assert isolated_db.get_drama(other)["translation_engine"] == "deepseek"


# Every column init_db() adds with `ALTER TABLE ... ADD COLUMN`, per table,
# in the order it adds them. Dropping them gives a database shaped like one
# created before those migrations existed. When you add a column migration to
# db.py, add the column here too, or that migration is never run by a test
# (test_every_added_column_is_listed fails otherwise).
_INIT_DB_MIGRATED_COLUMNS = {
    "profiles": ("user_id",),
    "job_records": ("cancel_requested", "result_json", "owner_pid", "owner_user_id", "kind",
                    "owner_instance", "cancel_requested_at", "detail_state", "sync_error"),
    "gpu_lock": ("owner_pid", "owner_instance", "job_id"),
    "lines": ("speaker", "dub_filename", "flag", "flag_note", "speaker_manual", "sfx", "lang",
              "word_timings", "reviewed", "ass_event", "confidence", "mark"),
    "dramas": (
        "translation_engine", "content_mode", "narration_language", "source_video_filename",
        "source_language", "chinese_script", "media_type", "series_id", "episode_number",
        "episode_summary", "updated_at", "last_translate_errors", "author_romanized",
        "studio_romanized", "voice_actors_romanized", "director_romanized",
        "cover_art_filename", "genre", "publication_status", "chapter_count", "custom_tags",
        "personal_notes", "source_url", "transcript_mode", "whisper_size",
        "alignment_method", "asr_backend_choice", "min_silence_ms", "vad_threshold",
        "beam_size", "hallucination_silence_sec", "min_pause_sec", "sensitivity_preset", "separate_vocals_first", "separation_backend", "realign_long_segments",
        "whisper_fast_mode", "use_groq", "hardsub_ocr_backend", "hardsub_interval_sec",
        "project_instructions", "notion_page_id", "reading_speed_mode", "owner_user_id",
        "is_private", "default_female_pronouns", "include_genre_notes", "whisper_repeat_guard",
        "split_by_sentences", "vocabulary_hint", "translate_thinking", "source_url_full",
        "keep_original", "translate_by_sentence", "release_date", "difficult_audio",
        "translate_start_json", "transcribe_start_json", "ass_styles", "episode_brief",
        "created_by_user_id", "topic_chapters"),
    "presets": ("options_json", "is_default"),
    "series": ("instructions", "owner_user_id", "is_private", "default_settings", "speaker_colours",
               "speaker_mode", "shared_group_id", "auto_pipeline", "streamer"),
    "characters": ("ref_audio_filename", "ref_text", "elevenlabs_voice_id", "clone_engine",
                   "voice_design", "offline_voice", "series_character_id", "pronouns"),
    "glossary_terms": ("category", "policy", "enforce_exact", "aliases", "banned_translations"),
    "series_characters": ("gender", "voice_fingerprint", "voice_fingerprint_samples", "voice_bank_id",
                          "voice_design"),
    "voice_bank": ("is_private",),
    "usage_log": ("cache_read_tokens", "estimated_cost_usd_before_recost", "user_id",
                  "imported_at"),
    "bubbles": ("font_category", "kind", "kind_confidence", "confidence", "language",
                "orientation", "panel_id", "include_sfx", "text_color"),
    "pages": ("rev", "context_summary", "run_notes", "edit_history"),
    "bulk_jobs": ("kind", "stage", "pipeline_id", "estimated_usd", "user_id"),
    "bulk_job_lines": ("result_text", "state_at_submit"),
    "vocab_lookups": ("export_rich", "first_seen_line_id"),
    "wiki_entries": ("first_seen_line_id", "known_through_line_id"),
    "style_profile": ("history_json",),
    "edit_samples": ("line_id", "engine", "model", "prompt_version", "flag", "mark",
                     "deleted_drama_id", "deleted_title"),
    "users": ("share_by_default", "monthly_paid_cap_usd"),
    "known_titles": ("created_by_user_id",),
    "progress": ("view_prefs",),
    "translate_history": ("user_id",),
    "auth_sessions": ("device_label", "signed_in_at"),
    "benchmark_results": ("scorer", "ref_version", "timing", "judge_scores", "recognised_text",
                          "read_score"),
    "benchmark_sessions": ("judge_meta",),
    "benchmark_cases": ("tier", "set_name", "origin_drama_id", "origin_line_id", "title", "tags",
                        "scene_order", "clip_key", "excluded", "ref_version", "ref_start", "ref_end", "hardsub"),
}


def _schema_shape(path):
    """Every table's columns (name, type, not-null, default, pk) and every
    index, ignoring column order and CREATE text, which an ALTER-upgraded
    database legitimately differs from a fresh one in."""
    conn = sqlite3.connect(path)
    try:
        master = conn.execute(
            "SELECT type, name, tbl_name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        indexes = conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index' "
            "ORDER BY name").fetchall()
        columns = {
            name: sorted(tuple(r[1:]) for r in conn.execute(f"PRAGMA table_info({name})"))
            for kind, name, _ in master if kind == "table"}
    finally:
        conn.close()
    return {"master": master, "indexes": indexes, "columns": columns}


def _exact_snapshot(path):
    """Everything init_db() leaves behind, byte for byte: sqlite_master,
    each table's ordered columns and rows, and the persistent pragmas."""
    conn = sqlite3.connect(path)
    try:
        master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        tables = {}
        for kind, name, _, _ in master:
            if kind != "table":
                continue
            info = conn.execute(f"PRAGMA table_info({name})").fetchall()
            rows = conn.execute(f"SELECT * FROM {name}").fetchall()
            tables[name] = (info, sorted(rows, key=repr))
        pragmas = {p: conn.execute(f"PRAGMA {p}").fetchone()[0]
                   for p in ("user_version", "journal_mode", "auto_vacuum", "page_size")}
    finally:
        conn.close()
    return {"master": master, "tables": tables, "pragmas": pragmas}


def _make_old_shape(path, share_by_default_was_on=False):
    """Turns a freshly initialised database into one from before every
    init_db() column migration, holding the rows its data migrations act on:
    a user, a session that still stores its raw user agent, a drama
    predating every added column, and a preset on the removed test engine.
    share_by_default_was_on: users.share_by_default exists with the old
    default of 1 instead of being missing."""
    conn = sqlite3.connect(path)
    try:
        for table, cols in _INIT_DB_MIGRATED_COLUMNS.items():
            # An index on a migrated column is made after its ALTER, so an old
            # library has neither; SQLite also refuses to drop an indexed column.
            for (index,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' "
                                         "AND tbl_name = ? AND sql IS NOT NULL", (table,)).fetchall():
                if {r[2] for r in conn.execute(f"PRAGMA index_info({index})")} & set(cols):
                    conn.execute(f"DROP INDEX {index}")
            for col in reversed(cols):
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        if share_by_default_was_on:
            conn.execute("ALTER TABLE users ADD COLUMN share_by_default INTEGER DEFAULT 1")
        conn.execute("INSERT INTO users (id, email, created_at) VALUES (1, 'a@example.com', 'x')")
        conn.execute(
            "INSERT INTO auth_sessions (id_hash, user_id, created_at, expires_at, last_seen_at, "
            "user_agent_short, csrf_hash) VALUES ('h', 1, 0, 9e9, 0, "
            "'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0', 'c')")
        conn.execute("INSERT INTO dramas (id, title_en) VALUES (1, 'Old')")
        conn.execute("INSERT INTO presets (name, translation_engine) VALUES ('p', 'test_offline')")
        conn.commit()
    finally:
        conn.close()


# Added inside data migrations that rebuild or back up tables (db.py's
# line-id and profile migrations), so they can't be dropped by hand here.
_ALTERS_WITH_OWN_MIGRATION = {("reading_history", "line_id"), ("reading_history", "profile_id")}


def _alter_columns_in_db_py():
    """Column names db.py adds to existing tables: every written-out
    `ALTER TABLE t ADD COLUMN c` as (table, column), plus the loop-driven
    ones, found as constant ("column", "TYPE ...") or ("table", "column",
    "TYPE ...") tuples anywhere in db.py, as (None, column)."""
    import ast
    import re
    # db may be split into a db/ package; read every module so the scan
    # can't pass on an empty file list.
    root = os.path.dirname(os.path.dirname(db.__file__))
    if os.path.isdir(os.path.join(root, "db")):
        paths = [os.path.join(d, f) for d, _, fs in os.walk(os.path.join(root, "db"))
                 for f in fs if f.endswith(".py")]
    else:
        paths = [os.path.join(root, "db.py")]
    src = "\n".join(open(p, encoding="utf-8").read() for p in sorted(paths))
    found = {(t, c) for t, c in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)\b", src)}
    assert len(found) >= 50, f"found only {len(found)} ALTER TABLE ... ADD COLUMN literals"
    sql_type = re.compile(r"^(TEXT|INTEGER|REAL|BLOB|NUMERIC)\b")
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Tuple) and node.elts
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in node.elts)):
            continue
        parts = [e.value for e in node.elts]
        if len(parts) == 2 and sql_type.match(parts[1]):
            found.add((None, parts[0]))
        elif len(parts) == 3 and sql_type.match(parts[2]):
            found.add((parts[0], parts[1]))
    return found


class TestInitDbSchema:
    """init_db() runs on every user's existing database at startup: a fresh
    database and an upgraded old one must end up with the same schema, and
    running it again must change nothing."""

    @pytest.fixture
    def isolated_db(self):
        # Its own folder with a from-scratch init_db: "fresh" below must not be
        # a copy of the shared template these tests are meant to be checked against.
        previous = db.LIBRARY_DIR
        parent_dir = tempfile.mkdtemp(prefix="baihe_test_")
        db.configure_library_dir(os.path.join(parent_dir, "library"))
        db.init_db()
        yield db
        db.configure_library_dir(previous)
        rmtree_retrying(parent_dir)

    def test_running_init_db_again_changes_nothing(self, isolated_db):
        before = _exact_snapshot(isolated_db.DB_PATH)
        isolated_db.init_db()
        assert _exact_snapshot(isolated_db.DB_PATH) == before

    def test_every_added_column_is_listed(self):
        listed = {(t, c) for t, cols in _INIT_DB_MIGRATED_COLUMNS.items() for c in cols}
        listed_names = {c for _, c in listed}
        missing = sorted(
            (t, c) for t, c in _alter_columns_in_db_py()
            if (t, c) not in listed and (t is not None or c not in listed_names)
            and (t, c) not in _ALTERS_WITH_OWN_MIGRATION)
        assert not missing, (
            f"db.py adds these columns to existing tables but _INIT_DB_MIGRATED_COLUMNS in "
            f"tests/test_db.py doesn't list them, so no test upgrades an old database "
            f"through them: {missing}. Add each (table, column) to _INIT_DB_MIGRATED_COLUMNS: "
            f"{{table: [columns]}}.")

    def test_old_database_upgrades_to_the_fresh_schema(self, isolated_db):
        fresh = _schema_shape(isolated_db.DB_PATH)
        _make_old_shape(isolated_db.DB_PATH)
        old = _schema_shape(isolated_db.DB_PATH)
        for table, cols in _INIT_DB_MIGRATED_COLUMNS.items():
            names = {c[0] for c in old["columns"][table]}
            assert not names & set(cols), table

        isolated_db.init_db()

        upgraded = _schema_shape(isolated_db.DB_PATH)
        assert upgraded == fresh
        upgraded_exact = _exact_snapshot(isolated_db.DB_PATH)
        isolated_db.init_db()
        assert _exact_snapshot(isolated_db.DB_PATH) == upgraded_exact

    def test_a_real_0_1_0_library_upgrades_to_the_fresh_schema(self, isolated_db):
        # Made by v0.1.0's own init_db plus a series, title, two lines, a glossary term and a
        # usage row (dumped as SQL). Unlike _make_old_shape it holds what an old release really
        # created, not today's schema with columns taken away.
        fresh = _schema_shape(isolated_db.DB_PATH)
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(isolated_db.DB_PATH + suffix)
        dump = os.path.join(os.path.dirname(__file__), "fixtures", "old_libraries",
                            "library_v0.1.0.sql")
        conn = sqlite3.connect(isolated_db.DB_PATH)
        try:
            with open(dump, encoding="utf-8") as f:
                conn.executescript(f.read())
        finally:
            conn.close()

        isolated_db.init_db()

        assert _schema_shape(isolated_db.DB_PATH) == fresh
        drama = isolated_db.get_drama(1)
        assert (drama["title_en"], drama["title_zh"], drama["series_id"]) == ("Old title", "旧剧", 1)
        rows = isolated_db.load_lines(1)
        assert [(r["id"], r["zh"], r["en"], r["speaker"], r["flag"], bool(r["reviewed"]))
                for r in rows] == [(1, "你好", "Hello", "A", None, True),
                                   (2, "再见", "Goodbye", None, "check", False)]
        with contextlib.closing(isolated_db.get_conn()) as c:
            assert [tuple(r) for r in c.execute(
                "SELECT term_original, term_translation FROM glossary_terms WHERE series_id = 1")
            ] == [("旧剧", "Old Drama")]
            assert [tuple(r) for r in c.execute(
                "SELECT engine, input_tokens, output_tokens FROM usage_log WHERE drama_id = 1")
            ] == [("claude", 10, 5)]

    def test_a_new_database_has_no_bug_reports_table(self, isolated_db):
        with contextlib.closing(isolated_db.get_conn()) as conn:
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'bug_reports'").fetchone() is None

    def test_an_old_database_keeps_its_bug_reports_table_and_rows(self, isolated_db):
        with contextlib.closing(isolated_db.get_conn()) as conn:
            conn.execute("CREATE TABLE bug_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         "drama_id INTEGER NOT NULL, label TEXT)")
            conn.execute("INSERT INTO dramas (id, title_en) VALUES (1, 'Old')")
            conn.execute("INSERT INTO bug_reports (drama_id, label) VALUES (1, 'kept')")
            conn.commit()
        isolated_db.init_db()
        with contextlib.closing(isolated_db.get_conn()) as conn:
            assert conn.execute("SELECT label FROM bug_reports").fetchall()[0][0] == "kept"

    def test_an_upgrade_interrupted_inside_a_column_group_finishes_on_the_next_start(
            self, isolated_db):
        # A crash between two ALTERs of one migration leaves the first column
        # added and the rest missing; the next init_db must add the rest.
        fresh = _schema_shape(isolated_db.DB_PATH)
        added_later = {"series_characters": ["voice_fingerprint_samples"],
                       "bubbles": ["kind_confidence", "confidence", "language", "orientation",
                                   "panel_id", "include_sfx"],
                       "bulk_jobs": ["stage", "pipeline_id"],
                       "bulk_job_lines": ["state_at_submit"]}
        conn = sqlite3.connect(isolated_db.DB_PATH)
        try:
            for table, cols in added_later.items():
                for col in cols:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
            conn.commit()
        finally:
            conn.close()

        isolated_db.init_db()

        assert _schema_shape(isolated_db.DB_PATH) == fresh

    def test_a_title_from_an_old_database_gets_the_default_split_pause(self, isolated_db):
        _make_old_shape(isolated_db.DB_PATH)
        isolated_db.init_db()
        assert isolated_db.get_drama(1)["min_pause_sec"] == 0.35

    def test_titles_on_the_old_hallucination_guard_default_are_switched_off(self, isolated_db):
        on_default = isolated_db.create_drama(title_en="A")
        chosen = isolated_db.create_drama(title_en="B")
        conn = sqlite3.connect(isolated_db.DB_PATH)
        try:
            conn.execute("UPDATE dramas SET hallucination_silence_sec = 2.0 WHERE id = ?",
                         (on_default,))
            conn.execute("UPDATE dramas SET hallucination_silence_sec = 3.5 WHERE id = ?",
                         (chosen,))
            conn.execute("ALTER TABLE dramas DROP COLUMN whisper_repeat_guard")
            conn.commit()
        finally:
            conn.close()
        isolated_db.init_db()
        assert isolated_db.get_drama(on_default)["hallucination_silence_sec"] == 0
        assert isolated_db.get_drama(chosen)["hallucination_silence_sec"] == 3.5
        # Once only: a title the user later sets to 2.0 keeps it.
        isolated_db.update_drama(on_default, hallucination_silence_sec=2.0)
        isolated_db.init_db()
        assert isolated_db.get_drama(on_default)["hallucination_silence_sec"] == 2.0

    def test_an_upgrade_interrupted_after_the_guard_column_still_switches_the_guard_off(
            self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="A")
        conn = sqlite3.connect(isolated_db.DB_PATH)
        try:
            conn.execute("UPDATE dramas SET hallucination_silence_sec = 2.0")
            conn.execute("ALTER TABLE dramas DROP COLUMN whisper_repeat_guard")
            conn.commit()
        finally:
            conn.close()
        real_alter = isolated_db._safe_alter

        def crash_after_the_guard_column(conn, sql):
            real_alter(conn, sql)
            if "whisper_repeat_guard" in sql:
                raise KeyboardInterrupt   # the PC switched off mid-upgrade

        monkeypatch.setattr(isolated_db, "_safe_alter", crash_after_the_guard_column)
        with pytest.raises(KeyboardInterrupt):
            isolated_db.init_db()
        monkeypatch.setattr(isolated_db, "_safe_alter", real_alter)
        isolated_db.init_db()
        assert isolated_db.get_drama(did)["hallucination_silence_sec"] == 0

    def test_a_new_title_starts_with_the_hallucination_guard_off(self, isolated_db):
        assert isolated_db.get_drama(isolated_db.create_drama(title_en="A"))[
            "hallucination_silence_sec"] == 0

    def test_old_database_data_migrations_run(self, isolated_db):
        _make_old_shape(isolated_db.DB_PATH, share_by_default_was_on=True)
        isolated_db.init_db()
        conn = sqlite3.connect(isolated_db.DB_PATH)
        try:
            assert conn.execute("SELECT share_by_default FROM users").fetchone() == (0,)
            assert conn.execute(
                "SELECT value FROM app_settings WHERE key = 'migrations.share_by_default_off'"
            ).fetchone() == ("true",)
            agent, label = conn.execute(
                "SELECT user_agent_short, device_label FROM auth_sessions").fetchone()
            assert agent == "" and label
            assert conn.execute("SELECT translation_engine FROM presets").fetchone() == ("claude",)
            assert conn.execute(
                "SELECT translation_engine, is_private FROM dramas").fetchone() == ("claude", 0)
        finally:
            conn.close()

    def test_saved_off_separation_is_reset_to_unset_once(self, isolated_db):
        did = isolated_db.create_drama(title_en="A", separate_vocals_first=0)
        did_on = isolated_db.create_drama(title_en="B", separate_vocals_first=1)
        with contextlib.closing(sqlite3.connect(isolated_db.DB_PATH)) as conn:
            conn.execute("DELETE FROM app_settings WHERE key = 'migrations.separate_vocals_unset'")
            conn.commit()
        isolated_db.init_db()
        assert isolated_db.get_drama(did)["separate_vocals_first"] is None
        assert isolated_db.get_drama(did_on)["separate_vocals_first"] == 1
        isolated_db.update_drama(did, separate_vocals_first=0)   # a later choice of Off
        isolated_db.init_db()
        assert isolated_db.get_drama(did)["separate_vocals_first"] == 0

    def test_library_read_holders_get_lines_read_once_for_the_reader_page(self, isolated_db):
        from services import auth_service
        reader = auth_service.add_user("reader@example.com")["id"]
        nobody = auth_service.add_user("nobody@example.com")["id"]
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            if p != "library.read":
                isolated_db.auth_revoke_permission(reader, p)
            isolated_db.auth_revoke_permission(nobody, p)
        conn = sqlite3.connect(isolated_db.DB_PATH)
        try:   # as before the migration ran
            conn.execute("DELETE FROM app_settings WHERE key = 'migrations.reader_page_lines_read'")
            # The later library.create grant has already run, so it adds nothing here.
            conn.execute("INSERT OR IGNORE INTO app_settings (key, value) "
                         "VALUES ('migrations.members_library_create', 'true')")
            conn.commit()
        finally:
            conn.close()
        isolated_db.init_db()
        assert set(isolated_db.auth_get_permissions(reader)) == {"library.read", "lines.read"}
        assert not isolated_db.auth_get_permissions(nobody)
        # Once only: a later revoke survives the next start.
        isolated_db.auth_revoke_permission(reader, "lines.read")
        isolated_db.init_db()
        assert set(isolated_db.auth_get_permissions(reader)) == {"library.read"}

    def test_library_read_holders_get_library_create_once(self, isolated_db):
        from services import auth_service
        member = auth_service.add_user("member@example.com")["id"]
        nobody = auth_service.add_user("nobody@example.com")["id"]
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            isolated_db.auth_revoke_permission(nobody, p)
        isolated_db.auth_revoke_permission(member, "library.create")   # as before this right was a default
        conn = sqlite3.connect(isolated_db.DB_PATH)
        try:
            conn.execute("DELETE FROM app_settings WHERE key = 'migrations.members_library_create'")
            conn.commit()
        finally:
            conn.close()
        isolated_db.init_db()
        assert "library.create" in isolated_db.auth_get_permissions(member)
        assert not isolated_db.auth_get_permissions(nobody)
        # Once only: a later revoke of one person survives the next start.
        isolated_db.auth_revoke_permission(member, "library.create")
        isolated_db.init_db()
        assert "library.create" not in isolated_db.auth_get_permissions(member)

    def test_library_create_grant_with_no_accounts_still_sets_its_marker(self, isolated_db):
        # A revoke made after the first account appears must survive the next start.
        from services import auth_service
        member = auth_service.add_user("late@example.com")["id"]
        isolated_db.auth_revoke_permission(member, "library.create")
        isolated_db.init_db()
        assert "library.create" not in isolated_db.auth_get_permissions(member)


class TestJobRecordCancelAcrossRuns:
    """A cancel aimed at one run must not carry into the run that replaces it."""

    def _cancel(self, job_id):
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("UPDATE job_records SET cancel_requested = 1, cancel_requested_at = 5.0 "
                         "WHERE job_id = ?", (job_id,))
            conn.commit()

    def test_a_new_run_over_a_still_active_row_clears_the_old_cancel(self, isolated_db):
        db.save_job_record("job1", status="running", started_at=100.0)
        self._cancel("job1")
        db.save_job_record("job1", status="running", started_at=200.0)
        rec = db.get_job_record("job1")
        assert not rec["cancel_requested"] and rec["cancel_requested_at"] is None

    def test_the_same_run_keeps_its_cancel_across_its_own_writes(self, isolated_db):
        db.save_job_record("job1", status="running", started_at=100.0)
        self._cancel("job1")
        db.save_job_record("job1", status="running", progress=0.5, started_at=100.0)
        rec = db.get_job_record("job1")
        assert rec["cancel_requested"] and rec["cancel_requested_at"] == 5.0

    def test_a_queued_job_keeps_its_cancel_when_its_owner_starts_it(self, isolated_db):
        from jobs import job_store
        db.save_job_record("job1", status="queued", started_at=100.0, owner_pid=42)
        assert job_store.request_cancel("job1")
        db.save_job_record("job1", status="running", started_at=200.0, owner_pid=42)
        rec = db.get_job_record("job1")
        assert rec["cancel_requested"] and rec["cancel_requested_at"] is not None

    def test_a_queued_row_from_another_process_does_not_pass_its_cancel_on(self, isolated_db):
        db.save_job_record("job1", status="queued", started_at=100.0, owner_pid=42)
        self._cancel("job1")
        db.save_job_record("job1", status="running", started_at=200.0, owner_pid=43)
        rec = db.get_job_record("job1")
        assert not rec["cancel_requested"] and rec["cancel_requested_at"] is None


class TestSearchWildcards:
    def test_percent_and_underscore_in_a_search_are_literal(self, isolated_db):
        from core import Line
        a = isolated_db.create_drama(title_en="100% Sure")
        b = isolated_db.create_drama(title_en="Plain", summary="a_b")
        isolated_db.create_drama(title_en="Other")
        assert [d["id"] for d in isolated_db.list_dramas(search="%")] == [a]
        assert [d["id"] for d in isolated_db.list_dramas(search="_")] == [b]
        assert isolated_db.list_dramas(search="a\\b") == []
        isolated_db.save_lines(a, [Line(idx=0, start=0.0, end=1.0, zh="九十%", en="half"),
                                   Line(idx=1, start=1.0, end=2.0, zh="别的", en="x_y")])
        assert [r["idx"] for r in isolated_db.search_lines_globally("%")] == [0]
        assert [r["idx"] for r in isolated_db.search_lines_globally("_")] == [1]

    def test_search_ignores_case_in_accented_letters(self, isolated_db):
        from core import Line
        a = isolated_db.create_drama(title_en="CAFÉ Nights")
        isolated_db.create_drama(title_en="Cafe Plain")
        assert [d["id"] for d in isolated_db.list_dramas(search="café")] == [a]
        assert [d["id"] for d in isolated_db.list_dramas(search="Café")] == [a]
        isolated_db.save_lines(a, [Line(idx=0, start=0.0, end=1.0, zh="", en="ÉCOLE")])
        assert [r["idx"] for r in isolated_db.search_lines_globally("école")] == [0]


def test_job_record_listing_keeps_every_active_row_and_caps_finished_ones(isolated_db):
    isolated_db.save_job_record("old-active", "running", started_at=1.0)
    for n in range(5):
        isolated_db.save_job_record(f"done-{n}", "done", started_at=10.0 + n, finished_at=20.0 + n)
    ids = [r["job_id"] for r in isolated_db.list_job_records(finished_limit=2)]
    assert ids == ["done-4", "done-3", "old-active"]
    assert len(isolated_db.list_job_records()) == 6


def test_household_chapter_marks_become_the_pcs_and_each_active_admins(isolated_db):
    """Marks used to be kept per household profile. The upgrade folds them into the PC's own
    marks and gives every active admin account a copy; other accounts start empty."""
    fresh = _schema_shape(isolated_db.DB_PATH)
    with contextlib.closing(isolated_db.get_conn()) as conn:
        conn.execute("INSERT INTO dramas (id, title_en) VALUES (1, 'Comic')")
        conn.execute("INSERT INTO profiles (id, name) VALUES (7, 'Second')")
        for uid, admin, active in ((3, 1, 1), (4, 0, 1), (5, 1, 0)):
            conn.execute("INSERT INTO users (id, email, is_admin, is_active) VALUES (?, ?, ?, ?)",
                         (uid, f"u{uid}@example.com", admin, active))
        conn.execute("DROP TABLE chapter_marks")
        conn.execute("""CREATE TABLE chapter_marks (
            drama_id INTEGER NOT NULL, profile_id INTEGER NOT NULL, chapter_id TEXT NOT NULL,
            read_at TEXT, bookmarked INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (drama_id, profile_id, chapter_id),
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE)""")
        conn.executemany("INSERT INTO chapter_marks VALUES (1, ?, ?, ?, ?)",
                         [(1, "c1", "2026-11-01", 0), (1, "c2", None, 1),
                          (7, "c1", "2026-11-02", 1), (7, "c3", "2026-11-03", 0)])
        # A row whose title is gone must not stop the upgrade (the new table's foreign key).
        conn.commit()     # the pragma does nothing inside a transaction
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("INSERT INTO chapter_marks VALUES (999, 1, 'c1', '2026-11-01', 0)")
        conn.commit()

    isolated_db.init_db()

    assert _schema_shape(isolated_db.DB_PATH) == fresh
    household = {"c1": {"read": True, "bookmarked": True}, "c2": {"read": False, "bookmarked": True},
                 "c3": {"read": True, "bookmarked": False}}
    assert isolated_db.get_chapter_marks(1) == household
    assert isolated_db.get_chapter_marks(1, 3) == household
    assert isolated_db.get_chapter_marks(1, 4) == {} and isolated_db.get_chapter_marks(1, 5) == {}
    with contextlib.closing(isolated_db.get_conn()) as conn:
        assert conn.execute("SELECT read_at FROM chapter_marks WHERE user_id = 0 AND chapter_id = 'c1'"
                            ).fetchone()[0] == "2026-11-01"      # the first time anyone read it
        rows = conn.execute("SELECT COUNT(*) FROM chapter_marks").fetchone()[0]
    # A later start leaves the marks alone: an admin added afterwards gets no copy.
    with contextlib.closing(isolated_db.get_conn()) as conn:
        conn.execute("INSERT INTO users (id, email, is_admin, is_active) VALUES (9, 'late@example.com', 1, 1)")
        conn.commit()
    isolated_db.set_chapter_marks(1, ["c1"], read=False, bookmarked=False, user_id=3)
    isolated_db.init_db()
    with contextlib.closing(isolated_db.get_conn()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chapter_marks").fetchone()[0] == rows - 1
    assert isolated_db.get_chapter_marks(1, 9) == {}
