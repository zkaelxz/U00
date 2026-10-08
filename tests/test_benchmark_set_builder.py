"""
Benchmark Lab: building a set of cases from a title's reviewed lines
(services/benchmark_set_builder_service.py).
"""
import pytest

import db
from core import Line, line_from_row
from services import benchmark_lab_service as lab
from services import benchmark_set_builder_service as builder
from services.service_errors import InvalidInputError, NotFoundError


def _drama(lines, **fields):
    did = db.create_drama(title_en="Moon Drama", source_language=fields.pop("source_language", "zh"),
                          **fields)
    db.save_lines(did, [Line(i, float(i), i + 0.9, zh, en, **kw)
                        for i, (zh, en, kw) in enumerate(lines)])
    return did


def _reviewed(did, n):
    """Marks line n as hand-edited the way the Review page does."""
    ln = db.load_lines(did)[n]
    db.record_edit_sample(did, ln["zh"], "machine text", ln["en"])


def _lines(count, **kw):
    return [(f"第{i}句", f"Line {i}", kw) for i in range(count)]


class TestWhatCountsAsReviewed:
    def test_only_hand_edited_lines_by_default(self, isolated_db):
        did = _drama(_lines(6))
        for n in (1, 2):
            _reviewed(did, n)
        res = builder.build_set(None, did, "moon")
        assert (res["reviewed_lines"], res["lines_used"], res["added"]) == (2, 2, 1)
        case = db.list_benchmark_cases("translation")[0]
        assert case["source_text"] == "第1句\n第2句"
        assert case["reference_text"] == "Line 1\nLine 2"
        assert (case["tier"], case["set_name"]) == ("application", "moon")
        assert case["origin_drama_id"] == did

    def test_an_edit_that_was_later_changed_again_is_not_reviewed(self, isolated_db):
        did = _drama(_lines(3))
        _reviewed(did, 0)
        changed = line_from_row(db.load_lines(did)[0])
        changed.en = "Replaced by find and replace"
        db.save_lines(did, [changed], fields=("en",))
        with pytest.raises(InvalidInputError, match="No lines to use"):
            builder.build_set(None, did, "moon")

    def test_translation_memory_approval_counts_for_a_series_title(self, isolated_db):
        series = db.create_series("Saga") if hasattr(db, "create_series") else None
        did = _drama(_lines(3), series_id=series)
        db.record_translation_memory(series, "第1句", "Line 1")
        res = builder.build_set(None, did, "moon")
        assert res["lines_used"] == 1

    def test_flagged_sfx_and_untranslated_lines_are_left_out(self, isolated_db):
        did = _drama([("第0句", "Zero", {"flag": "uncertain_translation"}),
                      ("[门响]", "[door]", {"sfx": True}),
                      ("第2句", "", {}), ("第3句", "Three", {})])
        for n in range(4):
            _reviewed(did, n)
        assert builder.build_set(None, did, "moon")["lines_used"] == 1

    def test_all_lines_option_uses_unreviewed_lines(self, isolated_db):
        did = _drama(_lines(5))
        res = builder.build_set(None, did, "moon", include="all")
        assert (res["reviewed_lines"], res["lines_used"]) == (0, 5)

    def test_nothing_reviewed_says_how_to_continue(self, isolated_db):
        did = _drama(_lines(3))
        with pytest.raises(InvalidInputError, match="choose all lines"):
            builder.build_set(None, did, "moon")


class TestScenes:
    def test_groups_of_lines_per_case_split_at_pauses_and_skipped_lines(self, isolated_db):
        lines = [(f"句{i}", f"L{i}", {}) for i in range(10)]
        did = _drama(lines)
        # A long silence after line 4 starts a new scene.
        later = [line_from_row(r) for r in db.load_lines(did)[5:]]
        for ln in later:
            ln.start += 30
            ln.end += 30
        db.save_lines(did, later, fields=("start", "end"))
        res = builder.build_set(None, did, "moon", include="all", lines_per_case=3)
        # 5 lines before the pause -> 3 + 2, 5 lines after -> 3 + 2
        assert res["case_count"] == 4
        texts = [c["source_text"] for c in db.list_benchmark_cases("translation")]
        assert texts[0] == "句0\n句1\n句2" and texts[1] == "句3\n句4"

    def test_a_skipped_line_ends_the_case(self, isolated_db):
        did = _drama(_lines(4))
        for n in (0, 1, 3):
            _reviewed(did, n)
        builder.build_set(None, did, "moon")
        texts = [c["source_text"] for c in db.list_benchmark_cases("translation")]
        assert texts == ["第0句\n第1句", "第3句"]

    def test_range_limits_lines_by_one_based_number(self, isolated_db):
        did = _drama(_lines(10))
        res = builder.build_set(None, did, "moon", include="all", line_start=3, line_end=5)
        assert res["lines_in_range"] == 3
        assert db.list_benchmark_cases("translation")[0]["label"].endswith("lines 3-5")

    def test_scene_count_picks_evenly_spaced_scenes_including_first_and_last(self, isolated_db):
        did = _drama(_lines(20))
        builder.build_set(None, did, "moon", include="all", lines_per_case=1, scene_count=3)
        texts = [c["source_text"] for c in db.list_benchmark_cases("translation")]
        assert texts == ["第0句", "第10句", "第19句"]

    def test_text_limit_splits_a_case(self, isolated_db):
        long = "字" * (lab.MAX_TEXT_CHARS - 10)
        did = _drama([(long, "a", {}), ("字字字" * 10, "b", {})])
        res = builder.build_set(None, did, "moon", include="all")
        assert res["case_count"] == 2

    def test_a_line_over_the_limit_is_skipped(self, isolated_db):
        did = _drama([("字" * (lab.MAX_TEXT_CHARS + 1), "a", {}), ("好", "b", {})])
        assert builder.build_set(None, did, "moon", include="all")["lines_used"] == 1

    def test_too_many_cases_is_refused_with_a_hint(self, isolated_db, monkeypatch):
        monkeypatch.setattr(lab, "MAX_IMPORT_CASES", 2)
        did = _drama(_lines(6))
        with pytest.raises(InvalidInputError, match="Narrow the line range"):
            builder.build_set(None, did, "moon", include="all", lines_per_case=1)


class TestLanguagesAndSets:
    def test_japanese_title_makes_japanese_cases(self, isolated_db):
        did = _drama([("こんにちは", "Hello", {}), ("さようなら", "Bye", {})], source_language="ja")
        builder.build_set(None, did, "ja set", include="all")
        assert db.list_benchmark_cases("translation")[0]["source_language"] == "ja"

    def test_a_line_in_another_language_gets_its_own_case(self, isolated_db):
        did = _drama([("你好", "Hello", {}), ("こんにちは", "Hi", {"lang": "ja"})])
        builder.build_set(None, did, "mixed", include="all")
        langs = [c["source_language"] for c in db.list_benchmark_cases("translation")]
        assert langs == ["zh", "ja"]

    def test_building_twice_skips_existing_cases(self, isolated_db):
        did = _drama(_lines(4))
        assert builder.build_set(None, did, "moon", include="all")["added"] == 1
        second = builder.build_set(None, did, "moon", include="all")
        assert (second["added"], second["skipped"]) == (0, 1)

    def test_dry_run_writes_nothing(self, isolated_db):
        did = _drama(_lines(4))
        res = builder.build_set(None, did, "moon", include="all", dry_run=True)
        assert res["dry_run"] and res["case_count"] == 1
        assert db.list_benchmark_cases("translation") == []

    def test_set_name_is_required_and_limited(self, isolated_db):
        did = _drama(_lines(2))
        with pytest.raises(InvalidInputError):
            builder.build_set(None, did, "  ")
        with pytest.raises(InvalidInputError):
            builder.build_set(None, did, "x" * (lab.MAX_SET_NAME_CHARS + 1))

    @pytest.mark.parametrize("kwargs", [{"include": "some"}, {"lines_per_case": 0},
                                        {"lines_per_case": 11}, {"scene_count": 0},
                                        {"line_start": 5, "line_end": 2}])
    def test_bad_options_are_refused(self, isolated_db, kwargs):
        did = _drama(_lines(2))
        with pytest.raises(InvalidInputError):
            builder.build_set(None, did, "moon", **kwargs)

    def test_unknown_title_is_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            builder.build_set(None, 999, "moon")

    def test_a_private_title_is_not_found_for_another_user(self, isolated_db):
        did = _drama(_lines(2), owner_user_id=1, is_private=1)
        other = {"user_id": 2, "is_admin": False, "is_local_owner": False}
        with pytest.raises(NotFoundError):
            builder.build_set(other, did, "moon", include="all")
