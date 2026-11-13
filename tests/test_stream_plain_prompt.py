"""The plain stream prompt: off by default, and when on it drops the rules, the
tone-carrying blocks and (for Gemma 4) the neighbouring lines."""
import db
from engine_backends import engine_registry, stream_recipe
from engine_backends.prompts import build_llm_instructions
from services import settings_service, workspace_job_service

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
        drama_id, drama, [_Line("小明你好")], "audio_drama", **toggles)


def test_off_by_default_keeps_the_existing_prompt(isolated_db):
    _terms, guidelines, _names = _style(isolated_db)
    assert not stream_recipe.is_active(guidelines)
    assert "Rules:" in build_llm_instructions("", STREAM, style_guidelines=guidelines)


def test_on_gives_the_plain_recipe(isolated_db):
    settings_service.set_settings({"stream_plain_prompt": True})
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
    assert not workspace_job_service.plain_stream_prompt({"content_mode": "video_drama"})


def test_brief_prompt_asks_only_for_register_and_who_talks_to_whom():
    prompt = stream_recipe.build_brief_prompt("[A] hi", "zh")
    assert "who talks to whom" in prompt and "no plot and no emotions" in prompt
    assert "recurring names and terms" not in prompt


def test_gemma4_gets_no_context_lines_and_ten_line_batches():
    opts = engine_registry.default_translate_options("ollama", "gemma4:31b-cloud", plain_stream=True)
    assert (opts["context_window"], opts["context_window_ahead"], opts["batch_size"]) == (0, 0, 10)
    other = engine_registry.default_translate_options("claude", None, plain_stream=True)
    assert other["context_window"] == 10 and other["batch_size"] == 10
    assert engine_registry.default_translate_options("ollama", "gemma4:31b-cloud")["batch_size"] == 30
