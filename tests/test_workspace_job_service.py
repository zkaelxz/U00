"""
Tests that services/workspace_job_service.py exports every expected job
function (a guard against a move or rename dropping one).
"""

import services.workspace_job_service as wjs


def test_every_moved_workspace_function_is_exported():
    for name in ("run_translate_job", "run_transcribe_job", "run_hardsub_ocr_job",
                "run_emotion_job", "run_sensevoice_job", "run_flag_job",
                "run_consistency_job", "run_translation_notes_job",
                "run_fix_flagged_lines_job"):
        assert callable(getattr(wjs, name, None)), f"{name} missing or not callable"


def test_every_moved_library_function_is_exported():
    for name in ("restore_library_backup", "run_bulk_series_translate_job"):
        assert callable(getattr(wjs, name, None)), f"{name} missing or not callable"


# --- Parity with translate_run_service (bulk + fix-flagged context) ---

import pytest

import background_jobs
from core import Line


def _stub_style_sources(monkeypatch):
    monkeypatch.setattr(wjs.db, "get_style_profile", lambda scope: {"profile": {"x": 1}})
    monkeypatch.setattr(wjs.adaptive_style, "profile_to_prompt_block", lambda p: "LEARNED-STYLE")
    monkeypatch.setattr(wjs.db, "load_emotions", lambda did: {0: "angry"})
    monkeypatch.setattr(wjs.emotion, "build_emotion_guidance", lambda emap, idxs: "EMOTION-GUIDE")


def _capture_bulk_start(isolated_db, monkeypatch, engine, content_mode):
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama", content_mode=content_mode,
                                   status="aligned", translation_engine=engine)
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句")])
    _stub_style_sources(monkeypatch)
    summary = object()
    seen = {}

    def fake_get_engine(name, key, *a, **k):
        if name == "ollama" and key is None:
            seen["summary_base_url"] = k.get("base_url")
            return summary
        return object()
    monkeypatch.setattr(wjs.translate_engines, "get_engine", fake_get_engine)

    def fake_start(job_id, fn, *args, **kwargs):
        seen.update(args=args, kwargs=kwargs)
        return False
    monkeypatch.setattr(wjs.background_jobs, "start_job", fake_start)
    monkeypatch.setattr(wjs.db, "get_month_spend", lambda: 0.0)
    wjs.run_bulk_series_translate_job("bulk_parity", [did], {engine: "k"}, monthly_cap=5.0,
                                      ollama_base_url="http://gpu-box:11434")
    background_jobs.clear_job("bulk_parity")
    return seen, summary


def test_bulk_uses_novel_defaults_custom_notes_and_summary_engine(isolated_db, monkeypatch):
    seen, summary = _capture_bulk_start(isolated_db, monkeypatch, "claude", "novel_narration")
    args, kwargs = seen["args"], seen["kwargs"]
    style_guidelines, context_window = args[10], args[13]
    assert context_window == 10
    assert kwargs["context_window_ahead"] == 6 and kwargs["batch_size"] == 30
    assert "LEARNED-STYLE" in style_guidelines and "EMOTION-GUIDE" in style_guidelines
    assert kwargs["summary_engine"] is summary and kwargs["summary_engine_choice"] == "ollama"
    assert seen["summary_base_url"] == "http://gpu-box:11434"


def test_bulk_drama_done_with_batch_errors_is_not_translated(isolated_db, monkeypatch):
    """run_translate_job catches batch errors (e.g. a revoked key), so the
    per-drama job ends "done" with errors; that is not a translated drama."""
    import types
    from services import jobs_service
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="aligned",
                                   translation_engine="fake")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句")])

    class Revoked:
        name = "fake"
        supports_reference = False
        last_usage = {}

        def translate_batch(self, zh, context):
            raise RuntimeError("401 invalid api key sk-ABCDEFGHIJKLMNOP12345")
    monkeypatch.setattr(wjs.translate_engines, "get_engine", lambda *a, **k: Revoked())
    real_time = wjs.time
    stub = types.SimpleNamespace(**{n: getattr(real_time, n) for n in dir(real_time)
                                    if not n.startswith("__")})
    stub.sleep = lambda s: real_time.sleep(0.02)
    monkeypatch.setattr(wjs, "time", stub)
    background_jobs.clear_job(f"translate_{did}")
    background_jobs.clear_job("bulk_revoked")
    captured = {}
    real_set = background_jobs.set_result

    def spy(jid, res):
        if jid == "bulk_revoked":
            captured["result"] = res
        real_set(jid, res)
    monkeypatch.setattr(background_jobs, "set_result", spy)
    wjs.run_bulk_series_translate_job("bulk_revoked", [did], {})
    result = captured["result"]
    assert result["translated"] == [] and result["partial"] == {did: "batch errors"}
    projected = jobs_service.project_result(result)
    assert "sk-ABCDEFGHIJKLMNOP12345" not in str(projected)
    assert jobs_service.derive_outcome("done", None, projected)[0] == "partial"
    background_jobs.clear_job(f"translate_{did}")
    background_jobs.clear_job("bulk_revoked")


@pytest.mark.parametrize("engine", ["claude", "deepseek"])
def test_bulk_cap_engines_match_translate_run(isolated_db, monkeypatch, engine):
    seen, _ = _capture_bulk_start(isolated_db, monkeypatch, engine, "audio_drama")
    assert seen["kwargs"]["cost_cap_usd"] == pytest.approx(5.0)
    assert seen["args"][13] == 6 and seen["kwargs"]["batch_size"] == 20


def test_fix_flagged_retranslate_gets_full_context(isolated_db, monkeypatch):
    sid = isolated_db.get_or_create_series("S")
    isolated_db.upsert_glossary_term(sid, "苏杉", "Su Shan", enforce_exact=True)
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="translated", series_id=sid)
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="苏杉来了", en="x",
                                      speaker="SPEAKER_00", flag="bad", flag_note="n")])
    isolated_db.upsert_character(did, "SPEAKER_00", character_name="Su Shan")
    _stub_style_sources(monkeypatch)

    class Engine:
        name = "fake"
        context = None

        def translate_batch(self, zh, context):
            Engine.context = context
            return ["Su Shan is here."]

    import core
    lines = core.lines_from_rows(isolated_db.load_lines(did))
    background_jobs.clear_job("fix_ctx")
    wjs.run_fix_flagged_lines_job("fix_ctx", did, lines, None, "small", False, "zh",
                                  Engine(), "fake", locale="en-GB")
    ctx = Engine.context
    assert ctx["locale"] == "en-GB" and ctx["source_language"] == "zh"
    assert "苏杉" in str(ctx["glossary_terms"])
    assert "LEARNED-STYLE" in ctx["style_guidelines"]
    # Whole-drama emotion guidance is keyed "Line N"; the single-line
    # prompt numbers its line "1.", so it is left out here.
    assert "EMOTION-GUIDE" not in ctx["style_guidelines"]
    assert ctx["speaker_labels"] == ["Su Shan"]
    assert isolated_db.load_lines(did)[0]["en"] == "Su Shan is here."
    background_jobs.clear_job("fix_ctx")


def _spy_style_context(monkeypatch):
    calls = []
    real = wjs.build_run_style_context

    def spy(*a, **k):
        calls.append(k)
        return real(*a, **k)
    monkeypatch.setattr(wjs, "build_run_style_context", spy)
    return calls


@pytest.mark.parametrize("genre,pronouns", [(True, False), (False, True)])
def test_fix_flagged_passes_translate_toggles(isolated_db, monkeypatch, genre, pronouns):
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="translated")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句", en="x", flag="bad")])
    calls = _spy_style_context(monkeypatch)

    class Engine:
        name = "fake"

        def translate_batch(self, zh, context):
            return ["ok"]
    import core
    lines = core.lines_from_rows(isolated_db.load_lines(did))
    background_jobs.clear_job("fix_toggles")
    wjs.run_fix_flagged_lines_job("fix_toggles", did, lines, None, "small", False, "zh",
                                  Engine(), "fake", include_genre_notes=genre,
                                  default_female_pronouns=pronouns)
    assert calls[0]["include_genre_notes"] is genre
    assert calls[0]["default_female_pronouns"] is pronouns
    background_jobs.clear_job("fix_toggles")


def test_bulk_series_passes_translate_toggles(isolated_db, monkeypatch):
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="aligned",
                                   translation_engine="claude")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句")])
    calls = _spy_style_context(monkeypatch)
    monkeypatch.setattr(wjs.translate_engines, "get_engine", lambda *a, **k: object())
    monkeypatch.setattr(wjs.background_jobs, "start_job", lambda *a, **k: False)
    monkeypatch.setattr(wjs.db, "get_month_spend", lambda: 0.0)
    wjs.run_bulk_series_translate_job("bulk_toggles", [did], {"claude": "k"},
                                      include_genre_notes=False, default_female_pronouns=True)
    background_jobs.clear_job("bulk_toggles")
    assert calls[0]["include_genre_notes"] is False
    assert calls[0]["default_female_pronouns"] is True
