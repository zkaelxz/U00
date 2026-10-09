"""
services/compare_transcription_service.py -- "Compare transcription" in Review:
re-transcribe a chosen set of lines with a model / ASR backend other than the
title's saved one, optionally translate the current text and each candidate,
and show both side by side as PROPOSALS.

The job (`comparetx_<drama_id>`) writes nothing to the lines. Proposals live in
its in-process result (not in GET /api/jobs, which only shows counts) and are
read back with get_compare_result. apply_compare writes only `zh` (and `en`
when the owner chose that candidate's English) per line, as a compare-and-set
against what the preview was built from, after a history snapshot.
"""
import os
import subprocess
import threading

import asr_backend
import background_jobs
import ollama_unload
import core as core_module
import db
import sensitivity_preset as presets
import translate_engines
from services import (asr_options_service, jobs_service, settings_service, transcribe_service,
                      translate_run_service, translate_service, workspace_job_service)
from services.service_errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    UnsupportedOperationError,
)

MAX_LINES = 200
_MAX_TEXT_CHARS = 2000
_SLICE_TIMEOUT_S = 120
_MAX_APPLY_ITEMS = MAX_LINES

SELECTION_KINDS = ("line_ids", "range", "flagged", "speaker", "time")
BACKEND_CHOICES = asr_options_service.ASR_BACKEND_CHOICES
# The Qwen3 backends that find speech themselves instead of hearing Whisper's segments.
_VAD_BACKENDS = ("qwen3_asr_vad", "qwen3_asr_long")
_BACKEND_LABELS = {
    "whisper": "Whisper", "qwen3_asr": "Qwen3 ASR",
    "qwen3_asr_vad": "Qwen3 ASR with speech detection",
    "qwen3_asr_long": "Qwen3 ASR on long windows",
}


def compare_job_id(drama_id: int) -> str:
    return f"comparetx_{drama_id}"


def _drama_or_404(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _backend_problem(choice: str, language: str):
    """Why this backend can't run here, or None. Same checks a full
    transcribe run makes, as a reason instead of an error."""
    try:
        if choice == "qwen3_asr":
            transcribe_service.require_qwen3_packages("Qwen3-ASR")
        elif choice in _VAD_BACKENDS:
            transcribe_service.require_vad_backend_packages(choice, language)
            transcribe_service._require_vad_packages()
    except (DependencyUnavailableError, InvalidInputError) as exc:
        return str(exc)
    # Whisper hears the audio first on every other backend too.
    if (choice in ("whisper", "qwen3_asr")
            and not transcribe_service.diagnostics.check_dependency("faster_whisper")):
        return transcribe_service.MISSING_TRANSCRIPTION_MESSAGE
    if (choice == "qwen3_asr" or choice in _VAD_BACKENDS) and language not in asr_backend.LANGUAGE_NAMES:
        return "Qwen3-ASR doesn't cover this title's language."
    return None


def get_options(drama_id: int) -> dict:
    """What the Compare panel offers: the title's saved model/backend/alignment
    as defaults, every Whisper size, each backend with whether it can run
    (reason when not), and the line cap. Alignment is shown, not choosable:
    it decides timing, never the heard text, so it can't change a candidate."""
    drama = _drama_or_404(drama_id)
    language = drama.get("source_language") or "zh"
    has_audio = (drama.get("content_mode") or "audio_drama") in ("audio_drama", "streamer_vod") \
        and transcribe_service._drama_audio_path(drama_id, drama) is not None
    sizes = sorted(transcribe_service._allowed_whisper_sizes())
    backends = []
    for choice in BACKEND_CHOICES:
        problem = _backend_problem(choice, language)
        backends.append({"id": choice, "label": _BACKEND_LABELS[choice],
                         "available": problem is None, "reason": problem})
    try:
        transcribe_service.require_qwen3_packages("The Qwen3 forced aligner", language)
        aligner_reason = None
    except DependencyUnavailableError as exc:
        aligner_reason = exc.message
    return {
        "aligner_reason": aligner_reason,
        "has_audio": has_audio,
        "no_audio_reason": None if has_audio else "This title has no stored audio to re-transcribe.",
        "max_lines": MAX_LINES,
        "saved_whisper_size": transcribe_service.stored_whisper_size(drama),
        "saved_asr_backend": asr_options_service.stored_asr_backend(drama),
        "saved_alignment_method": drama.get("alignment_method") or "whisper_diff",
        "whisper_sizes": sizes,
        "backends": backends,
        "translation_engine": drama.get("translation_engine") or settings_service.get_default_engine(),
    }


def select_lines(drama_id: int, selection: dict) -> list:
    """The Line objects a selection names, in title order, each with a timing
    window. selection: {"kind": one of SELECTION_KINDS, ...}. InvalidInputError
    for a malformed selection, no matching lines, or more than MAX_LINES."""
    if not isinstance(selection, dict) or selection.get("kind") not in SELECTION_KINDS:
        raise InvalidInputError("Choose which lines to compare.")
    kind = selection["kind"]
    lines = db.load_line_objects(drama_id)
    if kind == "line_ids":
        wanted = selection.get("line_ids")
        if not isinstance(wanted, list) or not wanted or not all(
                isinstance(i, int) and not isinstance(i, bool) for i in wanted):
            raise InvalidInputError("line_ids must be a list of line ids.")
        wanted = set(wanted)
        picked = [ln for ln in lines if ln.id in wanted]
    elif kind == "range":
        first, last = selection.get("from_number"), selection.get("to_number")
        if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in (first, last)) \
                or first > last:
            raise InvalidInputError("The line range must be 'from #N to #M' with N <= M.")
        picked = [ln for ln in lines if first <= ln.idx + 1 <= last]
    elif kind == "flagged":
        picked = [ln for ln in lines if ln.flag]
    elif kind == "speaker":
        speaker = selection.get("speaker")
        if not isinstance(speaker, str) or not speaker.strip():
            raise InvalidInputError("Choose a speaker.")
        picked = [ln for ln in lines if (ln.speaker or "") == speaker]
    else:
        begin, finish = selection.get("start_seconds"), selection.get("end_seconds")
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0
                   for n in (begin, finish)) or begin >= finish:
            raise InvalidInputError("The time range needs a start before its end.")
        picked = [ln for ln in lines if float(ln.start) < finish and float(ln.end) > begin]
    picked = [ln for ln in picked if float(ln.end) > float(ln.start)]
    if not picked:
        raise InvalidInputError("No lines with a timing window match that choice.")
    if len(picked) > MAX_LINES:
        raise InvalidInputError(
            f"That is {len(picked)} lines; one compare run is limited to {MAX_LINES}. "
            "Narrow the selection and run it in parts.")
    return picked


def _validate_candidate(drama: dict, whisper_size, backend_choice):
    """(whisper_size, backend) falling back to the title's saved ones."""
    size = whisper_size or transcribe_service.stored_whisper_size(drama)
    if size not in transcribe_service._allowed_whisper_sizes():
        raise InvalidInputError(f"Unknown Whisper model size {size!r}.")
    backend = backend_choice or asr_options_service.stored_asr_backend(drama)
    if backend not in BACKEND_CHOICES:
        raise InvalidInputError(f"Unknown ASR backend {backend!r}.")
    problem = _backend_problem(backend, drama.get("source_language") or "zh")
    if problem:
        raise DependencyUnavailableError(problem)
    return size, backend


def _translation_setup(drama: dict, engine_name, model, gemini_free_tier, job_cost_cap_usd):
    """(engine, engine_name, cap) the way start_fix_flagged resolves them, with
    the same refusals; the cap is the tighter of the per-job and monthly caps."""
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    if job_cost_cap_usd is not None and job_cost_cap_usd < 0:
        raise InvalidInputError("job_cost_cap_usd can't be negative.")
    engine_name = engine_name or drama.get("translation_engine") or settings_service.get_default_engine()
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    cap = None
    if translate_run_service.engine_cap_applies(engine_name, gemini_free_tier):
        monthly = translate_run_service.month_cap_usd()
        cap, refusal = translate_engines.resolve_cost_cap(
            job_cost_cap_usd, monthly, db.get_month_spend() if monthly else 0.0)
        if refusal:
            raise UnsupportedOperationError(refusal)
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=engine_name == "gemini" and gemini_free_tier,
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    return engine, engine_name, cap


def estimate_compare(drama_id: int, selection: dict, translate: bool = False,
                     retranslate_current: bool = False, engine: str = None, model: str = None,
                     gemini_free_tier: bool = None, job_cost_cap_usd: float = None) -> dict:
    """Pre-run summary: how many lines the selection names and, with
    translation on, the advisory cost of translating the current text where
    needed plus one candidate per line (estimated at the current text's
    length). Reads only; builds no engine and spends nothing."""
    drama = _drama_or_404(drama_id)
    picked = select_lines(drama_id, selection)
    out = {"line_count": len(picked), "max_lines": MAX_LINES, "translate": bool(translate),
           "estimated_usd": None, "free": True, "cap_applies": False,
           "effective_cap_usd": None, "monthly_refusal": False, "estimate_above_cap": False}
    if not translate:
        return out
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    engine_name = engine or drama.get("translation_engine") or settings_service.get_default_engine()
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    free_tier = engine_name == "gemini" and gemini_free_tier
    texts = []
    for ln in picked:
        texts.append(ln.zh or "")
        if retranslate_current or not (ln.en or "").strip():
            texts.append(ln.zh or "")
    stand_in = type("E", (), {"name": engine_name, "model": model, "free_tier": free_tier})()
    out["estimated_usd"] = float(translate_engines.estimate_translation_cost(stand_in, texts))
    out["free"] = engine_name in translate_engines.FREE_ENGINES or free_tier
    out["cap_applies"] = translate_run_service.engine_cap_applies(engine_name, gemini_free_tier)
    if out["cap_applies"]:
        monthly = translate_run_service.month_cap_usd()
        cap, refusal = translate_engines.resolve_cost_cap(
            job_cost_cap_usd, monthly, db.get_month_spend() if monthly else 0.0)
        out["effective_cap_usd"] = cap
        out["monthly_refusal"] = bool(refusal)
        out["estimate_above_cap"] = bool(cap is not None and out["estimated_usd"] > cap)
    return out


def start_compare(drama_id: int, selection: dict, whisper_size: str = None,
                  asr_backend_choice: str = None, translate: bool = False,
                  retranslate_current: bool = False, engine_name: str = None,
                  model: str = None, gemini_free_tier: bool = None,
                  job_cost_cap_usd: float = None, initial_prompt: str = "",
                  extra_names: str = "") -> dict:
    """Starts the GPU-queued compare job for the selected lines. Returns
    {job_id, drama_id, line_count}; poll GET /api/jobs/{job_id}, then read
    the proposals with get_compare_result. One candidate setting per run.

    NotFoundError for an unknown drama; UnsupportedOperationError with no
    audio pipeline / stored audio, or when the monthly cap is spent;
    InvalidInputError for a bad selection (including over MAX_LINES) or
    setting; DependencyUnavailableError for a backend or engine that isn't
    installed/configured; ConflictError while a compare run is active, or
    while a full transcription, fix-flagged, a re-segment or a narration run
    is running or queued for this drama."""
    drama = _drama_or_404(drama_id)
    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(f"Drama {drama_id} has no audio pipeline.")
    audio_path = transcribe_service._drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError("This title has no stored audio to re-transcribe.")
    picked = select_lines(drama_id, selection)
    size, backend = _validate_candidate(drama, whisper_size, asr_backend_choice)
    prompt = transcribe_service._resolve_initial_prompt(drama_id, initial_prompt or "",
                                                        extra_names or "")
    for prefix in transcribe_service._RETRANSCRIBE_BLOCKING_PREFIXES:
        other = background_jobs.get_status(f"{prefix}{drama_id}")
        if other and other.get("status") in ("running", "queued"):
            raise ConflictError("Another job is changing this drama's lines. "
                                "Try again when it finishes.")
    job_id = compare_job_id(drama_id)
    if background_jobs.is_running(job_id):
        raise ConflictError("A transcription comparison is already running for this title.")
    translation = None
    if translate:
        engine, name, cap = _translation_setup(drama, engine_name, model, gemini_free_tier,
                                               job_cost_cap_usd)
        translation = {"engine": engine, "engine_choice": name, "cost_cap_usd": cap,
                       "retranslate_current": bool(retranslate_current),
                       "locale": settings_service.get_preference("default_locale"),
                       "style_note": settings_service.get_preference("default_style_note") or ""}
    tuning = transcribe_service._DEFAULT_TUNING
    started = background_jobs.start_job(
        job_id, run_compare_job, job_id, drama_id, [ln.id for ln in picked], audio_path,
        {"whisper_size": size, "backend": backend, "prompt": prompt,
         "language": drama.get("source_language") or "zh",
         "beam_size": drama.get("beam_size") or tuning["beam_size"],
         "min_silence_ms": drama.get("min_silence_ms") or tuning["min_silence_ms"],
         "vad_threshold": presets.stored_vad_threshold(drama),
         "sensitivity_preset": presets.normalize(drama.get("sensitivity_preset")),
         "hallucination_silence_sec": transcribe_service.stored_hallucination_silence_sec(drama),
         "fast_mode": bool(drama.get("whisper_fast_mode")),
         "repeat_guard": bool(drama.get("whisper_repeat_guard")),
         "use_gpu": settings_service.get_use_gpu()},
        translation, gpu_touching=True,
        description=f"Comparing transcription of {len(picked)} line(s) (drama #{drama_id})")
    if not started:
        raise ConflictError("A transcription comparison is already running for this title.")
    return {"job_id": job_id, "drama_id": drama_id, "line_count": len(picked)}


def _line_language(ln, cfg: dict, line_number: int):
    """(language to hear this line in, reason to skip it or None). A line's own
    language wins over the title's so a mixed-language line isn't re-heard in
    the wrong one. Whisper takes any language; the
    VAD+Qwen3 backend hears an out-of-set language (English) by its own
    detection. Plain Qwen3 re-hears Whisper's spans in a fixed language and
    refuses one outside zh/ja/ko, so that line is skipped, not mis-heard."""
    language = ln.lang or cfg["language"]
    if language in asr_backend.LANGUAGE_NAMES:
        return language, None
    if cfg["backend"] in _VAD_BACKENDS:
        return None, None
    if cfg["backend"] == "qwen3_asr":
        return language, (f"line {line_number}: Qwen3-ASR doesn't cover this line's "
                          f"language ({language}), skipped")
    return language, None


def _hear(slice_path: str, cfg: dict, language, on_fallback, cancel_check) -> str:
    """Candidate source text for one cut line from the chosen backend."""
    backend, use_gpu = cfg["backend"], cfg["use_gpu"]
    if backend in _VAD_BACKENDS:
        segments = asr_backend.get_backend(backend).transcribe(
            slice_path, language, use_gpu=use_gpu, cancel_check=cancel_check)
    else:
        segments = core_module.transcribe_for_timing(
            slice_path, cfg["whisper_size"], language=language, use_gpu=use_gpu,
            initial_prompt=cfg["prompt"], beam_size=cfg["beam_size"],
            min_silence_duration_ms=cfg["min_silence_ms"], vad_threshold=cfg["vad_threshold"],
            sensitivity_preset=cfg.get("sensitivity_preset", "normal"),
            on_gpu_fallback=on_fallback, fast_mode=cfg["fast_mode"],
            hallucination_silence_sec=cfg.get("hallucination_silence_sec",
                                              core_module.DEFAULT_HALLUCINATION_SILENCE_SEC),
            repeat_guard=cfg.get("repeat_guard", False))
        if backend == "qwen3_asr" and segments:
            segments = asr_backend.get_backend(backend).transcribe(
                slice_path, language, segments, use_gpu=use_gpu)
    return " ".join((s.get("text") or "").strip() for s in segments or []).strip()


def run_compare_job(job_id, drama_id, line_ids, audio_path, cfg, translation):
    """Job body. Per selected line: cut its window, hear it with the candidate
    settings, optionally translate (current text where needed, and the
    candidate). Cancel and the cost cap end the run cleanly, keeping the
    proposals made so far. Result: {proposals, line_count, candidate_count,
    errors, cap_reached, partial, ...}; GET /api/jobs shows only the counts."""
    wanted = set(line_ids)
    lines = [ln for ln in db.load_line_objects(drama_id) if ln.id in wanted]
    proposals, errors = [], []
    gpu_fallback = []
    spent, cap_reached, cancelled = 0.0, None, False
    context = character_names = None
    engine = translation["engine"] if translation else None
    if translation:
        drama = db.get_drama(drama_id) or {}
        style_preset = "novel" if drama.get("content_mode") == "novel_narration" else "audio_drama"
        all_lines = db.load_line_objects(drama_id)
        glossary_terms, style_guidelines, character_names = \
            workspace_job_service.build_run_style_context(
                drama_id, drama, all_lines, style_preset, with_emotions=False)
        context = translate_engines.build_translation_context(
            engine, drama, style_note=translation["style_note"], locale=translation["locale"],
            glossary_terms=glossary_terms, style_guidelines=style_guidelines)
        context["source_language"] = cfg["language"]
    failed_reason = detail = None
    try:
        for n, ln in enumerate(lines):
            if background_jobs.is_cancel_requested(job_id):
                cancelled = True
                break
            background_jobs.update_progress(
                job_id, n / max(len(lines), 1), f"Line {n + 1} of {len(lines)}")
            language, skip_reason = _line_language(ln, cfg, ln.idx + 1)
            if skip_reason:
                errors.append(skip_reason)
                continue
            slice_path = os.path.join(os.path.dirname(audio_path), f"_comparetx_slice_{ln.id}.wav")
            try:
                try:
                    core_module.extract_audio_slice(audio_path, float(ln.start), float(ln.end),
                                                    slice_path, timeout=_SLICE_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    errors.append(f"line {ln.idx + 1}: cutting the audio took too long")
                    continue
                except (subprocess.CalledProcessError, OSError):
                    # str() of these carries the ffmpeg command line, i.e. absolute paths.
                    errors.append(f"line {ln.idx + 1}: couldn't cut this line's audio")
                    continue
                heard = _hear(slice_path, cfg, language,
                              lambda exc: gpu_fallback.append(core_module.short_reason(exc)),
                              lambda: _cancel_check(job_id))
            except background_jobs.JobCancelled:
                cancelled = True
                break
            except core_module.ModelDownloadError as exc:
                failed_reason, detail = "model_download", jobs_service.scrub_text(str(exc))
                break
            except ImportError:
                # The same for every line, so there is nothing to retry.
                failed_reason = "dependency_missing"
                detail = ("This transcription backend isn't installed yet. "
                          "Open Diagnostics to install it.")
                break
            except Exception as exc:
                errors.append(jobs_service.scrub_text(f"line {ln.idx + 1}: {exc}"))
                continue
            finally:
                if os.path.exists(slice_path):
                    os.remove(slice_path)
            if not heard:
                errors.append(f"line {ln.idx + 1}: nothing was heard")
                continue
            proposal = {"line_id": ln.id, "number": ln.idx + 1, "start": float(ln.start),
                        "end": float(ln.end), "base_zh": ln.zh or "", "base_en": ln.en or "",
                        "candidate_zh": heard[:_MAX_TEXT_CHARS], "current_en": ln.en or "",
                        "candidate_en": "", "translated": False}
            proposals.append(proposal)
            if translation:
                if cap_reached is None and translation["cost_cap_usd"] is not None \
                        and spent >= translation["cost_cap_usd"]:
                    cap_reached = spent
                if cap_reached is None:
                    spent += _translate_into(proposal, ln, engine, context, character_names,
                                             translation, drama_id, cfg["language"], errors)
    finally:
        core_module.release_gpu_models()
    result = {"proposals": proposals, "line_count": len(lines), "candidate_count": len(proposals),
              "errors": errors[:20], "cap_reached": cap_reached,
              "asr_backend": cfg["backend"], "whisper_size": cfg["whisper_size"],
              "translated": bool(translation), "partial": bool(cancelled or cap_reached),
              **ollama_unload.take_notice_result()}
    if gpu_fallback:
        result["gpu_fallback"] = gpu_fallback[0]
        result["device_notice"] = core_module.gpu_fallback_notice(
            "Comparing transcription", gpu_fallback[0])
    if failed_reason and not proposals:
        result = {"failed_reason": failed_reason, "detail": detail}
    elif failed_reason:
        result["errors"] = [detail] + result["errors"]
    if cancelled and not proposals:
        result = {"failed_reason": "cancelled"}
    background_jobs.set_result(job_id, result)


def _cancel_check(job_id):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


def _translate_into(proposal, ln, engine, context, character_names, translation, drama_id,
                    language, errors) -> float:
    """Translates the candidate (and the current text when it has no English
    or the owner asked) in one call; fills proposal's English fields. Returns
    the logged cost. A failure is recorded (redacted) and leaves them empty."""
    if translate_engines.is_english_line(ln):
        proposal["candidate_en"] = proposal["candidate_zh"]
        proposal["translated"] = True
        return 0.0
    current_needed = bool((ln.zh or "").strip()) and (
        translation["retranslate_current"] or not (ln.en or "").strip())
    texts = ([ln.zh] if current_needed else []) + [proposal["candidate_zh"]]
    try:
        out = engine.translate_batch(texts, {
            **context, "speaker_labels": [character_names.get(ln.speaker)] * len(texts),
            "line_languages": translate_engines.tagged_line_languages([ln] * len(texts), language)})
    except Exception as exc:
        errors.append(jobs_service.scrub_text(f"line {ln.idx + 1} translation: {exc}"))
        return 0.0
    cost = 0.0
    if hasattr(engine, "last_usage"):
        cost = translate_engines.estimate_cost_for_engine(
            engine, engine.last_usage.get("input_tokens", 0),
            engine.last_usage.get("output_tokens", 0))
        db.log_usage(drama_id, translation["engine_choice"],
                     getattr(engine, "model", translation["engine_choice"]),
                     "compare_transcription", engine.last_usage.get("input_tokens", 0),
                     engine.last_usage.get("output_tokens", 0), cost)
    if len(out) != len(texts):
        errors.append(f"line {ln.idx + 1} translation: the engine returned the wrong number of lines")
        return cost
    if current_needed:
        proposal["current_en"] = (out[0] or "").strip()[:_MAX_TEXT_CHARS]
    proposal["candidate_en"] = (out[-1] or "").strip()[:_MAX_TEXT_CHARS]
    proposal["translated"] = True
    return cost


def _finished_job(drama_id: int) -> dict:
    _drama_or_404(drama_id)
    job = background_jobs.get_status(compare_job_id(drama_id)) or {}
    result = job.get("result") if job.get("status") == "done" else None
    if not isinstance(result, dict) or not isinstance(result.get("proposals"), list):
        raise NotFoundError("No finished transcription comparison for this title.")
    return job


def _finished_result(drama_id: int) -> dict:
    return _finished_job(drama_id)["result"]


def get_compare_result(drama_id: int) -> dict:
    """The finished run's proposals, raw (the same line text lines.read
    returns). NotFoundError when there is none (not run, running, failed, or
    the API restarted since)."""
    result = _finished_result(drama_id)
    return {"job_id": compare_job_id(drama_id),
            "proposals": result["proposals"], "line_count": result.get("line_count", 0),
            "asr_backend": result.get("asr_backend"), "whisper_size": result.get("whisper_size"),
            "translated": bool(result.get("translated")), "partial": bool(result.get("partial")),
            "cap_reached": result.get("cap_reached") is not None,
            "errors": [jobs_service.scrub_text(str(e)) for e in result.get("errors") or []]}


# Serialises applies so two at once can't both decide a fresh snapshot is needed.
_apply_lock = threading.Lock()
# drama_id -> (run finished_at, history id) of the snapshot that run's first
# apply took. The per-row "Use this" sends one item per apply, and the DB keeps
# only the last 10 snapshots, so a snapshot per click would evict every older
# undo point.
_run_snapshots = {}


def _still_matches(ln, expected: dict) -> bool:
    """Python mirror of db.update_lines_fields_if_many's compare-and-set."""
    if ln is None:
        return False
    for col, val in expected.items():
        if col in ("start", "end"):
            if abs(float(getattr(ln, col)) - float(val)) >= 1e-6:
                return False
        elif (getattr(ln, col) or "") != val:
            return False
    return True


def _snapshot_once_per_run(drama_id: int, run_token, lines,
                           label: str = "before compare-transcription apply"):
    """Takes the 'before' snapshot, unless this run already took one and no
    other snapshot has been taken since (undo to it still restores the state
    before this run's first apply)."""
    taken = _run_snapshots.get(drama_id)
    if taken and taken[0] == run_token:
        latest = db.list_line_history(drama_id)
        if latest and latest[0]["id"] == taken[1]:
            return
    history_id = db.save_line_history_snapshot(drama_id, lines, label)
    _run_snapshots[drama_id] = (run_token, history_id)


def apply_compare(drama_id: int, job_id, items) -> dict:
    """Writes the chosen proposals. Each item is {line_id, expected_base_zh,
    expected_candidate_zh, use_english, expected_candidate_en}: it must equal
    what this run proposed, and the line must still hold its zh, en, start and
    end from when the run read it (one compare-and-set per line inside one
    transaction), so a line edited since is skipped and reported, never
    overwritten. Writes `zh`, plus `en` only for use_english items whose
    proposal has an English. A history snapshot ('before compare-transcription
    apply') is taken first when at least one item still matches, unless this
    run's earlier apply already took the latest snapshot. Returns {applied:
    [line_id], skipped: [line_id]}.

    InvalidInputError for malformed items or another drama's job id;
    NotFoundError with no finished run; ConflictError when an item isn't a
    proposal of this run (nothing written)."""
    if not isinstance(job_id, str) or job_id != compare_job_id(drama_id):
        raise InvalidInputError("job_id is not this title's transcription comparison.")
    if not isinstance(items, list) or not items or len(items) > _MAX_APPLY_ITEMS:
        raise InvalidInputError(f"Choose between 1 and {_MAX_APPLY_ITEMS} lines to use.")
    job = _finished_job(drama_id)
    result = job["result"]
    by_id = {p["line_id"]: p for p in result["proposals"]}
    writes, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or item.get("line_id") in seen:
            raise InvalidInputError("Each line can be applied once.")
        seen.add(item.get("line_id"))
        p = by_id.get(item.get("line_id"))
        if (p is None or item.get("expected_base_zh") != p["base_zh"]
                or item.get("expected_candidate_zh") != p["candidate_zh"]):
            raise ConflictError("These aren't the proposals you were shown. Compare again.")
        values = {"zh": p["candidate_zh"]}
        expected = {"zh": p["base_zh"], "start": p["start"], "end": p["end"]}
        if item.get("use_english"):
            if not p.get("candidate_en") or item.get("expected_candidate_en") != p["candidate_en"]:
                raise ConflictError("These aren't the proposals you were shown. Compare again.")
            values["en"] = p["candidate_en"]
            expected["en"] = p["base_en"]
        writes.append((p["line_id"], values, expected))
    with _apply_lock:
        lines = db.load_line_objects(drama_id)
        current = {ln.id: ln for ln in lines}
        if not any(_still_matches(current.get(lid), expected) for lid, _v, expected in writes):
            return {"applied": [], "skipped": [lid for lid, _v, _e in writes]}
        _snapshot_once_per_run(drama_id, job.get("finished_at"), lines)
        skipped = db.update_lines_fields_if_many(drama_id, writes)
    return {"applied": [lid for lid, _v, _e in writes if lid not in set(skipped)],
            "skipped": list(skipped)}
