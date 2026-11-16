import db
from core import Line
from services import name_fix_service as nf


def _title(texts, terms):
    sid = db.get_or_create_series("creator")
    did = db.create_drama(title_en="T", series_id=sid, source_language="ja")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=t, en="") for i, t in enumerate(texts)])
    for original, aliases in terms:
        db.upsert_glossary_term(sid, original, "X", aliases="|".join(aliases))
    return did, sid


def _texts(did):
    return [ln.zh for ln in db.load_line_objects(did)]


def test_confirmed_alias_is_rewritten_and_term_kept(isolated_db):
    did, _ = _title(["ヤローが来た", "ヤロ、行こう", "関係ない"], [("ヤロ", ["ヤロー"])])
    out = nf.apply_known_aliases(did)
    assert out == {"lines_changed": 1, "replacements": 1}
    assert _texts(did) == ["ヤロが来た", "ヤロ、行こう", "関係ない"]


def test_longer_term_containing_alias_is_not_mangled(isolated_db):
    did, _ = _title(["ヤローが来た"], [("ヤロ", ["ロ"]), ("ヤロー", [])])
    assert nf.apply_known_aliases(did)["lines_changed"] == 0
    assert _texts(did) == ["ヤローが来た"]


def test_ambiguous_alias_and_short_alias_are_left_alone(isolated_db):
    did, _ = _title(["アキが来た", "Bの話"], [("アカ", ["アキ"]), ("アオ", ["アキ", "B"])])
    assert nf.apply_known_aliases(did)["lines_changed"] == 0


def test_latin_alias_needs_word_boundary(isolated_db):
    did, _ = _title(["Yalo is here", "Yalodeen is here"], [("Yaloch", ["Yalo"])])
    nf.apply_known_aliases(did)
    assert _texts(did) == ["Yaloch is here", "Yalodeen is here"]


def test_other_line_fields_survive(isolated_db):
    did, _ = _title(["ヤローが来た"], [("ヤロ", ["ヤロー"])])
    ln = db.load_line_objects(did)[0]
    ln.en = "He came"
    db.save_lines(did, [ln], fields=("en",))
    nf.apply_known_aliases(did)
    assert db.load_line_objects(did)[0].en == "He came"


def test_apply_fixes_remembers_alias_for_later_titles(isolated_db):
    did, sid = _title(["ヤローが来た"], [("ヤロ", [])])
    term_id = db.list_glossary_terms(sid)[0]["id"]
    out = nf.apply_fixes(did, [{"term_id": term_id, "spelling": "ヤロー"}])
    assert out["aliases_added"] == 1 and out["lines_changed"] == 1
    # A second title of the same creator (series) is fixed with no further input.
    did2 = db.create_drama(title_en="T2", series_id=sid, source_language="ja")
    db.save_lines(did2, [Line(idx=0, start=0, end=1, zh="ヤローだ", en="")])
    assert nf.after_transcription(did2)["lines_changed"] == 1
    assert _texts(did2) == ["ヤロだ"]
    # A confirmed fix is logged; the pass after a transcription rewrites new lines, not
    # corrections, so it logs nothing.
    assert [(r["path"], r["before_zh"], r["after_zh"]) for r in db.list_source_edits(did)] == [
        ("name_fix", "ヤローが来た", "ヤロが来た")]
    assert db.list_source_edits(did2) == []


def test_apply_fixes_rejects_bad_input_and_writes_nothing(isolated_db):
    import pytest
    from lib.errors import InvalidInputError, NotFoundError
    did, sid = _title(["ヤローが来た"], [("ヤロ", []), ("ミカ", [])])
    ids = {t["term_original"]: t["id"] for t in db.list_glossary_terms(sid)}
    with pytest.raises(InvalidInputError):
        nf.apply_fixes(did, [{"term_id": ids["ヤロ"], "spelling": "ヤローx"}, {"term_id": ids["ヤロ"], "spelling": "ミカ"}])
    with pytest.raises(NotFoundError):
        nf.apply_fixes(did, [{"term_id": 99999, "spelling": "ヤロー"}])
    assert _texts(did) == ["ヤローが来た"]
    assert not db.list_glossary_terms(sid)[0]["aliases"]


def test_after_transcription_swallows_failures(isolated_db, monkeypatch):
    monkeypatch.setattr(nf, "apply_known_aliases", lambda d: 1 / 0)
    assert nf.after_transcription(1) == {"lines_changed": 0, "replacements": 0}


def test_alias_inside_a_novel_chapter_title_line_is_left_alone(isolated_db):
    from sources import pipeline
    from sources.models import ChapterInfo
    from services import novel_chapters_service
    did, sid = _title([], [("ヤロ", ["ヤロー"])])
    for n, (title, body) in enumerate([("ヤロー来る", "ヤローが来た"), ("次", "終わり")], 1):
        ch = ChapterInfo(source="xbanxia", series_id="s1", chapter_id=f"c{n}", title=title)
        assert pipeline._append_chapter_text("xbanxia", ch, did, body) == ""
        pipeline._clear_in_flight("xbanxia", ch, did)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=t, en="") for i, t in
                        enumerate(["ヤロー来る", "ヤローが来た", "次", "終わり"])])
    out = nf.apply_known_aliases(did)
    assert out == {"lines_changed": 1, "replacements": 1}
    assert _texts(did) == ["ヤロー来る", "ヤロが来た", "次", "終わり"]


def test_a_line_edited_since_it_was_read_is_kept(isolated_db, monkeypatch):
    did, _ = _title(["ヤローが来た"], [("ヤロ", ["ヤロー"])])
    real = db.load_line_objects

    def load_then_edit(drama_id):
        lines = real(drama_id)
        edited = real(drama_id)
        edited[0].zh = "手で直した"
        db.save_lines(drama_id, edited, fields=("zh",))
        return lines
    monkeypatch.setattr(db, "load_line_objects", load_then_edit)
    assert nf.apply_known_aliases(did) == {"lines_changed": 0, "replacements": 0}
    monkeypatch.undo()
    assert _texts(did) == ["手で直した"]
