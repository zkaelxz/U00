"""
services/model_reeval_service.py -- Step 40b: scheduled model
re-evaluation and promotion. UI-free.

"Is something better than my production model available?" -- answered
with Step 38's Benchmark Lab, not a second scoring system:

- Candidates are added by the user (a model string for an engine this app
  offers, or a local Ollama model already pulled). Nothing browses provider
  catalogs or downloads models (discovery stays opt-in).
- A re-evaluation is one benchmark_lab_service.start_run() arena run of
  the production model plus every open candidate over a golden set the
  user picked. It runs on "Run now", or on a schedule (off until the user
  turns it on; default interval 30 days) checked by the API's hourly
  background loop -- the same estimate refusal as any benchmark run, and the
  monthly cap when one is set. The schedule can only be turned on while a
  monthly cap or a per-run limit is set, so it never spends unbounded; the
  first scheduled run is one interval after it is turned on, and the loop
  never starts one while any other job is running or queued.
- report() lines each candidate up against production (quality, cost,
  latency, VRAM deltas) from the recorded runs.
- Nothing changes until the user approves: promote() is its own call with
  confirm=true, and it only updates the production record here and, when
  the engine differs, Settings' default engine (the existing "change the
  translation backend in Settings" setting). Presets keep their own model;
  Step 40's guided switch changes those.
- Every decision (promoted or rejected) is recorded with the candidate, its
  scores, the reason and the date. A rejected candidate is left out of later
  runs and re-adding it surfaces "already evaluated on <date>, rejected:
  <reason>" instead of starting over as if new.

Only the "translation" capability exists until Step 36's capability
registry lands; the capability field is kept so it can grow.
"""
import datetime
import json
import threading

import background_jobs
import db
from services import benchmark_lab_service, settings_service, translate_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError, UnsupportedOperationError)

CAPABILITIES = ("translation",)
DEFAULT_INTERVAL_DAYS = 30
MAX_OPEN_CANDIDATES = 3
_SETTINGS_KEY = "model_reeval_settings"
_PRODUCTION_KEY = "model_reeval_production"
_LAST_RUN_KEY = "model_reeval_last_run"
# A scheduled attempt that was refused is kept apart from the last good run,
# so the report and later decisions keep that run's scores.
_LAST_ATTEMPT_KEY = "model_reeval_last_attempt"
# One lock for every candidate decision (add, promote, reject, reopen), so a
# status check and its write can't interleave with another decision.
_decision_lock = threading.Lock()
MAX_REASON_CHARS = 300


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


def _json_setting(key: str, default):
    raw = db.get_app_setting(key)
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


# ---------------------------------------------------------------------------
# Production model and schedule settings
# ---------------------------------------------------------------------------

def get_production(capability: str = "translation") -> dict:
    """The model re-evaluations compare against: the recorded production
    model, or Settings' default engine with its built-in default model."""
    _check_capability(capability)
    engine = settings_service.get_default_engine()
    saved = _json_setting(_PRODUCTION_KEY, {}).get(capability)
    # The promoted record counts only while Settings still uses its engine: a
    # later change of the default engine in Settings wins.
    if isinstance(saved, dict) and saved.get("engine") == engine:
        return {"engine": saved["engine"], "model": saved.get("model"), "source": "promoted",
                "promoted_at": saved.get("promoted_at")}
    return {"engine": engine, "model": benchmark_lab_service.default_model(engine),
            "source": "settings"}


def _check_capability(capability: str):
    if capability not in CAPABILITIES:
        raise InvalidInputError("Unknown capability.")


def get_settings() -> dict:
    s = _json_setting(_SETTINGS_KEY, {})
    return {"schedule_enabled": bool(s.get("schedule_enabled", False)),
            "interval_days": int(s.get("interval_days") or DEFAULT_INTERVAL_DAYS),
            "tier": s.get("tier"), "set_name": s.get("set_name"),
            "max_cost_usd": s.get("max_cost_usd"), "enabled_at": s.get("enabled_at")}


def _spend_limit_set(max_cost_usd) -> bool:
    """A scheduled run is bounded: a monthly cap above $0 in Settings, or a
    per-run limit of its own."""
    return max_cost_usd is not None or settings_service.get_monthly_cap_usd() > 0


def set_settings(schedule_enabled: bool, interval_days: int, tier: str = None,
                 set_name: str = None, max_cost_usd: float = None) -> dict:
    """max_cost_usd: a scheduled run whose estimate is above it is skipped,
    and a scheduled run that starts stops (stopped_cap) once its real spend
    across all models reaches it; 0 means free engines only. None = only
    the monthly cap applies, and then a cap must be set.
    Turning the schedule on is refused without either. "Run now" shows its
    own estimate. An enabled schedule's answer carries schedule_estimate
    (what one scheduled run would cost now, or None with the reason)."""
    if not isinstance(schedule_enabled, bool):
        raise InvalidInputError("schedule_enabled must be true or false.")
    if not isinstance(interval_days, int) or not 1 <= interval_days <= 365:
        raise InvalidInputError("interval_days must be between 1 and 365.")
    if tier is not None and tier not in benchmark_lab_service.TIERS:
        raise InvalidInputError("Unknown tier.")
    if set_name is not None and (not isinstance(set_name, str) or len(set_name) > 60):
        raise InvalidInputError("set_name is invalid.")
    if max_cost_usd is not None and (isinstance(max_cost_usd, bool)
                                     or not isinstance(max_cost_usd, (int, float))
                                     or not 0 <= max_cost_usd <= 10000):
        raise InvalidInputError("max_cost_usd must be between 0 and 10000.")
    if schedule_enabled and not _spend_limit_set(max_cost_usd):
        raise InvalidInputError(
            "Set a monthly spending cap in Settings, or a limit per scheduled run "
            "(max_cost_usd), before turning the schedule on.")
    previous = get_settings()
    # The first scheduled run is one interval after the schedule is turned on.
    enabled_at = None
    if schedule_enabled:
        enabled_at = (previous["enabled_at"] if previous["schedule_enabled"]
                      and previous["enabled_at"] else _now())
    db.set_app_setting(_SETTINGS_KEY, json.dumps({
        "schedule_enabled": schedule_enabled, "interval_days": interval_days,
        "tier": tier, "set_name": set_name or None,
        "max_cost_usd": None if max_cost_usd is None else float(max_cost_usd),
        "enabled_at": enabled_at}))
    out = get_overview()
    out["schedule_estimate"] = None
    out["schedule_estimate_error"] = None
    if schedule_enabled:
        try:
            out["schedule_estimate"] = estimate_run()
        except ServiceError as exc:
            out["schedule_estimate_error"] = str(exc)[:300]
    return out


def _last_run() -> dict:
    return _json_setting(_LAST_RUN_KEY, {})


def next_due_at():
    s = get_settings()
    if not s["schedule_enabled"]:
        return None
    stamps = []
    for stamp in (_last_run().get("started_at"),
                  _json_setting(_LAST_ATTEMPT_KEY, {}).get("started_at"), s["enabled_at"]):
        try:
            stamps.append(datetime.datetime.fromisoformat(stamp))
        except (TypeError, ValueError):
            pass
    if not s["enabled_at"]:
        # A schedule saved without its enable time can't place its first
        # run: never due until it is saved again.
        return None
    started = max(stamps)
    return (started + datetime.timedelta(days=s["interval_days"])).isoformat()


def is_due(now: datetime.datetime = None) -> bool:
    due = next_due_at()
    if due is None or not open_candidates():
        return False
    now = now or datetime.datetime.utcnow()
    return now >= datetime.datetime.fromisoformat(due)


# ---------------------------------------------------------------------------
# Candidates and decisions
# ---------------------------------------------------------------------------

def _candidate_out(c: dict) -> dict:
    decision = db.latest_model_decision(c["id"])
    out = {k: c.get(k) for k in ("id", "capability", "engine", "model", "note", "status",
                                 "created_at")}
    out["last_decision"] = _decision_out(decision) if decision else None
    return out


def _decision_out(d: dict) -> dict:
    try:
        scores = json.loads(d.get("scores") or "{}")
    except ValueError:
        scores = {}
    return {"id": d["id"], "candidate_id": d["candidate_id"], "decision": d["decision"],
            "reason": d.get("reason") or "", "scores": scores, "decided_at": d.get("decided_at"),
            "summary": _decision_summary(d)}


def _decision_summary(d: dict) -> str:
    date = (d.get("decided_at") or "")[:10]
    reason = d.get("reason") or "no reason given"
    return f"Already evaluated on {date}, {d['decision']}: {reason}"


def open_candidates(capability: str = "translation") -> list:
    return [c for c in db.list_model_candidates(capability) if c["status"] == "candidate"]


def list_candidates(capability: str = "translation") -> dict:
    _check_capability(capability)
    return {"candidates": [_candidate_out(c) for c in db.list_model_candidates(capability)]}


def add_candidate(engine: str, model: str = None, note: str = "",
                  capability: str = "translation") -> dict:
    """Registers a candidate. A model already registered (open, promoted or
    rejected) comes back as-is with its recorded decision rather than being
    added again as if it were new."""
    _check_capability(capability)
    cfg = benchmark_lab_service.check_config("translation", {"engine": engine, "model": model})
    model = cfg["model"] or benchmark_lab_service.default_model(cfg["engine"])
    note = (note or "").strip()[:200]
    with _decision_lock:
        return _add_candidate_locked(capability, cfg, model, note)


def _add_candidate_locked(capability, cfg, model, note) -> dict:
    prod = get_production(capability)
    if (prod["engine"], prod["model"]) == (cfg["engine"], model):
        raise InvalidInputError("That's the current production model.")
    existing = db.find_model_candidate(capability, cfg["engine"], model)
    if existing:
        return {"candidate": _candidate_out(existing), "already_registered": True}
    if len(open_candidates(capability)) >= MAX_OPEN_CANDIDATES:
        raise UnsupportedOperationError(
            f"At most {MAX_OPEN_CANDIDATES} open candidates; promote or reject one first.")
    cid = db.create_model_candidate(capability, cfg["engine"], model, note)
    return {"candidate": _candidate_out(db.get_model_candidate(cid)), "already_registered": False}


def reopen_candidate(candidate_id: int) -> dict:
    """Puts a rejected candidate back in the running, explicitly. Its
    earlier decision stays recorded."""
    with _decision_lock:
        c = _require_candidate(candidate_id)
        if c["status"] not in ("rejected", "superseded"):
            raise ConflictError("Only a rejected or superseded candidate can be reopened.")
        if len(open_candidates(c["capability"])) >= MAX_OPEN_CANDIDATES:
            raise UnsupportedOperationError(f"At most {MAX_OPEN_CANDIDATES} open candidates.")
        db.set_model_candidate_status(candidate_id, "candidate")
        return _candidate_out(db.get_model_candidate(candidate_id))


def _require_candidate(candidate_id: int) -> dict:
    c = db.get_model_candidate(candidate_id)
    if not c:
        raise NotFoundError("Candidate not found.")
    return c


# ---------------------------------------------------------------------------
# Running and reporting
# ---------------------------------------------------------------------------

def _planned_configs(capability: str):
    prod = get_production(capability)
    # A candidate that has since become production (Settings changed) is left out.
    candidates = [c for c in open_candidates(capability)
                  if (c["engine"], c["model"]) != (prod["engine"], prod["model"])]
    if not candidates:
        raise UnsupportedOperationError("Add a candidate model first.")
    configs = [{"engine": prod["engine"], "model": prod["model"]}] + [
        {"engine": c["engine"], "model": c["model"]} for c in candidates]
    return candidates, configs


def estimate_run(capability: str = "translation") -> dict:
    """What "Run now" would cost, through the Benchmark Lab's own estimate."""
    _check_capability(capability)
    _cands, configs = _planned_configs(capability)
    s = get_settings()
    return benchmark_lab_service.estimate("translation", configs, s["tier"], s["set_name"])


def run_now(capability: str = "translation", scheduled: bool = False) -> dict:
    """One arena run: production + every open candidate, through the Benchmark
    Lab (same estimate, cap and job). Nothing is promoted by running."""
    _check_capability(capability)
    candidates, configs = _planned_configs(capability)
    s = get_settings()
    started = benchmark_lab_service.start_run(
        "translation", configs, tier=s["tier"], set_name=s["set_name"],
        label="Scheduled re-evaluation" if scheduled else "Re-evaluation",
        prompt_version="", max_cost_usd=s["max_cost_usd"] if scheduled else None)
    db.set_app_setting(_LAST_RUN_KEY, json.dumps({
        "started_at": _now(), "arena_group": started["arena_group"],
        "production_run_id": started["session_ids"][0],
        "candidate_runs": {str(c["id"]): sid
                           for c, sid in zip(candidates, started["session_ids"][1:])},
        "scheduled": scheduled}))
    return {**started, "candidate_ids": [c["id"] for c in candidates]}


def _system_busy() -> bool:
    """Any job running or queued in this process, or a restore / bulk
    maintenance hold: a scheduled run never competes with the user's work."""
    if background_jobs.exclusive_active() or background_jobs.maintenance_active():
        return True
    return any(j.get("status") in ("running", "queued")
               for j in background_jobs.list_all_jobs().values())


def run_if_due() -> bool:
    """The background loop's hook: starts a scheduled run when one is due.
    Refusals (cap used up, no cases, over the per-run limit) are recorded as
    the last attempt so the loop doesn't retry every tick. Nothing is
    recorded when the PC is busy (try again next tick) or when no spending
    limit is set and the run would cost anything (fail closed)."""
    if _system_busy():
        return False
    if not is_due():
        return False
    try:
        s = get_settings()
        if not _spend_limit_set(s["max_cost_usd"]):
            # Settings refuse this combination, but the monthly cap can be
            # cleared afterwards: an unbounded scheduled run never starts.
            if estimate_run()["estimated_cost_usd"] > 0:
                return False
        elif s["max_cost_usd"] is not None:
            est = estimate_run()
            if est["estimated_cost_usd"] > s["max_cost_usd"]:
                raise UnsupportedOperationError(
                    f"Skipped: estimated ${est['estimated_cost_usd']:.4f}, above the "
                    f"${s['max_cost_usd']:.2f} limit set for scheduled runs.")
        run_now(scheduled=True)
        db.set_app_setting(_LAST_ATTEMPT_KEY, json.dumps({}))
        return True
    except ConflictError:
        return False        # lost a race with a manual run: not an attempt
    except Exception as exc:
        db.set_app_setting(_LAST_ATTEMPT_KEY, json.dumps({
            "started_at": _now(), "scheduled": True,
            "error": benchmark_lab_service.redact(str(exc))[:300]}))
        return False


def _delta(a, b):
    return None if a is None or b is None else a - b


def report(capability: str = "translation") -> dict:
    """The latest re-evaluation: each candidate against production."""
    _check_capability(capability)
    benchmark_lab_service.close_stale_runs()
    last = _last_run()
    attempt = _json_setting(_LAST_ATTEMPT_KEY, {})
    prod_run = db.get_benchmark_session(last["production_run_id"]) \
        if last.get("production_run_id") else None
    rows = []
    for cid_str, run_id in (last.get("candidate_runs") or {}).items():
        c = db.get_model_candidate(int(cid_str))
        run = db.get_benchmark_session(run_id)
        if not c or not run:
            continue
        rows.append({
            "candidate": _candidate_out(c), "run_id": run_id,
            "status": run.get("status"),
            "aggregate_score": run.get("aggregate_score"),
            "quality_delta": _delta(run.get("aggregate_score"),
                                    prod_run.get("aggregate_score") if prod_run else None),
            "cost_delta_usd": _delta(run.get("total_cost_usd"),
                                     prod_run.get("total_cost_usd") if prod_run else None),
            "latency_delta_seconds": _delta(run.get("avg_latency_seconds"),
                                            prod_run.get("avg_latency_seconds") if prod_run else None),
            "vram_delta_mb": _delta(run.get("peak_vram_mb"),
                                    prod_run.get("peak_vram_mb") if prod_run else None),
            "total_cost_usd": run.get("total_cost_usd"),
            "avg_latency_seconds": run.get("avg_latency_seconds"),
        })
    return {
        "production": get_production(capability),
        "production_run": ({k: prod_run.get(k) for k in (
            "id", "status", "aggregate_score", "total_cost_usd", "avg_latency_seconds",
            "peak_vram_mb", "engine", "model")} if prod_run else None),
        "started_at": last.get("started_at"), "scheduled": bool(last.get("scheduled")),
        "arena_group": last.get("arena_group"), "error": attempt.get("error"),
        "error_at": attempt.get("started_at"),
        "rows": rows,
    }


def get_overview(capability: str = "translation") -> dict:
    return {"capability": capability, "production": get_production(capability),
            "settings": get_settings(), "next_due_at": next_due_at(),
            **list_candidates(capability), "report": report(capability)}


# ---------------------------------------------------------------------------
# Decisions (explicit only)
# ---------------------------------------------------------------------------

def _scores_for(candidate_id: int) -> dict:
    last = _last_run()
    run_id = (last.get("candidate_runs") or {}).get(str(candidate_id))
    run = db.get_benchmark_session(run_id) if run_id else None
    prod = db.get_benchmark_session(last["production_run_id"]) \
        if run and last.get("production_run_id") else None
    if not run:
        return {}
    return {"run_id": run_id, "aggregate_score": run.get("aggregate_score"),
            "total_cost_usd": run.get("total_cost_usd"),
            "avg_latency_seconds": run.get("avg_latency_seconds"),
            "peak_vram_mb": run.get("peak_vram_mb"),
            "production_run_id": prod["id"] if prod else None,
            "production_score": prod.get("aggregate_score") if prod else None,
            "production_cost_usd": prod.get("total_cost_usd") if prod else None,
            "production_latency_seconds": prod.get("avg_latency_seconds") if prod else None}


def _clean_reason(reason) -> str:
    if reason is not None and not isinstance(reason, str):
        raise InvalidInputError("reason must be text.")
    return (reason or "").strip()[:MAX_REASON_CHARS]


def promote(candidate_id: int, confirm: bool, reason: str = "") -> dict:
    """The one place production changes, and only with confirm=true."""
    if confirm is not True:
        raise InvalidInputError("Confirmation required (confirm=true).")
    # One promotion at a time: two concurrent confirms can't both pass the
    # status check and leave two "promoted" records.
    with _decision_lock:
        return _promote_locked(candidate_id, reason)


def _promote_locked(candidate_id: int, reason: str) -> dict:
    c = _require_candidate(candidate_id)
    if c["status"] != "candidate":
        raise ConflictError(f"This candidate is already {c['status']}.")
    previous = get_production(c["capability"])
    engine_changed = previous["engine"] != c["engine"]
    if engine_changed:
        # Settings first: if it refuses the engine, nothing else is written.
        settings_service.set_settings({"default_engine": c["engine"]})
    prod = _json_setting(_PRODUCTION_KEY, {})
    prod[c["capability"]] = {"engine": c["engine"], "model": c["model"], "promoted_at": _now()}
    db.set_app_setting(_PRODUCTION_KEY, json.dumps(prod))
    for other in db.list_model_candidates(c["capability"]):
        if other["status"] == "promoted":
            db.set_model_candidate_status(other["id"], "superseded")
    db.set_model_candidate_status(candidate_id, "promoted")
    db.record_model_decision(candidate_id, "promoted", _clean_reason(reason),
                             json.dumps({**_scores_for(candidate_id), "previous": previous}))
    return {"production": get_production(c["capability"]), "previous": previous,
            "default_engine_changed": engine_changed,
            "candidate": _candidate_out(db.get_model_candidate(candidate_id))}


def reject(candidate_id: int, reason: str = "") -> dict:
    reason = _clean_reason(reason)
    with _decision_lock:
        c = _require_candidate(candidate_id)
        if c["status"] != "candidate":
            raise ConflictError(f"This candidate is already {c['status']}.")
        db.set_model_candidate_status(candidate_id, "rejected")
        db.record_model_decision(candidate_id, "rejected", reason,
                                 json.dumps(_scores_for(candidate_id)))
        return {"candidate": _candidate_out(db.get_model_candidate(candidate_id))}


def list_decisions(capability: str = "translation") -> dict:
    _check_capability(capability)
    return {"decisions": [_decision_out(d) for d in db.list_model_decisions(capability)]}
