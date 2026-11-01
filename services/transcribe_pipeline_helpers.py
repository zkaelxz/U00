"""services/transcribe_pipeline_helpers.py -- the transcription pipeline helpers that need nothing from the pipeline class: the forced-aligner refinement, status text, and scratch cleanup.

Moved out of services/transcribe_pipeline.py to keep it under the size ceiling. Import these names from
`services.transcribe_pipeline`, which re-exports them at its end; nothing imports this module
first, because it needs `services.transcribe_pipeline` fully defined.
"""

import contextlib
import glob
import os
import shutil
import background_jobs
import whisper_models
from services.qwen3_requirements_service import import_failure_message
from services.transcribe_pipeline import (REALIGN_START)


_MODEL_DOWNLOAD_SIZES = {"large-v3": "~3 GB", "large-v3-turbo": "~1.6 GB", "large-v2": "~3 GB", "large-v1": "~3 GB",
                         "large": "~3 GB", "ja-1.5b": "~3.1 GB", "kotoba-v2": "~1.5 GB", "medium": "~1.5 GB", "small": "~500 MB",
                         "base": "~150 MB", "tiny": "~75 MB"}


def _refine_without_word_timestamps(segments, audio_path, language, use_gpu, rep, qwen_device,
                                    on_fallback, notices, mixed, stage_max) -> list:
    """Word timing for a Whisper model that cannot give it: the Qwen3 forced aligner
    refines the segment times. Where it cannot run (not installed, a mixed-language run
    whose spans have different languages, or any error while loading or running it) the segment
    times are kept and the job reports why, so the transcription itself is never lost
    to the optional step. Lines are not flagged: plain Whisper segment times are normal
    timing, and a flag on every line would bury the ones that really need a check."""
    import forced_align
    import qwen3_native
    problem = ("Mixed-language runs are not refined by the forced aligner." if mixed
               else qwen3_native.aligner_language_problem(language)
               or qwen3_native.transformers_problem("Qwen3 forced alignment"))
    if not problem:
        # Whisper has finished: its VRAM must be free before the aligner loads, or the
        # headroom check refuses the load while the transcription is already done.
        whisper_models.release_gpu_models()
        ticker = rep.stage("Refining line timing with the Qwen3 forced aligner").start()
        try:
            def _on_device(label):
                qwen_device["Qwen3 forced alignment"] = label

            def _progress(frac):
                ticker.stop()
                rep.progress(stage_max * (REALIGN_START + (1 - REALIGN_START) * frac),
                             f"Refining line timing... {frac * 100:.0f}%")

            return forced_align.refine_whisper_segments(
                audio_path, segments, language, use_gpu=use_gpu, cancel_check=rep.raise_if_cancelled,
                progress_cb=_progress, on_device=_on_device,
                on_gpu_fallback=lambda exc: on_fallback("Qwen3 forced alignment", exc))
        except background_jobs.JobCancelled:
            whisper_models.release_gpu_models()
            raise
        except ImportError as exc:
            problem = import_failure_message(exc)
        except Exception as exc:
            # Nothing here may cost the finished transcription.
            problem = forced_align.refine_failure_reason(exc)
        finally:
            ticker.stop()
    notices.append(f"Line timing was not refined ({problem})")
    return segments


def _separation_device_label(kind: str) -> str:
    return {"gpu": "on GPU", "cpu": "on CPU (slow)"}.get(kind, "")


def _stage_summary(stage_counts: dict) -> str:
    return "; ".join(f"{label}: {count}" for label, count in stage_counts.items())


def _model_loading_message(whisper_size: str, cached: bool) -> str:
    if cached:
        return f"Loading Whisper model {whisper_size}..."
    size = _MODEL_DOWNLOAD_SIZES.get(whisper_size)
    hint = f", {size}" if size else ""
    return f"Loading Whisper model {whisper_size} (downloading on first use{hint})"


def _remove_scratch_dir(path, _job_id=None, part_dir=None):
    shutil.rmtree(path, ignore_errors=True)
    if part_dir:
        # A worker killed during _move_into_place's cross-volume copy leaves
        # its .part- file beside vocals.wav, outside the scratch folder.
        for leftover in glob.glob(os.path.join(glob.escape(part_dir), ".part-*.wav")):
            with contextlib.suppress(OSError):
                os.remove(leftover)
