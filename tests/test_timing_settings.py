"""Settings > Timing and splitting: the declared rows, their defaults against the module
constants they stand in for, and that a changed value reaches the function that uses it
(and the default reaches it unchanged). Mocked: no audio, model or network."""
import inspect
import os
import re

import numpy as np
import pytest

import asmr_vad
import asr_backend
import whisper_hallucination
import segment_splitting
import sensitivity_preset
import sentence_groups
import vad_segments
import word_align
from core import Line
from engine_backends import translate_pipeline
from lib import settings_schema as schema
from services import (resplit_service, restructure_service, retime_service, run_settings_service,
                      settings_service, speech_coverage_service, timing_settings_service,
                      transcribe_gap_service)
from lib.errors import InvalidInputError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROWS = [s for s in schema.SETTINGS if s.section in schema.TIMING_SECTIONS]
BY_KEY = {s.key: s for s in ROWS}

# The owner's "E" rows; everything else in these sections is Developer Mode only.
EVERYONE = {"split_max_seconds", "split_max_cjk_chars", "quick_split_seconds",
            "gap_fill_min_gap_seconds", "scene_break_gap_seconds", "sentence_group_gap_seconds"}


def _frontend_quick_split_default():
    text = open(os.path.join(ROOT, "frontend/src/pages/workspace/stages/review/reviewResegment.ts"),
                encoding="utf-8").read()
    return float(re.search(r"QUICK_SPLIT_DEFAULTS = \{ seconds: ([\d.]+)", text).group(1))


def _constants():
    return {
        "sentence_split_min_silence_ms": asr_backend.SENTENCE_SPLIT_MIN_SILENCE_MS,
        "sensitive_vad_threshold": sensitivity_preset.SENSITIVE_VAD_THRESHOLD,
        "difficult_no_speech_threshold": sensitivity_preset.DIFFICULT_NO_SPEECH_THRESHOLD,
        "difficult_log_prob_threshold": sensitivity_preset.DIFFICULT_LOG_PROB_THRESHOLD,
        "qwen_vad_threshold": vad_segments.THRESHOLD,
        "qwen_vad_min_silence_ms": vad_segments.MIN_SILENCE_MS,
        "qwen_vad_min_speech_ms": vad_segments.MIN_SPEECH_MS,
        "qwen_vad_pad_ms": vad_segments.PAD_MS,
        "qwen_long_vad_threshold": asr_backend.LONG_VAD_KWARGS["threshold"],
        "qwen_long_vad_min_speech_ms": asr_backend.LONG_VAD_KWARGS["min_speech_ms"],
        "qwen_long_vad_pad_ms": asr_backend.LONG_VAD_KWARGS["pad_ms"],
        "qwen_merge_gap_seconds": asr_backend.MERGE_GAP_S,
        "qwen_merge_gap_mixed_seconds": asr_backend.MERGE_GAP_MIXED_S,
        "qwen_span_cap_seconds": vad_segments.SPAN_CAP_S,
        "qwen_span_cap_search_seconds": vad_segments.SPAN_CAP_SEARCH_S,
        "qwen_context_pad_seconds": asr_backend.CONTEXT_PAD_S,
        "qwen_long_merge_gap_seconds": asr_backend.LONG_MERGE_GAP_S,
        "qwen_long_window_seconds": asr_backend.LONG_WINDOW_S,
        "qwen_long_cut_search_seconds": asr_backend.LONG_CUT_SEARCH_S,
        "asmr_start_threshold": asmr_vad.START_THRESHOLD,
        "asmr_stop_threshold": asmr_vad.STOP_THRESHOLD,
        "speech_coverage_vad_threshold": speech_coverage_service.VAD_THRESHOLD,
        "split_max_seconds": segment_splitting.SPLIT_MAX_SECONDS,
        "split_max_cjk_chars": segment_splitting.SPLIT_MAX_CJK_CHARS,
        "split_min_piece_seconds": segment_splitting.MIN_PIECE_SECONDS,
        "split_min_piece_cjk_chars": segment_splitting.MIN_PIECE_CJK_CHARS,
        "split_min_piece_words": segment_splitting.MIN_PIECE_WORDS,
        "quick_split_seconds": _frontend_quick_split_default(),
        "realign_min_seconds": word_align.REALIGN_MIN_SECONDS,
        "realign_pause_seconds": word_align.REALIGN_PAUSE_SECONDS,
        "stock_phrase_isolation_seconds": whisper_hallucination.STOCK_PHRASE_ISOLATION_SEC,
        "repeat_loop_min_repeats": inspect.signature(
            whisper_hallucination.filter_hallucinated_segments).parameters["min_repeat_count"].default,
        "retime_window_pad_seconds": retime_service._PAD_S,
        "gap_fill_min_gap_seconds": transcribe_gap_service.MIN_GAP_SECONDS,
        "scene_break_gap_seconds": translate_pipeline.SCENE_BREAK_GAP_SECONDS,
        "sentence_group_gap_seconds": sentence_groups.MAX_GAP_SECONDS,
        "sentence_group_max_lines": sentence_groups.MAX_GROUP_LINES,
        "sentence_group_max_chars": sentence_groups.MAX_GROUP_CHARS,
        "sentence_group_max_seconds": sentence_groups.MAX_GROUP_SECONDS,
    }


def _inside(row):
    """A value in range that is not the default (the middle of the range, or a quarter in)."""
    for fraction in (0.5, 0.25, 0.75):
        value = row.min + (row.max - row.min) * fraction
        value = int(round(value)) if row.type == "int" else round(value, row.digits or 2)
        if value != row.default and row.min <= value <= row.max:
            return value
    raise AssertionError(row.key)


@pytest.fixture
def developer_mode(isolated_db):
    isolated_db.set_app_setting("assistant.developer_mode", True)
    return isolated_db


# --- the declared rows --------------------------------------------------------

class TestRows:
    def test_there_is_a_row_for_every_value_the_services_use(self):
        assert set(timing_settings_service.KEYS) == {s.key for s in ROWS} == set(_constants())

    @pytest.mark.parametrize("row", ROWS, ids=[s.key for s in ROWS])
    def test_range_default_and_wording(self, row):
        assert row.type in ("int", "float") and row.min is not None and row.max is not None
        assert row.min <= row.default <= row.max
        assert row.label and row.help and row.store_key == "pref." + row.key
        assert row.tab == "system"
        assert schema.coerce(row.key, row.default) == row.default
        # The help text is a plain tooltip: a sentence, not a developer note.
        assert row.help.endswith(".") and "TODO" not in row.help

    @pytest.mark.parametrize("row", ROWS, ids=[s.key for s in ROWS])
    def test_the_edges_are_accepted_and_just_outside_is_refused(self, row):
        step = 1 if row.type == "int" else 0.01
        assert schema.validate(row.key, row.min) == row.min
        assert schema.validate(row.key, row.max) == row.max
        for outside in (row.min - step, row.max + step):
            with pytest.raises(ValueError):
                schema.validate(row.key, outside)
        for not_a_number in (True, "5", float("nan"), float("inf")):
            with pytest.raises(ValueError):
                schema.validate(row.key, not_a_number)

    @pytest.mark.parametrize("row", [s for s in ROWS if s.min > 0], ids=lambda s: s.key)
    def test_zero_is_refused_where_the_minimum_is_above_zero(self, row):
        with pytest.raises(ValueError):
            schema.validate(row.key, 0)

    def test_blank_puts_the_default_back(self):
        for row in ROWS:
            assert schema.validate(row.key, None) == row.default

    @pytest.mark.parametrize("row", ROWS, ids=[s.key for s in ROWS])
    def test_only_the_owners_everyone_rows_are_outside_developer_mode(self, row):
        assert row.dev_only is (row.key not in EVERYONE)
        assert (row.scope == "developer") is row.dev_only

    def test_the_three_sections_group_the_rows(self):
        assert schema.TIMING_SECTIONS == ("Voice detection", "Line splitting", "Sentence grouping")
        assert {s.section for s in ROWS} == set(schema.TIMING_SECTIONS)

    @pytest.mark.parametrize("row", ROWS, ids=[s.key for s in ROWS])
    def test_the_default_equals_the_module_constant(self, row):
        assert row.default == _constants()[row.key]
        assert type(row.default) is (int if row.type == "int" else float)

    def test_the_module_defaults_the_asr_literals_stand_in_for_match(self):
        # asr_backend repeats vad_segments' numbers so importing it does not import numpy.
        tuning = asr_backend.AsrTuning()
        assert (tuning.vad_threshold, tuning.vad_min_silence_ms, tuning.vad_min_speech_ms,
                tuning.vad_pad_ms, tuning.span_cap_s, tuning.span_cap_search_s) == (
            vad_segments.THRESHOLD, vad_segments.MIN_SILENCE_MS, vad_segments.MIN_SPEECH_MS,
            vad_segments.PAD_MS, vad_segments.SPAN_CAP_S, vad_segments.SPAN_CAP_SEARCH_S)
        defaults = {k: v.default for k, v in inspect.signature(vad_segments.speech_spans).parameters.items()}
        assert (defaults["threshold"], defaults["min_silence_ms"], defaults["min_speech_ms"],
                defaults["pad_ms"]) == (0.5, 300, 250, 100)

    def test_the_frontend_lists_every_row(self):
        text = open(os.path.join(ROOT, "frontend/src/types/settings.ts"), encoding="utf-8").read()
        block = text[text.index("export interface TimingPreferences"):]
        block = block[:block.index("}")]
        assert set(re.findall(r"^\s+(\w+)\?: number", block, re.M)) == set(BY_KEY)

    def test_the_api_models_carry_every_row(self):
        from api.schemas import SettingsPreferences, SettingsUpdateRequest
        assert set(BY_KEY) <= set(SettingsPreferences.model_fields)
        assert set(BY_KEY) <= set(SettingsUpdateRequest.model_fields)
        for key, row in BY_KEY.items():
            assert SettingsPreferences.model_fields[key].default == row.default


# --- reading, writing, reset, Developer Mode --------------------------------------

class TestSettingsRoundTrip:
    def test_a_fresh_library_reads_every_default(self, isolated_db):
        assert timing_settings_service.values() == {s.key: s.default for s in ROWS}
        assert timing_settings_service.changed() == {}

    def test_an_everyone_row_saves_and_reads_back_without_developer_mode(self, isolated_db):
        out = settings_service.set_settings({"split_max_seconds": 6.5, "split_max_cjk_chars": 30})
        assert out["preferences"]["split_max_seconds"] == 6.5
        values = timing_settings_service.values()
        assert (values["split_max_seconds"], values["split_max_cjk_chars"]) == (6.5, 30)
        assert timing_settings_service.changed() == {"split_max_seconds": 6.5, "split_max_cjk_chars": 30}

    def test_reset_to_default_puts_the_declared_default_back(self, isolated_db):
        settings_service.set_settings({"split_max_seconds": 6.5})
        out = settings_service.set_settings({"split_max_seconds": schema.BY_KEY["split_max_seconds"].default})
        assert out["preferences"]["split_max_seconds"] == 8.0
        settings_service.set_settings({"split_max_seconds": 6.5})
        # A blank field saves null, which is also a reset.
        assert settings_service.set_settings({"split_max_seconds": None})["preferences"]["split_max_seconds"] == 8.0
        assert timing_settings_service.changed() == {}

    @pytest.mark.parametrize("key", sorted(BY_KEY))
    def test_every_row_resets(self, developer_mode, key):
        row = BY_KEY[key]
        settings_service.set_settings({key: _inside(row)})
        assert timing_settings_service.values()[key] == _inside(row)
        settings_service.set_settings({key: row.default})
        assert timing_settings_service.values()[key] == row.default

    def test_an_out_of_range_write_is_refused_and_changes_nothing(self, isolated_db):
        with pytest.raises(InvalidInputError):
            settings_service.set_settings({"split_max_seconds": 3, "split_max_cjk_chars": 30})
        assert settings_service.get("split_max_cjk_chars") == 40
        with pytest.raises(InvalidInputError):
            settings_service.set_settings({"split_max_seconds": 0})

    def test_a_developer_row_is_refused_while_developer_mode_is_off(self, isolated_db):
        with pytest.raises(InvalidInputError, match="Developer Mode"):
            settings_service.set_settings({"qwen_vad_threshold": 0.6})
        assert settings_service.get("qwen_vad_threshold") == 0.5

    def test_a_developer_row_saves_with_developer_mode_on(self, developer_mode):
        settings_service.set_settings({"qwen_vad_threshold": 0.6})
        assert timing_settings_service.values()["qwen_vad_threshold"] == 0.6

    def test_turning_developer_mode_off_puts_the_developer_rows_back_to_their_defaults(self, developer_mode):
        settings_service.set_settings({"qwen_vad_threshold": 0.6, "split_max_seconds": 6})
        developer_mode.set_app_setting("assistant.developer_mode", False)
        values = timing_settings_service.values()
        # The stored number is kept (it returns with Developer Mode); runs just do not use it.
        assert settings_service.get("qwen_vad_threshold") == 0.6
        assert values["qwen_vad_threshold"] == 0.5
        assert values["split_max_seconds"] == 6

    def test_the_schema_endpoint_lists_developer_rows_only_in_developer_mode(self, isolated_db):
        from services import settings_schema_service
        listed = lambda: {r["key"] for r in settings_schema_service.get_schema()["settings"]}  # noqa: E731
        assert listed() & set(BY_KEY) == EVERYONE
        isolated_db.set_app_setting("assistant.developer_mode", True)
        assert listed() & set(BY_KEY) == set(BY_KEY)

    def test_the_sentence_gap_must_stay_below_the_scene_break_gap(self, isolated_db):
        with pytest.raises(InvalidInputError, match="scene-break"):
            settings_service.set_settings({"sentence_group_gap_seconds": 2.0, "scene_break_gap_seconds": 2.0})
        settings_service.set_settings({"scene_break_gap_seconds": 2.0})
        with pytest.raises(InvalidInputError):
            settings_service.set_settings({"sentence_group_gap_seconds": 2.0})
        settings_service.set_settings({"sentence_group_gap_seconds": 1.5})
        with pytest.raises(InvalidInputError):
            settings_service.set_settings({"scene_break_gap_seconds": 1.5})
        settings_service.set_settings({"sentence_group_gap_seconds": 1.0, "scene_break_gap_seconds": 1.5})

    def test_rows_that_disagree_in_the_database_read_as_defaults(self, isolated_db):
        isolated_db.set_app_setting("pref.sentence_group_gap_seconds", 2.4)
        isolated_db.set_app_setting("pref.scene_break_gap_seconds", 1.6)
        values = timing_settings_service.values()
        assert (values["sentence_group_gap_seconds"], values["scene_break_gap_seconds"]) == (1.0, 3.0)

    def test_the_post_route_takes_a_row_and_refuses_a_bad_one(self, isolated_db):
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        client = TestClient(create_app(ApiSettings(allow_key_writes=True)), base_url="http://127.0.0.1:8600",
                            client=("127.0.0.1", 50000), raise_server_exceptions=False)
        ok = client.post("/api/settings", json={"quick_split_seconds": 9})
        assert ok.status_code == 200 and ok.json()["preferences"]["quick_split_seconds"] == 9
        assert client.get("/api/settings").json()["preferences"]["quick_split_seconds"] == 9
        assert client.post("/api/settings", json={"quick_split_seconds": 1}).status_code == 422
        assert client.post("/api/settings", json={"quick_split_seconds": "9"}).status_code == 422
        assert client.get("/api/settings").json()["preferences"]["quick_split_seconds"] == 9


# --- Group A: voice detection ------------------------------------------------------

class _Calls:
    """Stand-ins for the vad_segments helpers that record how Qwen3ASRVadBackend calls them."""

    def __init__(self, monkeypatch):
        self.speech, self.merge, self.cap, self.pad = [], [], [], []
        monkeypatch.setattr(asr_backend, "load_audio_16k", lambda p: np.zeros(32000, dtype=np.float32))
        monkeypatch.setattr(
            vad_segments, "speech_spans",
            lambda audio, sr, **kw: self.speech.append({k: v for k, v in kw.items() if k != "vad_fn"}) or [])
        monkeypatch.setattr(vad_segments, "merge_close",
                            lambda spans, gap_s=0.3: self.merge.append(gap_s) or [])
        monkeypatch.setattr(vad_segments, "cap_spans",
                            lambda spans, audio, sr, **kw: self.cap.append(kw) or [])
        monkeypatch.setattr(vad_segments, "context_windows",
                            lambda spans, dur, pad=2.0: self.pad.append(pad) or [])

    def run(self, backend, **kw):
        assert backend.transcribe("a.wav", "zh", **kw) == []
        return self


class TestVoiceDetection:
    @pytest.fixture
    def calls(self, monkeypatch):
        return _Calls(monkeypatch)

    def test_the_default_run_calls_the_helpers_exactly_as_before(self, calls):
        calls.run(asr_backend.Qwen3ASRVadBackend())
        assert (calls.speech, calls.merge, calls.cap, calls.pad) == ([{}], [1.0], [{}], [2.0])

    def test_the_default_long_window_run_calls_the_helpers_exactly_as_before(self, calls):
        calls.run(asr_backend.Qwen3ASRLongBackend())
        assert calls.speech == [asr_backend.LONG_VAD_KWARGS]
        assert (calls.merge, calls.pad) == ([asr_backend.LONG_MERGE_GAP_S], [asr_backend.CONTEXT_PAD_S])
        assert calls.cap == [{"max_s": asr_backend.LONG_WINDOW_S, "search_window_s": asr_backend.LONG_CUT_SEARCH_S}]

    def test_the_default_mixed_languages_run_uses_the_short_merge_gap(self, calls):
        calls.run(asr_backend.Qwen3ASRVadBackend(), mixed_languages=True)
        assert calls.merge == [asr_backend.MERGE_GAP_MIXED_S]

    def test_default_settings_build_the_default_tuning(self, isolated_db):
        assert timing_settings_service.asr_tuning() == asr_backend.DEFAULT_TUNING

    def test_a_changed_value_reaches_speech_spans_the_gaps_and_the_context_pad(self, developer_mode, calls):
        settings_service.set_settings({
            "qwen_vad_threshold": 0.6, "qwen_vad_min_silence_ms": 500, "qwen_vad_min_speech_ms": 100,
            "qwen_vad_pad_ms": 40, "qwen_merge_gap_seconds": 1.5, "qwen_span_cap_seconds": 20,
            "qwen_span_cap_search_seconds": 2, "qwen_context_pad_seconds": 1})
        tuning = timing_settings_service.asr_tuning()
        calls.run(asr_backend.Qwen3ASRVadBackend(), tuning=tuning)
        assert calls.speech == [{"threshold": 0.6, "min_silence_ms": 500, "min_speech_ms": 100, "pad_ms": 40}]
        assert (calls.merge, calls.pad) == ([1.5], [1.0])
        assert calls.cap == [{"max_s": 20.0, "search_window_s": 2.0}]

    def test_a_changed_mixed_gap_and_long_window_values_reach_their_helpers(self, developer_mode, calls):
        settings_service.set_settings({
            "qwen_merge_gap_mixed_seconds": 0.5, "qwen_long_vad_threshold": 0.4,
            "qwen_long_vad_min_speech_ms": 50, "qwen_long_vad_pad_ms": 200,
            "qwen_long_merge_gap_seconds": 4, "qwen_long_window_seconds": 25,
            "qwen_long_cut_search_seconds": 5})
        tuning = timing_settings_service.asr_tuning()
        calls.run(asr_backend.Qwen3ASRVadBackend(), mixed_languages=True, tuning=tuning)
        assert calls.merge == [0.5]
        calls.speech.clear()
        calls.merge.clear()
        calls.cap.clear()
        calls.run(asr_backend.Qwen3ASRLongBackend(), tuning=tuning)
        assert calls.speech == [{"threshold": 0.4, "min_speech_ms": 50, "pad_ms": 200}]
        assert calls.merge == [4.0]
        assert calls.cap == [{"max_s": 25.0, "search_window_s": 5.0}]

    def test_the_whisper_voice_thresholds_reach_the_detector_and_the_default_call_is_unchanged(
            self, developer_mode, calls, monkeypatch):
        seen = []

        def fake(on_notice=None, **kw):
            seen.append(kw)
            return None
        monkeypatch.setattr(asmr_vad, "vad_fn_or_fallback", fake)
        calls.run(asr_backend.Qwen3ASRVadBackend(), detector="asmr")
        assert seen == [{}]
        settings_service.set_settings({"asmr_start_threshold": 0.6, "asmr_stop_threshold": 0.3})
        calls.run(asr_backend.Qwen3ASRVadBackend(), detector="asmr", tuning=timing_settings_service.asr_tuning())
        assert seen[1] == {"start": 0.6, "stop": 0.3}

    def test_the_detector_is_built_with_the_thresholds_it_is_given(self, monkeypatch):
        built = []
        monkeypatch.setattr(asmr_vad, "load_detector",
                            lambda *a: built.append(a) or type("Detector", (), {})())
        asmr_vad.vad_fn_or_fallback()
        asmr_vad.vad_fn_or_fallback(None, 0.6, 0.3)
        assert built == [(), (0.6, 0.3)]

    def test_the_stop_threshold_must_stay_below_the_start_threshold(self, developer_mode):
        with pytest.raises(InvalidInputError, match="start threshold"):
            settings_service.set_settings({"asmr_stop_threshold": 0.5, "asmr_start_threshold": 0.45})
        settings_service.set_settings({"asmr_start_threshold": 0.4})
        with pytest.raises(InvalidInputError):
            settings_service.set_settings({"asmr_stop_threshold": 0.4})
        developer_mode.set_app_setting("pref.asmr_start_threshold", 0.3)
        developer_mode.set_app_setting("pref.asmr_stop_threshold", 0.6)
        values = timing_settings_service.values()
        assert (values["asmr_start_threshold"], values["asmr_stop_threshold"]) == (0.5, 0.35)

    def test_the_sentence_mode_silence_reaches_whisper_and_the_default_is_unchanged(
            self, isolated_db, tmp_path, monkeypatch):
        seen = []

        def fake(*a, min_silence_duration_ms=None, **k):
            seen.append(min_silence_duration_ms)
            return [{"start": 0.0, "end": 2.0, "text": "你好。"}]
        out = self._whisper_run(tmp_path, monkeypatch, fake, split_by_sentences=True)
        assert seen == [2000] and out["lines"]
        isolated_db.set_app_setting("assistant.developer_mode", True)
        settings_service.set_settings({"sentence_split_min_silence_ms": 1200})
        self._whisper_run(tmp_path, monkeypatch, fake, split_by_sentences=True,
                          timing=timing_settings_service.values())
        assert seen == [2000, 1200]

    @staticmethod
    def _whisper_run(tmp_path, monkeypatch, fake, **kw):
        import whisper_models
        from services import transcribe_pipeline
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake)
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: None)
        monkeypatch.setattr(whisper_models, "get_whisper_device_info", lambda *a, **k: {})
        monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: None)
        return transcribe_pipeline._transcribe_pipeline(
            transcribe_pipeline._ThreadReporter(None), str(tmp_path / "a.wav"), "whisper", None,
            "zh", "simplified", "medium", 5, 300, 0.5, False, "auto",
            kw.pop("realign_long_segments", False), False, False, None,
            "", False, "whisper", "whisper_diff", **kw)

    def test_the_sensitive_threshold_applies_only_to_the_sensitive_preset(self, developer_mode):
        sensitive = {"sensitivity_preset": "sensitive"}
        assert timing_settings_service.vad_threshold_for(sensitive) == 0.35
        assert timing_settings_service.vad_threshold_for({}) == 0.5
        settings_service.set_settings({"sensitive_vad_threshold": 0.25})
        assert timing_settings_service.vad_threshold_for(sensitive) == 0.25
        assert timing_settings_service.vad_threshold_for({}) == 0.5
        # The title's own threshold still wins over the preset.
        assert timing_settings_service.vad_threshold_for({**sensitive, "vad_threshold": 0.7}) == 0.7

    def test_the_default_sensitive_threshold_is_unchanged_for_other_callers(self):
        assert sensitivity_preset.effective_vad_threshold(None, "sensitive") == 0.35
        assert sensitivity_preset.stored_vad_threshold({"sensitivity_preset": "sensitive"}) == 0.35

    def test_the_missed_speech_check_uses_the_saved_threshold(self, developer_mode, monkeypatch):
        seen = []
        monkeypatch.setattr(vad_segments, "speech_spans",
                            lambda chunk, sr, **kw: seen.append(kw["threshold"]) or [])
        speech_coverage_service._detect_speech(np.zeros(16000, dtype=np.float32))
        settings_service.set_settings({"speech_coverage_vad_threshold": 0.4})
        speech_coverage_service._detect_speech(np.zeros(16000, dtype=np.float32))
        assert seen == [0.3, 0.4]


# --- Group B: line splitting and tidying -------------------------------------------

def _line_with_words(text, pauses, step=0.25):
    """One transcribed segment whose words are the characters of `text`."""
    words, t = [], 0.0
    for i, ch in enumerate(text):
        t += pauses.get(i, 0.0)
        words.append({"start": t, "end": t + step, "word": ch})
        t += step
    return {"start": 0.0, "end": t, "text": text, "words": words}


SEGMENTS = [
    {"start": 0.0, "end": 12.0, "text": "今天天气很好。我们出去走走吧，然后回家吃饭。明天再说。"},
    _line_with_words("一二三四五六七八九十" * 6, {2: 1.0, 30: 0.6}),
    {"start": 0.0, "end": 3.0, "text": "短句。"},
    {"start": 0.0, "end": 9.0, "text": "a long english line with no cjk characters at all in it"},
]


class TestLineSplitting:
    def test_the_default_run_cuts_exactly_as_it_did_before_the_settings_existed(self):
        # Captured from the splitter before max_seconds, max_cjk_chars and floors were arguments;
        # only the word-edge padding of word-timed pieces has changed since.
        import long_line_split
        golden = [(0.0, 3.111, "今天天气很好。"), (3.111, 9.778, "我们出去走走吧，然后回家吃饭。"),
                  (9.778, 12.0, "明天再说。"),
                  (0.0, 8.7, "一二三四五六七八九十" * 3), (9.0, 16.6, "一二三四五六七八九十" * 3),
                  (0.0, 3.0, "短句。"),
                  (0.0, 9.0, "a long english line with no cjk characters at all in it"),
                  (0.0, 5.2, "一二三四五六七八九十" * 2), (5.4, 10.5, "一二三四五六七八九十" * 2)]
        segments = SEGMENTS + [_line_with_words("一二三四五六七八九十" * 4, {20: 0.5})]
        out = long_line_split.split_long_segments(segments)
        assert [(round(p["start"], 3), round(p["end"], 3), p["text"]) for p in out] == golden
        assert all(p.get("flag") is None for p in out)
        explicit = long_line_split.split_long_segments(
            segments, max_seconds=8.0, max_cjk_chars=40, floors=segment_splitting.PieceFloors(0.8, 4, 2))
        assert explicit == out

    def test_the_sentence_rules_default_to_the_ones_asr_backend_always_had(self):
        rules = segment_splitting.SplitRules(max_seconds=8.0, max_chars=40, per_sentence=True,
                                             count_latin=False)
        assert asr_backend.sentence_split_rules() == asr_backend.SENTENCE_SPLIT_RULES == rules

    def test_default_floors_leave_the_cut_logic_alone(self):
        seg = SEGMENTS[1]
        assert (segment_splitting.split_long_segments([seg], floors=segment_splitting.PieceFloors())
                == segment_splitting.split_long_segments([seg]))

    def test_the_pipeline_cuts_with_the_default_limits_and_with_the_saved_ones(
            self, isolated_db, tmp_path, monkeypatch):
        text = "今天天气很好。我们出去走走吧。"
        fake = lambda *a, **k: [{"start": 0.0, "end": 6.0, "text": text}]  # noqa: E731
        run = lambda **kw: TestVoiceDetection._whisper_run(tmp_path, monkeypatch, fake, **kw)  # noqa: E731
        assert [ln.zh for ln in run()["lines"]] == [text]
        settings_service.set_settings({"split_max_seconds": 4})
        out = run(timing=timing_settings_service.values())
        assert [ln.zh for ln in out["lines"]] == ["今天天气很好。", "我们出去走走吧。"]
        # A run handed no values is still the built-in limits, whatever is saved.
        assert [ln.zh for ln in run()["lines"]] == [text]

    def test_the_character_limit_reaches_the_cut(self, isolated_db, tmp_path, monkeypatch):
        first, second = "今天天气真的很好啊朋友们。", "我们一起出去走走吧好不好。"
        fake = lambda *a, **k: [{"start": 0.0, "end": 3.0, "text": first + second}]  # noqa: E731
        run = lambda **kw: TestVoiceDetection._whisper_run(tmp_path, monkeypatch, fake, **kw)  # noqa: E731
        assert [ln.zh for ln in run()["lines"]] == [first + second]
        settings_service.set_settings({"split_max_cjk_chars": 20})
        assert [ln.zh for ln in run(timing=timing_settings_service.values())["lines"]] == [first, second]

    def test_a_smaller_shortest_piece_lets_a_pause_near_the_edge_cut(self):
        seg = _line_with_words("一二三四五六七八九十" * 6, {2: 1.0})
        assert len(segment_splitting.split_long_segments([seg])) == 1
        floors = segment_splitting.PieceFloors(0.3, 2, 2)
        assert [len(p["text"]) for p in segment_splitting.split_long_segments([seg], floors=floors)] == [2, 58]

    def test_the_floors_in_settings_reach_the_cut(self, developer_mode, tmp_path, monkeypatch):
        seg = _line_with_words("一二三四五六七八九十" * 6, {2: 1.0})
        fake = lambda *a, **k: [seg]  # noqa: E731
        run = lambda **kw: TestVoiceDetection._whisper_run(tmp_path, monkeypatch, fake, **kw)  # noqa: E731
        # No pause may cut two characters off the front, so the line is cut evenly (and flagged).
        assert run(timing=timing_settings_service.values())["lines"][0].flag == "timing_uncertain"
        settings_service.set_settings({"split_min_piece_cjk_chars": 2, "split_min_piece_seconds": 0.3})
        first = run(timing=timing_settings_service.values())["lines"][0]
        assert (first.zh, first.flag) == ("一二", None)

    def test_the_resplit_size_cap_is_still_ten_times_the_line_text_cap(self):
        from services import resplit_config
        assert resplit_config.RESPLIT_MAX_CHARS == 10 * restructure_service.MAX_LINE_TEXT_CHARS

    def test_the_review_resplit_follows_the_saved_limits(self, developer_mode):
        ln = Line(idx=0, start=0.0, end=6.0, zh="今天天气很好。我们出去走走吧。", id=1)
        default = resplit_service._Resplit(**timing_settings_service.resplit_limits())
        assert default == resplit_service._Resplit()
        assert [p["text"] for p in default.split(ln)] == [ln.zh]
        settings_service.set_settings({"split_max_seconds": 4})
        saved = resplit_service._Resplit(**timing_settings_service.resplit_limits())
        assert [p["text"] for p in saved.split(ln)] == ["今天天气很好。", "我们出去走走吧。"]

    def test_the_stock_phrase_silence_decides_whether_a_credit_line_is_dropped(self):
        credit = {"start": 0.0, "end": 1.0, "text": "Thanks for watching"}
        near = [credit, {"start": 4.0, "end": 5.0, "text": "ok"}]
        assert whisper_hallucination.filter_hallucinated_segments(near) == near
        assert whisper_hallucination.filter_hallucinated_segments(near, 4, 3.0) == near[1:]
        assert whisper_hallucination.filter_hallucinated_segments(near, 4, 5.0) == near

    def test_the_saved_stock_phrase_and_loop_limits_reach_transcribe_for_timing_callers(
            self, developer_mode, tmp_path, monkeypatch):
        settings_service.set_settings({"stock_phrase_isolation_seconds": 3, "repeat_loop_min_repeats": 3})
        seen = {}
        fake = lambda *a, **k: seen.update(k) or [{"start": 0.0, "end": 2.0, "text": "你好"}]  # noqa: E731
        TestVoiceDetection._whisper_run(tmp_path, monkeypatch, fake, timing=timing_settings_service.values())
        assert (seen["stock_isolation_sec"], seen["filter_hallucination_repeats"]) == (3.0, 3)

    def test_the_repeat_limit_collapses_a_shorter_run(self):
        run = [{"start": float(i), "end": i + 0.5, "text": "嗯"} for i in range(3)]
        assert len(whisper_hallucination.filter_hallucinated_segments(run, 4)) == 3
        assert len(whisper_hallucination.filter_hallucinated_segments(run, 3)) == 1

    def test_realign_limits_reach_the_realigner(self, developer_mode, tmp_path, monkeypatch):
        settings_service.set_settings({"realign_min_seconds": 9, "realign_pause_seconds": 0.8})
        seen = {}
        monkeypatch.setattr(word_align, "realign_oversized_segments",
                            lambda segs, *a, **k: seen.update(k) or segs)
        fake = lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "你好"}]  # noqa: E731
        TestVoiceDetection._whisper_run(tmp_path, monkeypatch, fake, realign_long_segments=True,
                                        timing=timing_settings_service.values())
        assert (seen["min_duration_to_realign"], seen["min_pause_seconds"]) == (9.0, 0.8)

    def test_the_default_realign_call_passes_the_old_defaults(self, isolated_db, tmp_path, monkeypatch):
        seen = {}
        defaults = inspect.signature(word_align.realign_oversized_segments).parameters
        monkeypatch.setattr(word_align, "realign_oversized_segments",
                            lambda segs, *a, **k: seen.update(k) or segs)
        fake = lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "你好"}]  # noqa: E731
        TestVoiceDetection._whisper_run(tmp_path, monkeypatch, fake, realign_long_segments=True)
        assert (seen["min_duration_to_realign"], seen["min_pause_seconds"]) == (12.0, 0.6)
        assert (defaults["min_duration_to_realign"].default, defaults["min_pause_seconds"].default) == (12.0, 0.6)

    def test_the_gap_fill_minimum_follows_the_setting(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="a"),
                                     Line(idx=1, start=4.0, end=5.0, zh="b"),
                                     Line(idx=2, start=6.5, end=7.0, zh="c")])
        spans = lambda: [(g["start"], g["end"]) for g in transcribe_gap_service.list_gaps(did)["gaps"]]  # noqa: E731
        assert spans() == [(1.0, 4.0)]
        settings_service.set_settings({"gap_fill_min_gap_seconds": 1.0})
        assert spans() == [(1.0, 4.0), (5.0, 6.5)]
        # An explicit argument still wins.
        assert transcribe_gap_service.list_gaps(did, 10)["gaps"] == []

    def test_the_retime_window_follows_the_setting(self, developer_mode):
        lines = [Line(idx=0, start=10.0, end=11.0, zh="a")]
        default = retime_service._segments(lines, [0])
        assert (default[0]["start"], default[0]["end"]) == (7.0, 14.0)
        settings_service.set_settings({"retime_window_pad_seconds": 1.5})
        pad = timing_settings_service.value("retime_window_pad_seconds")
        wide = retime_service._segments(lines, [0], pad)
        assert (wide[0]["start"], wide[0]["end"]) == (8.5, 12.5)


# --- Sentence grouping and scene breaks -------------------------------------------

def _fragments():
    """Four fragments 0.5 s apart, then one after a 2 s pause: one sentence, then another."""
    spans = [(0.0, 1.0), (1.5, 2.5), (3.0, 4.0), (4.5, 5.5), (7.5, 8.5)]
    return [Line(idx=i, start=s, end=e, zh=f"片段{i}", id=10 + i) for i, (s, e) in enumerate(spans)]


class TestSentenceGrouping:
    def test_the_default_limits_group_exactly_as_the_constants_do(self, isolated_db):
        lines = _fragments()
        assert timing_settings_service.sentence_limits() == sentence_groups.DEFAULT_LIMITS
        by_default = [[ln.idx for ln in g] for g in sentence_groups.group_fragments(lines, lines)]
        assert by_default == [[0, 1, 2, 3], [4]]
        assert by_default == [[ln.idx for ln in g] for g in sentence_groups.group_fragments(
            lines, lines, timing_settings_service.sentence_limits())]

    def _groups(self):
        lines = _fragments()
        return [[ln.idx for ln in g] for g in sentence_groups.group_fragments(
            lines, lines, timing_settings_service.sentence_limits())]

    def test_the_line_limit_reaches_the_grouping(self, developer_mode):
        settings_service.set_settings({"sentence_group_max_lines": 2})
        assert self._groups() == [[0, 1], [2, 3], [4]]

    def test_the_pause_limit_reaches_the_grouping(self, isolated_db):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", id=1), Line(idx=1, start=2.5, end=3.5, zh="世界", id=2)]
        groups = lambda: [[ln.idx for ln in g] for g in sentence_groups.group_fragments(  # noqa: E731
            lines, lines, timing_settings_service.sentence_limits())]
        assert groups() == [[0], [1]]
        settings_service.set_settings({"sentence_group_gap_seconds": 2.0})
        assert groups() == [[0, 1]]
        settings_service.set_settings({"sentence_group_gap_seconds": 0.3})
        assert self._groups() == [[0], [1], [2], [3], [4]]

    def test_the_time_limit_reaches_the_grouping(self, developer_mode):
        settings_service.set_settings({"sentence_group_max_seconds": 5.0})
        assert self._groups() == [[0, 1, 2], [3], [4]]

    def test_the_character_limit_reaches_the_grouping(self, developer_mode):
        lines = _fragments()
        for ln in lines:
            ln.zh = "一二三四五六七八九十"
        settings_service.set_settings({"sentence_group_max_chars": 30})
        groups = sentence_groups.group_fragments(lines, lines, timing_settings_service.sentence_limits())
        assert [[ln.idx for ln in g] for g in groups] == [[0, 1, 2], [3], [4]]

    def test_the_scene_break_gap_reaches_the_batch_plan(self, isolated_db):
        lines = [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.0, zh=f"l{i}", id=i + 1) for i in range(12)]
        for ln in lines[7:]:
            ln.start += 1.5
            ln.end += 1.5
        engine_batches = lambda **kw: self._batches(lines, **kw)  # noqa: E731
        assert engine_batches() == [8, 4]
        assert engine_batches(scene_break_gap=timing_settings_service.translate_limits()["scene_break_gap"]) == [8, 4]
        settings_service.set_settings({"scene_break_gap_seconds": 2.0})
        assert engine_batches(**timing_settings_service.translate_limits()) == [7, 5]

    @staticmethod
    def _batches(lines, **kw):
        from engine_backends import translate_pipeline as engine_translate_pipeline

        class Recorder:
            supports_reference = True
            model = "m"

            def __init__(self):
                self.batches = []

            def translate_batch(self, zh_lines, context):
                self.batches.append(list(zh_lines))
                return [f"en {z}" for z in zh_lines]
        engine = Recorder()
        engine_translate_pipeline.translate_lines_with_engine([Line(idx=ln.idx, start=ln.start, end=ln.end, zh=ln.zh, id=ln.id)
                                        for ln in lines], engine, {}, batch_size=8,
                                       scene_aware_batches=True, **kw)
        return [len(b) for b in engine.batches]

    def test_translate_by_sentence_takes_the_saved_limits(self, developer_mode):
        from engine_backends import translate_pipeline as engine_translate_pipeline
        lines = _fragments()

        class Recorder:
            supports_reference = True
            model = "m"

            def __init__(self):
                self.contexts = []

            def translate_batch(self, zh_lines, context):
                self.contexts.append(context.get("sentence_groups"))
                return [f"en {z}" for z in zh_lines]
        engine = Recorder()
        engine_translate_pipeline.translate_lines_with_engine(lines, engine, {}, translate_by_sentence=True)
        assert engine.contexts == [[[0, 1, 2, 3]]]
        settings_service.set_settings({"sentence_group_max_lines": 2})
        engine = Recorder()
        engine_translate_pipeline.translate_lines_with_engine(_fragments(), engine, {}, translate_by_sentence=True,
                                       **timing_settings_service.translate_limits())
        assert engine.contexts == [[[0, 1], [2, 3]]]


class TestSavedValuesReachTheRun:
    """The services hand the saved numbers down to the engines and workers."""

    def test_translate_gets_the_saved_limits_from_the_workspace_job(self, isolated_db, monkeypatch):
        from tests.test_plan_batches import TestSceneAwareSetting
        settings_service.set_settings({"scene_break_gap_seconds": 2.5, "sentence_group_gap_seconds": 0.8})
        kw = TestSceneAwareSetting()._seen(isolated_db, monkeypatch)
        assert kw["scene_break_gap"] == 2.5
        assert kw["sentence_limits"].max_gap_seconds == 0.8

    def test_the_default_translate_limits_are_the_built_in_ones(self, isolated_db, monkeypatch):
        from tests.test_plan_batches import TestSceneAwareSetting
        kw = TestSceneAwareSetting()._seen(isolated_db, monkeypatch)
        assert kw["scene_break_gap"] == translate_pipeline.SCENE_BREAK_GAP_SECONDS
        assert kw["sentence_limits"] == sentence_groups.DEFAULT_LIMITS

    def test_a_transcription_run_hands_its_worker_the_saved_values(self, isolated_db, monkeypatch):
        from tests.test_transcribe_service import _capture_worker_start, _drama_with_audio
        from services import transcribe_service
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["timing"] == {s.key: s.default for s in ROWS}
        assert "split_max_seconds" not in captured["start_kwargs"]["run_settings"]
        settings_service.set_settings({"split_max_seconds": 6})
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["timing"]["split_max_seconds"] == 6
        assert captured["start_kwargs"]["run_settings"]["split_max_seconds"] == 6

    def test_the_retranscribe_threshold_comes_from_the_same_reader(self, isolated_db):
        from services import transcribe_service
        did = isolated_db.create_drama(title_en="D", sensitivity_preset="sensitive")
        settings_service.set_settings({})
        assert transcribe_service.get_transcribe_config(did)["effective_vad_threshold"] == 0.35


class TestRunSettings:
    def test_an_unchanged_run_records_no_timing_rows(self):
        out = run_settings_service.for_transcribe({}, "zh", "simplified", "medium", 5, 300, 0.5, "normal",
                                                  0.0, 0.35, "auto", False, "whisper", "whisper_diff",
                                                  "whisper", False, None, None, timing={})
        assert not set(out) & set(BY_KEY)

    def test_a_changed_row_is_recorded(self):
        out = run_settings_service.for_transcribe(
            {}, "zh", "simplified", "medium", 5, 300, 0.5, "normal", 0.0, 0.35, "auto", False, "whisper",
            "whisper_diff", "whisper", False, None, None,
            timing={"split_max_seconds": 6.0, "qwen_vad_pad_ms": 40})
        assert out["split_max_seconds"] == 6.0 and out["qwen_vad_pad_ms"] == 40

    def test_every_row_is_allowed_in_the_record(self):
        assert set(BY_KEY) <= set(run_settings_service.ALLOWED_KEYS)
