"""
asr_benchmark.py -- side-by-side comparison harness for the experimental
Qwen3-ASR/Qwen3-ForcedAligner backends against the existing Whisper-based
defaults, on one clip at a time.

WHY THIS EXISTS: asr_backend.py and forced_align.py both ship as opt-in
alternatives specifically BECAUSE their quality on this project's actual
content is unverified -- particularly Japanese, where no public
Qwen3-ASR-vs-Whisper benchmark was found. This script runs both sides of
each comparison on the same clip and reports timing + output side by
side, so that question gets answered against real content instead of
assumption.

Deliberately standalone -- it doesn't touch the drama library/database
(db.py), because a benchmark clip usually isn't a drama you've imported
yet. Point it at any audio/video file directly.

Runs on your machine against your own test clip (it needs the models and
real audio).

Usage:
    # Transcription-only comparison (Whisper vs Qwen3-ASR text, same
    # Whisper segment timing either way):
    python asr_benchmark.py --audio clip.wav --language ja

    # Full comparison including forced-alignment timing, when you have a
    # real transcript for the clip:
    python asr_benchmark.py --audio clip.wav --language ja --transcript clip_transcript.txt

    # Extra options:
    python asr_benchmark.py --audio clip.wav --language zh --use-gpu \\
        --whisper-size large-v3 --qwen-model-size 1.7B --out report.json
"""

import argparse
import difflib
import json
import time
from dataclasses import asdict, dataclass, field

from core import SOURCE_LANGUAGES


def read_peak_vram_mb():
    """Peak CUDA memory allocated since the last reset, in MB, or None if
    torch/CUDA isn't available -- so the report says "N/A" on a CPU-only
    run instead of crashing."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        return torch.cuda.max_memory_allocated() / (1024 * 1024)
    except ImportError:
        return None


def reset_vram_counter():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except ImportError:
        pass


@dataclass
class StageResult:
    name: str
    ok: bool
    seconds: float = 0.0
    peak_vram_mb: float = None
    error: str = None
    data: dict = field(default_factory=dict)


def _run_stage(name, fn):
    """Times one stage and isolates its failure -- mirrors cli.py's
    per-drama isolation (_run_batch): one backend crashing shouldn't stop
    the rest of the comparison from reporting whatever it did get."""
    reset_vram_counter()
    start = time.perf_counter()
    try:
        data = fn()
        return StageResult(name=name, ok=True, seconds=time.perf_counter() - start,
                            peak_vram_mb=read_peak_vram_mb(), data=data)
    except Exception as exc:
        return StageResult(name=name, ok=False, seconds=time.perf_counter() - start,
                            peak_vram_mb=read_peak_vram_mb(), error=f"{type(exc).__name__}: {exc}")


def _similarity(a: str, b: str) -> float:
    """A rough, dependency-free proxy for transcription accuracy --
    character-level ratio via difflib, NOT a real WER (no jiwer or
    similar is in this project's requirements). Good enough to see at a
    glance which backend's text is closer to a reference transcript;
    don't quote it as a WER number in anything that matters."""
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def run_benchmark(audio_path: str, language: str, transcript_path: str = None,
                   whisper_size: str = "medium", qwen_model_size: str = "1.7B",
                   use_gpu: bool = False):
    import asr_backend
    import forced_align
    from core import align_transcript_to_timing, split_user_transcript

    results = {"audio_path": audio_path, "language": language,
               "whisper_size": whisper_size, "qwen_model_size": qwen_model_size,
               "use_gpu": use_gpu, "stages": []}

    whisper_stage = _run_stage(
        "whisper_transcribe",
        lambda: {"segments": asr_backend.WhisperBackend().transcribe(
            audio_path, language, whisper_size=whisper_size, use_gpu=use_gpu)},
    )
    results["stages"].append(whisper_stage)
    if not whisper_stage.ok:
        return results  # nothing else here can run without segment boundaries

    whisper_segments = whisper_stage.data["segments"]

    qwen_asr_stage = _run_stage(
        "qwen3_asr_transcribe",
        lambda: {"segments": asr_backend.Qwen3ASRBackend(model_size=qwen_model_size).transcribe(
            audio_path, language, whisper_segments=whisper_segments, use_gpu=use_gpu)},
    )
    results["stages"].append(qwen_asr_stage)

    if not transcript_path:
        return results

    with open(transcript_path, "r", encoding="utf-8") as f:
        reference_text = f.read()

    whisper_text = "".join(s["text"] for s in whisper_segments)
    results["whisper_similarity_to_reference"] = _similarity(whisper_text, reference_text)
    if qwen_asr_stage.ok:
        qwen_text = "".join(s["text"] for s in qwen_asr_stage.data["segments"])
        results["qwen3_asr_similarity_to_reference"] = _similarity(qwen_text, reference_text)

    user_lines = split_user_transcript(reference_text)

    diff_align_stage = _run_stage(
        "whisper_diff_align",
        lambda: {"lines": [asdict(ln) for ln in
                            align_transcript_to_timing(user_lines, whisper_segments)]},
    )
    results["stages"].append(diff_align_stage)

    forced_align_stage = _run_stage(
        "qwen3_forced_align",
        lambda: {"lines": [asdict(ln) for ln in
                            forced_align.align_with_qwen3(
                                audio_path, user_lines, whisper_segments,
                                language=language, use_gpu=use_gpu)]},
    )
    results["stages"].append(forced_align_stage)

    return results


def print_summary(results):
    print(f"\n=== ASR/alignment benchmark: {results['audio_path']} ({results['language']}) ===\n")
    for stage in results["stages"]:
        status = "OK" if stage.ok else f"FAILED: {stage.error}"
        vram = f"{stage.peak_vram_mb:.0f} MB" if stage.peak_vram_mb is not None else "N/A"
        print(f"  {stage.name:<22} {stage.seconds:6.1f}s   peak VRAM: {vram:<10}   {status}")

    if "whisper_similarity_to_reference" in results:
        print(f"\n  Whisper text similarity to reference:    "
              f"{results['whisper_similarity_to_reference']:.3f}")
    if "qwen3_asr_similarity_to_reference" in results:
        print(f"  Qwen3-ASR text similarity to reference:  "
              f"{results['qwen3_asr_similarity_to_reference']:.3f}")
    if "whisper_similarity_to_reference" in results:
        print("\n  (similarity is a rough difflib character-ratio proxy, not a real WER)")


def main():
    p = argparse.ArgumentParser(
        description="Compare Whisper vs Qwen3-ASR/Qwen3-ForcedAligner on one clip")
    p.add_argument("--audio", required=True, help="Path to a short (5-10 min) representative clip")
    p.add_argument("--language", required=True, choices=list(SOURCE_LANGUAGES))
    p.add_argument("--transcript", default=None,
                   help="Optional reference transcript (.txt) -- enables the forced-alignment "
                        "comparison and a rough text-similarity check for both ASR backends")
    p.add_argument("--whisper-size", default="medium", choices=["small", "medium", "large-v3"])
    p.add_argument("--qwen-model-size", default="1.7B", choices=["0.6B", "1.7B"])
    p.add_argument("--use-gpu", action="store_true")
    p.add_argument("--out", default=None, help="Write the full JSON report here")
    args = p.parse_args()

    results = run_benchmark(
        args.audio, args.language, transcript_path=args.transcript,
        whisper_size=args.whisper_size, qwen_model_size=args.qwen_model_size,
        use_gpu=args.use_gpu,
    )
    print_summary(results)

    if args.out:
        serializable = dict(results)
        serializable["stages"] = [asdict(s) for s in results["stages"]]
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)
        print(f"\nFull report written to {args.out}")


if __name__ == "__main__":
    main()
