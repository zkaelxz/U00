"""Real-hardware release check: `python -m tools.release_check MANIFEST.json [--previous RESULTS.json] [--yes]`.

Runs a private set of golden clips (kept outside the repo, which is public) through the app's own
transcription, speaker detection and translation code on this PC, and writes one markdown results
table plus a JSON file. Scores: 1 - CER (1 - WER for spaced languages), diarization error rate,
translation chrF against a reference, speed as a multiple of real time, and peak GPU memory.
With --previous it flags every score that got worse than the last run. Nothing is saved to the library.

Without --yes it is a dry run: it lists the clips and what each would load and writes nothing.
Paid translation engines also need --allow-paid; a run stops adding paid lines once --max-usd is spent.
"""
import argparse
import datetime
import json
import os
import platform
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

import media_inspect
import subtitle_parse
import whisper_models
from engine_backends import engine_registry, pricing
from engine_backends import shared as engine_shared
from lib.errors import ServiceError
from services import benchmark_scoring_service, settings_service, translate_service
from tools import self_test
from tools.asr_bench import Refusal, gpu_guard

REPORT_TITLE = "# Baihe real-hardware release check"
KINDS = ("transcription", "diarization", "translation")
# A drop bigger than this between runs is flagged; smaller moves are within run-to-run noise on one clip.
REGRESSION_MARGIN = 0.02
DIARIZATION_FRAME_S = 0.01
TRANSLATE_BATCH = 20
MAX_CELL_CHARS = 120


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
    return data


def read_cues(path: str, language: str) -> list:
    try:
        with open(path, "rb") as fh:
            data = fh.read(subtitle_parse.MAX_FILE_BYTES + 1)
        parsed = subtitle_parse.parse_subtitle(data, os.path.basename(path), language=language)
    except (subtitle_parse.SubtitleParseError, OSError):
        raise Refusal("Can't read a subtitle file named in the manifest.") from None
    return [c.text.strip() for c in parsed.cues if c.text.strip()]


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


def run_diarization(clip: dict, opts) -> dict:
    import diarize

    token = settings_service.resolve_key("hf_token")
    if not token:
        raise RuntimeError("No Hugging Face token is set up, so speaker detection can't load.")
    audio_s = media_inspect.duration_seconds(clip["audio"]) or 0.0
    with open(clip["reference_rttm"], "r", encoding="utf-8") as fh:
        reference = parse_rttm(fh.read())
    info = {}
    reset_peak_vram()
    started = time.monotonic()
    turns = diarize.diarize(clip["audio"], token, clip.get("num_speakers"), use_gpu=opts.use_gpu, run_info=info)
    wall = time.monotonic() - started
    vram = peak_vram_mb()
    der, miss, false_alarm, confusion = diarization_error_rate(reference, turns)
    found = len({t["speaker"] for t in turns})
    return {"score": der, "metric": "der", "speed_x": audio_s / wall if wall and audio_s else None,
            "vram_mb": vram,
            "detail": (f"found {found} of {len({t['speaker'] for t in reference})} speakers; miss {miss:.1%}, "
                       f"false alarm {false_alarm:.1%}, confusion {confusion:.1%}; ran on {info.get('device', '?')}")}


class Spend:
    """Running cost of paid translation lines, so a run stops before it passes --max-usd."""

    def __init__(self, cap: float):
        self.cap, self.used = cap, 0.0


def run_translation(clip: dict, opts, spend: Spend) -> dict:
    engine_name = clip.get("engine") or opts.engine
    sources = read_cues(clip["source_srt"], clip["language"])
    references = read_cues(clip["reference_srt"], clip["language"])
    if len(sources) != len(references):
        raise ValueError(f"The source has {len(sources)} lines but the reference {len(references)}; "
                         "they must match line for line.")
    paid = engine_name not in engine_registry.FREE_ENGINES
    if paid and not opts.allow_paid:
        raise RuntimeError(f"{engine_name} is a paid engine; add --allow-paid to use it.")
    key = translate_service.resolve_api_key(engine_name)
    if not key:
        raise RuntimeError(f"No key is set up for {engine_name}.")
    engine = engine_registry.get_engine(engine_name, key, clip.get("model"))
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
    score, metric = chrf_score(outputs, references)
    return {"score": score, "metric": metric, "speed_x": len(sources) / wall if wall else None,
            "vram_mb": None, "detail": f"{len(sources)} lines via {engine_name}; speed is lines/s"}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def worse(kind_metric: str, now, before) -> bool:
    """DER is better when lower; every other score is better when higher."""
    if now is None or before is None:
        return False
    return now > before + REGRESSION_MARGIN if kind_metric == "der" else now < before - REGRESSION_MARGIN


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
                  f"is worse by more than {REGRESSION_MARGIN}.", ""]
    lines += ["| Clip | Kind | Metric | Score | Previous | Speed | VRAM MB | Result |",
              "|---|---|---|---|---|---|---|---|"]
    flagged = 0
    for r in run["results"]:
        prev = before.get(r["id"])
        prev_score = prev.get("score") if prev else None
        if r.get("error"):
            result = "FAILED: " + _cell(r["error"])
        elif prev and worse(r["metric"], r["score"], prev_score):
            result, flagged = "WORSE", flagged + 1
        else:
            result = "ok"
        lines.append(f"| {_cell(r['id'])} | {r['kind']} | {r.get('metric') or '-'} | {_fmt(r.get('score'))} | "
                     f"{_fmt(prev_score)} | {_fmt(r.get('speed_x'), 1)} | {r.get('vram_mb') or '-'} | {result} |")
    failed = sum(1 for r in run["results"] if r.get("error"))
    lines += ["", f"{len(run['results'])} clips, {failed} failed, {flagged} worse than the previous run.", ""]
    notes = [f"- {_cell(r['id'])}: {_cell(r['detail'])}" for r in run["results"] if r.get("detail")]
    return "\n".join(lines + (["Details", ""] + notes + [""] if notes else []))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def default_out_dir() -> str:
    return portable.data_dir()


def run_all(manifest: dict, opts) -> dict:
    spend = Spend(opts.max_usd)
    results = []
    with gpu_guard(opts.use_gpu, "Release check"):
        for n, clip in enumerate(manifest["clips"]):
            print(f"[{n + 1}/{len(manifest['clips'])}] {clip['id']} ({clip['kind']}) ...", flush=True)
            row = {"id": clip["id"], "kind": clip["kind"]}
            try:
                if clip["kind"] == "transcription":
                    row.update(run_transcription(clip, opts))
                elif clip["kind"] == "diarization":
                    row.update(run_diarization(clip, opts))
                else:
                    row.update(run_translation(clip, opts, spend))
            except Exception as exc:
                # One broken clip must not cost the rest of a long GPU run.
                row.update({"score": None, "metric": None, "error": _clean(f"{type(exc).__name__}: {exc}")})
            print("    " + (f"failed: {row['error']}" if row.get("error") else f"score {_fmt(row['score'])}"),
                  flush=True)
            results.append(row)
    return {"when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "machine": platform.node() or "unknown",
            "gpu": gpu_name() if opts.use_gpu else None, "whisper_model": opts.whisper_model,
            "paid_usd": round(spend.used, 4), "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.release_check", description=__doc__.splitlines()[0])
    parser.add_argument("manifest", help="JSON file listing the golden clips (kept outside the repo)")
    parser.add_argument("--previous", metavar="RESULTS.json", help="the JSON of the last run, to flag regressions")
    parser.add_argument("--whisper-model", default="large-v3", help="transcription model (default large-v3)")
    parser.add_argument("--engine", default="ollama", help="translation engine for clips that name none (default: free local)")
    parser.add_argument("--cpu", action="store_true", help="run on the CPU instead of the GPU")
    parser.add_argument("--allow-paid", action="store_true", help="let translation use a paid engine")
    parser.add_argument("--max-usd", type=float, default=1.0, help="stop paid translation past this spend (default 1.00)")
    parser.add_argument("--yes", action="store_true", help="run the clips (default: dry run)")
    parser.add_argument("--out", metavar="DIR", help="folder for the report and JSON (default: the data folder)")
    opts = parser.parse_args(argv)
    opts.use_gpu = not opts.cpu
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
        report_path = os.path.join(out_dir, f"release-check-{stamp}.md")
        if self_test._refuse_to_overwrite(report_path, REPORT_TITLE):
            raise Refusal("Refusing to write the report: that file exists and is not a release check report.")
        run = run_all(manifest, opts)
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
