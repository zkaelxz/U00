"""Which Whisper models a source language may use: one table (whisper_models.WHISPER_LANGUAGE_RULES)
checked at every entry point and sent to the pickers. Fakes only: no network, no weights."""

import os

import pytest

import core
import whisper_transcribe
import whisper_models as wm
from lib.errors import InvalidInputError
from services import benchmark_lab_service as lab
from services import compare_transcription_service, transcribe_service

# (model, language, allowed). Every row follows from the rules' evidence in docs/asr-experiments.md.
CASES = [
    ("ja-1.5b", "ja", True), ("ja-1.5b", "zh", False), ("ja-1.5b", "ko", False),
    ("medium", "ja", False), ("medium", "zh", True), ("medium", "ko", True),
    ("large-v3", "ja", True), ("large-v3", "zh", True), ("large-v3", "ko", True),
    ("large-v3-turbo", "ja", True), ("large-v3-turbo", "zh", True), ("large-v3-turbo", "ko", True),
    ("small", "ja", True), ("tiny", "ko", True), ("base", "zh", True),
]
REFUSED = [(m, lang) for m, lang, ok in CASES if not ok]


class TestTable:
    @pytest.mark.parametrize("model,language,allowed", CASES)
    def test_fits_language(self, model, language, allowed):
        assert wm.whisper_model_fits_language(model, language) is allowed
        assert (wm.whisper_model_language_problem(model, language) is None) is allowed

    def test_every_rule_names_a_known_model_and_language_and_has_a_reason(self):
        for model, rule in wm.WHISPER_LANGUAGE_RULES.items():
            assert model in wm.WHISPER_MODELS
            assert rule.reason.strip() and bool(rule.only_for) != bool(rule.not_for)
            assert set(rule.only_for + rule.not_for) <= set(core.SOURCE_LANGUAGES)

    def test_the_default_is_allowed_for_every_language(self):
        for language in core.SOURCE_LANGUAGES:
            assert wm.whisper_model_fits_language(wm.DEFAULT_WHISPER_SIZE, language)

    def test_exclusions_payload_matches_the_checks(self):
        payload = wm.whisper_language_exclusions()
        for model, language, allowed in CASES:
            assert (language not in payload.get(model, {})) is allowed
        assert payload["medium"]["ja"] == wm.whisper_model_language_problem("medium", "ja")

    def test_an_unknown_language_is_not_blocked_by_a_not_for_rule(self):
        assert wm.whisper_model_fits_language("medium", "fr")
        assert not wm.whisper_model_fits_language("ja-1.5b", "fr")


@pytest.mark.parametrize("model,language", REFUSED)
class TestServerRefuses:
    def test_check_helper(self, model, language):
        with pytest.raises(InvalidInputError, match=wm.WHISPER_LANGUAGE_RULES[model].reason[:30]):
            transcribe_service.check_whisper_size_for_language(model, language)

    def test_update_config(self, isolated_db, model, language):
        did = isolated_db.create_drama(title_en="t", source_language=language)
        before = isolated_db.get_drama(did)["whisper_size"]
        with pytest.raises(InvalidInputError, match=wm.WHISPER_LANGUAGE_RULES[model].reason[:30]):
            transcribe_service.update_transcribe_config(did, whisper_size=model)
        assert isolated_db.get_drama(did)["whisper_size"] == before

    def test_start_run_naming_another_language_is_refused(self, isolated_db, model, language, monkeypatch):
        home = next(lang for lang in core.SOURCE_LANGUAGES if wm.whisper_model_fits_language(model, lang))
        did = isolated_db.create_drama(title_en="t", source_language=home, whisper_size=model,
                                       audio_filename="a.wav")
        with open(os.path.join(isolated_db.drama_dir(did), "a.wav"), "wb") as f:
            f.write(b"x")
        monkeypatch.setattr(transcribe_service.background_jobs, "start_job",
                            lambda *a, **k: pytest.fail("a job was started"), raising=False)
        with pytest.raises(InvalidInputError, match=wm.WHISPER_LANGUAGE_RULES[model].reason[:30]):
            transcribe_service.start_transcribe_run(did, source_language=language, transcript_text="hello")

    def test_compare(self, model, language, monkeypatch):
        monkeypatch.setattr(compare_transcription_service, "_backend_problem", lambda *a: None)
        with pytest.raises(InvalidInputError, match=wm.WHISPER_LANGUAGE_RULES[model].reason[:30]):
            compare_transcription_service._validate_candidate(
                {"id": 1, "source_language": language}, model, "whisper")

    def test_lab_refuses_a_mixed_selection(self, model, language, monkeypatch):
        cases = [{"id": 1, "source_language": "zh" if language != "zh" else "ko"},
                 {"id": 2, "source_language": language}]
        monkeypatch.setattr(lab, "_select_cases", lambda *a, **k: cases)
        with pytest.raises(InvalidInputError, match=wm.WHISPER_LANGUAGE_RULES[model].reason[:30]):
            lab._check_selection("transcription", [{"engine": "whisper", "model": model}], None, None, None)

    def test_a_saved_choice_falls_back_and_the_config_says_so(self, isolated_db, model, language):
        did = isolated_db.create_drama(title_en="t", source_language=language)
        isolated_db.update_drama(did, whisper_size=model)
        drama = isolated_db.get_drama(did)
        default = transcribe_service.default_whisper_size()
        assert transcribe_service.stored_whisper_size(drama) == default
        notice = transcribe_service.whisper_size_notice(drama)
        assert model in notice and default in notice and wm.WHISPER_LANGUAGE_RULES[model].reason in notice


@pytest.mark.parametrize("model,language", [(m, lang) for m, lang, ok in CASES if ok])
class TestServerAccepts:
    def test_update_config_and_check(self, isolated_db, model, language):
        if model not in transcribe_service._allowed_whisper_sizes():
            pytest.skip("not a stored size")
        did = isolated_db.create_drama(title_en="t", source_language=language)
        transcribe_service.check_whisper_size_for_language(model, language)
        assert transcribe_service.update_transcribe_config(did, whisper_size=model)["whisper_size"] == model

    def test_a_saved_choice_is_kept_without_a_notice(self, model, language):
        drama = {"whisper_size": model, "source_language": language}
        if model in transcribe_service._allowed_whisper_sizes():
            assert transcribe_service.stored_whisper_size(drama) == model
            assert transcribe_service.whisper_size_notice(drama) is None


class TestPayloads:
    def test_transcribe_config_carries_the_table_and_the_notice(self, isolated_db):
        did = isolated_db.create_drama(title_en="t", source_language="ja")
        isolated_db.update_drama(did, whisper_size="medium")
        cfg = transcribe_service.get_transcribe_config(did)
        assert cfg["whisper_size"] == transcribe_service.default_whisper_size()
        assert cfg["whisper_exclusions"] == wm.whisper_language_exclusions()
        assert "medium" in cfg["whisper_size_notice"]

    def test_compare_options_list_the_selectable_sizes_and_the_table(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="t", source_language="zh")
        monkeypatch.setattr(compare_transcription_service, "_backend_problem", lambda *a: None)
        opts = compare_transcription_service.get_options(did)
        assert {"large-v3", "ja-1.5b"} <= set(opts["whisper_sizes"])
        assert "medium" not in opts["whisper_sizes"]
        assert opts["whisper_exclusions"] == wm.whisper_language_exclusions()
        assert opts["source_language"] == "zh"

    def test_lab_options_carry_the_table(self):
        assert lab.get_options()["whisper_exclusions"] == wm.whisper_language_exclusions()


class TestSelectableSizes:
    """One list feeds every non-Live picker: medium and below are hidden unless Developer
    Mode is on, but the server keeps accepting them and a title that saved one keeps it."""

    HIDDEN = ("tiny", "base", "small", "medium")

    def test_workspace_pickers_list_only_the_large_models_and_the_opt_ins(self):
        for context in ("transcribe", "compare"):
            sizes = wm.selectable_whisper_sizes(context)
            assert sizes[:2] == ["large-v3", "large-v3-turbo"]
            assert not set(sizes) & set(self.HIDDEN)

    def test_developer_mode_shows_the_hidden_sizes_again(self):
        sizes = wm.selectable_whisper_sizes("transcribe", developer_mode=True)
        assert set(self.HIDDEN) <= set(sizes)

    def test_live_keeps_its_own_list(self):
        assert wm.selectable_whisper_sizes("live") == list(wm.LIVE_WHISPER_SIZES)
        assert "medium" in wm.selectable_whisper_sizes("live")

    def test_the_config_route_reads_the_developer_flag(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="D")
        monkeypatch.setattr("services.maintenance_assistant_service.developer_mode_enabled", lambda: False)
        assert "medium" not in transcribe_service.get_transcribe_config(did)["selectable_whisper_sizes"]
        monkeypatch.setattr("services.maintenance_assistant_service.developer_mode_enabled", lambda: True)
        assert "medium" in transcribe_service.get_transcribe_config(did)["selectable_whisper_sizes"]

    def test_a_saved_hidden_size_is_kept_and_still_accepted(self, isolated_db, monkeypatch):
        monkeypatch.setattr("services.maintenance_assistant_service.developer_mode_enabled", lambda: False)
        did = isolated_db.create_drama(title_en="D", source_language="zh")
        transcribe_service.update_transcribe_config(did, whisper_size="medium")
        config = transcribe_service.get_transcribe_config(did)
        assert config["whisper_size"] == "medium"
        assert "medium" not in config["selectable_whisper_sizes"]

    def test_compare_lists_a_hidden_saved_size_so_the_select_can_show_it(self, isolated_db, monkeypatch):
        monkeypatch.setattr("services.maintenance_assistant_service.developer_mode_enabled", lambda: False)
        monkeypatch.setattr(compare_transcription_service, "_backend_problem", lambda *a: None)
        did = isolated_db.create_drama(title_en="D", source_language="zh")
        transcribe_service.update_transcribe_config(did, whisper_size="small")
        options = compare_transcription_service.get_options(did)
        assert options["saved_whisper_size"] == "small"
        assert "small" in options["whisper_sizes"] and "tiny" not in options["whisper_sizes"]

    def test_the_default_is_large_v3_and_a_new_title_uses_it(self, isolated_db):
        assert wm.DEFAULT_WHISPER_SIZE == "large-v3"
        did = isolated_db.create_drama(title_en="D")
        assert transcribe_service.get_transcribe_config(did)["whisper_size"] == "large-v3"

    def test_live_default_and_groq_model_are_unchanged(self):
        assert whisper_transcribe.GROQ_DEFAULT_MODEL == "whisper-large-v3-turbo"
        assert "large-v3-turbo" in wm.LIVE_WHISPER_SIZES
