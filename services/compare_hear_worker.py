"""The spawned worker behind "compare transcription": cuts each chosen line's
window and hears it with the candidate backend, so Cancel can kill the process
instead of waiting for a model call. Kept free of the compare service (the
database, translation engines) so the child imports only what it needs."""

import os
import subprocess

import asr_backend
import background_jobs
import core as core_module
import ollama_unload
from translate_engines import redact_secrets

SLICE_TIMEOUT_S = 120
# The Qwen3 backends that find speech themselves instead of hearing Whisper's segments.
VAD_BACKENDS = ("qwen3_asr_vad", "qwen3_asr_long")


def hear(slice_path: str, cfg: dict, language, on_fallback) -> str:
    """Candidate source text for one cut line from the chosen backend."""
    backend, use_gpu = cfg["backend"], cfg["use_gpu"]
    if backend in VAD_BACKENDS:
        segments = asr_backend.get_backend(backend).transcribe(
            slice_path, language, use_gpu=use_gpu)
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


class _LineSink:
    """Sends each finished line to the parent as it is made."""

    def __init__(self, result_queue):
        self._queue = result_queue

    def append(self, entry):
        self._queue.put(("item", entry))


def hear_lines_worker(audio_path, windows, cfg, scratch_dir, result_queue):
    """gpu_process_job body. `windows` is [(line_id, number, start, end,
    language)]. Puts each line as ("item", {"line_id", "text"} or {"line_id",
    "error"}) when it is heard, so a cancel or timeout keeps the earlier ones,
    then ("ok", {optional "gpu_fallback", plus the Ollama notice}); a model
    download or missing backend ends the run at once (every line would fail
    the same way) with "failed_reason" and "detail". Writes nothing to the
    database."""
    slice_path = os.path.join(scratch_dir, "line.wav")
    gpu_fallback, fatal = [], {}
    lines = _LineSink(result_queue)
    for n, (line_id, number, start, end, language) in enumerate(windows):
        background_jobs.report_progress(
            result_queue, n / len(windows), f"Hearing line {n + 1} of {len(windows)}")
        try:
            try:
                core_module.extract_audio_slice(audio_path, start, end, slice_path,
                                                timeout=SLICE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                lines.append({"line_id": line_id,
                              "error": f"line {number}: cutting the audio took too long"})
                continue
            except (subprocess.CalledProcessError, OSError):
                # str() of these carries the ffmpeg command line, i.e. absolute paths.
                lines.append({"line_id": line_id,
                              "error": f"line {number}: couldn't cut this line's audio"})
                continue
            text = hear(slice_path, cfg, language,
                        lambda exc: gpu_fallback.append(core_module.short_reason(exc)))
            lines.append({"line_id": line_id, "text": text})
        except core_module.ModelDownloadError as exc:
            fatal = {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
            break
        except ImportError:
            fatal = {"failed_reason": "dependency_missing",
                     "detail": "This transcription backend isn't installed yet. "
                               "Open Diagnostics to install it."}
            break
        except Exception as exc:
            lines.append({"line_id": line_id,
                          "error": redact_secrets(f"line {number}: {exc}")})
        finally:
            if os.path.exists(slice_path):
                os.remove(slice_path)
    outcome = {**fatal, **ollama_unload.take_notice_result()}
    if gpu_fallback:
        outcome["gpu_fallback"] = gpu_fallback[0]
    result_queue.put(("ok", outcome))
