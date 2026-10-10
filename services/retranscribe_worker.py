"""The process-job side of Review's re-transcription: the spawned workers that
cut and transcribe a line's window (one line, whose start/read/apply halves
live in transcribe_service, or many lines in one process, see
retranscribe_many_service), and the parent hooks that turn their outcome into
the job result. Kept free of transcribe_service so the spawned child imports
only what it needs."""

import os
import subprocess
import tempfile
import threading

import background_jobs
import core as core_module
import db
import ollama_unload
from translate_engines import redact_secrets

# Proposed text kept in the job result: same cap as a line edit.
RETRANSCRIBE_MAX_CHARS = 2000
# ffmpeg cutting one line's window; a hung ffmpeg ends the job instead of
# holding the GPU slot.
_SLICE_TIMEOUT_S = 120
# Worker timeout: the base covers loading a cached model or downloading one
# on first use; transcribing runs well faster than real time, so the per-second
# part is generous.
_BASE_TIMEOUT_S = 1800
_PER_AUDIO_S = 10


def retranscribe_timeout_s(window_seconds: float) -> float:
    """How long the worker may run before it gives up. The base also covers a
    first-use model download, so it never depends on whether a half-fetched
    model folder looks cached; Cancel is always available for a download the
    user no longer wants."""
    return _BASE_TIMEOUT_S + _PER_AUDIO_S * max(0.0, window_seconds)


def hear_window(audio_path, start, end, language, slice_path, whisper_size, gpu_fallback,
                **transcribe_kwargs):
    """Cuts [start, end) to slice_path and transcribes it: {"segments": [...]}
    or {"failed_reason": "audio_slice", "detail"} when the cut failed. A
    model-download failure comes back as {"failed_reason": "model_download",
    "detail"} so a many-line run can stop at once (every line would fail the
    same way). One place for the Whisper call so the one-line and many-line
    jobs hear a window identically: not the batched pipeline (it is for long
    audio and has hung on repeated short windows) and never fast mode."""
    try:
        core_module.extract_audio_slice(audio_path, start, end, slice_path,
                                        timeout=_SLICE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"failed_reason": "audio_slice",
                "detail": "Cutting this line's audio took too long and was stopped."}
    except Exception:
        return {"failed_reason": "audio_slice", "detail": "Couldn't cut this line's audio."}
    try:
        segments = core_module.transcribe_for_timing(
            slice_path, whisper_size, language=language,
            on_gpu_fallback=lambda exc: gpu_fallback.append(core_module.short_reason(exc)),
            fast_mode=False, **transcribe_kwargs)
    except core_module.ModelDownloadError as exc:
        return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
    return {"segments": [{"text": (seg.get("text") or "")} for seg in segments or []]}


def retranscribe_worker(audio_path, start, end, source_language, whisper_size, beam_size,
                        min_silence_ms, vad_threshold, use_gpu, initial_prompt,
                        hallucination_silence_sec, repeat_guard, preset, loading_message,
                        timeout_s, scratch_dir, result_queue):
    """Process-job target (spawn; top level and plain arguments so it
    pickles): cuts [start, end) from the drama's audio, transcribes it and puts
    ("ok", outcome) with outcome {"segments", optional "gpu_fallback", plus the
    Ollama notice} or {"failed_reason", ...} for a slice or model-download
    failure or its own timeout; or ("error", type name, redacted message).
    Writes nothing to the database. Cancel kills the whole process, which is
    what really frees the VRAM of a wedged CUDA call.

    The timeout watchdog is a thread in this process: the stuck call (a
    ctranslate2 future wait) releases the GIL, so the watchdog can still
    report and end the process."""
    background_jobs.start_own_process_group()

    def give_up():
        result_queue.put(("ok", {"failed_reason": "timeout"}))
        result_queue.close()
        result_queue.join_thread()
        os._exit(0)

    watchdog = threading.Timer(timeout_s, give_up)
    watchdog.daemon = True
    watchdog.start()
    try:
        os.makedirs(scratch_dir, exist_ok=True)
        tempfile.tempdir = scratch_dir
        slice_path = os.path.join(scratch_dir, "line.wav")
        background_jobs.report_progress(result_queue, 0.1, loading_message)
        gpu_fallback = []
        heard = hear_window(
            audio_path, start, end, source_language, slice_path, whisper_size, gpu_fallback,
            use_gpu=use_gpu, initial_prompt=initial_prompt, beam_size=beam_size,
            min_silence_duration_ms=min_silence_ms, vad_threshold=vad_threshold,
            hallucination_silence_sec=hallucination_silence_sec,
            repeat_guard=repeat_guard, sensitivity_preset=preset)
        if heard.get("failed_reason"):
            result_queue.put(("ok", heard))
            return
        outcome = {**heard, **ollama_unload.take_notice_result()}
        if gpu_fallback:
            outcome["gpu_fallback"] = gpu_fallback[0]
        result_queue.put(("ok", outcome))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, redact_secrets(str(exc))))
    finally:
        watchdog.cancel()


def apply_retranscribe_outcome(job_id, outcome, drama_id, line_id, zh_before, start, end):
    """on_done for the re-transcribe process job: its return value is the
    job's result. Runs in the parent, where the database is; writes nothing
    to the line. Result on success: {"line_id", "proposed_zh", "base_zh",
    "base_start", "base_end"} (proposed_zh capped at RETRANSCRIBE_MAX_CHARS;
    base_zh raw, for the apply compare), plus "gpu_fallback" when it ran on
    CPU. GET /api/jobs shows only line_id and gpu_fallback (jobs_service's
    allowlist); the text is read in-process by get_retranscribe_result and
    apply_retranscribe_line. A failed_reason instead when the audio couldn't
    be cut ("audio_slice", including an ffmpeg timeout), "timeout" (the worker
    outran retranscribe_timeout_s), "model_download", nothing was heard
    ("empty"), or the line no longer exists ("line_gone"). A cancel kills the
    worker and ends the job as cancelled before this runs."""
    if outcome.get("failed_reason"):
        return {"line_id": line_id, **outcome}
    new_zh = " ".join((s.get("text") or "").strip() for s in outcome.get("segments") or []).strip()
    if not new_zh:
        return {"line_id": line_id, "failed_reason": "empty"}
    if not any(ln.id == line_id for ln in db.load_line_objects(drama_id)):
        return {"line_id": line_id, "failed_reason": "line_gone",
                "detail": "The line was merged, split or deleted meanwhile; nothing was changed."}
    result = {"line_id": line_id, "proposed_zh": new_zh[:RETRANSCRIBE_MAX_CHARS],
              "base_zh": zh_before or "", "base_start": start, "base_end": end,
              **{k: v for k, v in outcome.items() if k not in ("segments", "gpu_fallback")}}
    if outcome.get("gpu_fallback"):
        result["gpu_fallback"] = outcome["gpu_fallback"]
        result["device_notice"] = core_module.gpu_fallback_notice(
            "Re-transcribing this line", outcome["gpu_fallback"])
    return result


def retranscribe_many_worker(audio_path, windows, whisper_size, beam_size, min_silence_ms,
                             vad_threshold, use_gpu, initial_prompt, hallucination_silence_sec,
                             repeat_guard, preset, loading_message, timeout_s, scratch_dir,
                             result_queue):
    """Process-job target for several lines: `windows` is [(line_id, start,
    end, language)] in the order to run. One process, so the Whisper model is
    loaded once (core keeps it for the process) however many lines follow.
    Puts ("ok", outcome) with outcome {"lines": [{"line_id", "text"} or
    {"line_id", "failed_reason"}], optional "gpu_fallback", plus the Ollama
    notice}, or {"failed_reason", ...} when the whole run is lost (model
    download, its own timeout); or ("error", type name, redacted message). A
    line whose audio can't be cut is reported and the run goes on. Writes
    nothing to the database; Cancel kills the whole process like the one-line
    worker's. One watchdog covers the whole run."""
    background_jobs.start_own_process_group()

    def give_up():
        result_queue.put(("ok", {"failed_reason": "timeout"}))
        result_queue.close()
        result_queue.join_thread()
        os._exit(0)

    watchdog = threading.Timer(timeout_s, give_up)
    watchdog.daemon = True
    watchdog.start()
    try:
        os.makedirs(scratch_dir, exist_ok=True)
        tempfile.tempdir = scratch_dir
        slice_path = os.path.join(scratch_dir, "line.wav")
        background_jobs.report_progress(result_queue, 0.02, loading_message)
        gpu_fallback = []
        heard_lines = []
        for n, (line_id, start, end, language) in enumerate(windows):
            heard = hear_window(
                audio_path, start, end, language, slice_path, whisper_size, gpu_fallback,
                # A failed GPU load would otherwise be retried for every line.
                use_gpu=use_gpu and not gpu_fallback, initial_prompt=initial_prompt,
                beam_size=beam_size, min_silence_duration_ms=min_silence_ms,
                vad_threshold=vad_threshold, hallucination_silence_sec=hallucination_silence_sec,
                repeat_guard=repeat_guard, sensitivity_preset=preset)
            if heard.get("failed_reason") == "model_download":
                result_queue.put(("ok", heard))
                return
            if heard.get("failed_reason"):
                heard_lines.append({"line_id": line_id, "failed_reason": heard["failed_reason"]})
            else:
                text = " ".join((s.get("text") or "").strip() for s in heard["segments"]).strip()
                heard_lines.append({"line_id": line_id, "text": text})
            background_jobs.report_progress(
                result_queue, 0.05 + 0.95 * (n + 1) / len(windows),
                f"Heard line {n + 1} of {len(windows)}")
        outcome = {"lines": heard_lines, **ollama_unload.take_notice_result()}
        if gpu_fallback:
            outcome["gpu_fallback"] = gpu_fallback[0]
        result_queue.put(("ok", outcome))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, redact_secrets(str(exc))))
    finally:
        watchdog.cancel()


# Ceiling on the proposed plus original text kept in one run's job result, so
# a 200-line run of long lines can't pin megabytes in the API process.
MAX_HELD_CHARS = 1_000_000


def apply_retranscribe_many_outcome(job_id, outcome, drama_id, bases):
    """on_done for the many-line process job; its return value is the job's
    result. `bases` is {line_id: {"zh", "en", "start", "end", "number"}} as
    the lines stood when the job started (raw, for the apply compare). Runs in
    the parent; writes nothing to the lines.

    Result on success: {"line_count", "candidate_count", "failed_count",
    "skipped_count", "proposals": [{"line_id", "number", "base_zh", "base_en",
    "base_start", "base_end", "proposed_zh"}], "failures": [{"line_id",
    "number", "reason"}]} plus "gpu_fallback"/"device_notice" when it ran on CPU, and
    "truncated" when MAX_HELD_CHARS dropped proposals. GET /api/jobs shows
    only the counts (jobs_service's allow-list); the text is read in-process
    by get_retranscribe_many_result. skipped_count is lines that heard the
    same text, which have nothing to propose. A failed_reason alone when the
    whole run was lost ("model_download", "timeout"). A cancel kills the
    worker and ends the job as cancelled before this runs."""
    if outcome.get("failed_reason"):
        return {k: v for k, v in outcome.items() if k in ("failed_reason", "detail")}
    present = {ln.id for ln in db.load_line_objects(drama_id)}
    proposals, failures, unchanged, held, truncated = [], [], 0, 0, False
    for heard in outcome.get("lines") or []:
        line_id = heard["line_id"]
        base = bases.get(line_id)
        if base is None:
            continue
        proposed = (heard.get("text") or "")[:RETRANSCRIBE_MAX_CHARS]
        fail = {"line_id": line_id, "number": base["number"]}
        if line_id not in present:
            failures.append({**fail, "reason": "line_gone"})
        elif heard.get("failed_reason"):
            failures.append({**fail, "reason": heard["failed_reason"]})
        elif not proposed:
            failures.append({**fail, "reason": "empty"})
        elif proposed == base["zh"]:
            unchanged += 1
        elif held + len(proposed) + len(base["zh"]) + len(base["en"]) > MAX_HELD_CHARS:
            truncated = True
        else:
            held += len(proposed) + len(base["zh"]) + len(base["en"])
            proposals.append({
                "line_id": line_id, "number": base["number"], "base_zh": base["zh"],
                "base_en": base["en"], "base_start": base["start"], "base_end": base["end"],
                "proposed_zh": proposed})
    result = {"line_count": len(bases), "candidate_count": len(proposals),
              "failed_count": len(failures), "skipped_count": unchanged,
              "proposals": proposals, "failures": failures,
              **{k: v for k, v in outcome.items() if k not in ("lines", "gpu_fallback")}}
    if truncated:
        result["truncated"] = True
    if outcome.get("gpu_fallback"):
        result["gpu_fallback"] = outcome["gpu_fallback"]
        result["device_notice"] = core_module.gpu_fallback_notice(
            "Re-transcribing these lines", outcome["gpu_fallback"])
    return result
