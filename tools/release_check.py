"""Real-hardware release check: `python -m tools.release_check MANIFEST.json [--previous RESULTS.json] [--yes]`.

Runs a private set of golden clips (kept outside the repo, which is public) through the app's own
transcription, speaker detection and translation code on this PC, and writes one markdown results
table plus a JSON file. A transcription clip with "title_settings" runs the app's whole
transcription pipeline with those stored title settings; one without runs plain Whisper.
"fixed_decoding": true on such a clip pins Whisper's temperature at 0, so its score repeats exactly.
A translation clip with "title_settings" (and optionally a "glossary" of {"original", "translation"}) runs
the app's own translate path: id-keyed batches with retry, the reply guard, context lines, the glossary
and the style block; one without calls the bare engine. Each translation clip runs 3 times
(--translation-repeats) and scores the median, and its row counts the lines that came back blank.
Each transcription clip runs 5 times (--repeats) and scores the median, because Whisper's
default decoding differs from run to run (one title has scored 0.23 and 0.72 on the same audio). Scores: 1 - CER (1 - WER for spaced languages), diarization error rate,
translation chrF against a reference, speed as a multiple of real time, and peak GPU memory.
With --previous it flags every score that got worse than the last run. A clip's "baseline" is the
score it must keep (within REGRESSION_MARGIN); the run passes the release gate only when no clip
failed or fell below its baseline, and Diagnostics > Quality shows the newest result from the data
folder. Nothing is saved to the library.

Without --yes it is a dry run: it lists the clips and what each would load and writes nothing.
Paid translation engines also need --allow-paid; a run stops adding paid lines once --max-usd is spent.
"""
import argparse
import datetime
import json
import math
import multiprocessing
import os
import platform
import queue
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/release_check.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Must run before the app imports below (see portable.py's docstring).
import portable
portable.activate_portable_mode()

import diarize
import job_process_kill
import media_inspect
import subtitle_parse
import whisper_models
from engine_backends import engine_registry, pricing
from engine_backends import shared as engine_shared
from lib.errors import ServiceError
from services import (benchmark_scoring_service, quality_check_service, settings_service, transcribe_service,
                      translate_service)
from tools import release_check_worker, self_test
from tools.asr_bench import Refusal, gpu_guard

REPORT_TITLE = "# Baihe real-hardware release check"
KINDS = ("transcription", "diarization", "translation")
# A drop bigger than this between runs is flagged; smaller moves are within run-to-run noise on one clip.
REGRESSION_MARGIN = 0.02
# One default-decoding run can land anywhere in its spread, so it can't pass the gate.
GATE_MIN_REPEATS = 5
DEFAULT_REPEATS = GATE_MIN_REPEATS
# The same build's translation has scored 0.9 to 4 chrF points apart, more than the margin, so one
# run can pass or fail by luck; a median of three is not moved by one odd run.
TRANSLATION_GATE_MIN_REPEATS = 3
BASELINE_METRICS = frozenset(("cer", "wer", "der", "chrf"))
RESULT_PREFIX = quality_check_service.RESULT_PREFIX
GATE_MAX_AGE_DAYS = quality_check_service.GATE_MAX_AGE_DAYS
DIARIZATION_FRAME_S = 0.01
TRANSLATE_BATCH = 20
MAX_CELL_CHARS = 120
# The dramas columns a transcription run reads; a clip's "title_settings" may hold only these,
# so a misspelt one is refused instead of silently running on the default.
TITLE_SETTING_KEYS = frozenset((
    "source_language", "chinese_script", "content_mode", "media_type", "transcript_mode",
    "asr_backend_choice", "alignment_method", "whisper_size", "beam_size", "min_silence_ms",
    "vad_threshold", "sensitivity_preset", "difficult_audio", "hallucination_silence_sec",
    "min_pause_sec", "separation_backend", "separate_vocals_first", "realign_long_segments",
    "whisper_fast_mode", "whisper_repeat_guard", "split_by_sentences", "use_groq"))
# What a translation clip's "title_settings" may set: the title fields the prompt reads, and the run's
# own knobs (style preset and notes, batch size, context lines, English locale).
TITLE_PROMPT_FIELDS = ("content_mode", "media_type", "genre", "series_note", "episode_brief",
                       "previous_episode_summary")
TRANSLATION_SETTING_KEYS = frozenset((
    *TITLE_PROMPT_FIELDS, "style_preset", "style_note", "include_genre_notes", "default_female_pronouns",
    "batch_size", "context_window", "context_window_ahead", "locale"))


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def _clean(text) -> str:
    return self_test._clean(engine_shared.redact_secrets(str(text or "")))


def load_manifest(path: str) -> dict:
    """Reads and checks the manifest; relative paths in it resolve against its own folder."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        raise Refusal("Can't read the manifest as JSON.") from None
    clips = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(clips, list) or not clips:
        raise Refusal('The manifest needs a non-empty "clips" list.')
    base = os.path.dirname(os.path.abspath(path))
    seen = set()
    for clip in clips:
        if not isinstance(clip, dict) or clip.get("kind") not in KINDS:
            raise Refusal(f"Every clip needs a kind: one of {', '.join(KINDS)}.")
        cid = str(clip.get("id") or "")
        if not cid or cid in seen:
            raise Refusal("Every clip needs a unique non-empty id.")
        seen.add(cid)
        needed = {"transcription": ("audio", "language"), "diarization": ("audio", "reference_rttm"),
                  "translation": ("source_srt", "reference_srt", "language")}[clip["kind"]]
        for key in needed:
            if not clip.get(key):
                raise Refusal(f'Clip "{cid}" ({clip["kind"]}) needs "{key}".')
        for key in ("audio", "source_srt", "reference_srt", "reference_rttm"):
            if clip.get(key) and not os.path.isabs(clip[key]):
                clip[key] = os.path.join(base, clip[key])
            if clip.get(key) and not os.path.isfile(clip[key]):
                raise Refusal(f'Clip "{cid}": the {key} file was not found.')
        if clip["kind"] == "transcription" and not (clip.get("reference_srt") or clip.get("reference_text")):
            raise Refusal(f'Clip "{cid}" needs "reference_srt" or "reference_text".')
        settings = clip.get("title_settings")
        allowed = TRANSLATION_SETTING_KEYS if clip["kind"] == "translation" else TITLE_SETTING_KEYS
        if settings is not None and (not isinstance(settings, dict) or set(settings) - allowed):
            raise Refusal(f'Clip "{cid}": "title_settings" may only hold {", ".join(sorted(allowed))}.')
        glossary = clip.get("glossary")
        if glossary is not None and not (isinstance(glossary, list) and all(
                isinstance(t, dict) and isinstance(t.get("original"), str) and isinstance(t.get("translation"), str)
                for t in glossary)):
            raise Refusal(f'Clip "{cid}": "glossary" must be a list of {{"original", "translation"}}.')
        if clip.get("fixed_decoding") is not None and (
                not isinstance(clip["fixed_decoding"], bool) or not (clip["kind"] == "transcription" and settings is not None)):
            raise Refusal(f'Clip "{cid}": "fixed_decoding" is true or false, and only a transcription clip with "title_settings" takes it.')
        baseline = clip.get("baseline")
        if baseline is not None and (isinstance(baseline, bool) or not isinstance(baseline, (int, float))
                                     or not math.isfinite(baseline) or baseline < 0):
            raise Refusal(f'Clip "{cid}": "baseline" must be a score of 0 or more.')
        if clip.get("backend") is not None and clip["backend"] not in diarize.SPEAKER_MODELS:
            raise Refusal(f'Clip "{cid}": "backend" must be one of {", ".join(diarize.SPEAKER_MODELS)}.')
    return data


def read_cues(path: str, language: str) -> list:
    return [c.text.strip() for c in read_timed_cues(path, language)]


def read_timed_cues(path: str, language: str) -> list:
    """The file's non-empty cues with their times, which the app path's scene breaks come from."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(subtitle_parse.MAX_FILE_BYTES + 1)
        parsed = subtitle_parse.parse_subtitle(data, os.path.basename(path), language=language)
    except (subtitle_parse.SubtitleParseError, OSError):
        raise Refusal("Can't read a subtitle file named in the manifest.") from None
    return [c for c in parsed.cues if c.text.strip()]


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def parse_rttm(text: str) -> list:
    """SPEAKER lines of an RTTM file as {"start", "end", "speaker"} turns."""
    turns = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            start, dur = float(parts[3]), float(parts[4])
            turns.append({"start": start, "end": start + dur, "speaker": parts[7]})
    return turns


def _best_matching(overlap) -> int:
    """Most frames one-to-one speaker mapping can agree on. Exact for up to 8 speakers on the
    larger side (scipy is not a dependency); past that, greedy, which can only overstate the error."""
    import itertools

    rows, cols = overlap.shape
    if not rows or not cols:
        return 0
    if max(rows, cols) <= 8:
        small = overlap if rows <= cols else overlap.T
        return max(sum(int(small[i, j]) for i, j in enumerate(pick))
                   for pick in itertools.permutations(range(small.shape[1]), small.shape[0]))
    work, total = overlap.copy(), 0
    while work.size and work.max() > 0:
        i, j = divmod(int(work.argmax()), work.shape[1])
        total += int(work[i, j])
        work[i, :], work[:, j] = 0, 0
    return total


def diarization_error_rate(reference: list, hypothesis: list):
    """(DER, miss, false alarm, confusion) as fractions of reference speech time.

    Frame-based with one label per frame and no forgiveness collar, with the best one-to-one mapping
    of hypothesis speakers to reference speakers. Overlapping speech counts once (the later turn
    wins), so the number is stricter than the usual scorer on overlap-heavy audio and is for
    comparing runs of this tool, not for quoting against papers.
    """
    import numpy as np

    end = max([t["end"] for t in reference + hypothesis] or [0.0])
    frames = int(end / DIARIZATION_FRAME_S) + 1

    def paint(turns):
        names = sorted({t["speaker"] for t in turns})
        grid = np.full(frames, -1, dtype=np.int32)
        for t in sorted(turns, key=lambda t: t["start"]):
            grid[int(t["start"] / DIARIZATION_FRAME_S):int(t["end"] / DIARIZATION_FRAME_S)] = names.index(t["speaker"])
        return grid, names

    ref, ref_names = paint(reference)
    hyp, hyp_names = paint(hypothesis)
    ref_speech = int((ref >= 0).sum())
    if not ref_speech:
        raise ValueError("The reference has no speech.")
    overlap = np.zeros((len(ref_names), len(hyp_names)), dtype=np.int64)
    both = (ref >= 0) & (hyp >= 0)
    for r, h in zip(ref[both], hyp[both]):
        overlap[r, h] += 1
    matched = _best_matching(overlap)
    miss = int(((ref >= 0) & (hyp < 0)).sum())
    false_alarm = int(((ref < 0) & (hyp >= 0)).sum())
    confusion = int(both.sum()) - matched
    return ((miss + false_alarm + confusion) / ref_speech, miss / ref_speech,
            false_alarm / ref_speech, confusion / ref_speech)


def chrf_score(outputs: list, references: list):
    """Mean sentence chrF (0-1) and the metric name, pairing lines by position.

    Position is safe here: the reference is a line-for-line file, and a translated batch that came
    back short is padded with empty lines so it scores as missing text, not as a shifted match.
    """
    scores, metric = [], "chrf"
    for out, ref in zip(outputs, references):
        score, metric, _ = benchmark_scoring_service.score_output("translation", out, ref)
        scores.append(score)
    return (sum(scores) / len(scores) if scores else None), metric


# ---------------------------------------------------------------------------
# GPU facts
# ---------------------------------------------------------------------------

def gpu_name():
    try:
        import torch
        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        return None


def reset_peak_vram() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def peak_vram_mb():
    """Peak memory torch allocated in THIS process; a model loaded by another process is not counted."""
    try:
        import torch
        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / (1024 * 1024))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Runners: each returns {"score", "metric", "speed_x", "vram_mb", "detail"}
# ---------------------------------------------------------------------------

def run_transcription(clip: dict, opts) -> dict:
    if clip.get("title_settings") is not None:
        return run_app_transcription(clip, opts)
    import whisper_transcribe

    audio_s = media_inspect.duration_seconds(clip["audio"]) or 0.0
    language = clip["language"]
    reset_peak_vram()
    started = time.monotonic()
    segments = whisper_transcribe.transcribe_for_timing(
        clip["audio"], model_size=opts.whisper_model, language=language, use_gpu=opts.use_gpu)
    wall = time.monotonic() - started
    vram = peak_vram_mb()
    whisper_models.release_gpu_models()
    reference = clip.get("reference_text") or "".join(read_cues(clip["reference_srt"], language))
    score, metric, _ = benchmark_scoring_service.score_output(
        "transcription", "".join(s["text"] for s in segments), reference, language)
    return {"score": score, "metric": metric, "speed_x": audio_s / wall if wall and audio_s else None,
            "vram_mb": vram, "detail": f"{len(segments)} lines, model {opts.whisper_model}"}


def _transcribe_in_child(args: tuple, fixed_decoding: bool = False) -> dict:
    """The worker's outcome, run in a spawned process as the app runs it: a fresh process gets a
    clean GPU each time, which matters when one clip runs several times."""
    from services import transcribe_pipeline

    target = release_check_worker.fixed_decoding_worker if fixed_decoding else transcribe_pipeline._transcribe_worker
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    proc = context.Process(target=target, args=(*args, results), daemon=True)
    proc.start()
    try:
        while True:
            try:
                item = results.get(timeout=5)
            except queue.Empty:
                if not proc.is_alive():
                    raise RuntimeError(f"The transcription process ended without a result (exit code {proc.exitcode}).")
                continue
            if item[0] == "ok":
                return item[1]
            if item[0] == "error":
                raise RuntimeError(f"{item[1]}: {item[2]}")
    finally:
        proc.join(timeout=30)
        if proc.is_alive():
            job_process_kill.kill_tree(proc)


def run_app_transcription(clip: dict, opts) -> dict:
    audio_s = media_inspect.duration_seconds(clip["audio"]) or 0.0
    language = clip["language"]
    scratch = tempfile.mkdtemp(prefix="release-check-")
    try:
        args = transcribe_service.worker_args_for_settings(
            {**clip["title_settings"], "source_language": language}, clip["audio"], scratch,
            opts.use_gpu, clip.get("initial_prompt") or "")
        started = time.monotonic()
        outcome = _transcribe_in_child(args, bool(clip.get("fixed_decoding")))
        wall = time.monotonic() - started
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if outcome.get("failed_reason"):
        raise RuntimeError(f"The transcription stopped: {outcome['failed_reason']}")
    lines = outcome.get("lines") or []
    reference = clip.get("reference_text") or "".join(read_cues(clip["reference_srt"], language))
    score, metric, _ = benchmark_scoring_service.score_output(
        "transcription", "".join(line.zh for line in lines), reference, language)
    return {"score": score, "metric": metric, "speed_x": audio_s / wall if wall and audio_s else None,
            "vram_mb": None,
            "detail": (f"{len(lines)} lines, app pipeline ({outcome.get('raw_backend')} {outcome.get('raw_model')}); "
                       "VRAM not measured, it ran in its own process")}


def run_diarization(clip: dict, opts) -> dict:
    # As in the app: Nemotron ignores a clip's num_speakers rather than switching to pyannote.
    backend = clip.get("backend") or settings_service.get("speaker_model")
    token = "" if backend == "nemotron" else settings_service.resolve_key("hf_token")
    if token is None or (backend != "nemotron" and not token):
        raise RuntimeError("No Hugging Face token is set up, so speaker detection can't load.")
    audio_s = media_inspect.duration_seconds(clip["audio"]) or 0.0
    with open(clip["reference_rttm"], "r", encoding="utf-8") as fh:
        reference = parse_rttm(fh.read())
    info = {}
    reset_peak_vram()
    started = time.monotonic()
    if backend == "nemotron":
        import diarize_nemotron
        turns, _ = diarize_nemotron.diarize(clip["audio"], use_gpu=opts.use_gpu)
        info["device"] = "cuda" if opts.use_gpu else "cpu"
    else:
        turns = diarize.diarize(clip["audio"], token, clip.get("num_speakers"), use_gpu=opts.use_gpu, run_info=info)
    wall = time.monotonic() - started
    vram = peak_vram_mb()
    der, miss, false_alarm, confusion = diarization_error_rate(reference, turns)
    found = len({t["speaker"] for t in turns})
    return {"score": der, "metric": "der", "speed_x": audio_s / wall if wall and audio_s else None,
            "vram_mb": vram,
            "detail": (f"found {found} of {len({t['speaker'] for t in reference})} speakers; miss {miss:.1%}, "
                       f"false alarm {false_alarm:.1%}, confusion {confusion:.1%}; {backend} on {info.get('device', '?')}")}


class Spend:
    """Running cost of paid translation lines, so a run stops before it passes --max-usd."""

    def __init__(self, cap: float):
        self.cap, self.used = cap, 0.0


def _translation_engine(clip: dict, opts):
    """(engine name, engine, paid) for a translation clip, refusing a paid engine without --allow-paid."""
    engine_name = clip.get("engine") or opts.engine or translate_service.default_hosted_engine()
    paid = engine_name not in engine_registry.FREE_ENGINES
    if paid and not opts.allow_paid:
        raise RuntimeError(f"{engine_name} is a paid engine; add --allow-paid to use it.")
    key = translate_service.resolve_api_key(engine_name)
    if not key:
        raise RuntimeError(f"No key is set up for {engine_name}.")
    return engine_name, engine_registry.get_engine(engine_name, key, clip.get("model")), paid


def _translation_pair(clip: dict):
    sources = read_timed_cues(clip["source_srt"], clip["language"])
    references = read_cues(clip["reference_srt"], clip["language"])
    if len(sources) != len(references):
        raise ValueError(f"The source has {len(sources)} lines but the reference {len(references)}; "
                         "they must match line for line.")
    return sources, references


def _translation_row(outputs: list, references: list, wall: float, detail: str) -> dict:
    score, metric = chrf_score(outputs, references)
    blank = sum(1 for out in outputs if not (out or "").strip())
    per_100 = f"; {100 * wall / len(outputs):.1f} s per 100 lines" if outputs and wall else ""
    return {"score": score, "metric": metric, "speed_x": len(outputs) / wall if wall else None,
            "vram_mb": None, "blank_lines": blank,
            "detail": f"{detail}; {blank} of {len(outputs)} lines blank{per_100}; speed is lines/s"}


def run_translation(clip: dict, opts, spend: Spend) -> dict:
    if clip.get("title_settings") is not None:
        return run_app_translation(clip, opts, spend)
    cues, references = _translation_pair(clip)
    sources = [c.text.strip() for c in cues]
    engine_name, engine, paid = _translation_engine(clip, opts)
    outputs, started = [], time.monotonic()
    for i in range(0, len(sources), TRANSLATE_BATCH):
        if paid and spend.used >= spend.cap:
            raise RuntimeError(f"Stopped at the --max-usd cap (${spend.cap:.2f}) after {len(outputs)} lines.")
        batch = sources[i:i + TRANSLATE_BATCH]
        got = list(engine.translate_batch(batch, {}) or [])
        outputs += (got + [""] * len(batch))[:len(batch)]
        usage = getattr(engine, "last_usage", None) or {}
        spend.used += pricing.estimate_cost_for_engine(engine, usage.get("input_tokens", 0),
                                                    usage.get("output_tokens", 0)) or 0.0
    wall = time.monotonic() - started
    return _translation_row(outputs, references, wall, f"{len(sources)} lines via {engine_name}, bare engine")


def run_app_translation(clip: dict, opts, spend: Spend) -> dict:
    """The clip through translate_pipeline as a Workspace run sends it, on lines held in memory. Not carried:
    speaker names (a subtitle file has no speakers), the series' learned style and character hints, and
    the title's memory examples, which live in a library this check never opens."""
    import translation_guide
    from core import Line
    from engine_backends import translate_pipeline
    from services import timing_settings_service

    settings = clip["title_settings"]
    cues, references = _translation_pair(clip)
    engine_name, engine, paid = _translation_engine(clip, opts)
    if paid and spend.used >= spend.cap:
        raise RuntimeError(f"Stopped at the --max-usd cap (${spend.cap:.2f}) before this clip.")
    lines = [Line(idx=i, start=c.start, end=c.end, zh=c.text.strip(), id=i + 1) for i, c in enumerate(cues)]
    glossary = [{"term_original": t["original"], "term_translation": t["translation"]}
                for t in clip.get("glossary") or []] or None
    style_guidelines = translation_guide.build_style_guidelines(
        settings.get("style_preset") or "audio_drama", glossary_terms=glossary,
        include_genre_notes=bool(settings.get("include_genre_notes", True)),
        default_female_pronouns=bool(settings.get("default_female_pronouns", False)))
    drama_meta = {"source_language": clip["language"], **{k: settings[k] for k in TITLE_PROMPT_FIELDS if k in settings}}

    def usage_cb(inp, out, cache_read=0, cache_write=0):
        spend.used += pricing.estimate_cost_for_engine(engine, inp, out, cache_read, cache_write) or 0.0

    started = time.monotonic()
    _, errors = translate_pipeline.translate_lines_with_engine(
        lines, engine, drama_meta=drama_meta, batch_size=settings.get("batch_size"), style_note=settings.get("style_note") or "",
        locale=settings.get("locale") or "en-US", glossary_terms=glossary, style_guidelines=style_guidelines,
        context_window=settings.get("context_window", 6), context_window_ahead=settings.get("context_window_ahead", 3),
        scene_aware_batches=bool(settings_service.get("scene_aware_batches")),
        **timing_settings_service.translate_limits(), usage_cb=usage_cb,
        check_replies=settings_service.reply_guard_override(), reasoning=settings_service.reasoning_override(engine_name),
        parallel_chapters=settings_service.parallel_chapters(),
        cost_cap_usd=spend.cap - spend.used if paid else None)
    wall = time.monotonic() - started
    english = {ln.id: ln.en or "" for ln in lines}
    # Matched back by id, so a line the engine skipped scores as missing rather than shifting the rest.
    outputs = [english.get(i + 1, "") for i in range(len(lines))]
    failed = f"; {len(errors)} batch errors" if errors else ""
    return _translation_row(outputs, references, wall, f"{len(lines)} lines via {engine_name}, app path{failed}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def worse(kind_metric: str, now, before) -> bool:
    """DER is better when lower; every other score is better when higher."""
    if now is None or before is None:
        return False
    return now > before + REGRESSION_MARGIN if kind_metric == "der" else now < before - REGRESSION_MARGIN


def _baseline(row: dict):
    """The previous run's score to compare with: for a repeated clip its worst run, so a new median
    is flagged only when it falls below everything the last run's spread already showed."""
    scores = [s for s in row.get("scores") or [] if s is not None]
    if len(scores) < 2:
        return row.get("score")
    return max(scores) if row.get("metric") == "der" else min(scores)


def baseline_status(row: dict) -> str:
    """"pass", "fail" (failed, or worse than its baseline by more than the margin) or "none" (no baseline)."""
    if row.get("error"):
        return "fail"
    if row.get("baseline") is None:
        return "none"
    # Translation falls back to a difflib ratio without sacrebleu; a chrF baseline means nothing against it.
    if row.get("metric") not in BASELINE_METRICS:
        return "fail"
    if row.get("score") is None or worse(row["metric"], row["score"], row["baseline"]):
        return "fail"
    return "pass"


def _ran_enough(row: dict) -> bool:
    runs = len(row.get("scores") or [row.get("score")])
    if row.get("kind") == "transcription":
        return bool(row.get("fixed_decoding")) or runs >= GATE_MIN_REPEATS
    return row.get("kind") != "translation" or runs >= TRANSLATION_GATE_MIN_REPEATS


def gate_passed(run: dict) -> bool:
    """The release gate: something was compared with a baseline, nothing failed or fell below one,
    and every default-decoding and translation clip ran often enough for its median to mean something."""
    rows = run.get("results") or []
    if not any(r.get("baseline") is not None for r in rows) or any(baseline_status(r) == "fail" for r in rows):
        return False
    return all(_ran_enough(r) for r in rows)


def _fmt(value, digits=3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _cell(text) -> str:
    return _clean(text).replace("|", "/").replace("\n", " ")[:MAX_CELL_CHARS]


def format_report(run: dict, previous: dict | None) -> str:
    before = {r["id"]: r for r in (previous or {}).get("results", [])}
    lines = [REPORT_TITLE, "",
             f"Run {run['when']} on {_cell(run['machine'])}; GPU {_cell(run['gpu'] or 'none used')}; "
             f"Whisper model {_cell(run['whisper_model'])}.", "",
             "Score is 1 - error for transcription, DER (lower is better) for diarization and chrF (0-1) "
             "for translation. Speed is audio seconds per second (translation: lines per second). "
             "VRAM is peak torch memory in this process.", ""]
    if previous:
        lines += [f"Compared with the run of {_cell(previous.get('when', '?'))}; a score is flagged when it "
                  f"is worse than Previous by more than {REGRESSION_MARGIN}. For a clip that ran several times, "
                  "Previous is the worst of the last run's scores.", ""]
    lines += [f"Baseline is the score the clip must keep; Result says BELOW BASELINE when it is worse by more "
              f"than {REGRESSION_MARGIN}.", ""]
    lines += ["| Clip | Kind | Metric | Score | Baseline | Previous | Speed | VRAM MB | Result |",
              "|---|---|---|---|---|---|---|---|---|"]
    flagged = 0
    for r in run["results"]:
        prev = before.get(r["id"])
        prev_score = _baseline(prev) if prev else None
        if r.get("error"):
            result = "FAILED: " + _cell(r["error"])
        elif baseline_status(r) == "fail":
            result = "BELOW BASELINE"
        elif prev and worse(r["metric"], r["score"], prev_score):
            result, flagged = "WORSE", flagged + 1
        else:
            result = "ok"
        lines.append(f"| {_cell(r['id'])} | {r['kind']} | {r.get('metric') or '-'} | {_fmt(r.get('score'))} | "
                     f"{_fmt(r.get('baseline'))} | {_fmt(prev_score)} | {_fmt(r.get('speed_x'), 1)} | "
                     f"{r.get('vram_mb') or '-'} | {result} |")
    failed = sum(1 for r in run["results"] if r.get("error"))
    below = sum(1 for r in run["results"] if not r.get("error") and baseline_status(r) == "fail")
    lines += ["", f"{len(run['results'])} clips, {failed} failed, {flagged} worse than the previous run, "
                  f"{below} below baseline.", ""]
    if gate_passed(run):
        lines += [f"Release gate: passed. Promote Release takes `{run.get('stamp', '?')}` as its release check "
                  f"for {GATE_MAX_AGE_DAYS} days.", ""]
    else:
        lines += ["Release gate: not passed (a clip failed or fell below its baseline, no clip has a baseline, "
                  f"a transcription clip without fixed decoding ran fewer than {GATE_MIN_REPEATS} times, "
                  f"or a translation clip fewer than {TRANSLATION_GATE_MIN_REPEATS}).", ""]
    notes = [f"- {_cell(r['id'])}: {_cell(r['detail'])}" for r in run["results"] if r.get("detail")]
    return "\n".join(lines + (["Details", ""] + notes + [""] if notes else []))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def default_out_dir() -> str:
    return portable.data_dir()


def run_repeated(run, clip: dict, opts, times: int) -> dict:
    """run(clip, opts) `times` times: the median score and speed, every score in "scores", the
    highest VRAM."""
    rows = [run(clip, opts) for _ in range(times)]
    if times == 1:
        return rows[0]
    scores = [r["score"] for r in rows]
    known = [s for s in scores if s is not None]
    speeds = [r["speed_x"] for r in rows if r.get("speed_x") is not None]
    vrams = [r["vram_mb"] for r in rows if r.get("vram_mb") is not None]
    spread = f"; {times} runs, scores {min(known):.3f} to {max(known):.3f}" if known else f"; {times} runs"
    out = {"score": statistics.median(known) if known else None, "metric": rows[0]["metric"], "scores": scores,
           "speed_x": statistics.median(speeds) if speeds else None, "vram_mb": max(vrams) if vrams else None,
           "detail": (rows[0].get("detail") or "") + spread}
    blanks = [r["blank_lines"] for r in rows if r.get("blank_lines") is not None]
    if blanks:
        # The worst run: one run that drops lines is the slip the gate is for.
        out["blank_lines"] = max(blanks)
        out["detail"] += f"; most blank lines in one run {max(blanks)}"
    return out


def run_all(manifest: dict, opts) -> dict:
    spend = Spend(opts.max_usd)
    results = []
    repeats = getattr(opts, "repeats", DEFAULT_REPEATS)
    with gpu_guard(opts.use_gpu, "Release check"):
        for n, clip in enumerate(manifest["clips"]):
            print(f"[{n + 1}/{len(manifest['clips'])}] {clip['id']} ({clip['kind']}) ...", flush=True)
            # The language lets Diagnostics group score trends per language without reading the private manifest.
            row = {"id": clip["id"], "kind": clip["kind"], "baseline": clip.get("baseline"),
                   "language": clip.get("language"), "fixed_decoding": bool(clip.get("fixed_decoding"))}
            try:
                if clip["kind"] == "transcription":
                    row.update(run_repeated(run_transcription, clip, opts, repeats))
                elif clip["kind"] == "diarization":
                    row.update(run_diarization(clip, opts))
                else:
                    row.update(run_repeated(lambda c, o: run_translation(c, o, spend), clip, opts,
                                            getattr(opts, "translation_repeats", TRANSLATION_GATE_MIN_REPEATS)))
            except Exception as exc:
                # One broken clip must not cost the rest of a long GPU run.
                row.update({"score": None, "metric": None, "error": _clean(f"{type(exc).__name__}: {exc}")})
            print("    " + (f"failed: {row['error']}" if row.get("error") else f"score {_fmt(row['score'])}"),
                  flush=True)
            row["status"] = baseline_status(row)
            results.append(row)
    run = {"when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "machine": platform.node() or "unknown",
            "gpu": gpu_name() if opts.use_gpu else None, "whisper_model": opts.whisper_model,
            "repeats": repeats, "margin": REGRESSION_MARGIN, "paid_usd": round(spend.used, 4), "results": results}
    run["passed"] = gate_passed(run)
    return run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.release_check", description=__doc__.splitlines()[0])
    parser.add_argument("manifest", help="JSON file listing the golden clips (kept outside the repo)")
    parser.add_argument("--previous", metavar="RESULTS.json", help="the JSON of the last run, to flag regressions")
    parser.add_argument("--whisper-model", default="large-v3", help="transcription model (default large-v3)")
    parser.add_argument("--engine", default=None, help="translation engine for clips that name none (default: hosted Gemma through Ollama, else OpenAI)")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                        help=f"run each transcription clip this many times and score the median (default {DEFAULT_REPEATS}; "
                             f"the release gate needs {GATE_MIN_REPEATS} unless the clip has fixed decoding)")
    parser.add_argument("--cpu", action="store_true", help="run on the CPU instead of the GPU")
    parser.add_argument("--translation-repeats", type=int, default=TRANSLATION_GATE_MIN_REPEATS,
                        help=f"runs of each translation clip, scored by the median (default {TRANSLATION_GATE_MIN_REPEATS})")
    parser.add_argument("--allow-paid", action="store_true", help="let translation use a paid engine")
    parser.add_argument("--max-usd", type=float, default=1.0, help="stop paid translation past this spend, across every run of every clip (default 1.00; translation clips run 3 times)")
    parser.add_argument("--yes", action="store_true", help="run the clips (default: dry run)")
    parser.add_argument("--out", metavar="DIR", help="folder for the report and JSON (default: the data folder)")
    opts = parser.parse_args(argv)
    opts.use_gpu = not opts.cpu
    if opts.repeats < 1:
        parser.error("--repeats must be 1 or more")
    if opts.translation_repeats < 1:
        parser.error("--translation-repeats must be 1 or more")
    self_test._console_safe()
    try:
        manifest = load_manifest(opts.manifest)
        previous = None
        if opts.previous:
            try:
                with open(opts.previous, "r", encoding="utf-8") as fh:
                    previous = json.load(fh)
            except (OSError, ValueError):
                raise Refusal("Can't read the previous results JSON.") from None
        for clip in manifest["clips"]:
            print(f"{clip['id']:<24}{clip['kind']}")
        if not opts.yes:
            print("\nDry run only: nothing was loaded and nothing was written. Add --yes to run it for real.")
            return 0
        out_dir = opts.out or default_out_dir()
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = os.path.join(out_dir, f"{RESULT_PREFIX}{stamp}.md")
        if self_test._refuse_to_overwrite(report_path, REPORT_TITLE):
            raise Refusal("Refusing to write the report: that file exists and is not a release check report.")
        run = run_all(manifest, opts)
        run["stamp"] = stamp
        report = format_report(run, previous)
        os.makedirs(out_dir, exist_ok=True)
        Path(report_path).write_text(report, encoding="utf-8")
        Path(report_path[:-3] + ".json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        print(report)
        print(f"Report written to {Path(report_path).name} (with a .json beside it).")
        return 1 if any(r.get("error") for r in run["results"]) else 0
    except Refusal as refusal:
        print(f"Refused: {refusal}")
        return 2
    except KeyboardInterrupt:
        print("Interrupted; nothing was written.")
        return 130
    except ServiceError as exc:
        print("Failed: " + _clean(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
