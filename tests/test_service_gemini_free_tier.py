"""Parity audit (B2): services that build an LLM engine pass the saved
Settings "Gemini free tier" flag (and the saved Ollama URL) through to
translate_engines.get_engine, the same way translate_run_service does."""
import pytest

import translate_engines
from services import (discover_lookup_service, live_service, metadata_service,
                      narration_service, reader_service, restructure_service,
                      settings_service, translate_service)
from tests.saved_settings import patch_setting


class _Engine:
    supports_reference = True
    model = "m"


@pytest.fixture
def built(monkeypatch):
    calls = []
    patch_setting(monkeypatch, "gemini_free_tier", True)
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: "http://saved:11434" if k == "ollama_url" else "k")
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, *a, **kw: "k")

    def fake(name, key=None, model=None, **kw):
        calls.append((name, kw))
        return _Engine()
    monkeypatch.setattr(translate_engines, "get_engine", fake)
    return calls


BUILDERS = {
    "restructure": lambda: restructure_service._build_engine({}, "gemini", None),
    "reader": lambda: reader_service.llm_engine("gemini"),
    "live": lambda: live_service._build_engine("gemini", None),
    "discover_lookup": lambda: discover_lookup_service._build_engine("gemini"),
}


@pytest.mark.parametrize("which", sorted(BUILDERS))
def test_builder_passes_saved_free_tier(built, which):
    BUILDERS[which]()
    assert len(built) == 1 and built[0][0] == "gemini"
    assert built[0][1]["free_tier"] is True


def test_narration_job_passes_saved_free_tier(isolated_db, built, monkeypatch):
    did = isolated_db.create_drama(title_en="N", content_mode="novel_narration")
    monkeypatch.setattr(translate_engines, "tag_speakers_by_id", lambda *a, **k: {})
    narration_service._run_narration_job(f"narration_{did}", did, "一。", "gemini", "k", None)
    assert built[0][0] == "gemini" and built[0][1]["free_tier"] is True


def _autofill(isolated_db, monkeypatch, engine):
    monkeypatch.setattr(metadata_service.metadata_lookup, "extract_metadata_llm",
                        lambda text, eng: {})
    did = isolated_db.create_drama(title_en="M")
    metadata_service.autofill_suggestion(did, page_text="page", engine_name=engine)


def test_metadata_passes_saved_free_tier(isolated_db, built, monkeypatch):
    _autofill(isolated_db, monkeypatch, "gemini")
    assert built[0][1]["free_tier"] is True


def test_metadata_uses_saved_ollama_url(isolated_db, built, monkeypatch):
    _autofill(isolated_db, monkeypatch, "ollama")
    assert built[0][1]["base_url"] == "http://saved:11434"
