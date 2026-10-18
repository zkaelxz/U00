"""
services/benchmark_translate_sweep_service.py -- the Benchmark Lab's settings sweep for the
translation stage. Same method as the transcription sweep (benchmark_sweep_service): one golden
set and one language, split by whole titles, the default run twice for noise, one setting at a
time from the current best, a hold-out to confirm, nothing applied until the owner approves. It
varies the model, batch size, context window, Reflect and style preset of one engine; every pass
is an ordinary Lab run of scene-batched arms (benchmark_scene_service), so cancel, the monthly
cap and the paid-engine rules are the Lab's own.
"""

import datetime
import hashlib
import json
import sqlite3
import threading

import background_jobs
import db
import translate_engines
import translation_guide
from core import SOURCE_LANGUAGES
from engine_backends.engine_registry import CAP_INSTRUCTIONS, engine_capabilities
from jobs import job_launch
from services import benchmark_lab_service as lab
from services import benchmark_scene_service as scene
from services import benchmark_sweep_service as sweep
from services import settings_service, translate_preset_service, translate_service
from services.benchmark_case_service import model_identity, redact
from services.benchmark_runs_service import session_out, setup_key
from services.service_errors import (ConflictError, InvalidInputError, MissingKeyError,
                                     UnsupportedOperationError)

STAGE = "translation"
JOB_ID = lab.JOB_ID
_RECORD_KEY = "benchmark_translate_sweep"
# The plan's adoption rule for translation: 1.5 chrF points, and (as for transcription) twice the noise.
MIN_SCORE_MARGIN = 0.015
COST_MARGIN = 2.0
COST_MARGIN_FLOOR_USD = 0.05
# Not "translation": that capability is model re-evaluation's list of candidate models.
DECISION_CAPABILITY = "translation_settings"

# (option, label, values): the translation rows of the plan's "Starting numbers and sweep ranges".
# Context before and after are swept one at a time, like every other setting.
KNOBS = (
    ("batch_size", "Batch size", (15, 30, 60)),
    ("context_window", "Context lines before", (3, 6, 10, 20)),
    ("context_window_ahead", "Context lines after", (1, 3, 6, 10)),
    ("reflect", "Reflect", (False, True)),
    ("style_preset", "Style preset", None),
)
_LABELS = {k: label for k, label, _ in KNOBS}
NOT_SWEPT = (
    "Temperature and translation-memory examples: not part of this sweep.",
    "The judge score: the sweep compares chrF only. Check a winner with the judge in the Arena before approving.",
)

_lock = threading.Lock()
_active = {"id": None}


class _Stopped(Exception):
    """A pass was cancelled: the sweep stops and can be resumed."""


class _PassFailed(Exception):
    """A pass ended without finishing (failed or stopped by a cap): the sweep stops on it."""


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
    """The latest translation sweep, with "interrupted" for one the app stopped during."""
    record = _record()
    if not record:
        return {"sweep": None}
    if record.get("status") in ("queued", "running"):
        job = background_jobs.get_status(JOB_ID) or {}
        if _active["id"] != record.get("id"):
            record = dict(record, status="interrupted",
                          note="The app stopped during the sweep. Start it again to resume.")
        elif job.get("status") == "cancelled" and record.get("status") == "queued":
            record = dict(record, status="cancelled", note="Stopped before the first pass started.")
        elif job.get("status") not in ("queued", "running"):
            record = dict(record, status="interrupted",
                          note="The sweep's job ended without finishing. Start it again to resume.")
    rec = record.get("recommendation") or {}
    if record.get("status") == "done" and rec.get("state") == "adopt" and not record.get("decision"):
        record = dict(record, applies=_pending_applies(rec["winner"]))
    return {"sweep": record}


def _pending_applies(winner: dict):
    # A stored winner the profile rules no longer accept (a setting removed since) shows no list
    # rather than breaking the page.
    try:
        return _applies(winner)
    except InvalidInputError:
        return None


# ---------------------------------------------------------------------------
# What a sweep runs
# ---------------------------------------------------------------------------

def _check_params(set_name, language, engine, model, series_id):
    if not isinstance(set_name, str) or not set_name.strip() or len(set_name) > lab.MAX_SET_NAME_CHARS:
        raise InvalidInputError("Pick a golden set.")
    if language not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"language must be one of {', '.join(SOURCE_LANGUAGES)}.")
    if not isinstance(engine, str):
        raise InvalidInputError("Pick a translation engine.")
    # Checks the engine and model the way a Lab run does.
    lab.check_config(STAGE, {"engine": engine, "model": model,
                             "options": {"series_id": series_id} if series_id else None})
    return set_name.strip()


def _sweep_cases(set_name: str, language: str) -> list:
    """The set's scorable translation cases in this language: one language per sweep, because
    results are never averaged across Chinese, Japanese and Korean."""
    return [c for c in lab._select_cases(STAGE, set_name=set_name, scorable_only=True)
            if (c.get("source_language") or "zh") == language and c.get("reference_text")
            and (c.get("source_text") or "").strip()]


class _Plan:
    def __init__(self, set_name, language, engine, model, series_id):
        self.set_name, self.language, self.engine, self.series_id = set_name, language, engine, series_id
        cases = _sweep_cases(set_name, language)
        if not cases:
            raise UnsupportedOperationError("This set has no translation cases with a reference in that language.")
        self.tuning, self.holdout = sweep.split_by_title(cases)
        self.default_model = model or translate_engines.effective_default_model(engine)
        self.default = {"engine": engine, "model": model, "changes": {}}
        entry = next(e for e in translate_service.list_engines() if e["name"] == engine)
        self.models = [{"engine": engine, "model": m, "label": m, "included": True, "reason": None}
                       for m in (entry["models"] or []) if m != self.default_model]
        self.knobs = [{"key": key, "label": label, "included": True, "reason": None,
                       "values": list(values if values is not None else translation_guide.STYLE_PRESETS)}
                      for key, label, values in KNOBS
                      if key != "reflect" or CAP_INSTRUCTIONS in engine_capabilities(engine)]
        self._identity, self._configs = {}, {}
        context = lab.run_context(STAGE, self.config(self.default), cases, False)
        # What a reused run must share: the app version, since a fix to translation can change what
        # the same setup produces.
        self.markers = {"app_version": context.get("app_version")}

    def model_of(self, setup: dict) -> str:
        return setup["model"] or self.default_model

    def with_model(self, setup: dict, model: str) -> dict:
        """The setup on another model; the default model stays as the sweep was asked for it."""
        return {**setup, "model": self.default["model"] if model == self.default_model else model}

    def defaults_of(self, setup: dict) -> dict:
        return scene.defaults(setup["engine"], setup["model"])

    def options(self, setup: dict) -> dict:
        return {**setup["changes"], **({"series_id": self.series_id} if self.series_id else {})}

    def config(self, setup: dict) -> dict:
        # Checking an arm lists the engines (package and key probes), and the descent asks for the
        # same few dozen setups many times.
        memo = json.dumps(setup, sort_keys=True)
        if memo not in self._configs:
            self._configs[memo] = lab.check_config(STAGE, {"engine": setup["engine"], "model": setup["model"],
                                                            "options": self.options(setup)})
        return self._configs[memo]

    def key(self, setup: dict) -> str:
        cfg = self.config(setup)
        return setup_key({"engine": cfg["engine"], "model": self.model_of(setup),
                          "context_settings": json.dumps({"translate_options": cfg["options"]})})

    def identity(self, setup: dict) -> dict:
        model = self.model_of(setup)
        if model not in self._identity:
            self._identity[model] = model_identity(STAGE, self.config(setup))
        return self._identity[model]

    def cases(self, half: str) -> list:
        return self.tuning if half == "tuning" else self.holdout


def _value_text(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _describe(plan: _Plan, setup: dict) -> str:
    parts = [plan.model_of(setup)]
    defaults = plan.defaults_of(setup)
    parts += [f"{_LABELS.get(k, k)} {_value_text(v)}" for k, v in setup["changes"].items() if v != defaults.get(k)]
    return ", ".join(parts)


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

    def record(setup, half, run, step):
        steps.append({**step, "half": half, "setup": _describe(plan, setup),
                      "run_id": run["id"] if run else None, "score": score(run),
                      "errors": run.get("error_count") if run else None, "kept": None})

    noise_step = {"step": "noise", "value": None}
    first = measure(plan.default, "tuning", 0, noise_step)
    record(plan.default, "tuning", first, noise_step)
    second = measure(plan.default, "tuning", 1, noise_step)
    record(plan.default, "tuning", second, noise_step)
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
        kept = _describe(plan, best) if best is not None else None
        for st in steps:
            if st["step"] == candidates[0][1]["step"]:
                st["kept"] = st["setup"] == kept
        if best is not None:
            current, current_score = best, best_score

    pick([({"engine": m["engine"], "model": m["model"], "changes": {}}, {"step": "model", "value": m["label"]})
          for m in plan.models if m["included"]])
    for knob in plan.knobs:
        defaults = plan.defaults_of(current)
        now = current["changes"].get(knob["key"], defaults[knob["key"]])
        candidates = []
        for value in knob["values"]:
            if value == now:
                continue
            changes = {**current["changes"], knob["key"]: value}
            if value == defaults[knob["key"]]:
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
    """Finished runs of this setup that translated exactly these cases at their current reference
    versions, oldest first: the passes a sweep can skip."""
    key = plan.key(setup)
    identity = plan.identity(setup)
    want = {c["id"]: c.get("ref_version") or 1 for c in plan.cases(half)}
    out = []
    for s in sorted(sessions, key=lambda r: r["id"]):
        if s.get("status") != "done" or s.get("stage") != STAGE or setup_key(s) != key:
            continue
        ctx = session_out(s)["context_settings"]
        if any(ctx.get(k) != v for k, v in plan.markers.items()) or ctx.get("model_identity") != identity:
            continue
        # A pass with failed cases (an engine that errored on some) is not a measurement.
        if s.get("error_count"):
            continue
        results = db.list_benchmark_results(s["id"])
        if len(results) == len(want) and {r["case_id"]: r.get("ref_version") or 1 for r in results} == want:
            out.append(s)
    return out


def _pass_cost(plan: _Plan, setup: dict, half: str, ceiling_model: str) -> float:
    """What one pass is expected to cost. Passes that can run on a model the sweep may pick use the
    dearest one, so the estimate is never lower than what the sweep can spend."""
    if setup["changes"] or half == "holdout":
        setup = plan.with_model(setup, ceiling_model)
    return lab._estimate_config(plan.config(setup), plan.cases(half))


def estimate(set_name: str, language: str, engine: str, model: str = None, series_id: int = None) -> dict:
    """What a sweep would run before it starts: the two halves, the settings it covers, the passes
    already finished that it will skip, and what the paid passes would cost against the monthly cap."""
    set_name = _check_params(set_name, language, engine, model, series_id)
    plan = _Plan(set_name, language, engine, model, series_id)
    sessions = db.list_benchmark_sessions(10_000, STAGE)
    todo, done = [], 0

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
    models = [plan.default_model] + [m["model"] for m in plan.models]
    ceiling = max(models, key=lambda m: _pass_cost(plan, plan.with_model(plan.default, m), "tuning", m))
    total = sum(_pass_cost(plan, setup, half, ceiling) for setup, half in todo)
    monthly_cap, spend = settings_service.get_monthly_cap_usd(), db.get_month_spend()
    capped = lab._cap_applies(engine)
    remaining, refusal = translate_engines.resolve_cost_cap(None, monthly_cap, spend) if capped else (None, None)
    return {
        "set_name": set_name, "language": language, "engine": engine, "model": plan.default_model,
        "series_id": series_id, "tuning_cases": len(plan.tuning), "holdout_cases": len(plan.holdout),
        "tuning_titles": len({sweep._title_of(c) for c in plan.tuning}),
        "holdout_titles": len({sweep._title_of(c) for c in plan.holdout}),
        "small_set": len(plan.tuning) + len(plan.holdout) < sweep.MIN_SET_CASES, "min_set_cases": sweep.MIN_SET_CASES,
        "models": plan.models, "knobs": plan.knobs, "not_swept": list(NOT_SWEPT),
        "passes_done": done, "passes_to_run": len(todo), "estimated_cost_usd": round(total, 6),
        "cap_applies": capped, "monthly_cap_usd": monthly_cap, "month_spend_usd": round(spend, 6),
        "remaining_usd": None if remaining is None else round(remaining, 6), "monthly_refusal": refusal,
        "estimate_above_cap": bool(remaining is not None and total > remaining),
    }


# ---------------------------------------------------------------------------
# Starting and running
# ---------------------------------------------------------------------------

def start(set_name: str, language: str, engine: str, model: str = None, series_id: int = None) -> dict:
    """Starts (or resumes) a translation sweep as the Lab's background job. The estimate is
    worked out here, whatever the client showed: a sweep over the monthly cap is refused, and a
    pass that finished before is skipped, so starting a stopped sweep picks up where it stopped."""
    est = estimate(set_name, language, engine, model, series_id)
    if est["monthly_refusal"]:
        raise UnsupportedOperationError(est["monthly_refusal"])
    if est["estimate_above_cap"]:
        raise UnsupportedOperationError(
            f"This sweep is estimated at ${est['estimated_cost_usd']:.4f}, more than the "
            f"${est['remaining_usd']:.4f} left of this month's cap.")
    if translate_service.resolve_api_key(engine) is None:
        raise MissingKeyError(engine)
    if lab._job_active():
        raise ConflictError("A benchmark run is already going.")
    if job_launch.exclusive_active() or job_launch.maintenance_active():
        raise ConflictError("A library restore or cleanup is running; try again when it finishes.")
    record = {"id": hashlib.sha1(f"{est['set_name']}|{language}|{engine}|{est['model']}|{series_id}".encode()).hexdigest()[:12],
              "set_name": est["set_name"], "language": language, "engine": engine, "model": est["model"],
              "series_id": series_id, "status": "queued", "started_at": _now(), "finished_at": None,
              "note": None, "passes_run": 0, "passes_reused": 0, "recommendation": None, "decision": None,
              "estimate": {k: est[k] for k in ("tuning_cases", "holdout_cases", "passes_to_run")}}
    # The estimate leaves out re-asks and retries and counts Reflect as a flat three, so the sweep as
    # a whole may spend a margin above it, and no more.
    job_cap = round(est["estimated_cost_usd"] * COST_MARGIN + COST_MARGIN_FLOOR_USD, 6) if est["cap_applies"] else None
    gpu = any(translate_engines.ollama_touches_local_gpu(engine, m["model"]) for m in
              [{"model": est["model"]}, *est["models"]])
    # Held across the start so the job's first record update waits for this record, and a start
    # that loses the race leaves the stored sweep (and any recommendation waiting on a decision) alone.
    with _lock:
        started = job_launch.start_job(JOB_ID, _run_job, JOB_ID, est["set_name"], language, engine, model,
                                       series_id, est["passes_to_run"], job_cap, gpu_touching=gpu,
                                       description="Translation sweep")
        if not started:
            raise ConflictError("A benchmark run is already going.")
        _active["id"] = record["id"]
        _save_record(record)
    return {"job_id": JOB_ID, "sweep": record}


def _run_job(job_id, set_name, language, engine, model, series_id, planned, job_cap=None):
    created = []
    _update_record(status="running")
    try:
        plan = _Plan(set_name, language, engine, model, series_id)
        api_key = translate_service.resolve_api_key(engine)
        if api_key is None:
            raise MissingKeyError(engine)
        sessions = db.list_benchmark_sessions(10_000, STAGE)
        counts = {"run": 0, "reused": 0}

        def measure(setup, half, repeat, step):
            found = _finished_runs(plan, setup, half, sessions)
            if len(found) > repeat:
                counts["reused"] += 1
                return found[repeat]
            if background_jobs.is_cancel_requested(job_id):
                raise _Stopped()
            left = None if job_cap is None else max(job_cap - sum(
                (db.get_benchmark_session(c) or {}).get("total_cost_usd") or 0.0 for c in created), 0.0)
            sid = _run_pass(job_id, plan, setup, half, step, created, counts["run"], max(planned, 1), api_key, left)
            run = db.get_benchmark_session(sid) or {}
            if run.get("status") == "cancelled" or background_jobs.is_cancel_requested(job_id):
                raise _Stopped()
            if run.get("status") != "done":
                raise _PassFailed(f"The pass {_describe(plan, setup)} ({half}) did not finish"
                                  + (f": {run['note']}" if run.get("note") else "."))
            if run.get("error_count"):
                # The same rule as _finished_runs, so a winner is never picked from a pass a resume
                # would not accept (and pay for again).
                raise _PassFailed(f"The pass {_describe(plan, setup)} ({half}) had {run['error_count']} failed cases.")
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
    except _PassFailed as exc:
        _update_record(status="failed", finished_at=_now(),
                       note=redact(f"{exc} Finished passes are kept; fix the cause, then start the sweep again "
                                   "to retry it.")[:300])
    except Exception as exc:
        _update_record(status="failed", finished_at=_now(), note=redact(str(exc))[:300])
        raise
    finally:
        _active["id"] = None
        for sid in created:
            s = db.get_benchmark_session(sid)
            if s and s.get("status") in ("queued", "running"):
                db.update_benchmark_session(sid, status="failed", finished_at=_now(),
                                            note="The run stopped unexpectedly.")
        background_jobs.set_result(job_id, {"session_ids": created, "sweep": True})
    background_jobs.update_progress(job_id, 1.0, "Sweep finished.")


def _run_pass(job_id, plan, setup, half, step, created, done, planned, api_key, job_cap=None) -> int:
    cfg = plan.config(setup)
    cases = plan.cases(half)
    what = _describe(plan, setup)
    sid = lab.create_session(
        STAGE, cfg, cases, False, label=f"Sweep {plan.set_name}: {what}"[:lab.MAX_LABEL_CHARS],
        case_filter={"tier": None, "set_name": plan.set_name, "case_ids": sorted(c["id"] for c in cases)},
        extra_context={"sweep": {"half": half, "step": step["step"]}})
    created.append(sid)

    def progress(frac, message):
        background_jobs.update_progress(
            job_id, min(0.99, (done + frac) / planned),
            f"Sweep pass {done + 1} of about {planned} ({what}, {half}): {message}")

    lab._run_plan(job_id, STAGE, [(sid, cfg, api_key)], [c["id"] for c in cases], False, job_cap, progress=progress)
    return sid


# ---------------------------------------------------------------------------
# The recommendation and the owner's decision
# ---------------------------------------------------------------------------

def _recommend(plan: _Plan, outcome: dict) -> dict:
    noise = outcome["noise"] or 0.0
    margin = max(MIN_SCORE_MARGIN, sweep.NOISE_FACTOR * noise)
    winner = outcome["winner"]
    out = {"noise": outcome["noise"], "margin": margin,
           "margin_rule": (f"A change is recommended only when it beats the default on the held-out titles by "
                           f"at least 1.5 chrF points and at least twice the noise ({noise * 100:.1f} points), "
                           f"so here by {margin * 100:.1f} points, with no more failed cases. The judge "
                           "score is not part of the sweep."),
           "default": {**plan.default, "label": _describe(plan, plan.default)},
           "winner": {**winner, "label": _describe(plan, winner)},
           "tuning": {"default_score": outcome["default_score"], "winner_score": outcome["winner_score"]},
           "holdout": None, "tags": [], "steps": outcome["steps"],
           "case_counts": {"tuning": len(plan.tuning), "holdout": len(plan.holdout)},
           "small_set": len(plan.tuning) + len(plan.holdout) < sweep.MIN_SET_CASES}
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
    out["tags"] = sweep._tag_breakdown(plan.holdout, base, win)
    if delta is not None and delta >= margin and not more_errors:
        return {**out, "state": "adopt",
                "text": "The winner also beats the default on the held-out titles by the margin. "
                        "Approve to save it as a setting profile."}
    why = "more cases failed" if more_errors else "the gain was smaller than the margin"
    return {**out, "state": "not_confirmed",
            "text": f"The winner did not hold up on the held-out titles ({why}). Keep the default."}


def _profile_fields(winner: dict) -> tuple:
    """(engine model, style preset, run options) a profile saves for the winner. The model is
    left empty for an engine without a model picker, as a profile requires."""
    options = scene.resolve_options(winner["engine"], winner["model"], winner["changes"])
    run = {k: options[k] for k in ("batch_size", "context_window", "context_window_ahead", "reflect")}
    entry = next((e for e in translate_service.list_engines() if e["name"] == winner["engine"]), {})
    return (winner["model"] if entry.get("models") else None), options["style_preset"], run


def _applies(winner: dict) -> list:
    """What the approved profile holds, in the words of the Translate form, shown before approving."""
    model, style, run = _profile_fields(winner)
    rows = [("engine", "Engine", winner["engine"]), ("model", "Model", model or "the engine's default"),
            ("style_preset", "Style preset", style)]
    rows += [(k, scene.OPTION_LABELS[k], _value_text(v)) for k, v in run.items()]
    return [{"key": k, "label": label, "value": value} for k, label, value in rows]


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
        applies = _applies(winner) if decision == "approve" else None
        if decision == "approve":
            name = f"Sweep {record['set_name']} ({record['language']}) {_now()[:16].replace('T', ' ')}"[:100]
            model, style, run = _profile_fields(winner)
            try:
                saved = translate_preset_service.save_translate_preset(
                    name, winner["engine"], engine_model=model, style_preset=style, options=run)
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
        return db.create_model_candidate(DECISION_CAPABILITY, winner["engine"], model, "translation sweep")
    except sqlite3.IntegrityError:
        return db.find_model_candidate(DECISION_CAPABILITY, winner["engine"], model)["id"]


def list_decisions() -> dict:
    """Earlier translation sweep decisions, newest first."""
    out = []
    for d in db.list_model_decisions(DECISION_CAPABILITY):
        c = db.get_model_candidate(d["candidate_id"]) or {}
        try:
            setup = json.loads(c.get("model") or "{}")
        except ValueError:
            setup = {}
        out.append({"decision": d["decision"], "reason": d.get("reason") or "", "decided_at": d.get("decided_at"),
                    "language": setup.get("language"),
                    "setup": ", ".join([setup.get("model") or c.get("engine") or "",
                                        *(f"{_LABELS.get(k, k)} {_value_text(v)}"
                                          for k, v in (setup.get("changes") or {}).items())])})
    return {"decisions": out}
