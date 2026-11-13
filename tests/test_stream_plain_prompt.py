"""The plain stream prompt: on by default, Gemma 4 stream runs only, switchable off, and when on it
drops the rules, the tone-carrying blocks and the neighbouring lines."""
import db
from engine_backends import engine_registry, stream_recipe
from engine_backends.prompts import build_llm_instructions
from services import settings_service, workspace_job_service

GEMMA = "gemma4:31b-cloud"
STREAM = {"content_mode": "streamer_vod", "source_language": "zh"}


class _Line:
    def __init__(self, zh):
        self.zh = zh


def _style(isolated_db, **toggles):
    series = db.create_series(f"S{len(db.list_series())}")
    drama_id = db.create_drama(title_en="T", series_id=series, content_mode="streamer_vod")
    db.upsert_glossary_term(series, "小明", "Xiaoming", category="person_name", policy="keep_pinyin")
    drama = db.get_drama(drama_id)
    return workspace_job_service.build_run_style_context(
        drama_id, drama, [_Line("小明你好")], "audio_drama",
        **{"engine_name": "ollama", "model": GEMMA, **toggles})


def test_switched_off_keeps_the_existing_prompt(isolated_db):
    settings_service.set_settings({"stream_plain_prompt": False})
    _terms, guidelines, _names = _style(isolated_db)
    assert not stream_recipe.is_active(guidelines)
    assert "Rules:" in build_llm_instructions("", STREAM, style_guidelines=guidelines)


def test_default_gives_the_plain_recipe(isolated_db):
    _terms, guidelines, _names = _style(isolated_db, default_female_pronouns=False)
    assert stream_recipe.is_active(guidelines)
    text = build_llm_instructions("", {**STREAM, "episode_brief": "A talks to chat. Casual."},
                                  style_guidelines=guidelines)
    assert "小明 -> Xiaoming" in text
    assert "Write normal sentences with capital letters and end punctuation." in text
    assert "A talks to chat. Casual." in text
    for dropped in ("Rules:", "baihe", "reference novel", "TERM GLOSSARY", "Pronoun default"):
        assert dropped not in text


def test_pronoun_default_still_follows_the_title_setting(isolated_db):
    settings_service.set_settings({"stream_plain_prompt": True})
    _t, on, _n = _style(isolated_db, default_female_pronouns=True)
    assert "Pronoun default" in on
    _t2, off, _n2 = _style(isolated_db, default_female_pronouns=False)
    assert "Pronoun default" not in off


def test_non_stream_titles_ignore_the_setting(isolated_db):
    settings_service.set_settings({"stream_plain_prompt": True})
    assert not workspace_job_service.plain_stream_prompt({"content_mode": "video_drama"}, "ollama", GEMMA)


def test_only_gemma4_runs_use_it(isolated_db):
    settings_service.set_settings({"stream_plain_prompt": True})
    for engine, model in (("ollama", "translategemma:27b"), ("openai", None), ("claude", None)):
        _t, guidelines, _n = _style(isolated_db, engine_name=engine, model=model)
        assert not stream_recipe.is_active(guidelines)
    _t, none_given, _n = _style(isolated_db, engine_name=None, model=None)
    assert not stream_recipe.is_active(none_given)


def test_glossary_keeps_names_and_ruled_slang_but_not_ordinary_words(isolated_db):
    series = db.create_series("G")
    db.upsert_glossary_term(series, "小明", "Xiaoming", category="person_name")
    db.upsert_glossary_term(series, "敲", "acting tough", category=None)
    db.upsert_glossary_term(series, "牛肉", "beef", category="culture_item")
    text = stream_recipe.names_glossary(db.list_glossary_terms(series))
    assert "小明 -> Xiaoming" in text and "敲 -> acting tough" in text
    assert "牛肉" not in text


def test_brief_prompt_asks_only_for_register_and_who_talks_to_whom():
    prompt = stream_recipe.build_brief_prompt("[A] hi", "zh")
    assert "who talks to whom" in prompt and "no plot and no emotions" in prompt
    assert "recurring names and terms" not in prompt


def test_plain_stream_defaults_are_no_context_and_ten_line_batches():
    opts = engine_registry.default_translate_options("ollama", GEMMA, plain_stream=True)
    assert (opts["context_window"], opts["context_window_ahead"], opts["batch_size"]) == (0, 0, 10)
    assert engine_registry.default_translate_options("ollama", GEMMA)["batch_size"] == 30
    assert engine_registry.is_gemma4("ollama", GEMMA) and not engine_registry.is_gemma4("ollama", "translategemma:27b")


def test_the_brief_is_written_with_the_plain_prompt_only_for_a_plain_run(isolated_db, monkeypatch):
    from core import Line
    from engine_backends import llm_tasks
    from jobs import job_table
    seen = []
    monkeypatch.setattr(llm_tasks, "generate_episode_brief",
                        lambda *a, **k: seen.append(k["plain_stream_prompt"]) or "A talks to chat.")
    job_table._jobs["j"] = {"status": "running", "progress": 0.0, "message": "", "error": None,
                            "cancel_requested": False, "result": None}
    try:
        for guidelines, expected in ((stream_recipe.STREAM_RECIPE_MARKER, True), ("", False)):
            did = db.create_drama(title_en=f"D{expected}", content_mode="streamer_vod")
            db.save_lines(did, [Line(idx=0, start=0, end=2, zh="你好"), Line(idx=1, start=2, end=4, zh="再见")])
            workspace_job_service._with_episode_context(
                "j", did, db.load_line_objects(did), object(), "ollama", db.get_drama(did), {}, None, guidelines)
            assert seen[-1] is expected
    finally:
        job_table._jobs.pop("j", None)


def test_glossary_lists_a_terms_aliases_beside_its_main_spelling(isolated_db):
    series = db.create_series("A")
    db.upsert_glossary_term(series, "亞洛赫", "Anok", category="person_name", aliases="阿洛|雅露")
    db.upsert_glossary_term(series, "小明", "Xiaoming", category="person_name")
    text = stream_recipe.names_glossary(db.list_glossary_terms(series))
    assert "亞洛赫 / 阿洛 / 雅露 -> Anok" in text
    assert "小明 -> Xiaoming" in text


def _stream_title(isolated_db, start_json, engine="ollama"):
    did = db.create_drama(title_en="T", content_mode="streamer_vod", source_language="zh",
                          translate_start_json=start_json)
    return did, db.get_drama(did)


def _resolve(drama, engine="ollama", model=GEMMA, **given):
    from services import translate_run_service
    args = dict(style_preset=None, style_note=None, locale=None, context_window=None,
                context_window_ahead=None, batch_size=None, job_cost_cap_usd=None,
                gemini_free_tier=False)
    args.update(given)
    return translate_run_service._resolve_run_options(drama, engine, model, **args)


OLD_DEFAULTS = '{"context_window": 10, "context_window_ahead": 6, "batch_size": 30}'


def test_previously_translated_stream_title_gets_the_tested_values(isolated_db):
    _did, drama = _stream_title(isolated_db, OLD_DEFAULTS)
    opts = _resolve(drama)
    assert (opts.context_window, opts.context_window_ahead, opts.batch_size) == (
        0, 0, engine_registry.PLAIN_STREAM_BATCH_SIZE)


def test_values_the_person_changed_still_win_on_a_stream_title(isolated_db):
    _did, drama = _stream_title(
        isolated_db, '{"context_window": 3, "context_window_ahead": 2, "batch_size": 12}')
    opts = _resolve(drama)
    assert (opts.context_window, opts.context_window_ahead, opts.batch_size) == (3, 2, 12)


def test_explicit_request_values_are_kept(isolated_db):
    _did, drama = _stream_title(isolated_db, OLD_DEFAULTS)
    opts = _resolve(drama, context_window=10, context_window_ahead=6, batch_size=30)
    assert (opts.context_window, opts.context_window_ahead, opts.batch_size) == (10, 6, 30)


def test_switched_off_keeps_the_remembered_values(isolated_db):
    settings_service.set_settings({"stream_plain_prompt": False})
    _did, drama = _stream_title(isolated_db, OLD_DEFAULTS)
    opts = _resolve(drama)
    assert (opts.context_window, opts.context_window_ahead, opts.batch_size) == (10, 6, 30)


def test_other_engines_and_non_stream_titles_keep_the_remembered_values(isolated_db):
    _did, stream = _stream_title(isolated_db, OLD_DEFAULTS)
    other = _resolve(stream, engine="ollama", model="translategemma:27b")
    assert (other.context_window, other.context_window_ahead) == (10, 6)
    did = db.create_drama(title_en="V", content_mode="video_drama", translate_start_json=OLD_DEFAULTS)
    video = _resolve(db.get_drama(did))
    assert (video.context_window, video.context_window_ahead, video.batch_size) == (10, 6, 30)


def test_translate_form_start_drops_untouched_old_defaults(isolated_db):
    from services import translate_run_service
    did, _ = _stream_title(isolated_db, OLD_DEFAULTS)
    db.update_drama(did, translation_engine="ollama",
                    translate_start_json='{"context_window": 10, "context_window_ahead": 4, '
                                         '"batch_size": 30, "locale": "en-GB", '
                                         '"engine_model": "%s"}' % GEMMA)
    config = translate_run_service.get_translate_config(did)
    start = config["translate_start"]
    assert "context_window" not in start and "batch_size" not in start
    assert start["context_window_ahead"] == 4 and start["locale"] == "en-GB"
