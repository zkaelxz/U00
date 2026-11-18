"""misheard_finder / misheard_service: sound-alike suggestions, offline with a fake word cutter."""
import importlib.util

import pytest

import db
import misheard_finder as mf
from core import Line
from lib.errors import ConflictError, InvalidInputError
from services import misheard_service

pytest.importorskip("pypinyin")


def cut(text):
    """Two-character words, which is all these fake lines hold."""
    return [text[i:i + 2] for i in range(0, len(text), 2)]


def _lines(*texts):
    return [(i, 100 + i, t) for i, t in enumerate(texts)]


class TestFinder:
    def test_rare_sound_alike_of_a_glossary_name_is_suggested(self):
        out = mf.find_misheard(_lines("亞羅赫來了"[:4], "亞洛赫說"[:4]), [("亞洛", "glossary")], cut)
        found = {s["heard"]: s for s in out["suggestions"]}
        assert found["亞羅"]["suggested"] == "亞洛"
        assert found["亞羅"]["source"] == "glossary" and found["亞羅"]["lines"][0]["line_id"] == 100

    def test_a_word_heard_often_is_trusted_and_a_rare_twin_points_to_it(self):
        lines = _lines("書院", "書院", "書院", "數院")
        out = mf.find_misheard(lines, [], cut)
        assert [(s["heard"], s["suggested"], s["source"]) for s in out["suggestions"]] == [
            ("數院", "書院", "frequent")]
        assert out["suggestions"][0]["suggested_count"] == 3

    def test_traditional_and_simplified_are_one_word(self):
        fold = {"书": "書"}.get
        out = mf.find_misheard(_lines("书院", "書院", "書院", "書院"), [], cut,
                               canon=lambda w: "".join(fold(c, c) for c in w))
        assert out["suggestions"] == []

    def test_different_length_or_unrelated_sound_is_not_suggested(self):
        out = mf.find_misheard(_lines("今天", "今天", "今天", "明年"), [("亞洛赫", "name")], cut)
        assert out["suggestions"] == []

    def test_glossary_outranks_a_frequent_word(self):
        lines = _lines("書院", "書院", "書院", "數院")
        out = mf.find_misheard(lines, [("輸院", "glossary")], cut)
        assert out["suggestions"][0]["suggested"] == "輸院"

    def test_capped_and_reports_more_existed(self, monkeypatch):
        monkeypatch.setattr(mf, "MAX_SUGGESTIONS", 1)
        out = mf.find_misheard(_lines("書院", "書院", "書院", "數院", "疏院"), [], cut)
        assert len(out["suggestions"]) == 1 and out["truncated"]


@pytest.mark.skipif(importlib.util.find_spec("jieba") is None, reason="jieba not installed")
def test_service_reads_title_lines(isolated_db):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="zh")
    db.upsert_glossary_term(sid, "亞洛赫", "Yaloch", aliases="")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="亞羅赫來了", en="")])
    out = misheard_service.find_for_title(did)
    assert out["available"] and out["suggestions"][0]["suggested"] == "亞洛赫"


def test_service_reports_unavailable_for_other_languages(isolated_db):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="ja")
    out = misheard_service.find_for_title(did)
    assert not out["available"] and out["suggestions"] == []


def test_service_reports_missing_jieba(isolated_db, monkeypatch):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="zh")
    monkeypatch.setattr(misheard_service.segment, "missing_word_segmenters", lambda lang: ["jieba"])
    out = misheard_service.find_for_title(did)
    assert not out["available"] and "jieba" in out["reason"]


def test_a_dictionary_word_is_not_a_slip_of_a_merely_frequent_word():
    lines = _lines("書院", "書院", "書院", "數院")
    kept = mf.find_misheard(lines, [], cut)
    dropped = mf.find_misheard(lines, [], cut, is_dictionary_word=lambda w: w == "數院")
    assert kept["suggestions"] and dropped["suggestions"] == []


def test_a_dictionary_word_can_still_be_a_slip_of_a_glossary_word():
    out = mf.find_misheard(_lines("亞羅"), [("亞洛", "glossary")], cut, is_dictionary_word=lambda w: True)
    assert out["suggestions"][0]["suggested"] == "亞洛"


def _title_with_slips(isolated_db):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="zh")
    db.upsert_glossary_term(sid, "亞洛赫", "Yaloch", aliases="")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="亞羅赫來了", en="Yaloch came"),
                        Line(idx=1, start=1, end=2, zh="亞羅赫說話", en=""),
                        Line(idx=2, start=2, end=3, zh="沒有", en="")])
    return did, [ln.id for ln in db.load_line_objects(did)]


class TestApply:
    def test_the_list_counts_the_lines_that_hold_the_heard_spelling(self, isolated_db):
        pytest.importorskip("jieba")
        did, _ = _title_with_slips(isolated_db)
        [s] = misheard_service.find_for_title(did)["suggestions"]
        assert (s["heard"], s["line_count"]) == ("亞羅赫", 2)

    def test_use_it_changes_one_line_flags_its_translation_and_snapshots(self, isolated_db):
        did, ids = _title_with_slips(isolated_db)
        out = misheard_service.apply(did, "亞羅赫", "亞洛赫", ids[0], "亞羅赫來了")
        assert out == {"applied": 1, "skipped": 0}
        lines = {ln.id: ln for ln in db.load_line_objects(did)}
        assert lines[ids[0]].zh == "亞洛赫來了" and lines[ids[0]].flag == "source_changed"
        assert lines[ids[1]].zh == "亞羅赫說話"
        assert db.list_line_history(did)[0]["label"] == "before mis-heard word fix"

    def test_use_everywhere_changes_every_line_holding_it(self, isolated_db):
        did, ids = _title_with_slips(isolated_db)
        assert misheard_service.apply(did, "亞羅赫", "亞洛赫")["applied"] == 2
        assert [ln.zh for ln in db.load_line_objects(did)] == ["亞洛赫來了", "亞洛赫說話", "沒有"]

    def test_a_line_edited_since_is_refused(self, isolated_db):
        did, ids = _title_with_slips(isolated_db)
        with pytest.raises(ConflictError):
            misheard_service.apply(did, "亞羅赫", "亞洛赫", ids[0], "亞羅赫去了")
        assert db.load_line_objects(did)[0].zh == "亞羅赫來了"

    def test_one_line_needs_the_text_the_person_saw(self, isolated_db):
        did, ids = _title_with_slips(isolated_db)
        with pytest.raises(InvalidInputError):
            misheard_service.apply(did, "亞羅赫", "亞洛赫", ids[0])

    def test_the_change_is_logged_as_a_person_correction(self, isolated_db):
        did, ids = _title_with_slips(isolated_db)
        misheard_service.apply(did, "亞羅赫", "亞洛赫", ids[0], "亞羅赫來了")
        [row] = db.list_source_edits(did)
        assert (row["path"], row["before_zh"], row["after_zh"]) == ("person", "亞羅赫來了", "亞洛赫來了")

    def test_use_it_accepts_the_stripped_text_the_list_shows(self, isolated_db):
        did, ids = _title_with_slips(isolated_db)
        ln = db.load_line_objects(did)[0]
        ln.zh = " 亞羅赫來了 "
        db.save_lines(did, [ln], fields=("zh",))
        assert misheard_service.apply(did, "亞羅赫", "亞洛赫", ids[0], "亞羅赫來了")["applied"] == 1


class TestSafeEverywhere:
    def test_replace_skips_the_heard_spelling_inside_a_longer_ordinary_word(self, monkeypatch):
        monkeypatch.setattr(misheard_service, "_in_dictionary", lambda w: w == "亞羅斯")
        cut = lambda t: ["亞羅斯", "亞羅", "來了"]  # noqa: E731
        out = misheard_service._replace("亞羅斯亞羅來了", "亞羅", "亞洛", (cut, lambda w: w))
        assert out == "亞羅斯亞洛來了"

    def test_replace_without_the_word_cutter_changes_every_occurrence(self):
        assert misheard_service._replace("亞羅亞羅", "亞羅", "亞洛", None) == "亞洛亞洛"

    def test_remember_rewrites_lines_with_one_snapshot_and_saves_the_alias(self, isolated_db):
        did, ids = _title_with_slips(isolated_db)
        sid = db.get_drama(did)["series_id"]
        [term] = db.list_glossary_terms(sid)
        out = misheard_service.remember_spelling(did, "亞羅赫", "亞洛赫", term["id"])
        assert out == {"applied": 2, "skipped": 0, "aliases_added": 1}
        assert [ln.zh for ln in db.load_line_objects(did)][:2] == ["亞洛赫來了", "亞洛赫說話"]
        assert db.list_glossary_terms(sid)[0]["aliases"] == "亞羅赫"
        assert db.list_line_history(did)[0]["label"] == "before mis-heard word fix"

    def test_remember_refuses_a_term_that_is_not_the_suggestion_and_writes_nothing(self, isolated_db):
        did, ids = _title_with_slips(isolated_db)
        sid = db.get_drama(did)["series_id"]
        db.upsert_glossary_term(sid, "別人", "Other", aliases="")
        other = [t for t in db.list_glossary_terms(sid) if t["term_original"] == "別人"][0]
        with pytest.raises(InvalidInputError):
            misheard_service.remember_spelling(did, "亞羅赫", "亞洛赫", other["id"])
        assert db.load_line_objects(did)[0].zh == "亞羅赫來了" and db.list_line_history(did) == []
