"""ASR recipe bench: `python -m tools.asr_bench TITLE_ID --config RECIPE [--config RECIPE ...] [--yes]`.

Runs the SAME stretch of a title's audio through several transcription recipes
(model, speech detector preset, beam, prompt, forced-aligner timing, voice detector)
with the app's own transcription code, and writes one markdown comparison report.
Nothing is saved to the title: the tool only reads it, and the report goes to the
data folder.

Without --yes it is a dry run: it prints the recipes, the models they would load and a
rough time estimate, loads nothing and writes nothing. With --yes it takes the GPU
slot the app's jobs use (waiting while another job holds it), releases the models
between recipes and records peak GPU memory when the PC can report it.
"""
import argparse
import contextlib
import datetime
import math
import os
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/asr_bench.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Must run before the app imports below (as cli.py does).
import portable
portable.activate_portable_mode()

import asr_backend
import audio_extract
import whisper_hallucination
import db
import memory_headroom
import subtitle_parse
import translate_engines
import vad_segments
import whisper_models
from jobs import gpu_slots
from services import benchmark_scoring_service, settings_service, transcribe_pipeline, transcribe_service
from services.service_errors import ServiceError
from tools import self_test
from tools import asr_bench_recipes as recipes_mod
from tools.asr_bench_recipes import BACKENDS, RecipeError, RunContext

REPORT_TITLE = "# Baihe ASR bench report"
REPORT_NAME = "asr-bench-report.md"
DEFAULT_WINDOW_S = 300.0
MAX_WINDOW_S = 1800.0
MAX_RECIPES = 6
MAX_CELL_CHARS = 300
MAX_DIFF_ROWS = 150
# A hypothesis line "covers" a reference line when they overlap by this share of the shorter one.
COVER_FRACTION = 0.3
# A line with less than this share of its length over detected speech sits in silence.
SPEECH_UNDER_LINE_FRACTION = 0.2
# Identical neighbours past this count are a loop; the app's own filter only cuts them at 4.
LOOP_RUN = 3
# Reference lines must sit inside the window, give or take what a cut can lose.
EDGE_TOLERANCE_S = 0.25
GPU_WAIT_S = 600.0
GPU_POLL_S = 5.0
MONITOR_TICK_S = 2.0
HEARTBEAT_EVERY_S = 60.0

SPEECH_NOTE = ("not measured (the speech detector is not installed)")


class Refusal(Exception):
    """A safety or input check said no; nothing was run."""


# ---------------------------------------------------------------------------
# Plan: everything read from the title, shared by every recipe
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    title_id: int
    drama: dict
    language: str
    audio_path: str
    start: float
    end: float
    recipes: list
    reference: list
    reference_name: str
    use_gpu: bool
    local_model_path: str | None


def _clean(text) -> str:
    return self_test._clean(translate_engines.redact_secrets(str(text or "")))


def _lines_of_title(rows: list) -> list:
    return [{"start": float(r["start"]), "end": float(r["end"]), "text": (r.get("zh") or "").strip()}
            for r in rows if (r.get("zh") or "").strip()]


def _inside_window(lines: list, start: float, end: float) -> list:
    return sorted((ln for ln in lines if ln["start"] >= start - EDGE_TOLERANCE_S
                   and ln["end"] <= end + EDGE_TOLERANCE_S), key=lambda ln: ln["start"])


def load_reference_srt(path: str, language: str) -> list:
    try:
        with open(path, "rb") as fh:
            data = fh.read(subtitle_parse.MAX_FILE_BYTES + 1)
        parsed = subtitle_parse.parse_subtitle(data, os.path.basename(path), language=language)
    except subtitle_parse.SubtitleParseError as exc:
        raise Refusal(f"Can't use the reference file: {_clean(exc)}") from None
    except OSError:
        raise Refusal("Can't read the reference file.") from None
    return [{"start": c.start, "end": c.end, "text": c.text.strip()} for c in parsed.cues]


def build_plan(args) -> Plan:
    drama = db.get_drama(args.title_id)
    if not drama:
        raise Refusal(f"Title {args.title_id} not found.")
    language = drama.get("source_language") or "zh"
    audio_path = transcribe_service._drama_audio_path(args.title_id, drama)
    if not audio_path:
        raise Refusal("This title has no stored audio to transcribe.")
    start = 0.0 if args.from_s is None else args.from_s
    end = start + DEFAULT_WINDOW_S if args.to_s is None else args.to_s
    if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
        raise Refusal("--from must be 0 or more and --to must be after it (seconds).")
    if end - start > MAX_WINDOW_S:
        raise Refusal(f"The window can be at most {int(MAX_WINDOW_S)} seconds.")
    if not args.config:
        raise Refusal("Give at least one --config RECIPE (the first is the baseline); "
                      "see --help for the grammar.")
    if len(args.config) > MAX_RECIPES:
        raise Refusal(f"At most {MAX_RECIPES} recipes per run.")
    default_model = transcribe_service.stored_whisper_size(drama)
    try:
        recipes = recipes_mod.unique_labels(
            [recipes_mod.parse_recipe(text, language, default_model) for text in args.config])
    except RecipeError as exc:
        raise Refusal(str(exc)) from None
    for r in recipes:
        problem = recipes_mod.availability_problem(r, language)
        if problem:
            raise Refusal(f"Recipe {r.label} can't run on this PC: {_clean(problem)}")
    if args.reference:
        reference = _inside_window(load_reference_srt(args.reference, language), start, end)
        reference_name = os.path.basename(args.reference)
    else:
        reference = _inside_window(_lines_of_title(db.load_lines(args.title_id)), start, end)
        reference_name = ""
    if not reference:
        raise Refusal("No reference lines fall inside this window: pass --reference FILE.srt, or pick "
                      "a window (--from/--to, in seconds) where the title already has lines.")
    return Plan(args.title_id, drama, language, audio_path, start, end, recipes, reference,
                _clean(reference_name), bool(settings_service.get("use_gpu")),
                settings_service.get("whisper_model_path") or None)


# ---------------------------------------------------------------------------
# GPU safety
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def gpu_guard(use_gpu: bool, description: str):
    """The cross-process GPU slot the app's jobs and the CLI take (jobs/gpu_slots), so a bench
    waits for a running job instead of loading a model next to it. A CPU run needs none."""
    if not use_gpu:
        yield None
        return
    holder = f"asr-bench:{os.getpid()}"
    deadline = time.monotonic() + GPU_WAIT_S
    waited = False
    while not gpu_slots.acquire(holder, description, check_external_load=True):
        if time.monotonic() >= deadline:
            busy = gpu_slots.status()[1] or "another job"
            raise Refusal(f"The GPU stayed busy with: {_clean(busy)}. Nothing was run; try again "
                          "when it is free.")
        if not waited:
            print(f"Waiting for the GPU -- busy with: {_clean(gpu_slots.status()[1] or 'another job')}")
            waited = True
        time.sleep(GPU_POLL_S)
    try:
        yield holder
    finally:
        gpu_slots.release(holder)


class Monitor:
    """Samples whole-GPU memory while a recipe runs (peak above what was in use at the start)
    and keeps the GPU slot's heartbeat alive. Peak is None when the PC can't report it."""

    def __init__(self, holder, use_gpu: bool):
        self._holder, self._use_gpu = holder, use_gpu
        self._stop = threading.Event()
        self._thread = None
        self._baseline = None
        self._peak_used = None

    @staticmethod
    def _used_mb():
        reading = memory_headroom.read_memory_mb("vram")
        return None if not reading else reading[0] - reading[1]

    def _sample(self):
        used = self._used_mb()
        if used is not None and (self._peak_used is None or used > self._peak_used):
            self._peak_used = used

    def __enter__(self):
        if self._use_gpu:
            self._baseline = self._used_mb()
            self._peak_used = self._baseline
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def _loop(self):
        last_beat = time.monotonic()
        while not self._stop.wait(MONITOR_TICK_S):
            self._sample()
            if self._holder and time.monotonic() - last_beat >= HEARTBEAT_EVERY_S:
                last_beat = time.monotonic()
                try:
                    gpu_slots.heartbeat([self._holder])
                except Exception:
                    pass  # a missed beat must not stop the run; the slot only goes stale after minutes

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._sample()
        return False

    @property
    def peak_mb(self):
        if self._baseline is None or self._peak_used is None:
            return None
        return max(0.0, self._peak_used - self._baseline)


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

@dataclass
class Result:
    recipe: object
    lines: list = field(default_factory=list)   # absolute seconds in the title
    wall_s: float = 0.0
    peak_vram_mb: float | None = None
    notes: list = field(default_factory=list)
    error: str = ""


def run_recipe(plan: Plan, recipe, wav: str, holder) -> Result:
    ctx = RunContext(plan.title_id, plan.drama, plan.language, wav, plan.use_gpu,
                     plan.local_model_path)
    result = Result(recipe)
    started = time.monotonic()
    try:
        with Monitor(holder, plan.use_gpu) as monitor:
            segments = BACKENDS[recipe.backend].run(ctx, recipe)
        result.peak_vram_mb = monitor.peak_mb
        result.lines = [{"start": plan.start + float(s["start"]),
                         "end": plan.start + max(float(s["end"]), float(s["start"])),
                         "text": (s.get("text") or "").strip()}
                        for s in segments or [] if (s.get("text") or "").strip()]
    except Exception as exc:
        # A failed recipe is a column in the report, not the end of the run; the text can carry
        # a key, a path or a URL, so only the cleaned text is kept.
        result.error = f"{type(exc).__name__}: {_clean(exc)}"
    finally:
        result.wall_s = time.monotonic() - started
        whisper_models.release_gpu_models()
    result.notes = [_clean(n) for n in ctx.notes]
    return result


def cut_window(plan: Plan, out_path: str) -> float:
    """Cuts the window with the helper the app uses; returns the audio's length in seconds."""
    import subprocess
    try:
        audio_extract.extract_audio_slice(plan.audio_path, plan.start, plan.end, out_path)
    except subprocess.TimeoutExpired:
        raise Refusal("Cutting the window's audio took too long.") from None
    except (subprocess.CalledProcessError, OSError):
        # str() of these carries the ffmpeg command line, i.e. absolute paths.
        raise Refusal("Couldn't cut that window of audio (is it past the end of the file?).") from None
    return transcribe_pipeline._audio_duration_seconds(out_path) or (plan.end - plan.start)


def speech_spans(wav: str, offset: float):
    """Speech spans (absolute seconds) under the app's default detector, or None when
    the detector is not installed or fails."""
    try:
        spans = vad_segments.speech_spans(asr_backend.load_audio_16k(wav), 16000)
    except Exception:
        return None
    return [(offset + s.start_s, offset + s.end_s) for s in spans]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _overlap(a: dict, b: dict) -> float:
    return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def _covers(hyp: dict, ref: dict) -> bool:
    shorter = max(0.05, min(hyp["end"] - hyp["start"], ref["end"] - ref["start"]))
    return _overlap(hyp, ref) >= COVER_FRACTION * shorter


def match(reference: list, lines: list) -> dict:
    """covering[i] = indexes of the hypothesis lines that cover reference line i."""
    return {i: [j for j, h in enumerate(lines) if _covers(h, r)] for i, r in enumerate(reference)}


def _key(text: str) -> str:
    return whisper_hallucination._stock_key(text)


def count_loops(lines: list) -> int:
    """Lines holding a phrase repeated past the app's loop limits, plus runs of LOOP_RUN or
    more identical neighbouring lines (the app's own repeat filter already cut longer runs)."""
    inside = sum(1 for ln in lines if whisper_hallucination.collapse_phrase_loop(ln["text"]) != ln["text"])
    runs, i = 0, 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and _key(lines[i]["text"]) and _key(lines[j]["text"]) == _key(lines[i]["text"]):
            j += 1
        runs += (j - i) >= LOOP_RUN
        i = j
    return inside + runs


def count_stock_phrases(lines: list) -> int:
    return sum(1 for ln in lines if any(rx.fullmatch(_key(ln["text"])) for rx in whisper_hallucination._STOCK_PHRASE_RES))


def count_in_silence(lines: list, spans) -> int | None:
    if spans is None:
        return None
    count = 0
    for ln in lines:
        length = max(ln["end"] - ln["start"], 1e-6)
        speech = sum(max(0.0, min(ln["end"], e) - max(ln["start"], s)) for s, e in spans)
        count += speech / length < SPEECH_UNDER_LINE_FRACTION
    return count


def score_text(lines: list, reference: list, language: str):
    """(score 0..1 or None, metric name, scorer) with the Benchmark Lab's scorer."""
    return benchmark_scoring_service.score_output(
        "transcription", " ".join(ln["text"] for ln in lines),
        " ".join(r["text"] for r in reference), language)


def metrics(result: Result, reference: list, spans, language: str) -> dict:
    lines = result.lines
    covering = match(reference, lines)
    absorbed = {}
    for i, js in covering.items():
        for j in js:
            absorbed.setdefault(j, []).append(i)
    start_err, end_err = [], []
    for i, js in covering.items():
        if js:
            best = lines[max(js, key=lambda j: _overlap(lines[j], reference[i]))]
            start_err.append(abs(best["start"] - reference[i]["start"]))
            end_err.append(abs(best["end"] - reference[i]["end"]))
    score, metric, scorer = score_text(lines, reference, language)
    return {
        "lines": len(lines),
        "merged": sum(1 for refs in absorbed.values() if len(refs) >= 2),
        "missed": sum(1 for js in covering.values() if not js),
        "start_median": statistics.median(start_err) if start_err else None,
        "start_p95": recipes_mod.percentile(start_err, 0.95),
        "end_median": statistics.median(end_err) if end_err else None,
        "end_p95": recipes_mod.percentile(end_err, 0.95),
        "score": score, "metric": metric, "scorer": scorer,
        "loops": count_loops(lines), "stock": count_stock_phrases(lines),
        "silence": count_in_silence(lines, spans),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _cell(text) -> str:
    text = self_test._URL.sub("[URL]", translate_engines.redact_secrets(" ".join(str(text or "").split())))
    text = text.replace("|", "\\|")
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS - 1] + "…"


def _num(value, digits=2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def diff_rows(reference: list, results: list) -> list:
    """(time, reference text, [text per recipe]) for every place the recipes disagree: one row per
    reference line, plus one per line that covers no reference line. Compared ignoring case and
    punctuation, so a full stop alone is not a difference."""
    ok = [r for r in results if not r.error]
    covers = [match(reference, r.lines) for r in ok]
    rows = []
    for i, ref in enumerate(reference):
        cells = []
        for r, cov in zip(ok, covers):
            js = cov[i]
            text = " / ".join(r.lines[j]["text"] for j in js) or "(none)"
            merged = any(sum(1 for c in cov.values() if j in c) > 1 for j in js)
            cells.append(("(merged) " if merged else "") + text)
        rows.append((ref["start"], ref["text"], cells))
    for k, (r, cov) in enumerate(zip(ok, covers)):
        used = {j for js in cov.values() for j in js}
        for j, ln in enumerate(r.lines):
            if j not in used:
                cells = ["(none)"] * len(ok)
                cells[k] = ln["text"]
                rows.append((ln["start"], "(no reference line)", cells))
    rows.sort(key=lambda row: row[0])
    return [row for row in rows if len({_key(c.removeprefix("(merged) ")) for c in row[2]}) > 1]


def format_report(plan: Plan, results: list, audio_s: float, spans, use_gpu: bool) -> str:
    stats = [metrics(r, plan.reference, spans, plan.language) if not r.error else None for r in results]
    metric = next((s["metric"] for s in stats if s), "cer")
    accuracy = "1-WER" if metric == "wer" else "1-CER"
    reference_what = (f"the hand-corrected file {plan.reference_name}" if plan.reference_name
                      else "the title's current lines (not hand-corrected, so scores measure agreement "
                           "with them, not truth)")
    out = [REPORT_TITLE, "",
           f"Date: {datetime.date.today().isoformat()}  ",
           f"Title id: {plan.title_id}  ",
           f"Language: {plan.language}  ",
           f"Window: {plan.start:.1f}-{plan.end:.1f} s ({audio_s:.1f} s of audio); nothing saved to the title  ",
           f"Device: {'GPU' if use_gpu else 'CPU'}  ",
           f"Reference: {reference_what}; {len(plan.reference)} lines fully inside the window  ",
           f"Baseline: {plan.recipes[0].label}  ", "",
           "## What the columns mean", "",
           "- **wall s**: seconds from starting the recipe to its lines, including loading its models "
           "(models are released between recipes, so each pays the same load).",
           "- **RTF**: wall seconds divided by seconds of audio; below 1 is faster than real time.",
           "- **lines**: lines the recipe produced, after the app's own repeat and stock-phrase filter.",
           "- **merged**: recipe lines that cover two or more reference lines (separate lines run together).",
           "- **missed**: reference lines no recipe line overlaps by at least "
           f"{int(COVER_FRACTION * 100)}% of the shorter line.",
           "- **start / end error**: seconds between a reference line's start (end) and the start (end) of the "
           "recipe line overlapping it most; median and 95th percentile over the reference lines that were found.",
           f"- **{accuracy}**: 1 minus the character error rate of all the recipe's text against all the reference "
           "text (1 minus the word error rate for space-delimited languages), same normalisation and scorer as the "
           "Benchmark Lab; higher is better.",
           f"- **loops**: lines with a phrase repeated past the app's loop limits, plus runs of {LOOP_RUN} or more "
           "identical neighbouring lines.",
           "- **stock phrases**: lines that are only a known outro or credit phrase (\"thanks for watching\", "
           "\"ご視聴ありがとうございました\") that Whisper writes over silence.",
           "- **in silence**: lines with almost no detected speech under them (the app's speech detector, "
           "run once on the window).",
           "- **peak VRAM MB**: most whole-GPU memory in use during the recipe above what was in use before it; "
           "other programs on the GPU count too.", ""]
    out += ["## Speed", "",
            "| recipe | wall s | RTF | peak VRAM MB | note |", "|---|---|---|---|---|"]
    for r in results:
        note = "; ".join(([f"FAILED: {r.error}"] if r.error else []) + r.notes)
        out.append(f"| {_cell(r.recipe.label)} | {r.wall_s:.1f} | {r.wall_s / audio_s:.2f} | "
                   f"{_num(r.peak_vram_mb, 0) if use_gpu else 'n/a (CPU)'} | {_cell(note)} |")
    out += ["", "## Quality", "",
            f"| recipe | lines | merged | missed | start err median | start err p95 | end err median | "
            f"end err p95 | {accuracy} | loops | stock phrases | in silence |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r, s in zip(results, stats):
        if s is None:
            out.append(f"| {_cell(r.recipe.label)} | " + " | ".join(["failed"] + [""] * 10) + " |")
            continue
        silence = SPEECH_NOTE if s["silence"] is None else s["silence"]
        out.append(f"| {_cell(r.recipe.label)} | {s['lines']} | {s['merged']} | {s['missed']} | "
                   f"{_num(s['start_median'])} | {_num(s['start_p95'])} | {_num(s['end_median'])} | "
                   f"{_num(s['end_p95'])} | {_num(s['score'], 3)} | {s['loops']} | {s['stock']} | {silence} |")
    scorers = sorted({s["scorer"] for s in stats if s and s["scorer"]})
    if scorers:
        out += ["", f"Scored with: {', '.join(scorers)}."]
    if sum(len(r["text"]) for r in plan.reference) > benchmark_scoring_service.MAX_TEXT_CHARS:
        out.append(f"Only the first {benchmark_scoring_service.MAX_TEXT_CHARS} reference characters are scored.")

    ok = [r for r in results if not r.error]
    out += ["", "## Lines that differ between recipes", ""]
    if len(ok) < 2:
        out.append("(fewer than two recipes finished, nothing to compare)")
    else:
        rows = diff_rows(plan.reference, results)
        out += ["| time s | reference | " + " | ".join(_cell(r.recipe.label) for r in ok) + " |",
                "|---|---|" + "---|" * len(ok)]
        out += [f"| {t:.1f} | {_cell(ref)} | " + " | ".join(_cell(c) for c in cells) + " |"
                for t, ref, cells in rows[:MAX_DIFF_ROWS]] or ["| (identical) |" + " |" * (len(ok) + 1)]
        if len(rows) > MAX_DIFF_ROWS:
            out.append(f"\n{len(rows) - MAX_DIFF_ROWS} more differing lines not shown.")
        out.append(f"\n{len(rows)} lines differ. Two runs of the same recipe also differ a little; to see "
                   "that noise, pass the same --config twice.")
    return "\n".join(out) + "\n"


def default_report_path() -> str:
    # The data folder, as self_test does: the title's own folder is backed up and exported with it.
    return os.path.join(portable.data_dir(), REPORT_NAME)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_plan(plan: Plan):
    window = plan.end - plan.start
    print(f"Title {plan.title_id} ({plan.language}), window {plan.start:.0f}-{plan.end:.0f} s, "
          f"{'GPU' if plan.use_gpu else 'CPU'}; reference: "
          f"{plan.reference_name or 'the current lines of the title'} ({len(plan.reference)} lines)")
    total = 0.0
    for n, r in enumerate(plan.recipes):
        seconds = recipes_mod.estimate_seconds(r, window, plan.use_gpu)
        total += seconds
        vram = recipes_mod.peak_vram_estimate_mb(r, plan.use_gpu)
        cached = ""
        if r.backend in ("whisper", "qwen3_asr") and r.model:
            cached = ("; downloaded" if whisper_models.is_whisper_model_cached(r.model)
                      else "; not downloaded, would download")
        print(f"{'baseline' if n == 0 else f'recipe {n + 1}':<10}{r.label}\n"
              f"          loads {recipes_mod.describe_loads(r)}{cached}\n"
              f"          about {vram and int(vram) or '?'} MB {'VRAM' if plan.use_gpu else 'RAM'}, "
              f"roughly {seconds / 60:.1f} min")
    print(f"Rough total: {total / 60:.1f} min (model loads and speed are guesses unless this PC has "
          "measured runs).")


def run(args) -> int:
    out = args.out or (default_report_path() if args.yes else None)
    # Before any model loads: a refused file must not cost a run.
    if out and self_test._refuse_to_overwrite(out, REPORT_TITLE):
        raise Refusal("Refusing to write the report: that file exists and is not a previous ASR bench report.")
    plan = build_plan(args)
    print_plan(plan)
    if not args.yes:
        print("\nDry run only: nothing was loaded and nothing was written. Add --yes to run it for real.")
        return 0

    results = []
    with gpu_guard(plan.use_gpu, f"ASR bench (title #{plan.title_id})") as holder, \
            tempfile.TemporaryDirectory(prefix="baihe_asr_bench_") as tmp:
        wav = os.path.join(tmp, "window.wav")
        audio_s = cut_window(plan, wav)
        spans = speech_spans(wav, plan.start)
        try:
            for n, recipe in enumerate(plan.recipes):
                print(f"[{n + 1}/{len(plan.recipes)}] {recipe.label} ...", flush=True)
                results.append(run_recipe(plan, recipe, wav, holder))
                done = results[-1]
                print("    " + (f"failed: {done.error}" if done.error else f"{done.wall_s:.1f} s, "
                                f"{len(done.lines)} lines"), flush=True)
        finally:
            whisper_models.release_gpu_models()
    report = format_report(plan, results, audio_s, spans, plan.use_gpu)
    try:
        # The first check was minutes ago; the file may have been replaced since.
        if self_test._refuse_to_overwrite(out, REPORT_TITLE):
            raise OSError("report path changed")
        Path(out).write_text(report, encoding="utf-8")
        saved = f"Report written to {Path(out).name}"
    except OSError:
        saved = "Could not write the report file."
    print(saved)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.asr_bench", description=__doc__.splitlines()[0],
        epilog=recipes_mod.GRAMMAR, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("title_id", type=int, help="the title to measure")
    parser.add_argument("--from", dest="from_s", type=float, metavar="SECONDS",
                        help="window start in seconds (default 0)")
    parser.add_argument("--to", dest="to_s", type=float, metavar="SECONDS",
                        help=f"window end in seconds (default: 300 s after --from; at most {int(MAX_WINDOW_S)} s long)")
    parser.add_argument("--reference", metavar="FILE.srt",
                        help="hand-corrected subtitles to score against (default: the title's current lines)")
    parser.add_argument("--config", action="append", metavar="RECIPE",
                        help=f"repeatable, up to {MAX_RECIPES}; the first is the baseline (grammar below)")
    parser.add_argument("--yes", action="store_true", help="run the recipes (default: dry run)")
    parser.add_argument("--out", help=f"report file (default: {REPORT_NAME} in the data folder)")
    args = parser.parse_args(argv)
    self_test._console_safe()
    if not os.path.isfile(db.DB_PATH):
        print("No library found here; nothing to measure.")
        return 2
    try:
        return run(args)
    except Refusal as refusal:
        print(f"Refused: {refusal}")
        return 2
    except KeyboardInterrupt:
        print("Interrupted; nothing was written.")
        return 130
    except ServiceError as exc:
        print("Failed: " + _clean(exc))
        return 1
    except Exception as exc:
        print(f"Failed ({type(exc).__name__}): " + _clean(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
