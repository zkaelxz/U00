"""
Tests that services/workspace_job_service.py and its audio and review
siblings export every expected job function (a guard against a move or rename dropping one).
"""

import services.workspace_audio_job_service as wajs
from engine_backends import engine_registry
import services.workspace_job_service as wjs
from services import translate_run_service
import services.workspace_review_job_service as wrjs
from tests.saved_settings import patch_setting


def test_every_moved_workspace_function_is_exported():
    for mod, names in ((wjs, ("run_translate_job",)),
                       (wajs, ("run_sensevoice_job",)),
                       (wrjs, ("run_emotion_job", "run_flag_job", "run_consistency_job",
                               "run_translation_notes_job", "run_fix_flagged_lines_job"))):
        for name in names:
            assert callable(getattr(mod, name, None)), f"{name} missing or not callable"


def test_every_moved_library_function_is_exported():
    from services import library_restore_service as lrs
    assert callable(getattr(wjs, "run_bulk_series_translate_job", None))
    assert callable(getattr(lrs, "restore_library_backup", None))


# --- Parity with translate_run_service (bulk + fix-flagged context) ---

import pytest

import background_jobs
from core import Line


def _stub_style_sources(monkeypatch):
    monkeypatch.setattr(wjs.db, "get_style_profile", lambda scope: {"profile": {"x": 1}})
    monkeypatch.setattr(wjs.adaptive_style, "profile_to_prompt_block", lambda p: "LEARNED-STYLE")
    monkeypatch.setattr(wjs.db, "load_emotions", lambda did: {0: "angry"})
    monkeypatch.setattr(wjs.emotion, "build_emotion_guidance", lambda emap, idxs: "EMOTION-GUIDE")


def _capture_bulk_start(isolated_db, monkeypatch, engine, content_mode, translate_start_json=None):
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama", content_mode=content_mode,
                                   status="aligned", translation_engine=engine,
                                   translate_start_json=translate_start_json)
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句")])
    _stub_style_sources(monkeypatch)
    summary = object()
    seen = {}

    def fake_get_engine(name, key, *a, **k):
        if key == "summary-key":
            return summary
        # The real class without its constructor (no SDK import, no network).
        built = engine_registry.ENGINES[name].__new__(engine_registry.ENGINES[name])
        if a and a[0]:
            built.model = a[0]
        return built
    monkeypatch.setattr(engine_registry, "get_engine", fake_get_engine)
    monkeypatch.setattr(translate_run_service.translate_service, "resolve_api_key", lambda n: "summary-key")

    def fake_start(job_id, fn, *args, **kwargs):
        seen.update(args=args, kwargs=kwargs)
        return False
    monkeypatch.setattr(wjs.job_launch, "start_job", fake_start)
    monkeypatch.setattr(wjs.db, "get_month_spend", lambda: 0.0)
    wjs.run_bulk_series_translate_job("bulk_parity", [did], {engine: "k"}, monthly_cap=5.0,
                                      ollama_base_url="http://gpu-box:11434")
    background_jobs.clear_job("bulk_parity")
    return seen, summary


def test_bulk_uses_novel_defaults_custom_notes_and_summary_engine(isolated_db, monkeypatch):
    seen, summary = _capture_bulk_start(isolated_db, monkeypatch, "claude", "novel_narration")
    args, kwargs = seen["args"], seen["kwargs"]
    style_guidelines, context_window = args[10], args[13]
    assert context_window == 30
    assert kwargs["context_window_ahead"] == 15 and kwargs["batch_size"] == 30
    assert "LEARNED-STYLE" in style_guidelines and "EMOTION-GUIDE" in style_guidelines
    assert kwargs["summary_engine"] is summary and kwargs["summary_engine_choice"] == "claude"


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
    monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: Revoked())
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
    # Claude lost lines at 60 per request, so both engines keep 30.
    assert seen["args"][13] == 10 and seen["kwargs"]["batch_size"] == (90 if engine == "deepseek" else 30)


# A local tag: the Library batch never picks up a remembered cloud tag.
GEMMA_LOCAL = "gemma4:31b-cloud"


def test_bulk_novel_keeps_the_character_budget_unless_a_size_was_chosen(isolated_db, monkeypatch):
    seen, _ = _capture_bulk_start(isolated_db, monkeypatch, "claude", "novel_narration")
    assert seen["kwargs"]["novel_char_budget"] is True
    seen, _ = _capture_bulk_start(isolated_db, monkeypatch, "claude", "novel_narration",
                                  '{"batch_size": 12}')
    assert seen["kwargs"]["batch_size"] == 12 and seen["kwargs"]["novel_char_budget"] is False


def _bulk_sizes(seen):
    return seen["args"][13], seen["kwargs"]["context_window_ahead"], seen["kwargs"]["batch_size"]


@pytest.mark.parametrize("remembered", [
    '{"engine_model": "%s"}' % GEMMA_LOCAL,
    # The form used to echo its defaults back; those are not the person's choice.
    '{"engine_model": "%s", "context_window": 10, "context_window_ahead": 6, "batch_size": 30}'
    % GEMMA_LOCAL])
def test_bulk_gemma_stream_title_gets_the_plain_prompt_and_its_sizes(isolated_db, monkeypatch,
                                                                     remembered):
    from engine_backends import stream_recipe
    seen, _ = _capture_bulk_start(isolated_db, monkeypatch, "ollama", "streamer_vod", remembered)
    style_guidelines = seen["args"][10]
    assert stream_recipe.is_active(style_guidelines)
    assert "LEARNED-STYLE" not in style_guidelines and "EMOTION-GUIDE" not in style_guidelines
    assert _bulk_sizes(seen) == (0, 0, engine_registry.PLAIN_STREAM_BATCH_SIZE)
    assert seen["kwargs"]["run_settings"]["batch_size"] == engine_registry.PLAIN_STREAM_BATCH_SIZE


def test_bulk_gemma_stream_title_keeps_sizes_the_person_chose(isolated_db, monkeypatch):
    from engine_backends import stream_recipe
    seen, _ = _capture_bulk_start(
        isolated_db, monkeypatch, "ollama", "streamer_vod",
        '{"engine_model": "%s", "context_window": 3, "context_window_ahead": 2, "batch_size": 12}'
        % GEMMA_LOCAL)
    assert stream_recipe.is_active(seen["args"][10])
    assert _bulk_sizes(seen) == (3, 2, 12)




def test_bulk_stream_title_with_the_plain_prompt_off_keeps_remembered_sizes(isolated_db, monkeypatch):
    from engine_backends import stream_recipe
    patch_setting(monkeypatch, "stream_plain_prompt", False)
    seen, _ = _capture_bulk_start(
        isolated_db, monkeypatch, "ollama", "streamer_vod",
        '{"engine_model": "%s", "context_window": 10, "context_window_ahead": 6, "batch_size": 30}'
        % GEMMA_LOCAL)
    assert not stream_recipe.is_active(seen["args"][10])
    assert _bulk_sizes(seen) == (10, 6, 30)


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
    wrjs.run_fix_flagged_lines_job("fix_ctx", did, lines, None, "small", False, "zh",
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


def _spy_style_context(monkeypatch, module=wjs):
    calls = []
    real = module.build_run_style_context

    def spy(*a, **k):
        calls.append(k)
        return real(*a, **k)
    monkeypatch.setattr(module, "build_run_style_context", spy)
    return calls


@pytest.mark.parametrize("genre,pronouns", [(True, False), (False, True)])
def test_fix_flagged_passes_translate_toggles(isolated_db, monkeypatch, genre, pronouns):
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="translated")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句", en="x", flag="bad")])
    calls = _spy_style_context(monkeypatch, wrjs)

    class Engine:
        name = "fake"

        def translate_batch(self, zh, context):
            return ["ok"]
    import core
    lines = core.lines_from_rows(isolated_db.load_lines(did))
    background_jobs.clear_job("fix_toggles")
    wrjs.run_fix_flagged_lines_job("fix_toggles", did, lines, None, "small", False, "zh",
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
    monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: object())
    monkeypatch.setattr(wjs.job_launch, "start_job", lambda *a, **k: False)
    monkeypatch.setattr(wjs.db, "get_month_spend", lambda: 0.0)
    wjs.run_bulk_series_translate_job("bulk_toggles", [did], {"claude": "k"}, monthly_cap=50.0,
                                      include_genre_notes=False, default_female_pronouns=True)
    background_jobs.clear_job("bulk_toggles")
    assert calls[0]["include_genre_notes"] is False
    assert calls[0]["default_female_pronouns"] is True


def test_fix_flagged_reads_each_lines_language(isolated_db, monkeypatch):
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="translated",
                                   source_language="ja")
    isolated_db.save_lines(did, [
        Line(idx=0, start=0, end=1, zh="안녕", en="x", flag="bad", flag_note="n", lang="ko"),
        Line(idx=1, start=1, end=2, zh="hello", en="", flag="bad", flag_note="n", lang="en"),
        Line(idx=2, start=2, end=3, zh="やあ", en="y", flag="bad", flag_note="n")])
    _stub_style_sources(monkeypatch)
    calls = []

    class Engine:
        name = "fake"

        def translate_batch(self, zh, context):
            calls.append((list(zh), context.get("line_languages")))
            return ["T"] * len(zh)

    import core
    lines = core.lines_from_rows(isolated_db.load_lines(did))
    background_jobs.clear_job("fix_lang")
    wrjs.run_fix_flagged_lines_job("fix_lang", did, lines, None, "small", False, "ja",
                                  Engine(), "fake")
    assert calls == [(["안녕", "やあ"], ["ko", None])]
    rows = {r["zh"]: r for r in isolated_db.load_lines(did)}
    assert rows["hello"]["en"] == "hello" and rows["hello"]["flag"] is None
    background_jobs.clear_job("fix_lang")


def test_bulk_passes_the_settings_default_style_note(isolated_db, monkeypatch):
    patch_setting(monkeypatch, "default_style_note", "Keep honorifics.")
    seen, _ = _capture_bulk_start(isolated_db, monkeypatch, "claude", "audio_drama")
    assert seen["args"][5] == "Keep honorifics."


def test_bulk_skips_a_title_that_is_being_deleted_and_goes_on(isolated_db, monkeypatch):
    """A start refused because the title is held (deleted or cleaned up right
    now) is that title's skip, not the end of the whole bulk run."""
    from jobs import job_launch
    held = isolated_db.create_drama(title_en="Held", media_type="audio_drama",
                                    content_mode="audio_drama", status="aligned",
                                    translation_engine="claude")
    free = isolated_db.create_drama(title_en="Free", media_type="audio_drama",
                                    content_mode="audio_drama", status="aligned",
                                    translation_engine="claude")
    for did in (held, free):
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句")])
    _stub_style_sources(monkeypatch)
    monkeypatch.setattr(engine_registry, "get_engine", lambda name, *a, **k:
                        engine_registry.ENGINES[name].__new__(engine_registry.ENGINES[name]))
    monkeypatch.setattr(wjs.db, "get_month_spend", lambda: 0.0)
    real_start = job_launch.start_job
    started = []

    def start(job_id, fn, *args, **kwargs):
        if job_id == f"translate_{held}":
            return real_start(job_id, lambda: None)   # refused by the hold below
        started.append(job_id)
        return False
    monkeypatch.setattr(wjs.job_launch, "start_job", start)
    captured = {}
    real_set = background_jobs.set_result
    monkeypatch.setattr(background_jobs, "set_result", lambda jid, res: (
        captured.__setitem__("result", res) if jid == "bulk_held" else None, real_set(jid, res)))
    with job_launch.hold_title(held, "busy"):
        wjs.run_bulk_series_translate_job("bulk_held", [held, free], {"claude": "k"},
                                          monthly_cap=5.0)
    background_jobs.clear_job("bulk_held")
    assert started == [f"translate_{free}"]
    assert held in captured["result"]["skipped_running"]


def test_run_style_context_carries_character_voice_notes(monkeypatch):
    _stub_style_sources(monkeypatch)
    monkeypatch.setattr(wjs.db, "list_series_characters",
                        lambda sid: [{"character_name": "Su Shan", "notes": "blunt, short clauses"}])
    monkeypatch.setattr(wjs.db, "list_characters_with_series_names", lambda did: [])
    _, guidelines, _ = wjs.build_run_style_context(1, {"series_id": 3}, [], "audio_drama")
    assert "CHARACTER VOICE NOTES" in guidelines and "Su Shan: blunt, short clauses" in guidelines
    monkeypatch.setattr(wjs.db, "list_series_characters", lambda sid: [])
    _, guidelines, _ = wjs.build_run_style_context(1, {"series_id": 3}, [], "audio_drama")
    assert "CHARACTER VOICE NOTES" not in guidelines


def test_run_style_context_memory_examples_are_opt_in_and_never_the_runs_own_answers(monkeypatch):
    from core import Line
    _stub_style_sources(monkeypatch)
    monkeypatch.setattr(wjs.db, "list_characters_with_series_names", lambda did: [])
    monkeypatch.setattr(wjs.db, "list_series_characters", lambda sid: [])
    monkeypatch.setattr(wjs.db, "list_translation_memory", lambda sid: [
        {"id": 1, "source_text": "你放心吧我一定会好好照顾她的", "translation": "Leave her to me.", "use_count": 3},
        {"id": 2, "source_text": "今天天气很好", "translation": "Nice weather today.", "use_count": 1}])
    lines = [Line(idx=0, start=0.0, end=1.0, zh="你放心吧我一定会好好照顾她的！", en=""),
             Line(idx=1, start=1.0, end=2.0, zh="今天天气很好", en="Nice weather today.")]
    _, off, _ = wjs.build_run_style_context(1, {"series_id": 3}, lines, "audio_drama")
    assert "APPROVED EXAMPLES" not in off
    _, on, _ = wjs.build_run_style_context(1, {"series_id": 3}, lines, "audio_drama", memory_examples=True)
    # Both entries are a line of the run (or one punctuation mark off it): never shown back to it.
    assert "APPROVED EXAMPLES" not in on
    _, other, _ = wjs.build_run_style_context(
        1, {"series_id": 3}, [Line(idx=0, start=0.0, end=1.0, zh="你放心吧我一定会照顾好她的呢", en="")],
        "audio_drama", memory_examples=True)
    assert "你放心吧我一定会好好照顾她的 → Leave her to me." in other and "今天天气很好" not in other
    _, noseries, _ = wjs.build_run_style_context(
        1, {}, [Line(idx=0, start=0.0, end=1.0, zh="你放心吧我一定会照顾好她的呢", en="")], "audio_drama", memory_examples=True)
    assert "APPROVED EXAMPLES" not in noseries


def test_bulk_title_cancelled_on_its_own_is_not_counted_translated(isolated_db):
    """Cancelling one title's translate job (Jobs page, Workspace) while the
    batch goes on leaves it untranslated; the batch must not count it."""
    from jobs import job_table
    did = isolated_db.create_drama(title_en="D", status="aligned")
    job_id = f"translate_{did}"
    with job_table._lock:
        job_table._jobs[job_id] = {"status": "cancelled", "result": {"errors": []}, "error": None}
    try:
        results = {"translated": [], "errors": {}, "partial": {}}
        wjs._record_bulk_outcome(results, did, job_id)
        assert results == {"translated": [], "errors": {}, "partial": {did: "cancelled"}}
        # Fully translated before the cancel landed: it counts.
        isolated_db.update_drama(did, status="translated")
        results = {"translated": [], "errors": {}, "partial": {}}
        wjs._record_bulk_outcome(results, did, job_id)
        assert results["translated"] == [did]
    finally:
        background_jobs.clear_job(job_id)


def test_the_alignment_judge_stops_once_the_monthly_cap_is_reached(monkeypatch):
    asked = {}
    monkeypatch.setattr(wjs.settings_service, "alignment_embedder",
                        lambda should_stop=None, on_progress=None: (lambda texts: None))
    monkeypatch.setattr(wjs.alignment_judge, "judge_for",
                        lambda engine, usage_cb=None, should_stop=None, **kw: asked.setdefault("stop", should_stop))
    monkeypatch.setattr(wjs, "_usage_logger", lambda *a: None)
    month_cap = {"hit": False}
    monkeypatch.setattr(wjs, "_month_cap_check", lambda choice, engine: lambda: month_cap["hit"])
    monkeypatch.setattr(wjs.background_jobs, "is_cancel_requested", lambda job_id: False)
    check = {}
    wjs._alignment_args(1, object(), "claude", "job1", {}, check)
    assert asked["stop"]() is False
    month_cap["hit"] = True
    assert asked["stop"]() is True
    assert "cancelled" not in check  # a cap stop is not the user's cancel


def test_the_source_match_check_reports_its_progress_and_notes_a_cancel(monkeypatch):
    got, messages = {}, []
    monkeypatch.setattr(wjs.settings_service, "alignment_embedder",
                        lambda should_stop=None, on_progress=None:
                        got.update(embed_stop=should_stop, embed_progress=on_progress) or (lambda texts: None))
    monkeypatch.setattr(wjs.alignment_judge, "judge_for",
                        lambda engine, usage_cb=None, should_stop=None, on_progress=None, **kw:
                        got.update(judge_progress=on_progress))
    monkeypatch.setattr(wjs, "_usage_logger", lambda *a: None)
    monkeypatch.setattr(wjs, "_month_cap_check", lambda choice, engine: None)
    monkeypatch.setattr(wjs.background_jobs, "update_progress", lambda job_id, frac, msg: messages.append(msg))
    cancel = {"on": False}
    monkeypatch.setattr(wjs.background_jobs, "is_cancel_requested", lambda job_id: cancel["on"])
    check = {}
    wjs._alignment_args(1, object(), "claude", "job1", {}, check)
    got["embed_progress"](32, 80)  # 40 lines: their sources, then their English
    got["judge_progress"](15, 21)
    assert messages == ["Checking source match: 16 of 40 lines",
                        "Checking source match: 15 of 21 lines (engine check)"]
    assert got["embed_stop"]() is False and "cancelled" not in check
    cancel["on"] = True
    assert got["embed_stop"]() is True and check["cancelled"] is True


def _judge_args(monkeypatch, cost_cap_usd, run_spend, month_left=None):
    """_alignment_args with the judge_for arguments captured and no database."""
    asked = {}
    monkeypatch.setattr(wjs.settings_service, "alignment_embedder", lambda should_stop=None, on_progress=None: (lambda texts: None))
    monkeypatch.setattr(wjs.alignment_judge, "judge_for",
                        lambda engine, usage_cb=None, should_stop=None, fits_budget=None, **kw:
                        asked.update(usage=usage_cb, stop=should_stop, fits=fits_budget))
    monkeypatch.setattr(wjs, "_usage_logger", lambda *a: (lambda *args: None))
    monkeypatch.setattr(wjs, "_month_cap_check", lambda choice, engine: None)
    monkeypatch.setattr(wjs, "_month_cap_left", lambda choice, engine: month_left)
    monkeypatch.setattr(wjs.background_jobs, "is_cancel_requested", lambda job_id: False)
    budget = wjs._JudgeBudget()
    budget.spend = run_spend
    wjs._alignment_args(1, object(), "claude", "job1", {}, {}, cost_cap_usd, budget)
    return asked, budget


def test_the_alignment_judge_sends_no_request_that_would_pass_the_runs_own_cap(monkeypatch):
    asked, budget = _judge_args(monkeypatch, cost_cap_usd=0.10, run_spend=[0.06])
    assert asked["fits"](0.03) is True and not budget.capped
    assert asked["fits"](0.05) is False
    assert budget.capped is True
    # Once stopped by the cap it stays stopped, and the finished job is told.
    assert asked["fits"](0.0) is False and asked["stop"]() is True


def test_the_alignment_judges_own_spend_counts_against_the_runs_cap(monkeypatch):
    spend = [0.05]
    monkeypatch.setattr(wjs.pricing, "estimate_cost_for_engine", lambda engine, inp, out, *a: inp / 1000)
    asked, budget = _judge_args(monkeypatch, cost_cap_usd=0.10, run_spend=spend)
    asked["usage"](40, 0)
    assert spend == [0.05, 0.04]
    assert asked["fits"](0.02) is False and budget.capped


def test_the_alignment_judge_also_prices_against_whats_left_of_the_month(monkeypatch):
    asked, budget = _judge_args(monkeypatch, cost_cap_usd=None, run_spend=[], month_left=lambda: 0.01)
    assert asked["fits"](0.005) is True
    assert asked["fits"](0.02) is False and budget.capped


def test_without_a_cap_the_alignment_judge_is_never_priced_out(monkeypatch):
    asked, budget = _judge_args(monkeypatch, cost_cap_usd=None, run_spend=[])
    assert asked["fits"](99.0) is True and not budget.capped


def test_a_translate_run_whose_cap_stops_the_judge_says_so_when_it_finishes(isolated_db, monkeypatch):
    """The batches spend 9 of the run's 10 cents; a judge request priced at 2 cents is not sent."""
    did = isolated_db.create_drama(title_en="Judge cap")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    lines = isolated_db.load_line_objects(did)
    sent, results = [], {}

    class Engine:
        supports_reference = True
        model = "fake-model"

    def fake_translate(ls, engine, **kw):
        ls[0].en = "hello"
        kw["usage_cb"](900, 0)
        return ls, []

    def fake_finish(*a, judge=None, **k):
        results["verdicts"] = judge([{"id": 0, "zh": "你好", "en": "hello"}])
        return True

    # The batch's usage has no output tokens here; a judge request is priced with a full reply.
    monkeypatch.setattr(wjs.pricing, "estimate_cost_for_engine",
                        lambda engine, inp, out, *rest: 0.09 if out == 0 else 0.02)
    monkeypatch.setattr(wjs.alignment_judge.llm_tasks, "call_llm_json", lambda *a, **k: sent.append(1) or "{}")
    monkeypatch.setattr(wjs.settings_service, "alignment_embedder", lambda should_stop=None, on_progress=None: (lambda texts: None))
    monkeypatch.setattr(wjs, "_month_cap_left", lambda choice, engine: None)
    monkeypatch.setattr(wjs.translate_pipeline, "translate_lines_with_engine", fake_translate)
    monkeypatch.setattr(wjs.translation_finish, "finish_translation_run", fake_finish)
    monkeypatch.setattr(wjs.background_jobs, "set_result", lambda job_id, result, **k: results.update(result=result))
    wjs.run_translate_job("translate_judge_cap", did, lines, Engine(), {}, "", "", False, "en-US",
                          [], "", "claude", "default", cost_cap_usd=0.10)
    assert sent == [] and results["verdicts"] == {}
    assert results["result"]["judge_skipped"] == wjs.JUDGE_CAP_SKIPPED


def test_the_alignment_judge_is_told_the_titles_source_language(monkeypatch):
    got = {}
    monkeypatch.setattr(wjs.settings_service, "alignment_embedder", lambda should_stop=None, on_progress=None: (lambda texts: None))
    monkeypatch.setattr(wjs.alignment_judge, "judge_for",
                        lambda engine, usage_cb=None, source_language=None, **kw: got.update(lang=source_language))
    monkeypatch.setattr(wjs, "_usage_logger", lambda *a: None)
    monkeypatch.setattr(wjs, "_month_cap_check", lambda choice, engine: None)
    monkeypatch.setattr(wjs, "_month_cap_left", lambda choice, engine: None)
    monkeypatch.setattr(wjs.background_jobs, "is_cancel_requested", lambda job_id: False)
    wjs._alignment_args(1, object(), "claude", "job1", {}, {}, drama_meta={"source_language": "ko"})
    assert got["lang"] == "ko"


@pytest.mark.parametrize("embedder_answers, said", [(False, True), (True, False)])
def test_a_translate_run_says_when_the_source_match_check_could_not_run(
        isolated_db, monkeypatch, embedder_answers, said):
    did = isolated_db.create_drama(title_en="No bge-m3")
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    lines = isolated_db.load_line_objects(did)
    results = {}

    class Engine:
        supports_reference = True
        model = "fake-model"

    def fake_translate(ls, engine, **kw):
        ls[0].en = "hello"
        return ls, []

    def fake_finish(*a, embed_texts=None, **k):
        embed_texts(["你好"])
        return True

    monkeypatch.setattr(wjs.settings_service, "alignment_embedder",
                        lambda should_stop=None, on_progress=None:
                        (lambda texts: [[1.0]] if embedder_answers else None))
    monkeypatch.setattr(wjs.alignment_judge, "judge_for",
                        lambda *a, source_language=None, **k: results.update(language=source_language))
    monkeypatch.setattr(wjs, "_month_cap_left", lambda choice, engine: None)
    monkeypatch.setattr(wjs.translate_pipeline, "translate_lines_with_engine", fake_translate)
    monkeypatch.setattr(wjs.translation_finish, "finish_translation_run", fake_finish)
    monkeypatch.setattr(wjs.background_jobs, "set_result", lambda job_id, result, **k: results.update(result=result))
    wjs.run_translate_job("translate_no_bge", did, lines, Engine(), {"source_language": "ja"}, "", "", False,
                          "en-US", [], "", "claude", "default")
    assert ("source_match_skipped" in results["result"]) is said
    assert results["language"] == "ja"


def test_the_source_match_embedding_waits_for_a_transcription_to_finish(monkeypatch):
    got, order = {}, []
    monkeypatch.setattr(wjs.settings_service, "alignment_embedder",
                        lambda should_stop=None, on_progress=None: (lambda texts: order.append("embed") or [[1.0]]))
    monkeypatch.setattr(wjs.alignment_judge, "judge_for", lambda *a, **kw: None)
    monkeypatch.setattr(wjs, "_usage_logger", lambda *a: None)
    monkeypatch.setattr(wjs, "_month_cap_check", lambda choice, engine: None)
    monkeypatch.setattr(wjs.background_jobs, "is_cancel_requested", lambda job_id: False)
    monkeypatch.setattr(wjs.gpu_slots, "wait_until_no_ui_job",
                        lambda stop, exclude_holder=None, on_wait=None: order.append(("wait", exclude_holder)) or True)
    args = wjs._alignment_args(1, object(), "claude", "job1", {}, {})
    args["embed_texts"](["a"])
    args["embed_texts"](["b"])
    assert order == [("wait", "ui:job1"), "embed", "embed"]


def test_a_cancel_while_waiting_for_the_transcription_ends_the_check_without_an_error(monkeypatch):
    monkeypatch.setattr(wjs.settings_service, "alignment_embedder",
                        lambda should_stop=None, on_progress=None: (lambda texts: [[1.0]]))
    monkeypatch.setattr(wjs.alignment_judge, "judge_for", lambda *a, **kw: None)
    monkeypatch.setattr(wjs, "_usage_logger", lambda *a: None)
    monkeypatch.setattr(wjs, "_month_cap_check", lambda choice, engine: None)
    monkeypatch.setattr(wjs.background_jobs, "is_cancel_requested", lambda job_id: True)
    monkeypatch.setattr(wjs.gpu_slots, "wait_until_no_ui_job", lambda *a, **kw: False)
    check = {}
    assert wjs._alignment_args(1, object(), "claude", "job1", {}, check)["embed_texts"](["a"]) is None
    assert "embed_unavailable" not in check
