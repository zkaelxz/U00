"""Discover catalog service (Migration Slice 55). Mocked; no network or LLM."""
import pytest

import db
from services import discover_catalog_service as svc
from services.service_errors import ConflictError, InvalidInputError, NotFoundError


def _title(**kw):
    base = {"title_original": "女将军", "title_en": "The General", "author": "A",
            "language": "zh", "media_type": "audio_drama"}
    base.update(kw)
    return svc.create_title(base)


def test_create_list_filter(isolated_db):
    t = _title()
    assert t["source_name"] == "manual" and t["id"] >= 1
    _title(title_original="X", title_en="Ex", language="ja", media_type="novel")
    assert len(svc.list_titles()["titles"]) == 2
    assert svc.list_titles(q="General")["titles"][0]["id"] == t["id"]
    assert len(svc.list_titles(language="ja")["titles"]) == 1
    assert len(svc.list_titles(media_type="novel")["titles"]) == 1
    assert svc.list_titles(q="zzz")["total"] == 2


@pytest.mark.parametrize("bad", [
    {"id": 5}, {"created_at": "x"}, {"evil; DROP": "x"},
    {"title_original": ""}, {"title_original": "x" * 301},
    {"language": "en"}, {"media_type": "bogus"}, {"source_url": "file:///etc/passwd"},
    {"title_en": 5},
])
def test_create_rejects(isolated_db, bad):
    with pytest.raises(InvalidInputError):
        _title(**bad)
    assert db.list_known_titles() == []


def test_delete_needs_confirm_and_exists(isolated_db):
    t = _title()
    with pytest.raises(InvalidInputError):
        svc.delete_title(t["id"], confirm=False)
    assert len(db.list_known_titles()) == 1
    with pytest.raises(NotFoundError):
        svc.delete_title(9999, confirm=True)
    assert svc.delete_title(t["id"], confirm=True) == {"deleted": True, "id": t["id"]}
    assert db.list_known_titles() == []


def test_seed_idempotent(isolated_db):
    first = svc.seed_titles()
    assert first["added"] > 0
    second = svc.seed_titles()
    assert second["added"] == 0 and second["total"] == first["total"]


def test_import_creates_drama_and_dup_conflicts(isolated_db):
    t = _title()
    d = svc.import_to_library(t["id"])
    assert d["title_en"] == "The General" and d["title_zh"] == "女将军"
    with pytest.raises(ConflictError) as e:
        svc.import_to_library(t["id"])
    assert e.value.details == {"drama_id": d["id"]}
    assert len(db.list_dramas()) == 1


def test_import_unknown_and_media_fallback(isolated_db):
    with pytest.raises(NotFoundError):
        svc.import_to_library(42)
    g = _title(title_original="G", title_en="Game", media_type="game", language="ko")
    d = svc.import_to_library(g["id"])
    assert d["media_type"] == "other" and d["title_zh"] == ""


def test_import_validates_lengths(isolated_db):
    db.create_known_title(title_original="o", title_en="e" * 400, language="zh",
                          media_type="novel", source_name="manual")
    tid = db.list_known_titles()[0]["id"]
    with pytest.raises(InvalidInputError):
        svc.import_to_library(tid)
    assert db.list_dramas() == []


def test_platforms_and_links():
    assert svc.list_platforms()["platforms"]
    zh = svc.list_platforms(language="zh")["platforms"]
    assert zh and all(p["language"] == "zh" for p in zh)
    with pytest.raises(InvalidInputError):
        svc.list_platforms(language="xx")
    links = svc.search_links("女将军", "novel")["links"]
    assert links and all(l["url"].startswith("https://") for l in links)
    with pytest.raises(InvalidInputError):
        svc.search_links("  ")
    with pytest.raises(InvalidInputError):
        svc.search_links("x", "bogus")
