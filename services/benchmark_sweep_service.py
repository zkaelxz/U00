"""
services/benchmark_sweep_service.py -- the Benchmark Lab's settings sweep. UI-free.

A sweep tunes transcription for one golden set in one language, following "Method" in
docs/specs/accuracy-tuning-plan.md:

- The set's clips are split by title, about 70% for tuning and 30% held out, so no title is in
  both halves. The hold-out only confirms a winner; it never picks one.
- The default setup is run twice on the tuning clips; the gap between the two is the noise.
- Coordinate descent over the plan's range table: the model first, then one setting at a time
  from the current best, keeping a value only when it beats the current best by more than the
  noise. After the model step every pass uses one model, so each model loads once.
- A winner other than the default is run beside the default on the hold-out. It is recommended
  only when it beats the default there by the margin (2 CER points and twice the noise) with no
  more failed clips. Nothing is applied: approve() saves it as a setting profile and records the
  decision in model_decisions, as model_reeval_service does for models.

Every pass is an ordinary Lab run (benchmark_sessions), made by benchmark_lab_service's own run
loop under its job id, so the Lab's refusals while a run is going, its cancel and its GPU queue
cover a sweep too. Stopping and resuming need no state of their own: a pass is skipped when a
finished run of the same setup already scored exactly the same clips at the same reference
versions under the same scoring rules, so starting the sweep again redoes only what is missing.
The sweep's own record (set, language, status, recommendation, decision) is one app setting.

Transcription runs on this PC's own models, so a sweep costs nothing and the monthly cap never
applies to it. Models are never downloaded: a model that is not on the PC is left out and says so.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import threading

import background_jobs
import db
import media_inspect
import whisper_models
from core import SOURCE_LANGUAGES
from jobs import job_launch
from services import benchmark_lab_service as lab
from services import (model_readiness_service, profile_service, series_defaults_service, settings_service,
                      transcribe_options_service, transcribe_speed_service, translate_preset_service)
from services.benchmark_case_service import backend_problem, ignored_options, redact
from services.benchmark_runs_service import session_out, setup_key
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     UnsupportedOperationError)

STAGE = "transcription"
JOB_ID = lab.JOB_ID
_RECORD_KEY = "benchmark_sweep"
HOLDOUT_SHARE = 0.3
# The plan's adoption rule for transcription: 2 CER points and twice the measured noise.
MIN_SCORE_MARGIN = 0.02
NOISE_FACTOR = 2
# Below these the plan calls a result too small to decide on; it is still shown.
MIN_TAG_CASES = 15
MIN_SET_CASES = 50
DECISION_CAPABILITY = "transcription"

_MODELS = (("whisper", "large-v3"), ("whisper", "large-v3-turbo"))
_EXTRA_MODELS = {"ja": (("whisper", "kotoba-v2"), ("anime_whisper", None))}
_MODEL_LABELS = {"anime_whisper": "anime-whisper"}

# (option, label, values): the transcription rows of the plan's "Starting numbers and sweep ranges".
KNOBS = (
    ("beam_size", "Beam size", (1, 3, 5)),
    ("min_silence_ms", "Minimum silence (ms)", (200, 300, 500, 800)),
    ("vad_threshold", "Voice detection threshold", (0.35, 0.45, 0.5, 0.6)),
    ("hallucination_silence_sec", "Skip silences longer than (s)", (0, 1, 2)),
    ("difficult_audio", "Difficult audio", (False, True)),
    ("whisper_repeat_guard", "Repeat guard", (False, True)),
    ("separate_vocals_first", "Separate vocals first", (False, True)),
)
_KNOB_LABELS = {k: label for k, label, _ in KNOBS}
NOT_SWEPT = (
    "Initial prompt: a title builds it from its glossary, and the Lab has no glossary to build it from.",
    "Qwen context pad: not a Lab option.",
    "Line timing settings (speech pad, timing refinement, split limits): compare them in the Arena, "
    "which judges timing against a repeat of the default.",
    "Translation settings: not part of this sweep.",
)

# (GPU, CPU) seconds of work per second of audio when this PC has not measured the model
# (tools/asr_bench_recipes.py's guesses), the extra for vocal separation, and one model load.
_SPEED_GUESS = {"whisper": (0.12, 1.0), "anime_whisper": (0.15, 1.0)}
_SEPARATION_GUESS = (0.3, 1.5)
_LOAD_SECONDS = 20.0
_UNKNOWN_CLIP_SECONDS = 10.0

_lock = threading.Lock()
_durations = {}


class _Stopped(Exception):
    """A pass was cancelled: the sweep stops and can be resumed."""


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# The sweep's own record
# ---------------------------------------------------------------------------

def _record() -> dict:
    try:
        value = json.loads(db.get_app_setting(_RECORD_KEY) or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_record(record: dict):
    db.set_app_setting(_RECORD_KEY, json.dumps(record))


def _update_record(**fields):
    with _lock:
        record = _record()
        record.update(fields)
        _save_record(record)


def get_sweep() -> dict:
    """The latest sweep, with "interrupted" for one the app stopped during."""
    record = _record()
    if not record:
        return {"sweep": None}
    status = (background_jobs.get_status(JOB_ID) or {}).get("status")
    if record.get("status") in ("queued", "running") and status not in ("queued", "running"):
        record = dict(record, status="interrupted",
                      note="The app stopped during the sweep. Start it again to resume.")
    rec = record.get("recommendation") or {}
    if record.get("status") == "done" and rec.get("state") == "adopt" and not record.get("decision"):
        # Read now, not at the sweep's end: the profile takes the Settings default engine as it is when approved.
        record = dict(record, applies=_applies(record["language"], rec["winner"]))
    return {"sweep": record}


# ---------------------------------------------------------------------------
# What a sweep runs
# ---------------------------------------------------------------------------

def _check_params(set_name, language):
    if not isinstance(set_name, str) or not set_name.strip() or len(set_name) > lab.MAX_SET_NAME_CHARS:
        raise InvalidInputError("Pick a golden set.")
    if language not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"language must be one of {', '.join(SOURCE_LANGUAGES)}.")
    return set_name.strip()


def _sweep_cases(set_name: str, language: str) -> list:
    """The set's scorable audio clips in this language: one language per sweep, because results
    are never averaged across Chinese, Japanese and Korean."""
    return [c for c in lab._select_cases(STAGE, set_name=set_name, scorable_only=True)
            if (c.get("source_language") or "zh") == language
            and c.get("reference_text") and c.get("input_filename")]


def _title_of(case: dict) -> str:
    return (case.get("title") or "").strip()


def split_by_title(cases: list) -> tuple:
    """(tuning, holdout): whole titles in each half, the hold-out as close to 30% of the clips as
    whole titles allow. Titles are taken in a fixed order (a hash of the name), so the same set
    always splits the same way and a resumed sweep keeps its halves."""
    groups = {}
    for c in cases:
        groups.setdefault(_title_of(c), []).append(c)
    if len(groups) < 2:
        raise UnsupportedOperationError(
            "A sweep needs clips from at least 2 titles, so one can be held out to confirm the winner. "
            "Give the clips their titles, or add clips from another title.")
    target = HOLDOUT_SHARE * len(cases)
    held, count = [], 0
    for title in sorted(groups, key=lambda t: hashlib.sha1(t.encode("utf-8")).hexdigest()):
        size = len(groups[title])
        if len(held) < len(groups) - 1 and (not held or abs(count + size - target) < abs(count - target)):
            held.append(title)
            count += size
    tuning = [c for c in cases if _title_of(c) not in held]
    holdout = [c for c in cases if _title_of(c) in held]
    return tuning, holdout


def _model_label(engine, model) -> str:
    return _MODEL_LABELS.get(engine) or model


def _model_choices(language: str, refine: bool) -> list:
    """Every model the range table names for this language, and why one is left out."""
    out = []
    for engine, model in _MODELS + _EXTRA_MODELS.get(language, ()):
        if engine == "whisper":
            reason = whisper_models.whisper_model_language_problem(model, language) or (
                None if whisper_models.is_whisper_model_cached(model) else
                f"{model} isn't downloaded on this PC. Run a title with it once; the sweep never downloads a model.")
        else:
            reason = backend_problem(engine, language, refine)
        out.append({"engine": engine, "model": model, "label": _model_label(engine, model),
                    "included": reason is None, "reason": reason})
    return out


def _knob_choices(use_gpu) -> list:
    out = []
    for key, label, values in KNOBS:
        reason = None
        if key == "separate_vocals_first" and not model_readiness_service.separation_ready("auto", use_gpu):
            reason = ("The vocal separation model isn't downloaded yet. Run Separate vocals first once on a "
                      "title; the sweep does not download it.")
        out.append({"key": key, "label": label, "values": list(values), "included": reason is None,
                    "reason": reason})
    return out


class _Plan:
    def __init__(self, set_name, language, use_gpu):
        self.set_name, self.language = set_name, language
        self.use_gpu = settings_service.get("use_gpu") if use_gpu is None else bool(use_gpu)
        cases = _sweep_cases(set_name, language)
        if not cases:
            raise UnsupportedOperationError(
                "This set has no audio clips with a reference in that language.")
        self.tuning, self.holdout = split_by_title(cases)
        self.defaults = transcribe_options_service.lab_option_defaults()
        default_model = transcribe_options_service.default_whisper_size()
        self.models = _model_choices(language, self.defaults["refine_timing"])
        if not any(m["included"] and m["model"] == default_model for m in self.models):
            raise DependencyUnavailableError(
                f"The default model, {default_model}, isn't on this PC yet. Run a title with it once; "
                "the sweep never downloads a model.")
        self.knobs = _knob_choices(self.use_gpu)
        self.default = {"engine": "whisper", "model": default_model, "changes": {}}
        cfg = self.config(self.default)
        context = lab.run_context(STAGE, cfg, cases, self.use_gpu)
        # The scoring rules a reused run must share (benchmark_runs_service._check_same_scoring).
        self.markers = {k: context.get(k) for k in ("text_normaliser", "script_normaliser")}

    def config(self, setup: dict) -> dict:
        return lab.check_config(STAGE, {"engine": setup["engine"], "model": setup["model"],
                                        "options": setup["changes"]})

    def key(self, setup: dict) -> str:
        cfg = self.config(setup)
        return setup_key({"engine": cfg["engine"], "model": cfg["model"],
                          "context_settings": json.dumps({"transcribe_options": cfg["options"],
                                                          "use_gpu": self.use_gpu})})

    def cases(self, half: str) -> list:
        return self.tuning if half == "tuning" else self.holdout


def _describe(setup: dict, defaults: dict) -> str:
    parts = [_model_label(setup["engine"], setup["model"])]
    for key, value in setup["changes"].items():
        if value != defaults.get(key):
            parts.append(f"{_KNOB_LABELS.get(key, key)} {_value_text(value)}")
    return ", ".join(parts)


def _value_text(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return f"{value:g}" if isinstance(value, float) else str(value)


# ---------------------------------------------------------------------------
# Coordinate descent, shared by the estimate and the job
# ---------------------------------------------------------------------------

def _descend(plan: _Plan, measure) -> dict:
    """Walks the sweep. measure(setup, half, repeat, step) returns the finished run's
    benchmark_sessions row, or None for a pass not run yet (the estimate). Unmeasured passes
    never win, so the estimate walks the passes a sweep that keeps the default would run."""
    steps = []

    def score(run):
        return None if run is None else run.get("aggregate_score")

    def record(setup, half, run, step, kept=None):
        steps.append({**step, "half": half, "setup": _describe(setup, plan.defaults),
                      "run_id": run["id"] if run else None, "score": score(run),
                      "errors": run.get("error_count") if run else None, "kept": kept})

    first = measure(plan.default, "tuning", 0, {"step": "noise", "value": None})
    record(plan.default, "tuning", first, {"step": "noise", "value": None})
    second = measure(plan.default, "tuning", 1, {"step": "noise", "value": None})
    record(plan.default, "tuning", second, {"step": "noise", "value": None})
    a, b = score(first), score(second)
    noise = abs(a - b) if None not in (a, b) else None
    current, current_score = plan.default, None if None in (a, b) else (a + b) / 2

    def pick(candidates):
        nonlocal current, current_score
        if not candidates:
            return
        best, best_score = None, None
        for setup, step in candidates:
            run = measure(setup, "tuning", 0, step)
            s = score(run)
            record(setup, "tuning", run, step)
            if s is not None and current_score is not None and s > current_score + (noise or 0.0) \
                    and (best_score is None or s > best_score):
                best, best_score = setup, s
        kept = _describe(best, plan.defaults) if best is not None else None
        for st in steps:
            if st["step"] == candidates[0][1]["step"]:
                st["kept"] = st["setup"] == kept
        if best is not None:
            current, current_score = best, best_score

    pick([({"engine": m["engine"], "model": m["model"], "changes": {}},
           {"step": "model", "value": m["label"]})
          for m in plan.models if m["included"] and m["model"] != plan.default["model"]])
    for knob in plan.knobs:
        if not knob["included"] or knob["key"] in ignored_options(current["engine"]):
            continue
        now = current["changes"].get(knob["key"], plan.defaults[knob["key"]])
        candidates = []
        for value in knob["values"]:
            if value == now:
                continue
            changes = {**current["changes"], knob["key"]: value}
            if value == plan.defaults[knob["key"]]:
                changes.pop(knob["key"])
            candidates.append(({**current, "changes": changes}, {"step": knob["key"], "value": value}))
        pick(candidates)

    holdout = None
    if plan.key(current) != plan.key(plan.default):
        win = measure(current, "holdout", 0, {"step": "holdout", "value": "winner"})
        record(current, "holdout", win, {"step": "holdout", "value": "winner"})
        base = measure(plan.default, "holdout", 0, {"step": "holdout", "value": "default"})
        record(plan.default, "holdout", base, {"step": "holdout", "value": "default"})
        holdout = (base, win)
    return {"noise": noise, "default_score": None if None in (a, b) else (a + b) / 2,
            "winner": current, "winner_score": current_score, "holdout": holdout, "steps": steps}


def _finished_runs(plan: _Plan, setup: dict, half: str, sessions: list) -> list:
    """Finished runs of this setup that scored exactly these clips at their current reference
    versions under today's scoring rules, oldest first: the passes a sweep can skip."""
    key = plan.key(setup)
    want = {c["id"]: c.get("ref_version") or 1 for c in plan.cases(half)}
    out = []
    for s in sorted(sessions, key=lambda r: r["id"]):
        if s.get("status") != "done" or s.get("stage") != STAGE or setup_key(s) != key:
            continue
        ctx = session_out(s)["context_settings"]
        if any(ctx.get(k) != v for k, v in plan.markers.items()):
            continue
        results = db.list_benchmark_results(s["id"])
        if len(results) == len(want) and {r["case_id"]: r.get("ref_version") or 1 for r in results} == want:
            out.append(s)
    return out


def _clip_seconds(case: dict) -> tuple:
    """(length in seconds, whether it was read) of a case's clip, cached per file and change time."""
    path = os.path.join(db.BENCHMARK_DIR, os.path.basename(case.get("input_filename") or ""))
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return _UNKNOWN_CLIP_SECONDS, False
    if (path, stamp) not in _durations:
        _durations[(path, stamp)] = media_inspect.duration_seconds(path)
    seconds = _durations[(path, stamp)]
    return (seconds, True) if seconds else (_UNKNOWN_CLIP_SECONDS, False)


def _pass_seconds(setup: dict, audio_seconds: float, plan: _Plan) -> float:
    gpu = 0 if plan.use_gpu else 1
    factor = _SPEED_GUESS[setup["engine"]][gpu]
    if setup["engine"] == "whisper":
        speed = transcribe_speed_service.measured_transcribe_speed(setup["model"], plan.use_gpu)
        if speed and speed > 0:
            factor = 1.0 / speed
    if setup["changes"].get("separate_vocals_first"):
        factor += _SEPARATION_GUESS[gpu]
    return audio_seconds * factor


def estimate(set_name: str, language: str, use_gpu: bool = None) -> dict:
    """What a sweep would run before it starts: the two halves, the models and settings it covers
    (and why any is left out), the passes already finished that it will skip, and a rough time."""
    set_name = _check_params(set_name, language)
    plan = _Plan(set_name, language, use_gpu)
    sessions = db.list_benchmark_sessions(10_000, STAGE)
    lengths = {}
    guessed = False
    for half in ("tuning", "holdout"):
        total = 0.0
        for c in plan.cases(half):
            seconds, read = _clip_seconds(c)
            total += seconds
            guessed = guessed or not read
        lengths[half] = total
    todo, done, models = [], 0, set()

    def measure(setup, half, repeat, step):
        nonlocal done
        found = _finished_runs(plan, setup, half, sessions)
        if len(found) > repeat:
            done += 1
            return found[repeat]
        todo.append((setup, half))
        return None

    outcome = _descend(plan, measure)
    if outcome["holdout"] is None:
        # A winner is only known once the tuning passes ran: allow for its two hold-out passes.
        todo.extend([(outcome["winner"], "holdout"), (plan.default, "holdout")])
    seconds = 0.0
    for setup, half in todo:
        seconds += _pass_seconds(setup, lengths[half], plan)
        models.add((setup["engine"], setup["model"]))
    seconds += _LOAD_SECONDS * len(models)
    lab_cost = lab.estimate(STAGE, [plan.config(plan.default)], set_name=set_name,
                            case_ids=[c["id"] for c in plan.tuning + plan.holdout])
    return {
        "set_name": set_name, "language": language, "use_gpu": plan.use_gpu,
        "tuning_cases": len(plan.tuning), "holdout_cases": len(plan.holdout),
        "tuning_titles": len({_title_of(c) for c in plan.tuning}),
        "holdout_titles": len({_title_of(c) for c in plan.holdout}),
        "audio_seconds": round(lengths["tuning"] + lengths["holdout"], 1), "durations_guessed": guessed,
        "small_set": len(plan.tuning) + len(plan.holdout) < MIN_SET_CASES, "min_set_cases": MIN_SET_CASES,
        "models": plan.models, "knobs": plan.knobs, "not_swept": list(NOT_SWEPT),
        "passes_done": done, "passes_to_run": len(todo), "estimated_seconds": round(seconds),
        "estimated_cost_usd": lab_cost["estimated_cost_usd"], "monthly_cap_usd": lab_cost["monthly_cap_usd"],
        "monthly_refusal": lab_cost["monthly_refusal"],
    }


# ---------------------------------------------------------------------------
# Starting and running
# ---------------------------------------------------------------------------

def start(set_name: str, language: str, use_gpu: bool = None) -> dict:
    """Starts (or resumes) a sweep as the Lab's background job. Passes that already have a
    finished run are skipped, so starting a stopped sweep again picks up where it stopped."""
    est = estimate(set_name, language, use_gpu)
    if est["monthly_refusal"]:
        raise UnsupportedOperationError(est["monthly_refusal"])
    if lab._job_active():
        raise ConflictError("A benchmark run is already going.")
    if job_launch.exclusive_active() or job_launch.maintenance_active():
        raise ConflictError("A library restore or cleanup is running; try again when it finishes.")
    record = {"id": hashlib.sha1(f"{est['set_name']}|{language}|{est['use_gpu']}".encode()).hexdigest()[:12],
              "set_name": est["set_name"], "language": language, "use_gpu": est["use_gpu"],
              "status": "queued", "started_at": _now(), "finished_at": None, "note": None,
              "passes_run": 0, "passes_reused": 0, "recommendation": None, "decision": None,
              "estimate": {k: est[k] for k in ("tuning_cases", "holdout_cases", "passes_to_run",
                                                 "estimated_seconds")}}
    with _lock:
        _save_record(record)
    started = job_launch.start_job(JOB_ID, _run_job, JOB_ID, est["set_name"], language, est["use_gpu"],
                                   est["passes_to_run"], gpu_touching=True, description="Benchmark sweep")
    if not started:
        _update_record(status="failed", note="Another benchmark run started first.", finished_at=_now())
        raise ConflictError("A benchmark run is already going.")
    return {"job_id": JOB_ID, "sweep": record}


def _run_job(job_id, set_name, language, use_gpu, planned):
    created = []
    _update_record(status="running")
    try:
        plan = _Plan(set_name, language, use_gpu)
        sessions = db.list_benchmark_sessions(10_000, STAGE)
        counts = {"run": 0, "reused": 0}

        def measure(setup, half, repeat, step):
            found = _finished_runs(plan, setup, half, sessions)
            if len(found) > repeat:
                counts["reused"] += 1
                return found[repeat]
            if background_jobs.is_cancel_requested(job_id):
                raise _Stopped()
            sid = _run_pass(job_id, plan, setup, half, step, created, counts["run"], max(planned, 1))
            run = db.get_benchmark_session(sid)
            if not run or run.get("status") != "done":
                raise _Stopped()
            counts["run"] += 1
            sessions.append(run)
            _update_record(passes_run=counts["run"], passes_reused=counts["reused"])
            return run

        outcome = _descend(plan, measure)
        _update_record(status="done", finished_at=_now(), passes_run=counts["run"],
                       passes_reused=counts["reused"], recommendation=_recommend(plan, outcome))
    except _Stopped:
        _update_record(status="cancelled", finished_at=_now(),
                       note="Stopped. Start the sweep again to resume: finished passes are kept.")
    except Exception as exc:
        _update_record(status="failed", finished_at=_now(), note=redact(str(exc))[:300])
        raise
    finally:
        # The job runs in the API process: models it loaded would otherwise stay on the GPU.
        whisper_models.release_gpu_models()
        for sid in created:
            s = db.get_benchmark_session(sid)
            if s and s.get("status") in ("queued", "running"):
                db.update_benchmark_session(sid, status="failed", finished_at=_now(),
                                            note="The run stopped unexpectedly.")
        background_jobs.set_result(job_id, {"session_ids": created, "sweep": True})
    background_jobs.update_progress(job_id, 1.0, "Sweep finished.")


def _run_pass(job_id, plan, setup, half, step, created, done, planned) -> int:
    cfg = plan.config(setup)
    cases = plan.cases(half)
    what = _describe(setup, plan.defaults)
    sid = lab.create_session(
        STAGE, cfg, cases, plan.use_gpu, label=f"Sweep {plan.set_name}: {what}"[:lab.MAX_LABEL_CHARS],
        case_filter={"tier": None, "set_name": plan.set_name, "case_ids": sorted(c["id"] for c in cases)},
        extra_context={"sweep": {"half": half, "step": step["step"]}})
    created.append(sid)

    def progress(frac, message):
        background_jobs.update_progress(
            job_id, min(0.99, (done + frac) / planned),
            f"Sweep pass {done + 1} of about {planned} ({what}, {half}): {message}")

    lab._run_plan(job_id, STAGE, [(sid, cfg, None)], [c["id"] for c in cases], plan.use_gpu,
                  progress=progress)
    return sid


# ---------------------------------------------------------------------------
# The recommendation and the owner's decision
# ---------------------------------------------------------------------------

def _tag_breakdown(cases: list, base_run: dict, win_run: dict) -> list:
    scores = []
    for run in (base_run, win_run):
        scores.append({r["case_id"]: r.get("score") for r in db.list_benchmark_results(run["id"])})
    groups = {}
    for c in cases:
        for tag in lab._tags(c.get("tags")) or ["(no tag)"]:
            groups.setdefault(tag, []).append(c["id"])
    out = []
    for tag, ids in sorted(groups.items()):
        pair = []
        for by_case in scores:
            vals = [by_case[i] for i in ids if by_case.get(i) is not None]
            pair.append(sum(vals) / len(vals) if vals else None)
        out.append({"tag": tag, "cases": len(ids), "default_score": pair[0], "winner_score": pair[1],
                    "delta": None if None in pair else pair[1] - pair[0], "too_small": len(ids) < MIN_TAG_CASES})
    return out


def _recommend(plan: _Plan, outcome: dict) -> dict:
    noise = outcome["noise"] or 0.0
    margin = max(MIN_SCORE_MARGIN, NOISE_FACTOR * noise)
    winner = outcome["winner"]
    out = {"noise": outcome["noise"], "margin": margin,
           "margin_rule": (f"A change is recommended only when it beats the default on the held-out titles by "
                           f"at least 2 CER points and at least twice the noise ({noise * 100:.1f} points), "
                           f"so here by {margin * 100:.1f} points, with no more failed clips."),
           "default": {**plan.default, "label": _describe(plan.default, plan.defaults)},
           "winner": {**winner, "label": _describe(winner, plan.defaults)},
           "tuning": {"default_score": outcome["default_score"], "winner_score": outcome["winner_score"]},
           "holdout": None, "tags": [], "steps": outcome["steps"],
           "case_counts": {"tuning": len(plan.tuning), "holdout": len(plan.holdout)},
           "small_set": len(plan.tuning) + len(plan.holdout) < MIN_SET_CASES}
    if outcome["holdout"] is None:
        return {**out, "state": "keep_default",
                "text": "Nothing beat the default by more than the noise on the tuning titles. Keep the default."}
    base, win = outcome["holdout"]
    delta = None if None in (base.get("aggregate_score"), win.get("aggregate_score")) \
        else win["aggregate_score"] - base["aggregate_score"]
    more_errors = (win.get("error_count") or 0) > (base.get("error_count") or 0)
    out["holdout"] = {"default_score": base.get("aggregate_score"), "winner_score": win.get("aggregate_score"),
                      "delta": delta, "default_run_id": base["id"], "winner_run_id": win["id"],
                      "default_errors": base.get("error_count") or 0, "winner_errors": win.get("error_count") or 0}
    out["tags"] = _tag_breakdown(plan.holdout, base, win)
    if delta is not None and delta >= margin and not more_errors:
        return {**out, "state": "adopt",
                "text": "The winner also beats the default on the held-out titles by the margin. "
                        "Approve to save it as a setting profile."}
    why = "more clips failed" if more_errors else "the gain was smaller than the margin"
    return {**out, "state": "not_confirmed",
            "text": f"The winner did not hold up on the held-out titles ({why}). Keep the default."}


def _profile_options(winner: dict) -> dict:
    options = {"asr_backend_choice": winner["engine"]}
    if winner["engine"] == "whisper":
        options["whisper_size"] = winner["model"]
    options.update(winner["changes"])
    return options


# What save_translate_preset stores besides the options: the column defaults of a new profile.
_PROFILE_TOGGLES = {"include_genre_notes": True, "default_female_pronouns": False}


def _applies(language: str, winner: dict) -> list:
    """Exactly what applying the approved profile writes on a title of this language, in the
    words of the title's settings, so the owner sees it before approving. Worked out by the
    same plan the apply route runs, from the profile as it would be saved."""
    engine = settings_service.get("default_engine")
    options = profile_service.validate_options(_profile_options(winner))
    preset = {"translation_engine": engine, "engine_model": None, "style_preset": None,
              "options_json": profile_service.dump_options(options),
              **{k: int(v) for k, v in _PROFILE_TOGGLES.items()}}
    columns = profile_service.plan(preset, {"source_language": language})["columns"]
    out = []
    for key, value in columns.items():
        if key == "translate_start_json":
            continue
        spec = series_defaults_service.SETTINGS.get(key, {})
        if spec.get("type") == "bool":
            value = bool(value)
        out.append({"key": key, "label": spec.get("label", key), "value": _value_text(value)})
    out.append({"key": "translate_start", "label": "Translate form starting style and model",
                "value": "back to the engine's defaults"})
    return out


def decide(decision: str, reason: str = "") -> dict:
    """The owner's call on the finished sweep's recommendation. "approve" (only for a
    recommendation that held up on the hold-out) saves the winner as a setting profile, which a
    title gets only when it is picked; "reject" keeps the default. Both are recorded in
    model_decisions with the scores they were made on."""
    if decision not in ("approve", "reject"):
        raise InvalidInputError("decision must be approve or reject.")
    if reason is not None and not isinstance(reason, str):
        raise InvalidInputError("reason must be text.")
    reason = (reason or "").strip()[:300]
    with _lock:
        record = _record()
        rec = record.get("recommendation") if record.get("status") == "done" else None
        if not rec or rec.get("state") == "keep_default":
            raise UnsupportedOperationError("There is no sweep winner to decide on.")
        if record.get("decision"):
            raise ConflictError(f"This sweep's winner was already {record['decision']['decision']}.")
        if decision == "approve" and rec["state"] != "adopt":
            raise UnsupportedOperationError("The winner did not hold up on the held-out titles, so it can't be approved.")
        winner = rec["winner"]
        profile = None
        applies = _applies(record["language"], winner) if decision == "approve" else None
        if decision == "approve":
            name = f"Sweep {record['set_name']} ({record['language']}) {_now()[:16].replace('T', ' ')}"[:100]
            try:
                saved = translate_preset_service.save_translate_preset(
                    name, settings_service.get("default_engine"), options=_profile_options(winner),
                    **_PROFILE_TOGGLES)
            except ConflictError:
                raise ConflictError("A profile with this name already exists; try again in a minute.") from None
            profile = {"id": saved["preset"]["id"], "name": name}
        candidate_id = _decision_candidate(record["language"], winner)
        verdict = "promoted" if decision == "approve" else "rejected"
        scores = {k: rec.get(k) for k in ("noise", "margin", "tuning", "holdout", "tags", "case_counts")}
        # The decision route is PC only, so whoever decided is the owner sitting at this PC.
        decided_by = "owner at this PC"
        db.record_model_decision(candidate_id, verdict, reason, json.dumps({
            **scores, "sweep_id": record.get("id"), "set_name": record["set_name"],
            "language": record["language"], "decided_by": decided_by, "profile": profile, "applies": applies}))
        db.set_model_candidate_status(candidate_id, verdict)
        record["decision"] = {"decision": verdict, "reason": reason, "decided_at": _now(),
                              "decided_by": decided_by, "profile": profile, "applies": applies}
        _save_record(record)
    return {"sweep": record}


def _decision_candidate(language: str, winner: dict) -> int:
    """The model_candidates row a decision hangs on: one per language and setup, so a setup decided
    before keeps its history and a later decision on it is added beside the earlier one."""
    model = json.dumps({"language": language, "model": winner["model"], "changes": winner["changes"]},
                       sort_keys=True)
    found = db.find_model_candidate(DECISION_CAPABILITY, winner["engine"], model)
    if found:
        return found["id"]
    try:
        return db.create_model_candidate(DECISION_CAPABILITY, winner["engine"], model, "settings sweep")
    except sqlite3.IntegrityError:
        return db.find_model_candidate(DECISION_CAPABILITY, winner["engine"], model)["id"]


def list_decisions() -> dict:
    """Earlier sweep decisions, newest first."""
    out = []
    for d in db.list_model_decisions(DECISION_CAPABILITY):
        c = db.get_model_candidate(d["candidate_id"]) or {}
        try:
            setup = json.loads(c.get("model") or "{}")
        except ValueError:
            setup = {}
        out.append({"decision": d["decision"], "reason": d.get("reason") or "", "decided_at": d.get("decided_at"),
                    "language": setup.get("language"),
                    "setup": _describe({"engine": c.get("engine"), "model": setup.get("model"),
                                        "changes": setup.get("changes") or {}},
                                       transcribe_options_service.lab_option_defaults())})
    return {"decisions": out}
