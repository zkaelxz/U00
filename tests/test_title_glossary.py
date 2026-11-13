"""A title's own glossary: kept and used when the title joins a series, copied into it only on request."""
import pytest

import db
from services import glossary_service, workspace_job_service as wjs


class _Line:
    def __init__(self, zh):
        self.zh = zh


def _title(series=None):
    return db.create_drama(title_en="VOD", series_id=series, content_mode="streamer_vod")


def _add(drama_id, original, english, **kw):
    glossary_service.upsert_glossary_term(drama_id, {"term_original": original,
                                                     "term_translation": english, **kw})


def _run_terms(drama_id, text):
    _t, guidelines, _n = wjs.build_run_style_context(
        drama_id, db.get_drama(drama_id), [_Line(text)], "audio_drama",
        engine_name="ollama", model="gemma4:31b-cloud")
    return guidelines


def test_a_title_outside_any_series_translates_with_its_own_terms(isolated_db):
    d = _title()
    _add(d, "亞洛赫", "Anok", aliases=["阿洛"])
    assert "亞洛赫 / 阿洛 -> Anok" in _run_terms(d, "阿洛來了")


def test_joining_a_series_keeps_the_title_terms_and_the_title_wins_a_clash(isolated_db):
    d = _title()
    _add(d, "亞洛赫", "Anok")
    _add(d, "小明", "Xiaoming")
    series = db.create_series("S")
    db.upsert_glossary_term(series, "亞洛赫", "Alo")
    db.upsert_glossary_term(series, "大白", "Dabai")
    db.assign_drama_series(d, series)
    text = _run_terms(d, "亞洛赫和小明和大白")
    assert "亞洛赫 -> Anok" in text and "小明 -> Xiaoming" in text and "大白 -> Dabai" in text
    assert "Alo" not in text
    # nothing was merged or deleted
    assert [t["term_translation"] for t in db.list_glossary_terms(series)
            if t["term_original"] == "亞洛赫"] == ["Alo"]
    origins = {t["term_original"]: t["origin"] for t in glossary_service.effective_glossary_terms(d)}
    assert origins == {"亞洛赫": "title", "小明": "title", "大白": "series"}


def test_a_series_without_title_terms_is_unchanged(isolated_db):
    series = db.create_series("S")
    db.upsert_glossary_term(series, "大白", "Dabai")
    d = _title(series)
    assert db.list_run_glossary_terms(db.get_drama(d)) == db.list_effective_glossary_terms(series)


def test_leaving_the_series_gives_the_title_back_its_own_glossary_not_a_second_one(isolated_db):
    d = _title()
    _add(d, "小明", "Xiaoming")
    own = db.get_title_glossary_series(d)
    series = db.create_series("S")
    db.assign_drama_series(d, series)
    db.unassign_drama_series(d)
    assert "小明 -> Xiaoming" in _run_terms(d, "小明")
    _add(d, "大白", "Dabai")
    assert db.get_drama(d)["series_id"] == own
    assert {t["term_original"] for t in db.list_glossary_terms(own)} == {"小明", "大白"}


def test_copy_adds_only_terms_the_series_lacks_and_keeps_the_title_terms(isolated_db):
    d = _title()
    _add(d, "亞洛赫", "Anok")
    _add(d, "小明", "Xiaoming", aliases=["明明"])
    series = db.create_series("S")
    db.upsert_glossary_term(series, "亞洛赫", "Alo")
    db.assign_drama_series(d, series)
    report = glossary_service.copy_title_terms_to_series(d)
    assert report == {"copied": ["小明"], "skipped": ["亞洛赫"]}
    rows = {t["term_original"]: t for t in db.list_glossary_terms(series)}
    assert rows["亞洛赫"]["term_translation"] == "Alo" and rows["小明"]["aliases"] == "明明"
    assert len(db.title_only_terms(db.get_drama(d))) == 2


def test_copy_without_title_terms_is_refused(isolated_db):
    d = _title()
    with pytest.raises(Exception):
        glossary_service.copy_title_terms_to_series(d)


def test_an_existing_private_series_is_linked_on_upgrade(isolated_db):
    d = _title()
    sid = db.create_series(f"VOD (title {d})")
    db.assign_drama_series(d, sid)
    with db.get_conn() as conn:
        db.link_existing_private_series(conn)
    assert db.get_title_glossary_series(d) == sid
