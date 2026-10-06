"""
benchmark.py -- accuracy/regression tracking across content types (audio
drama, streamer VOD, novel, manhua), the "prevent an 'improvement' from
silently making things worse" tool.

WHY THIS EXISTS: every pipeline change this project makes (a VAD
threshold, a new translation engine, an OCR backend swap) is a bet that
it helps -- verified so far by reasoning about the change and by unit
tests against mocked library boundaries, never against real content,
since no real audio/image/text samples exist in the development
environment. This module is the tool for the OTHER half: register a
handful of your own real reference cases once (a short audio clip, a
novel excerpt, a manhua page -- one per content type you actually use),
optionally with a known-correct reference transcript/translation/OCR
text, then re-run the SAME cases through the CURRENT pipeline any time
you change something, and see whether the score went up or down instead
of just hoping.

Scoring: a case with no reference text gets STRUCTURAL checks only
(did it produce non-empty output, how long did it take, did it error) --
still useful as a smoke test, just not a quality number. A case WITH a
reference gets a 0.0-1.0 character-level similarity score via
difflib.SequenceMatcher -- the same tool align_transcript_to_timing()
already uses elsewhere in this app for the same reason: no new NLP-
metric dependency, and "how much of the reference text's characters
show up in the same relative order" is a reasonable, easy-to-explain
proxy for transcription/OCR/translation accuracy without needing a
proper WER/BLEU library this app doesn't otherwise depend on. It is a
proxy, not a ground truth: a real WER-style score treats word order and
substitutions differently than raw character overlap does, so a
noticeably wrong translation with the right words in a different order
can still score deceptively high, and vice versa.

History is stored in the database (db.py's benchmark_cases/benchmark_runs
tables) rather than a JSON file, for the same reason everything else in
this app is: it lives in the same backed-up, always-present sqlite file
already used for dramas/lines/glossary, not a separate file the person
has to remember exists.
"""
import difflib
import time


def score_text_similarity(actual: str, reference: str) -> float:
    """
    Character-level similarity ratio, 0.0-1.0, via difflib.SequenceMatcher
    (same tool this app's own align_transcript_to_timing() already uses).
    Whitespace-normalized on both sides first -- a transcript/translation
    that's word-for-word identical but re-wrapped onto different lines
    shouldn't score lower just because of where the newlines fall.
    """
    a = "".join((actual or "").split())
    b = "".join((reference or "").split())
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def translation_similarity(actual: str, reference: str):
    """(score 0.0-1.0, metric): "chrf" (plain character 6-gram F-score) when
    sacrebleu is installed, else "similarity" (the difflib ratio above).
    The two scales differ, so callers store the metric next to the score."""
    try:
        from sacrebleu.metrics import CHRF
    except ImportError:
        return score_text_similarity(actual, reference), "similarity"
    a, b = (actual or "").strip(), (reference or "").strip()
    if not a and not b:
        return 1.0, "chrf"
    # word_order=0 keeps it chrF, not chrF++: CJK has no word boundaries to count.
    return CHRF(char_order=6, word_order=0).sentence_score(a, [b]).score / 100.0, "chrf"


def run_transcription_case(case: dict, whisper_size: str = "medium", use_gpu: bool = False):
    """
    case: {"input_path": audio file, "source_language": "zh"/"ja"/"ko",
    "reference_text": optional known-correct transcript}.

    Runs this app's own core.transcribe_for_timing() (the exact function
    every real drama's transcription goes through) and joins every
    segment's text into one string to score -- this benchmark cares about
    overall transcription accuracy, not per-segment timing correctness
    (diagnose_line_coverage already covers timing-shape problems
    separately, on a real drama's own lines, not a benchmark case).

    Returns {"output_text", "score" (None if no reference_text),
    "duration_seconds", "error" (None on success)}. An exception is
    caught and recorded, not raised -- one broken/missing case file must
    never stop the rest of the suite from running.
    """
    import core

    started = time.monotonic()
    try:
        segments = core.transcribe_for_timing(
            case["input_path"], model_size=whisper_size,
            language=case.get("source_language", "zh"), use_gpu=use_gpu)
        output_text = "".join(s["text"] for s in segments)
        error = None
    except Exception as exc:
        output_text = ""
        error = str(exc)
    duration = time.monotonic() - started

    reference = case.get("reference_text")
    score = score_text_similarity(output_text, reference) if reference else None
    return {"output_text": output_text, "score": score, "duration_seconds": duration, "error": error}


def run_translation_case(case: dict, engine):
    """
    case: {"source_text": the text to translate, "reference_text": optional
    known-good translation}.

    Runs the given engine's own translate_batch() -- whichever engine the
    caller picked (Claude/DeepSeek/Gemini/etc, or NLLB),
    exactly the call every real translated line in this app goes through.
    """
    started = time.monotonic()
    try:
        result = engine.translate_batch([case["source_text"]], {})
        output_text = result[0] if result else ""
        error = None
    except Exception as exc:
        output_text = ""
        error = str(exc)
    duration = time.monotonic() - started

    reference = case.get("reference_text")
    score = score_text_similarity(output_text, reference) if reference else None
    cost = 0.0
    if hasattr(engine, "last_usage"):
        import translate_engines
        usage = engine.last_usage
        cost = translate_engines.estimate_cost(
            getattr(engine, "model", ""), usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return {"output_text": output_text, "score": score, "duration_seconds": duration,
            "error": error, "cost_usd": cost}


def run_ocr_case(case: dict, backend: str = "tesseract"):
    """
    case: {"input_path": a page/panel image, "source_language": "zh"/"ja"/"ko",
    "reference_text": optional known-correct OCR text}.

    Runs ocr.extract_text_from_images() -- the same function Scanlate and
    novel-page-scan OCR both already use.
    """
    import ocr as ocr_module

    started = time.monotonic()
    try:
        output_text = ocr_module.extract_text_from_images(
            [case["input_path"]], backend=backend, source_language=case.get("source_language", "zh"))
        error = None
    except Exception as exc:
        output_text = ""
        error = str(exc)
    duration = time.monotonic() - started

    reference = case.get("reference_text")
    score = score_text_similarity(output_text, reference) if reference else None
    return {"output_text": output_text, "score": score, "duration_seconds": duration, "error": error}


# Maps a case's own "stage" to the function that runs it -- run_suite()
# dispatches through this instead of an if/elif chain, so adding a new
# stage later (e.g. hardsub OCR, or a dubbing/TTS quality check) is one
# registry entry, not a new branch buried in the orchestration loop.
_STAGE_RUNNERS = {
    "transcription": run_transcription_case,
    "translation": run_translation_case,
    "ocr": run_ocr_case,
}


def run_suite(cases: list, stage: str, **kwargs):
    """
    Runs every case in `cases` (all of the SAME stage -- "transcription",
    "translation", or "ocr") through that stage's runner, with `kwargs`
    passed through (e.g. engine=..., whisper_size=..., backend=...).

    Returns a list of result dicts, one per case, each with the case's
    own "id" merged in so a caller can match a result back to its case
    (e.g. to save it against the right benchmark_cases row) -- a case's
    own failure is isolated to its own result (see each runner's own
    try/except), never stopping the rest of the suite.
    """
    runner = _STAGE_RUNNERS.get(stage)
    if runner is None:
        raise ValueError(f"Unknown benchmark stage: {stage!r} -- expected one of {list(_STAGE_RUNNERS)}")
    results = []
    for case in cases:
        result = runner(case, **kwargs)
        result["case_id"] = case.get("id")
        results.append(result)
    return results


def compare_configs(case: dict, stage: str, configs: list):
    """
    Runs ONE case through each config back to back, for a
    side-by-side view. configs: [(label, kwargs), ...] -- e.g. two
    translation engines, two OCR backends, two Whisper sizes.

    Returns one result per config, each with its "label" added. Nothing
    is saved: a comparison isn't a run of "the current pipeline", so
    writing it into the case's run history would make the next
    run-over-run regression check compare engine A against engine B.
    """
    results = []
    for label, kwargs in configs:
        result = run_suite([case], stage, **kwargs)[0]
        result["label"] = label
        results.append(result)
    return results
