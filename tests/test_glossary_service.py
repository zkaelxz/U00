"""Tests for services/glossary_service.py (fully mocked/isolated)."""
import pytest

import translation_guide as tguide
from translate_engines import WORKFLOW_TIERS
from services import glossary_service as gs
from services.service_errors import (
    ConflictError, InvalidInputError, NotFoundError, UnsupportedOperationError,
)


def _drama(db, name="A", series=True):
    sid = db.get_or_create_series(name) if series else None
    did = db.create_drama(title_en=f"D-{name}", series_id=sid)
    return did, sid


FULL = {"term_original": "沈清疑", "term_translation": "Shen Qingyi", "notes": "hero",
        "category": "person_name", "policy": "keep_pinyin", "enforce_exact": True,
        "aliases": ["清疑", "Qing Yi"], "banned_translations": ["Shen Clear Doubt"]}


class TestNoSeries:
    def test_reads_empty_writes_unsupported(self, isolated_db):
        did, _ = _drama(isolated_db, series=False)
        assert gs.list_glossary_terms(did) == []
        assert gs.get_instructions(did) == {"project_instructions": "", "series_instructions": ""}
        with pytest.raises(UnsupportedOperationError):
            gs.upsert_glossary_term(did, FULL)
        with pytest.raises(UnsupportedOperationError):
            gs.set_series_instructions(did, "x")
        with pytest.raises(NotFoundError):
            gs.delete_glossary_term(did, 1, confirm=True)
        assert gs.set_project_instructions(did, "hi")["project_instructions"] == "hi"

    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            gs.list_glossary_terms(999)


class TestCrud:
    def test_round_trip_all_fields(self, isolated_db):
        did, _ = _drama(isolated_db)
        saved = gs.upsert_glossary_term(did, FULL)
        for k, v in FULL.items():
            assert saved[k] == v
        assert gs.list_glossary_terms(did) == [saved]

    def test_upsert_by_original_updates(self, isolated_db):
        did, _ = _drama(isolated_db)
        a = gs.upsert_glossary_term(did, FULL)
        b = gs.upsert_glossary_term(did, {**FULL, "term_translation": "Shen Q."})
        assert a["id"] == b["id"] and b["term_translation"] == "Shen Q."
        assert len(gs.list_glossary_terms(did)) == 1

    def test_update_by_id_renames_and_keeps_omitted(self, isolated_db):
        did, _ = _drama(isolated_db)
        a = gs.upsert_glossary_term(did, FULL)
        b = gs.upsert_glossary_term(did, {"id": a["id"], "term_original": "沈清"})
        assert b["id"] == a["id"] and b["term_original"] == "沈清"
        assert b["aliases"] == FULL["aliases"] and b["term_translation"] == "Shen Qingyi"
        assert len(gs.list_glossary_terms(did)) == 1

    def test_rename_onto_existing_conflicts(self, isolated_db):
        did, _ = _drama(isolated_db)
        a = gs.upsert_glossary_term(did, FULL)
        gs.upsert_glossary_term(did, {"term_original": "X", "term_translation": "Y"})
        with pytest.raises(ConflictError):
            gs.upsert_glossary_term(did, {"id": a["id"], "term_original": "X"})

    def test_clear_lists(self, isolated_db):
        did, _ = _drama(isolated_db)
        gs.upsert_glossary_term(did, FULL)
        out = gs.upsert_glossary_term(did, {**FULL, "aliases": [], "banned_translations": []})
        assert out["aliases"] == [] and out["banned_translations"] == []

    def test_delete_requires_confirm(self, isolated_db):
        did, _ = _drama(isolated_db)
        t = gs.upsert_glossary_term(did, FULL)
        with pytest.raises(InvalidInputError):
            gs.delete_glossary_term(did, t["id"])
        assert len(gs.list_glossary_terms(did)) == 1
        gs.delete_glossary_term(did, t["id"], confirm=True)
        assert gs.list_glossary_terms(did) == []
        with pytest.raises(NotFoundError):
            gs.delete_glossary_term(did, t["id"], confirm=True)


class TestValidation:
    @pytest.mark.parametrize("patch", [
        {"category": "nope"}, {"policy": "nope"}, {"enforce_exact": "yes"},
        {"aliases": "a|b"}, {"aliases": [""]}, {"aliases": ["a|b"]}, {"aliases": [1]},
        {"banned_translations": ["  "]}, {"term_original": ""}, {"term_translation": None},
        {"aliases": ["x"] * (gs.MAX_LIST_ITEMS + 1)},
        {"term_original": "x" * (gs.MAX_TERM_LEN + 1)},
        {"notes": "x" * (gs.MAX_NOTES_LEN + 1)},
        {"aliases": ["x" * (gs.MAX_ITEM_LEN + 1)]},
    ])
    def test_rejected(self, isolated_db, patch):
        did, _ = _drama(isolated_db)
        with pytest.raises(InvalidInputError):
            gs.upsert_glossary_term(did, {**FULL, **patch})
        assert gs.list_glossary_terms(did) == []

    def test_error_does_not_echo_user_text(self, isolated_db):
        did, _ = _drama(isolated_db)
        with pytest.raises(InvalidInputError) as e:
            gs.upsert_glossary_term(did, {**FULL, "category": "SECRETVALUE"})
        assert "SECRETVALUE" not in str(e.value)


class TestIsolation:
    def test_cross_series(self, isolated_db):
        da, _ = _drama(isolated_db, "A")
        db_, _ = _drama(isolated_db, "B")
        t = gs.upsert_glossary_term(da, FULL)
        assert gs.list_glossary_terms(db_) == []
        with pytest.raises(NotFoundError):
            gs.delete_glossary_term(db_, t["id"], confirm=True)
        with pytest.raises(NotFoundError):
            gs.upsert_glossary_term(db_, {"id": t["id"], "term_translation": "hacked"})
        assert gs.list_glossary_terms(da)[0]["term_translation"] == "Shen Qingyi"

    def test_same_series_dramas_share(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        d1 = isolated_db.create_drama(title_en="1", series_id=sid)
        d2 = isolated_db.create_drama(title_en="2", series_id=sid)
        gs.upsert_glossary_term(d1, FULL)
        assert len(gs.list_glossary_terms(d2)) == 1


class TestInstructions:
    def test_independent_round_trip(self, isolated_db):
        did, _ = _drama(isolated_db)
        gs.set_project_instructions(did, "proj")
        out = gs.set_series_instructions(did, "ser")
        assert out == {"project_instructions": "proj", "series_instructions": "ser"}
        assert gs.set_project_instructions(did, "proj2") == {
            "project_instructions": "proj2", "series_instructions": "ser"}
        assert gs.set_series_instructions(did, "") == {
            "project_instructions": "proj2", "series_instructions": ""}

    def test_series_instructions_shared_project_not(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        d1 = isolated_db.create_drama(title_en="1", series_id=sid)
        d2 = isolated_db.create_drama(title_en="2", series_id=sid)
        gs.set_series_instructions(d1, "shared")
        gs.set_project_instructions(d1, "mine")
        assert gs.get_instructions(d2) == {"project_instructions": "", "series_instructions": "shared"}

    def test_caps_and_types(self, isolated_db):
        did, _ = _drama(isolated_db)
        big = "x" * (gs.MAX_INSTRUCTIONS_LEN + 1)
        with pytest.raises(InvalidInputError):
            gs.set_project_instructions(did, big)
        with pytest.raises(InvalidInputError):
            gs.set_series_instructions(did, big)
        with pytest.raises(InvalidInputError):
            gs.set_project_instructions(did, 5)


class TestCatalogues:
    def test_match_real_constants(self):
        c = gs.get_catalogues()
        assert [p["key"] for p in c["style_presets"]] == list(tguide.STYLE_PRESETS)
        assert {p["key"]: p["label"] for p in c["style_presets"]} == {
            k: v["label"] for k, v in tguide.STYLE_PRESETS.items()}
        assert {x["key"]: x["label"] for x in c["term_categories"]} == tguide.TERM_CATEGORIES
        assert {x["key"]: x["label"] for x in c["term_policies"]} == {
            k: v["label"] for k, v in tguide.TERM_POLICIES.items()}
        assert [t["key"] for t in c["workflow_tiers"]] == list(WORKFLOW_TIERS)

    def test_no_secrets_or_paths(self, isolated_db):
        did, _ = _drama(isolated_db)
        gs.upsert_glossary_term(did, FULL)
        blob = repr(gs.get_catalogues()) + repr(gs.list_glossary_terms(did))
        for bad in ("api_key", "token", "secret", str(isolated_db.LIBRARY_DIR)):
            assert bad not in blob.lower() or bad == ""
        assert set(gs.get_catalogues()["workflow_tiers"][0]) == {
            "key", "label", "translation_engine", "engine_model", "reflect", "auto_qc"}
