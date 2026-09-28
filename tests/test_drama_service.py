"""Tests for services/drama_service.py (Migration Slice 35)."""
import pytest

import db
from services import drama_service as ds
from services.service_errors import InvalidInputError, NotFoundError


def _count():
    return len(db.list_dramas())


def test_create_basic(isolated_db):
    d = ds.create_drama(source_language="ja", title_en="T", media_type="anime")
    assert d["title_en"] == "T" and d["source_language"] == "ja"
    assert d["media_type"] == "anime" and d["preset_defaults"] is None


@pytest.mark.parametrize("lang", [None, "", "en", "ZH"])
def test_create_bad_language(isolated_db, lang):
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language=lang)
    assert _count() == 0


def test_create_bad_media_type(isolated_db):
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language="zh", media_type="podcast")


def test_create_unknown_series_and_preset(isolated_db):
    with pytest.raises(NotFoundError):
        ds.create_drama(source_language="zh", series_id=99)
    with pytest.raises(NotFoundError):
        ds.create_drama(source_language="zh", preset_id=99)
    assert _count() == 0


def test_series_id_and_new_name_rejected(isolated_db):
    sid = db.get_or_create_series("S")
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language="zh", series_id=sid, new_series_name="X")
    assert _count() == 0


def test_blank_new_series_name_rejected(isolated_db):
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language="zh", new_series_name="   ")
    assert _count() == 0 and db.list_series() == []


def test_new_series_created_and_assigned(isolated_db):
    d = ds.create_drama(source_language="zh", new_series_name=" Saga ")
    series = db.list_series()
    assert [s["name"] for s in series] == ["Saga"]
    assert d["series_id"] == series[0]["id"]


def test_existing_series_assigned(isolated_db):
    sid = db.get_or_create_series("S")
    assert ds.create_drama(source_language="zh", series_id=sid)["series_id"] == sid


def test_preset_applied(isolated_db):
    pid = db.save_preset("P", translation_engine="gemini", engine_model="m",
                         style_preset="casual", locale="en-GB",
                         default_female_pronouns=True, include_genre_notes=False)
    d = ds.create_drama(source_language="ko", preset_id=pid)
    assert d["translation_engine"] == "gemini"
    assert d["preset_defaults"] == {
        "style_preset": "casual", "locale": "en-GB",
        "default_female_pronouns": True, "include_genre_notes": False}


def test_preset_without_engine_leaves_default(isolated_db):
    pid = db.save_preset("P", style_preset="casual")
    d = ds.create_drama(source_language="zh", preset_id=pid)
    assert d["translation_engine"] == db.get_drama(d["id"])["translation_engine"]
    assert d["preset_defaults"]["style_preset"] == "casual"


@pytest.mark.parametrize("key", ["status", "content_mode", "source_language",
                                 "audio_filename", "translation_engine",
                                 "personal_notes", "id; DROP TABLE dramas"])
def test_non_whitelisted_key_rejected(isolated_db, key):
    did = db.create_drama(title_en="A", source_language="zh")
    before = db.get_drama(did)
    with pytest.raises(InvalidInputError) as e:
        ds.update_drama_metadata(did, **{key: "evil-value"})
    assert "evil-value" not in str(e.value)
    assert db.get_drama(did) == before
    assert _count() == 1


def test_partial_update_touches_only_passed(isolated_db):
    did = db.create_drama(title_en="A", author="Au", source_language="zh")
    db.update_drama(did, updated_at="2000-01-01T00:00:00")
    before = db.get_drama(did)
    d = ds.update_drama_metadata(did, studio="Studio", publication_status="ongoing",
                                 chapter_count=5, episode_number=2, media_type="novel")
    assert d["studio"] == "Studio" and d["publication_status"] == "ongoing"
    assert d["chapter_count"] == 5 and d["episode_number"] == 2
    assert d["title_en"] == "A" and d["author"] == "Au"
    assert d["updated_at"] != before["updated_at"]


def test_none_means_not_passed_and_empty_call_is_noop(isolated_db):
    did = db.create_drama(title_en="A", source_language="zh")
    before = db.get_drama(did)
    assert ds.update_drama_metadata(did, title_en=None) == before
    assert ds.update_drama_metadata(did) == before


def test_clear_text_field_with_empty_string(isolated_db):
    did = db.create_drama(title_en="A", author="Au", source_language="zh")
    d = ds.update_drama_metadata(did, author="")
    assert d["author"] == "" and d["title_en"] == "A"


def test_zero_clears_int(isolated_db):
    did = db.create_drama(source_language="zh", chapter_count=4)
    assert ds.update_drama_metadata(did, chapter_count=0)["chapter_count"] is None


def test_update_unknown_drama(isolated_db):
    with pytest.raises(NotFoundError):
        ds.update_drama_metadata(42, title_en="x")
    assert _count() == 0


@pytest.mark.parametrize("field", ["chapter_count", "episode_number"])
@pytest.mark.parametrize("bad", ["3", 1.5, -1, True])
def test_int_type_errors(isolated_db, field, bad):
    did = db.create_drama(source_language="zh")
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, **{field: bad})


def test_text_type_error_and_bad_enums(isolated_db):
    did = db.create_drama(source_language="zh")
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, title_en=5)
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, publication_status="dead")
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, media_type="nope")


def test_update_series_id(isolated_db):
    did = db.create_drama(source_language="zh")
    with pytest.raises(NotFoundError):
        ds.update_drama_metadata(did, series_id=77)
    sid = db.get_or_create_series("S")
    assert ds.update_drama_metadata(did, series_id=sid)["series_id"] == sid


def test_detail_has_no_filesystem_path(isolated_db):
    d = ds.create_drama(source_language="zh", title_en="A")
    d = ds.update_drama_metadata(d["id"], summary="s")
    for v in d.values():
        if isinstance(v, str):
            assert db.LIBRARY_DIR not in v
