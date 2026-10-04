"""
scripts/smoke_pack.py -- does the transcription pipeline still behave after an upgrade?

Run this on your own PC (GPU and real models), once BEFORE upgrading
dependencies or models and again AFTER. CI cannot run it: it needs your GPU,
your models and a clip of your own content, which never leaves your PC.

    python scripts/smoke_pack.py init --audio clip.wav --language zh
    python scripts/smoke_pack.py run
    python scripts/smoke_pack.py run --update-baseline
    python scripts/smoke_pack.py versions

init  runs the current pipeline on the clip and writes a DRAFT baseline,
      smoke_pack/<name>/expected.json, plus profile.json (the settings used and
      the tolerances). Review expected.json, then set "approved" to true.
run   re-runs the same clip with the same profile, writes last_run.json and
      compares it to expected.json. --update-baseline then accepts this run as
      the new baseline (the old one is kept as expected.previous.json).

Exit code: 0 PASS, 1 FAIL, 2 ERROR (setup, clip or baseline missing), 3 WARN only.

Reports hold the clip's file name only, no folders and no keys. The pack folder
smoke_pack/ is gitignored. The library and database are never touched: the
pipeline runs on a temporary copy of the clip. Standard library at import time;
the app's own modules are imported only by the commands that need them.
"""

import argparse
import contextlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACK_ROOT = REPO_ROOT / "smoke_pack"

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
EXIT_PASS, EXIT_FAIL, EXIT_ERROR, EXIT_WARN = 0, 1, 2, 3

KEY_PACKAGES = ("faster-whisper", "ctranslate2", "torch", "torchaudio", "transformers",
                "qwen-asr", "pyannote.audio", "audio-separator", "demucs", "onnxruntime",
                "numpy", "huggingface_hub", "yt-dlp", "opencv-python", "pillow")

DEFAULT_TOLERANCES = {
    "max_cer": 0.08,               # character error rate against the baseline text
    "line_count_pct": 15,          # line count may differ by this many percent
    "max_line_seconds": 8.0,
    "max_line_chars": 40,
    "long_line_tolerance": 2,      # lines over a limit allowed beyond the baseline's own
    "max_overlap_seconds": 0.2,
    "speaker_count_diff": 0,       # 1 to accept a speaker more or fewer
    "max_slowdown_pct": 40,        # a stage may take this much longer than the baseline
    "min_stage_seconds": 5.0,      # faster stages in the baseline are too noisy to compare
}

DEFAULT_PROFILE = {
    "asr_backend": "whisper",      # whisper or qwen3
    "whisper_size": None,          # None: the app's default size
    "beam_size": 5,
    "min_silence_ms": 300,
    "vad_threshold": 0.5,
    "separate_vocals": False,
    "separation_backend": "auto",
    "diarization": False,
    "forced_align": False,
    "use_gpu": True,
    "chinese_script": "simplified",
}

STAGES = ("separate_vocals", "model_load", "transcribe", "qwen3", "realign", "forced_align",
          "diarize")
# model_load depends on the disk cache, not on the upgrade.
UNTIMED_CHECK_STAGES = ("model_load",)


class SmokeError(Exception):
    """A setup problem (exit 2), as opposed to a failed check."""


# ---------------------------------------------------------------------------
# Redaction: reports are meant to be shareable
# ---------------------------------------------------------------------------

_ABS_PATH = re.compile(r"(?<![\w.])(?:[A-Za-z]:[\\/]|\\\\|/)(?:[^\s\"'<>|:*?]+[\\/])+([^\s\"'<>|:*?\\/]*)")


def scrub(text) -> str:
    """Error text for a report: secrets redacted, every absolute path cut down
    to its last component."""
    text = str(text or "")
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from translate_engines import redact_secrets
        text = redact_secrets(text)
    except Exception:
        text = re.sub(r"(?i)\b(?:sk|hf|ghp|gho|pk|rk)[-_][A-Za-z0-9_-]{16,}", "[REDACTED]", text)
    return _ABS_PATH.sub(lambda m: m.group(1) or "<folder>", text)


# ---------------------------------------------------------------------------
# Versions and environment
# ---------------------------------------------------------------------------

def collect_versions() -> dict:
    versions = {"python": platform.python_version()}
    for pkg in KEY_PACKAGES:
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            versions[pkg] = None
    cuda = None
    if versions.get("torch"):
        with contextlib.suppress(Exception):
            import torch
            cuda = torch.version.cuda if torch.cuda.is_available() else None
    versions["cuda"] = cuda
    return versions


def version_changes(old: dict, new: dict) -> list:
    """[(name, old, new)] for every entry that differs; None means not installed."""
    return [(name, (old or {}).get(name), (new or {}).get(name))
            for name in sorted(set(old or {}) | set(new or {}))
            if (old or {}).get(name) != (new or {}).get(name)]


# ---------------------------------------------------------------------------
# Running the pipeline on one clip
# ---------------------------------------------------------------------------

class _Ticker:
    def start(self):
        return self

    def stop(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# Reporter messages the pipeline emits, by the stage they announce.
_STAGE_PREFIXES = (
    ("separate_vocals", ("Separating vocals", "Loading the vocal separation")),
    ("model_load", ("Loading Whisper model",)),
    ("transcribe", ("Transcribing",)),
    ("realign", ("Splitting long merged lines",)),
    ("qwen3", ("Re-transcribing with Qwen3-ASR",)),
)


class StageRecorder:
    """A no-op reporter for services.transcribe_service._transcribe_pipeline
    that notes when each stage's first message arrives, so stage durations come
    from the real pipeline without changing it."""

    job_id = None

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.events = []        # (time, stage)
        self.separation_device = ""

    def _note(self, message):
        message = message or ""
        for stage, prefixes in _STAGE_PREFIXES:
            if message.startswith(prefixes):
                if not self.events or self.events[-1][1] != stage:
                    self.events.append((self._clock(), stage))
                break
        if "Separating vocals on " in message:
            self.separation_device = "cpu" if "CPU" in message else "gpu"

    def progress(self, frac, message=""):
        self._note(message)

    def stage(self, message, frac=0.0):
        self._note(message)
        return _Ticker()

    def cancelled(self):
        return False

    def raise_if_cancelled(self):
        pass

    def durations(self, end_time) -> dict:
        out = {}
        for i, (t, stage) in enumerate(self.events):
            nxt = self.events[i + 1][0] if i + 1 < len(self.events) else end_time
            out[stage] = round(out.get(stage, 0.0) + max(0.0, nxt - t), 2)
        return out


def _hf_token():
    for name in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    with contextlib.suppress(Exception):
        from services import settings_service
        return settings_service.resolve_key("hf_token")
    return None


def _whisper_model_path():
    """Settings > Offline Whisper model folder, as the app's own jobs use it, so
    a model already on disk is loaded instead of downloaded. None when unset."""
    with contextlib.suppress(Exception):
        from services import settings_service
        return settings_service.get_whisper_model_path()
    return None


def _device_class(text) -> str:
    low = (text or "").lower()
    if not low:
        return ""
    if "unavailable" in low or "cpu" in low:
        return "cpu"
    return "gpu" if ("gpu" in low or "cuda" in low) else low


def run_clip(profile: dict, audio_path, expected_texts=None) -> dict:
    """Runs the app's own pipeline on one clip and returns
    {"lines": [{"start", "end", "text", "speaker"?}], "stage_seconds": {...},
     "devices": {...}, "speaker_count": int or None, "notes": [...]}.
    Works on a temporary copy of the clip and touches no database.
    expected_texts: with forced_align on, the line texts to align (the
    baseline's, so a run tests the aligner alone); None aligns the run's own."""
    sys.path.insert(0, str(REPO_ROOT))
    import core
    from services import transcribe_service as ts

    use_gpu = bool(profile.get("use_gpu", True))
    qwen = profile.get("asr_backend") == "qwen3"
    size = profile.get("whisper_size") or core.DEFAULT_WHISPER_SIZE
    language = profile["language"]
    notes, devices, stage_seconds = [], {}, {}
    token = _hf_token() if profile.get("diarization") else None
    if profile.get("diarization") and not token:
        raise SmokeError("Speaker detection is on in the profile but no Hugging Face token was "
                         "found (set HF_TOKEN or save it in the app's Settings).")

    with tempfile.TemporaryDirectory(prefix="smoke_pack_") as scratch:
        clip = os.path.join(scratch, "clip" + (os.path.splitext(str(audio_path))[1] or ".wav"))
        shutil.copyfile(audio_path, clip)
        work = os.path.join(scratch, "work")
        os.makedirs(work)
        rec = StageRecorder()
        t0 = time.monotonic()
        outcome = ts._transcribe_pipeline(
            rec, clip, "whisper", "", language, profile.get("chinese_script", "simplified"),
            size, int(profile.get("beam_size", 5)), int(profile.get("min_silence_ms", 300)),
            float(profile.get("vad_threshold", 0.5)), bool(profile.get("separate_vocals")),
            profile.get("separation_backend", "auto"), False, False, False, None, "", use_gpu,
            "qwen3_asr" if qwen else "whisper", "whisper_diff",
            local_model_path=_whisper_model_path(), qwen_batch_size=1, vocals_work_dir=work)
        t_pipeline_end = time.monotonic()
        if "failed_reason" in outcome:
            detail = scrub(outcome.get("detail") or "")
            raise RuntimeError(f"The pipeline stopped: {outcome['failed_reason']}"
                               + (f" ({detail})" if detail else ""))
        stage_seconds = rec.durations(t_pipeline_end)
        stage_seconds["total_pipeline"] = round(t_pipeline_end - t0, 2)
        device_msg = outcome.get("device_msg") or ""
        if device_msg:
            devices["transcribe"] = device_msg
        if rec.separation_device:
            devices["separate_vocals"] = rec.separation_device
        for key in ("word_align_error", "coverage_warning"):
            if outcome.get(key):
                notes.append(f"{key}: {scrub(outcome[key])}")
        lines = outcome["lines"]

        if profile.get("forced_align"):
            import forced_align
            texts = list(expected_texts) if expected_texts else [ln.zh for ln in lines]
            user_lines = core.split_user_transcript("\n".join(texts))
            t1 = time.monotonic()
            lines = forced_align.align_with_qwen3(
                clip, user_lines, outcome["segments"], language=language, use_gpu=use_gpu)
            stage_seconds["forced_align"] = round(time.monotonic() - t1, 2)
            devices["forced_align"] = "gpu" if use_gpu and _cuda_present() else "cpu"

        speaker_count = None
        if profile.get("diarization"):
            import diarize
            info = {}
            t1 = time.monotonic()
            turns = diarize.diarize(clip, token, use_gpu=use_gpu, run_info=info)
            stage_seconds["diarize"] = round(time.monotonic() - t1, 2)
            devices["diarize"] = "gpu" if info.get("device") == "cuda" else "cpu"
            diarize.merge_speakers(lines, turns)
            speaker_count = len({t["speaker"] for t in turns})
        with contextlib.suppress(Exception):
            core.release_gpu_models()

    rows = []
    for ln in lines:
        row = {"start": round(float(ln.start), 2), "end": round(float(ln.end), 2), "text": ln.zh}
        if getattr(ln, "speaker", None):
            row["speaker"] = ln.speaker
        rows.append(row)
    return {"lines": rows, "stage_seconds": stage_seconds, "devices": devices,
            "speaker_count": speaker_count, "notes": notes}


def _cuda_present() -> bool:
    with contextlib.suppress(Exception):
        import torch
        return bool(torch.cuda.is_available())
    return False


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _tol(profile, key):
    return (profile.get("tolerances") or {}).get(key, DEFAULT_TOLERANCES[key])


def _cer(actual: str, reference: str) -> float:
    sys.path.insert(0, str(REPO_ROOT))
    from services.benchmark_lab_service import error_rate
    return error_rate(actual, reference, "char")


def _chars(text) -> int:
    return len("".join((text or "").split()))


def _long_lines(lines, max_seconds, max_chars) -> int:
    return sum(1 for ln in lines
               if ln["end"] - ln["start"] > max_seconds or _chars(ln["text"]) > max_chars)


def _check(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def compare(expected: dict, actual: dict, profile: dict, expected_versions=None,
            actual_versions=None) -> dict:
    """Checks `actual` (a run_clip result) against `expected`; returns
    {"checks": [...], "status": PASS|WARN|FAIL, "version_changes": [...]}."""
    checks = []
    exp_lines, act_lines = expected.get("lines", []), actual.get("lines", [])

    if not expected.get("approved"):
        checks.append(_check("Baseline approved", WARN,
                             'expected.json still says "approved": false; review it and set it to true'))

    # Text
    cer = _cer(" ".join(ln["text"] for ln in act_lines), " ".join(ln["text"] for ln in exp_lines))
    limit = _tol(profile, "max_cer")
    status = FAIL if cer > limit else WARN if cer > 0.75 * limit else PASS
    checks.append(_check("Text matches baseline", status,
                         f"character error rate {cer:.1%}, limit {limit:.0%}"))

    # Line count
    n_exp, n_act = len(exp_lines), len(act_lines)
    pct = _tol(profile, "line_count_pct")
    diff = abs(n_act - n_exp) / n_exp * 100 if n_exp else (0 if not n_act else 100)
    checks.append(_check("Line count", PASS if diff <= pct else FAIL,
                         f"{n_act} lines, baseline {n_exp} ({diff:.0f}% apart, limit {pct}%)"))

    # Line length
    max_s, max_c = _tol(profile, "max_line_seconds"), _tol(profile, "max_line_chars")
    base_long = _long_lines(exp_lines, max_s, max_c)
    long_now = _long_lines(act_lines, max_s, max_c)
    allowed = base_long + _tol(profile, "long_line_tolerance")
    checks.append(_check("No over-long lines", PASS if long_now <= allowed else FAIL,
                         f"{long_now} lines over {max_s:g} s or {max_c} characters "
                         f"(baseline {base_long}, allowed {allowed})"))

    # Timing sanity
    bad_len = sum(1 for ln in act_lines if ln["end"] <= ln["start"])
    backwards = sum(1 for a, b in zip(act_lines, act_lines[1:]) if b["start"] < a["start"])
    problems = []
    if bad_len:
        problems.append(f"{bad_len} zero-length or negative")
    if backwards:
        problems.append(f"{backwards} out of order")
    checks.append(_check("Times in order, no empty lines", FAIL if problems else PASS,
                         ", ".join(problems) or "all lines have a positive length, in order"))
    max_ov = _tol(profile, "max_overlap_seconds")
    overlaps = sum(1 for a, b in zip(act_lines, act_lines[1:]) if a["end"] - b["start"] > max_ov)
    checks.append(_check("No overlaps", FAIL if overlaps else PASS,
                         f"{overlaps} overlaps over {max_ov:g} s"))

    # Speakers
    if profile.get("diarization"):
        want, got = expected.get("speaker_count"), actual.get("speaker_count")
        slack = _tol(profile, "speaker_count_diff")
        ok = want is not None and got is not None and abs(got - want) <= slack
        checks.append(_check("Speaker count", PASS if ok else FAIL,
                             f"{got} speakers, baseline {want}"
                             + (f" (within {slack} allowed)" if slack else "")))

    # Devices
    for stage, old in sorted((expected.get("devices") or {}).items()):
        new = (actual.get("devices") or {}).get(stage, "")
        name = f"Device: {stage}"
        if new == old:
            checks.append(_check(name, PASS, new))
        elif _device_class(old) == "gpu" and _device_class(new) != "gpu":
            checks.append(_check(name, FAIL, f"baseline used the GPU ({old}) but this run used "
                                             f"{new or 'no recorded device'}: GPU downgraded to CPU"))
        elif _device_class(old) != _device_class(new):
            checks.append(_check(name, FAIL, f"baseline {old}, this run {new or 'unknown'}"))
        else:
            checks.append(_check(name, WARN, f"baseline {old}, this run {new}"))

    # Speed
    slow_pct, min_s = _tol(profile, "max_slowdown_pct"), _tol(profile, "min_stage_seconds")
    new_times = actual.get("stage_seconds") or {}
    for stage, base in sorted((expected.get("stage_seconds") or {}).items()):
        if stage in UNTIMED_CHECK_STAGES or stage == "total_pipeline":
            continue
        name = f"Speed: {stage}"
        if base < min_s:
            continue
        now = new_times.get(stage)
        if now is None:
            checks.append(_check(name, WARN, f"baseline {base:g} s, this run has no timing"))
            continue
        over = (now - base) / base * 100
        checks.append(_check(name, WARN if over > slow_pct else PASS,
                             f"{now:g} s, baseline {base:g} s ({over:+.0f}%, limit +{slow_pct}%)"))

    order = {PASS: 0, WARN: 1, FAIL: 2}
    status = max((c["status"] for c in checks), key=order.get, default=PASS)
    return {"checks": checks, "status": status,
            "version_changes": version_changes(expected_versions, actual_versions)}


def exit_code_for(status: str) -> int:
    return {PASS: EXIT_PASS, WARN: EXIT_WARN, FAIL: EXIT_FAIL}[status]


def format_report(result: dict, name: str = "") -> str:
    checks = result["checks"]
    width = max([len(c["name"]) for c in checks] + [5])
    out = [f"Smoke pack {name}".strip(), ""]
    for c in checks:
        out.append(f"  {c['status']:<5} {c['name']:<{width}}  {c['detail']}")
    out.append("")
    changes = result.get("version_changes") or []
    if changes:
        out.append("Packages that changed since the baseline:")
        for pkg, old, new in changes:
            out.append(f"  {pkg}: {old or 'not installed'} -> {new or 'not installed'}")
        if result["status"] == FAIL:
            out.append("A failure above probably comes from one of these.")
    else:
        out.append("No package versions changed since the baseline.")
    out.append("")
    out.append(f"Result: {result['status']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Pack files and commands
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SmokeError(f"{path.name} not found in {path.parent.name}/. "
                         "Run `python scripts/smoke_pack.py init` first.")
    except ValueError as exc:
        raise SmokeError(f"{path.name} is not valid JSON ({exc}).")


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _pack_dir(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or "") or name.startswith("."):
        raise SmokeError("The name may only use letters, digits, dots, dashes and underscores.")
    return root / name


def _result_doc(res: dict, profile: dict, versions: dict) -> dict:
    return {"clip": profile["audio_file"], "line_count": len(res["lines"]),
            "lines": res["lines"], "speaker_count": res.get("speaker_count"),
            "stage_seconds": res["stage_seconds"], "devices": res["devices"],
            "versions": versions, "notes": res.get("notes", [])}


def cmd_init(args, run=None, root: Path = PACK_ROOT, out=print) -> int:
    run = run or run_clip
    audio = Path(args.audio)
    if not audio.is_file():
        raise SmokeError(f"The clip {audio.name} was not found.")
    name = args.profile or audio.stem
    pack = _pack_dir(root, name)
    if (pack / "expected.json").exists() and not args.force:
        raise SmokeError(f"smoke_pack/{name} already has a baseline. Pick another --profile "
                         "name, or add --force to replace it.")
    pack.mkdir(parents=True, exist_ok=True)
    profile = {**DEFAULT_PROFILE, "name": name, "language": args.language,
               "audio_file": audio.name, "asr_backend": args.asr,
               "whisper_size": args.whisper_size, "separate_vocals": args.separate_vocals,
               "diarization": args.diarization, "forced_align": args.forced_align,
               "use_gpu": not args.cpu, "tolerances": dict(DEFAULT_TOLERANCES)}
    shutil.copyfile(audio, pack / audio.name)   # stays inside the gitignored pack folder
    out(f"Running the pipeline on {audio.name} (this can take several minutes)...")
    try:
        res = run(profile, str(pack / audio.name))
    except SmokeError:
        raise
    except Exception as exc:
        raise SmokeError(f"The pipeline failed on the clip: {type(exc).__name__}: {scrub(exc)}")
    doc = _result_doc(res, profile, collect_versions())
    doc["approved"] = False
    _write_json(pack / "profile.json", profile)
    _write_json(pack / "expected.json", doc)
    out(f"\nDraft baseline written to smoke_pack/{name}/ ({doc['line_count']} lines).")
    out("Next:")
    out(f"  1. Open smoke_pack/{name}/expected.json and read the line texts and times. "
        "Fix anything clearly wrong by hand.")
    out('  2. Change "approved": false to "approved": true and save.')
    out(f"  3. Upgrade, then run: python scripts/smoke_pack.py run --name {name}")
    out("The clip and these files stay on this PC (smoke_pack/ is gitignored).")
    return EXIT_PASS


def cmd_run(args, run=None, root: Path = PACK_ROOT, out=print) -> int:
    run = run or run_clip
    name = args.name
    if not name:
        packs = sorted(p.name for p in root.iterdir() if (p / "expected.json").exists()) \
            if root.is_dir() else []
        if len(packs) != 1:
            raise SmokeError("Pass --name NAME; packs found: " + (", ".join(packs) or "none"))
        name = packs[0]
    pack = _pack_dir(root, name)
    profile = _read_json(pack / "profile.json")
    expected = _read_json(pack / "expected.json")
    clip = pack / profile["audio_file"]
    if not clip.is_file():
        raise SmokeError(f"The clip {profile['audio_file']} is missing from smoke_pack/{name}/.")
    versions = collect_versions()
    out(f"Running the pipeline on {clip.name} (this can take several minutes)...")
    texts = [ln["text"] for ln in expected.get("lines", [])]
    try:
        res = run(profile, str(clip), texts if profile.get("forced_align") else None)
    except SmokeError:
        raise
    except Exception as exc:
        reason = f"{type(exc).__name__}: {scrub(exc)}"
        result = {"checks": [_check("Pipeline completes", FAIL, reason)], "status": FAIL,
                  "version_changes": version_changes(expected.get("versions"), versions)}
        _write_json(pack / "last_run.json", {"clip": clip.name, "versions": versions,
                                             "result": result})
        out(format_report(result, name))
        return EXIT_FAIL
    result = compare(expected, res, profile, expected.get("versions"), versions)
    _write_json(pack / "last_run.json", {**_result_doc(res, profile, versions), "result": result})
    out(format_report(result, name))
    if args.update_baseline:
        shutil.copyfile(pack / "expected.json", pack / "expected.previous.json")
        doc = _result_doc(res, profile, versions)
        doc["approved"] = bool(expected.get("approved"))
        _write_json(pack / "expected.json", doc)
        out("Baseline replaced by this run (the old one is expected.previous.json).")
    return exit_code_for(result["status"])


def cmd_versions(args, out=print) -> int:
    out(json.dumps(collect_versions(), indent=2))
    return EXIT_PASS


def build_parser():
    p = argparse.ArgumentParser(
        description="Check that the transcription pipeline still behaves after an upgrade. "
                    "Exit code: 0 PASS, 1 FAIL, 2 ERROR, 3 WARN only.")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("init", help="run the current pipeline on a clip and write a draft baseline")
    i.add_argument("--audio", required=True, help="a 2-5 minute clip of your own content")
    i.add_argument("--language", required=True, choices=("zh", "ja", "ko"))
    i.add_argument("--profile", help="pack name (default: the clip's file name)")
    i.add_argument("--asr", choices=("whisper", "qwen3"), default="whisper")
    i.add_argument("--whisper-size", default=None, help="default: the app's default size")
    i.add_argument("--separate-vocals", action="store_true")
    i.add_argument("--diarization", action="store_true", help="needs a Hugging Face token")
    i.add_argument("--forced-align", action="store_true", help="also test Qwen3 forced alignment")
    i.add_argument("--cpu", action="store_true", help="run on the CPU instead of the GPU")
    i.add_argument("--force", action="store_true", help="replace an existing baseline")
    r = sub.add_parser("run", help="re-run the clip and compare with the baseline")
    r.add_argument("--name")
    r.add_argument("--update-baseline", action="store_true",
                   help="accept this run as the new baseline")
    sub.add_parser("versions", help="print the key package versions as JSON")
    return p


def main(argv=None, run=None, root: Path = PACK_ROOT) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "versions":
            return cmd_versions(args)
        return (cmd_init if args.command == "init" else cmd_run)(args, run=run, root=root)
    except SmokeError as exc:
        print(f"error: {scrub(exc)}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
