"""Per-span language detection for mixed-language titles. Models, audio, VAD and the
language detector are all fakes."""
from types import SimpleNamespace

import numpy as np
import pytest

import asr_backend as ab
import mixed_language as ml
from core import Line
from services import asr_options_service
from vad_segments import Span

KO, ZH, JA = "안녕하세요 여러분", "大家好，今天天气很好", "こんにちは、みなさん"
CYRILLIC = "Привет всем"


def _span_runner(script):
    """script: {span start: (text, detected)}; records every retry as (start, lang)."""
    retries = []

    def run(span):
        text, detected = script[span.start_s]
        return [{"start": span.start_s, "end": span.end_s, "text": text}], detected

    def retry(span, lang):
        retries.append((span.start_s, lang))
        text = script.get(("retry", span.start_s), KO)
        return [{"start": span.start_s, "end": span.end_s, "text": text}]

    return run, retry, retries


def test_script_check():
    assert ml.text_matches_language(KO, "ko") and ml.text_matches_language(JA, "ja")
    assert ml.text_matches_language("大家好 OK", "zh")
    assert not ml.text_matches_language(CYRILLIC, "ko")
    assert not ml.text_matches_language(KO, "ja")      # Hangul forced into a kana language
    assert not ml.text_matches_language(JA, "zh")      # kana is not Chinese
    assert ml.text_matches_language("…", "ko")         # no letters: nothing to disagree with


def test_lang_written_only_when_different_from_source():
    spans = [Span(0, 2), Span(2, 4), Span(4, 6)]
    run, retry, retries = _span_runner({0: (KO, "ko"), 2: (ZH, "zh"), 4: (JA, "ja")})
    out = ml.transcribe_spans(spans, "ko", run, retry)
    assert [s.get("lang") for s in out] == [None, "zh", "ja"]
    assert not retries and not any("flag" in s for s in out)


def test_single_language_title_has_no_lang_or_flag():
    run, retry, _ = _span_runner({0: (KO, "ko"), 2: (KO, None)})
    out = ml.transcribe_spans([Span(0, 2), Span(2, 4)], "ko", run, retry)
    assert all("lang" not in s and "flag" not in s for s in out)


def test_cyrillic_span_is_retried_in_the_source_language():
    run, retry, retries = _span_runner({0: (CYRILLIC, "ko"), ("retry", 0): KO})
    out = ml.transcribe_spans([Span(0, 2)], "ko", run, retry)
    assert retries == [(0, "ko")]
    assert out[0]["text"] == KO and "lang" not in out[0] and "flag" not in out[0]


def test_language_outside_the_allowed_set_is_retried():
    run, retry, retries = _span_runner({0: (CYRILLIC, "ru"), ("retry", 0): KO})
    ml.transcribe_spans([Span(0, 2)], "ko", run, retry)
    assert retries == [(0, "ko")]


def test_still_disagreeing_after_retry_keeps_text_and_flags_it():
    run, retry, retries = _span_runner({0: (CYRILLIC, "zh"), ("retry", 0): CYRILLIC})
    out = ml.transcribe_spans([Span(0, 2)], "ko", run, retry)
    assert len(retries) == 1                     # once only
    assert out[0]["text"] == CYRILLIC and "lang" not in out[0]
    assert out[0]["flag"] == "language_uncertain" and out[0]["flag_note"]


def test_pick_allowed_language_ignores_languages_outside_the_set():
    probs = [("ru", 0.7), ("ko", 0.2), ("ja", 0.1)]
    assert ml.pick_allowed_language(probs, "zh") == "ko"
    assert ml.pick_allowed_language([("ru", 1.0)], "zh") == "zh"
    assert ml.pick_allowed_language(None, "ja") == "ja"


def test_cancel_check_runs_before_each_span():
    run, retry, _ = _span_runner({0: (KO, "ko"), 2: (KO, "ko")})
    calls = []

    def cancel():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError):
        ml.transcribe_spans([Span(0, 2), Span(2, 4)], "ko", run, retry, cancel_check=cancel)


class FakeWhisper:
    """detect_language gives the first span an unrestricted Russian guess."""
    def __init__(self, by_second):
        self.by_second = by_second
        self.transcribe_languages = []

    def detect_language(self, wave):
        second = int(round(float(wave[0])))
        lang = self.by_second[second]
        return lang, 0.9, [("ru", 0.6), (lang, 0.3)]

    def transcribe(self, wave, language, **_kw):
        self.transcribe_languages.append(language)
        text = {"ko": KO, "zh": ZH, "ja": JA}[language]
        return [SimpleNamespace(text=text, start=0.0, end=1.0, words=None)], None


def test_whisper_path_detects_per_span_and_restricts_to_allowed(monkeypatch):
    # Sample value = start second, so the fake detector knows which span it was given.
    audio = np.concatenate([np.full(16000, s, dtype="float32") for s in range(9)])
    model = FakeWhisper({0: "ko", 2: "zh", 5: "ja"})
    monkeypatch.setattr(ab, "load_audio_16k", lambda path: audio)
    monkeypatch.setattr(ml, "load_whisper_model", lambda *a, **k: model)
    out = ml.transcribe_mixed_whisper(
        "x.wav", "ko", "tiny", vad_fn=lambda a, sr: [(0, 2), (3, 5), (6, 8)])
    assert model.transcribe_languages == ["ko", "zh", "ja"]
    assert [s.get("lang") for s in out] == [None, "zh", "ja"]
    assert out[1]["start"] == pytest.approx(2.9)


class FakeQwenResult:
    def __init__(self, text, language):
        self.text, self.language = text, language


class FakeQwen:
    def __init__(self, heard):
        self.heard = list(heard)
        self.languages = []

    def transcribe(self, audio, language):
        self.languages.append(language)
        if language is None:
            text, name = self.heard.pop(0)
            return [FakeQwenResult(text, name)]
        return [FakeQwenResult(KO, language)]


@pytest.fixture
def qwen(monkeypatch):
    monkeypatch.setattr(ab, "load_audio_16k", lambda path: np.zeros(16000 * 6, dtype="float32"))
    monkeypatch.setattr(ab, "extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())

    def use(heard):
        model = FakeQwen(heard)
        monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": model)
        return model
    return use


VAD = lambda a, sr: [(0, 2), (3, 5)]  # noqa: E731


def test_qwen_vad_mixed_uses_its_own_language_per_span(qwen):
    model = qwen([(KO, "Korean"), (ZH, "Chinese")])
    out = ab.Qwen3ASRVadBackend().transcribe("x.wav", "ko", vad_fn=VAD, mixed_languages=True)
    assert [s.get("lang") for s in out] == [None, "zh"]
    assert model.languages == [None, None]


def test_qwen_vad_mixed_retries_unknown_language_in_source_language(qwen):
    model = qwen([(CYRILLIC, "Russian"), (ZH, "Chinese")])
    out = ab.Qwen3ASRVadBackend().transcribe("x.wav", "ko", vad_fn=VAD, mixed_languages=True)
    assert model.languages == [None, "Korean", None]
    assert out[0]["text"] == KO and "lang" not in out[0]


def test_qwen_vad_without_mixed_is_unchanged(qwen):
    model = qwen([])
    out = ab.Qwen3ASRVadBackend().transcribe("x.wav", "ko", vad_fn=VAD)
    assert model.languages == ["Korean", "Korean"]
    assert all(set(s) == {"start", "end", "text"} for s in out)


def test_option_defaults_off_and_validates(isolated_db):
    assert asr_options_service.get_asr_options()["mixed_languages"] is False
    assert asr_options_service.set_asr_options(mixed_languages=True)["mixed_languages"] is True
    with pytest.raises(Exception):
        asr_options_service.set_asr_options(mixed_languages="yes")


def test_pipeline_writes_lang_on_lines_only_in_mixed_mode(monkeypatch):
    import services.transcribe_service as ts
    seen = {}

    def fake_mixed(audio_path, language, size, **kw):
        seen["mixed"] = True
        return [{"start": 0.0, "end": 2.0, "text": KO},
                {"start": 2.0, "end": 4.0, "text": ZH, "lang": "zh"}]

    monkeypatch.setattr(ml, "transcribe_mixed_whisper", fake_mixed)
    monkeypatch.setattr(ts.core_module, "load_whisper_model", lambda *a, **k: None)
    monkeypatch.setattr(ts.core_module, "is_whisper_model_cached", lambda s: True)
    monkeypatch.setattr(ts.core_module, "get_whisper_device_info", lambda *a, **k: {})
    monkeypatch.setattr(ts.core_module, "release_gpu_models", lambda: None)
    monkeypatch.setattr(ts, "transcribe_for_timing",
                        lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": KO}])

    class Stage:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def start(self): return self
        def stop(self): pass
    rep = SimpleNamespace(cancelled=lambda: False, raise_if_cancelled=lambda: None,
                          progress=lambda *a, **k: None, stage=lambda *a, **k: Stage())

    def run(mixed):
        return ts._transcribe_pipeline(
            rep, "x.wav", "whisper", None, "ko", "simplified", "tiny", 5, 2000, 0.5, False,
            "auto", False, False, False, None, "", False, "whisper", "whisper_diff",
            mixed_languages=mixed)

    mixed_lines = run(True)["lines"]
    assert [ln.lang for ln in mixed_lines] == [None, "zh"] and seen["mixed"]
    assert all(isinstance(ln, Line) for ln in mixed_lines)
    assert [ln.lang for ln in run(False)["lines"]] == [None]
