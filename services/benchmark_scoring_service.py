"""
services/benchmark_scoring_service.py -- the Benchmark Lab's scorers. UI-free.

Turns one output and its reference into a 0.0-1.0 score: chrF or difflib
similarity for translation, 1 - CER / 1 - WER for transcription and OCR
(jiwer when installed, a built-in edit distance when not). Chinese texts are
converted to Simplified on both sides, and transcripts go through one text
normalisation (normalise_text), before CER (script_normaliser). The lab
(benchmark_lab_service) calls score_output and pass_threshold per result.
"""
import functools
import unicodedata

import benchmark
import segment
from core import SOURCE_LANGUAGES
from services.benchmark_script_service import _PAIRS

MAX_TEXT_CHARS = 4000
# A result at or above this counts as a pass (the per-example pass/fail).
PASS_THRESHOLD = 0.8
# chrF scores a sentence far lower than difflib does for the same acceptable
# paraphrase (a close rewording lands near 0.5-0.6, a different meaning below
# 0.3 on hand-checked en/zh pairs), so 0.8 would fail nearly everything.
CHRF_PASS_THRESHOLD = 0.5
# Languages whose transcripts are scored per character (no word spaces).
_CHARACTER_LANGUAGES = SOURCE_LANGUAGES


# Traditional-only character -> its Simplified form, for when OpenCC is absent.
_PAIRS_T2S = str.maketrans({p[1]: p[0] for p in _PAIRS})


def script_normaliser(stage: str, source_language: str = "zh") -> str:
    """How a case's texts are brought to one script before CER: "opencc" or
    "pairs" (the character table, when OpenCC isn't installed) for Chinese
    transcription and OCR, "none" for everything else. Japanese and Korean
    are never converted: kanji forms differ by design there."""
    if stage not in ("transcription", "ocr") or source_language != "zh":
        return "none"
    try:
        segment.opencc_t2s()
    except ImportError:
        return "pairs"
    return "opencc"


def _to_simplified(text: str, normaliser: str) -> str:
    text = text or ""
    if normaliser == "opencc":
        return segment.opencc_t2s().convert(text)
    if normaliser == "pairs":
        return text.translate(_PAIRS_T2S)
    return text


def normalise_text(text: str, unit: str = "char") -> str:
    """The one text form both CER scorers compare, so a score doesn't depend
    on whether jiwer is installed or on how a transcript is punctuated:
    full-width folded to half-width (and half-width kana to full-width) by
    NFKC, lower-cased, punctuation and symbols (including the music notes and
    tildes common in drama transcripts) dropped. Spacing is ignored for
    characters and collapsed to single spaces for words."""
    folded = unicodedata.normalize("NFKC", text or "").lower()
    kept = "".join(ch for ch in folded if unicodedata.category(ch)[0] not in "PS")
    return "".join(kept.split()) if unit == "char" else " ".join(kept.split())


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _edit_distance(a, b) -> int:
    """Levenshtein distance between two sequences (strings or word lists)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


# Scoring is O(n*m) in pure Python: an output far longer than its reference
# is cut to a bound (its extra length already counts fully as errors).
_MAX_SCORED_OUTPUT_FACTOR = 2


def error_rate(actual: str, reference: str, unit: str = "char") -> float:
    """Built-in CER (unit "char", whitespace ignored) or WER (unit "word"):
    edits needed to turn the output into the reference, over the reference
    length. Can exceed 1.0 when the output is much longer."""
    if unit == "word":
        a, r = (actual or "").split(), (reference or "").split()
    else:
        a, r = "".join((actual or "").split()), "".join((reference or "").split())
    if not r:
        return 0.0 if not a else 1.0
    r = r[:MAX_TEXT_CHARS]
    limit = _MAX_SCORED_OUTPUT_FACTOR * len(r) + 10
    extra = max(0, len(a) - limit)
    return (_edit_distance(a[:limit], r) + extra) / len(r)


@functools.lru_cache(maxsize=None)
def _jiwer_transform(jiwer, unit: str, keep_punctuation: bool = False):
    """jiwer's usual normalisation, applied the same way to both texts:
    lower-case, punctuation (unless kept, as for OCR) and extra whitespace
    removed, then split into words (WER) or characters with all whitespace
    dropped (CER). Cached: jiwer 3.x's RemovePunctuation scans all of Unicode
    when it is built."""
    steps = [jiwer.ToLowerCase()]
    if not keep_punctuation:
        steps.append(jiwer.RemovePunctuation())
    if unit == "word":
        steps += [jiwer.RemoveMultipleSpaces(), jiwer.Strip(), jiwer.ReduceToListOfListOfWords()]
    else:
        # Every Unicode space (U+3000 in CJK text too), not only ASCII ones
        # as jiwer.RemoveWhiteSpace does.
        steps += [jiwer.SubstituteRegexes({r"\s+": ""}), jiwer.ReduceToListOfListOfChars()]
    return jiwer.Compose(steps)


def _jiwer_error_rate(actual: str, reference: str, unit: str, keep_punctuation: bool = False):
    """CER/WER from jiwer, or None when jiwer isn't installed or the
    reference is empty after normalisation (the caller falls back)."""
    try:
        import jiwer
    except ImportError:
        return None
    transform = _jiwer_transform(jiwer, unit, keep_punctuation)
    r = [t for sentence in transform(reference or "") for t in sentence][:MAX_TEXT_CHARS]
    if not r:
        return None
    a = [t for sentence in transform(actual or "") for t in sentence]
    # Same length bound as the built-in scorer: output past the bound counts
    # fully as insertions and isn't aligned. Tokens hold no whitespace, so
    # joining them with spaces makes jiwer's word alignment a token alignment.
    limit = _MAX_SCORED_OUTPUT_FACTOR * len(r) + 10
    extra = max(0, len(a) - limit)
    out = jiwer.process_words(" ".join(r), " ".join(a[:limit]))
    return (out.substitutions + out.deletions + out.insertions + extra) / len(r)


def pass_threshold(metric: str) -> float:
    return CHRF_PASS_THRESHOLD if metric == "chrf" else PASS_THRESHOLD


def score_output(stage: str, output: str, reference: str, source_language: str = "zh"):
    """(score 0.0-1.0 or None when there is no reference, metric name,
    scorer): scorer is "jiwer" or "builtin" for CER/WER, "sacrebleu" or
    "builtin" for translation similarity, None when nothing was scored."""
    if stage == "translation":
        if not reference:
            return None, "similarity", None
        score, metric = benchmark.translation_similarity(output, reference)
        return score, metric, "sacrebleu" if metric == "chrf" else "builtin"
    # Both sides go to Simplified first, so CER measures recognition errors,
    # not which script the model or the reference wrote; the Lab's script
    # counts still show the script. Before the edit distance, because a
    # Traditional character is otherwise a substitution.
    normaliser = script_normaliser(stage, source_language)
    unit = "char" if stage == "ocr" or source_language in _CHARACTER_LANGUAGES else "word"
    metric = "cer" if unit == "char" else "wer"
    if not reference:
        return None, metric, None
    output, reference = _to_simplified(output, normaliser), _to_simplified(reference, normaliser)
    if stage == "transcription":
        output, reference = normalise_text(output, unit), normalise_text(reference, unit)
    # User decision (2026-09-30): OCR keeps punctuation in its CER.
    rate, scorer = _jiwer_error_rate(output, reference, unit,
                                     keep_punctuation=stage == "ocr"), "jiwer"
    if rate is None:
        rate, scorer = error_rate(output, reference, unit), "builtin"
    return max(0.0, 1.0 - rate), metric, scorer
