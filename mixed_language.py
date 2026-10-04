"""mixed_language.py -- transcription of a title whose speakers use several languages.

Language is decided per VAD speech span, not per file: one file-wide guess
misreads the opening of a mixed clip (a real Korean/Chinese/Japanese clip came
back as Russian with Cyrillic text), and a fixed language forces the others
into its script. After each span is transcribed, the text's script is checked
against the language it was transcribed as; a span that disagrees is retried
once with the title's source language.
"""
import re

from core import (
    LINE_LANGUAGES, WHISPER_ANTI_LOOP_KWARGS, filter_hallucinated_segments, is_gpu_error,
    load_whisper_model, tighten_to_words,
)

LANGUAGE_UNCERTAIN_FLAG = "language_uncertain"
LANGUAGE_UNCERTAIN_NOTE = ("The spoken language of this line couldn't be confirmed: the text "
                           "doesn't match the language detected, and a retry in the title's "
                           "language didn't match either -- check the text and the language.")

_SCRIPTS = {
    "hangul": re.compile(r"[ᄀ-ᇿ㄰-㆏가-힯]"),
    "kana": re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]"),
    "han": re.compile(r"[㐀-䶿一-鿿豈-﫿]"),
    "latin": re.compile(r"[A-Za-zÀ-ɏ]"),
}
# Han and Latin are allowed everywhere: names, loanwords and quoted English
# are normal inside Korean and Japanese speech.
_EXPECTED_SCRIPTS = {
    "ko": ("hangul", "han", "latin"),
    "ja": ("kana", "han", "latin"),
    "zh": ("han", "latin"),
    "en": ("latin",),
}
# Share of letters outside the expected scripts above which the text is taken
# to be in another language (or hallucinated Cyrillic and the like).
_DISAGREE_SHARE = 0.5

# Qwen3-ASR names the language it heard in English.
_QWEN_NAME_TO_CODE = {"chinese": "zh", "japanese": "ja", "korean": "ko", "english": "en"}


def text_matches_language(text: str, lang: str) -> bool:
    """False when most of the text's letters are in a script `lang` doesn't
    use. Text with no letters matches (nothing to disagree with)."""
    expected = _EXPECTED_SCRIPTS.get(lang)
    if expected is None:
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return True
    inside = sum(1 for ch in letters if any(_SCRIPTS[s].match(ch) for s in expected))
    return (len(letters) - inside) / len(letters) <= _DISAGREE_SHARE


def transcribe_spans(spans, source_language, run_span, retry_span, cancel_check=None,
                     progress_cb=None) -> list:
    """Segments ({"start", "end", "text"}, plus "lang" and "flag"/"flag_note"
    where they apply) for the speech spans, in order.

    run_span(span) -> (segments, detected language code or None) transcribes
    with the detected language; retry_span(span, lang) -> segments transcribes
    in a given one. "lang" is set only where the span's language is not
    source_language, so a single-language title comes out as it always did.
    A span whose text still disagrees after the retry keeps that text with no
    lang and a language_uncertain flag. cancel_check() should raise to stop."""
    out = []
    for n, span in enumerate(spans):
        if cancel_check:
            cancel_check()
        segments, detected = run_span(span)
        lang = detected if detected in LINE_LANGUAGES else source_language
        uncertain = False
        if not all(text_matches_language(s["text"], lang) for s in segments):
            lang = source_language
            segments = retry_span(span, source_language)
            uncertain = not all(text_matches_language(s["text"], lang) for s in segments)
        for seg in segments:
            seg = dict(seg)
            if uncertain:
                seg["flag"], seg["flag_note"] = LANGUAGE_UNCERTAIN_FLAG, LANGUAGE_UNCERTAIN_NOTE
            elif lang != source_language:
                seg["lang"] = lang
            out.append(seg)
        if progress_cb:
            progress_cb((n + 1) / len(spans))
    return out


def pick_allowed_language(probabilities, fallback):
    """The most probable code in `probabilities` ((code, probability) pairs)
    that is a LINE_LANGUAGES code; `fallback` when none is."""
    best = max(((p, c) for c, p in probabilities or [] if c in LINE_LANGUAGES),
               default=None)
    return best[1] if best else fallback


def _span_runners(model, audio, sr, source_language, beam_size, initial_prompt):
    def transcribe(span, language):
        wave = audio[int(span.start_s * sr):int(span.end_s * sr)]
        kwargs = {"language": language, "vad_filter": False, "beam_size": beam_size,
                  "word_timestamps": True, **WHISPER_ANTI_LOOP_KWARGS}
        if initial_prompt.strip():
            kwargs["initial_prompt"] = initial_prompt.strip()
        segments, _info = model.transcribe(wave, **kwargs)
        out = []
        for s in segments:
            text = s.text.strip()
            if any(ch.isalnum() for ch in text):
                start, end = tighten_to_words(s.start, s.end, getattr(s, "words", None))
                out.append({"start": span.start_s + start, "end": span.start_s + end,
                            "text": text})
        return out

    def run_span(span):
        wave = audio[int(span.start_s * sr):int(span.end_s * sr)]
        # Detection is limited to the allowed languages: the unrestricted
        # best guess is what called a Korean opening Russian.
        _best, _prob, probabilities = model.detect_language(wave)
        lang = pick_allowed_language(probabilities, source_language)
        return transcribe(span, lang), lang

    return run_span, lambda span, lang: transcribe(span, lang)


def transcribe_mixed_whisper(audio_path, source_language, whisper_size, use_gpu=False,
                             local_model_path=None, initial_prompt="", beam_size=5,
                             on_gpu_fallback=None, progress_cb=None, cancel_check=None,
                             vad_fn=None) -> list:
    """The Whisper backend's mixed-language run: speech spans from the Silero
    VAD, the language detected on each, then the same segment list shape
    core.transcribe_for_timing returns (plus "lang"/"flag" per transcribe_spans).
    Uses vad_segments' defaults, not the drama's saved VAD tuning, as the
    Qwen3 speech-detection backend does."""
    import vad_segments
    from asr_backend import load_audio_16k
    sr = 16000
    audio = load_audio_16k(audio_path)
    spans = vad_segments.cap_spans(
        vad_segments.merge_close(vad_segments.speech_spans(audio, sr, vad_fn=vad_fn)), audio, sr)
    if not spans:
        return []

    def run(use_gpu_now):
        model = load_whisper_model(whisper_size, use_gpu=use_gpu_now,
                                   local_model_path=local_model_path)
        run_span, retry_span = _span_runners(model, audio, sr, source_language, beam_size,
                                             initial_prompt)
        return transcribe_spans(spans, source_language, run_span, retry_span,
                                cancel_check=cancel_check, progress_cb=progress_cb)

    try:
        segments = run(use_gpu)
    except Exception as exc:
        # ctranslate2 only fails on a broken CUDA install at inference, as in
        # core.transcribe_for_timing.
        if use_gpu and is_gpu_error(exc):
            if on_gpu_fallback:
                on_gpu_fallback(exc)
            segments = run(False)
        else:
            raise
    return filter_hallucinated_segments(segments)


def qwen_language_code(name):
    """A LINE_LANGUAGES code for the language name Qwen3-ASR reports; None for
    a missing or unknown name."""
    return _QWEN_NAME_TO_CODE.get(str(name or "").strip().lower())
