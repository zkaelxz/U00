"""
services/benchmark_sweep_core.py -- what the Benchmark Lab's two settings sweeps share: the split
by title, coordinate descent, skipping finished passes, the job body, the hold-out recommendation
and the owner's decision. UI-free.

benchmark_sweep_service (transcription) and benchmark_translate_sweep_service (translation) each
hold their knob lists, cost model and a plan object; the method itself is described in
benchmark_sweep_service's docstring. A plan provides: stage, set_name, language, default, models,
knobs, markers, tuning, holdout, and the methods describe(setup), defaults_of(setup),
ignored_options(setup), config(setup), key(setup), identity(setup) and cases(half).
"""
import datetime
import hashlib
import json
import sqlite3
import threading

import background_jobs
import db
from core import SOURCE_LANGUAGES
from jobs import job_launch
from services import benchmark_lab_service as lab
from services.benchmark_case_service import redact
from services.benchmark_runs_service import session_out, setup_key
from services.service_errors import ConflictError, InvalidInputError, UnsupportedOperationError

JOB_ID = lab.JOB_ID
HOLDOUT_SHARE = 0.3
# The adoption margin is at least this many times the measured noise.
NOISE_FACTOR = 2
# Below these the plan calls a result too small to decide on; it is still shown.
MIN_TAG_CASES = 15
MIN_SET_CASES = 50


class Stopped(Exception):
    """A pass was cancelled: the sweep stops and can be resumed."""


class PassFailed(Exception):
    """A pass ended without finishing (failed or stopped by a cap): the sweep stops on it."""


def now() -> str:
    return datetime.datetime.utcnow().isoformat()


class SweepRecord:
    """A sweep's own record (set, language, status, recommendation, decision), stored as one app
    setting. `active` holds the id of the sweep this process started and has not seen end: the
    Lab's job id is shared with plain Lab runs, so the job table alone can't tell whether a stored
    queued/running sweep is live."""

    def __init__(self, key: str):
        self.key = key
        self.lock = threading.Lock()
        self.active = {"id": None}

    def read(self) -> dict:
        try:
            value = json.loads(db.get_app_setting(self.key) or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def save(self, record: dict):
        db.set_app_setting(self.key, json.dumps(record))

    def update(self, **fields):
        with self.lock:
            record = self.read()
            record.update(fields)
            self.save(record)


def get_sweep(store: SweepRecord, applies) -> dict:
    """The latest sweep, with "interrupted" for one the app stopped during. applies(record, winner)
    lists what approving would write, for a winner still waiting on a decision."""
    record = store.read()
    if not record:
        return {"sweep": None}
    if record.get("status") in ("queued", "running"):
        job = background_jobs.get_status(JOB_ID) or {}
        if store.active["id"] != record.get("id"):
            record = dict(record, status="interrupted",
                          note="The app stopped during the sweep. Start it again to resume.")
        elif job.get("status") == "cancelled" and record.get("status") == "queued":
            # Cancelled while it waited in the GPU queue: the job never ran, so nothing updated the record.
            record = dict(record, status="cancelled", note="Stopped before the first pass started.")
        elif job.get("status") not in ("queued", "running"):
            record = dict(record, status="interrupted",
                          note="The sweep's job ended without finishing. Start it again to resume.")
    rec = record.get("recommendation") or {}
    if record.get("status") == "done" and rec.get("state") == "adopt" and not record.get("decision"):
        # Read now, not at the sweep's end: the profile takes the settings as they are when approved.
        # A stored winner the profile rules no longer accept (a setting removed since) shows no list
        # rather than breaking the page; approving it is refused with the reason.
        try:
            pending = applies(record, rec["winner"])
        except InvalidInputError:
            pending = None
        record = dict(record, applies=pending)
    return {"sweep": record}


# ---------------------------------------------------------------------------
# What a sweep runs
# ---------------------------------------------------------------------------

def check_set_and_language(set_name, language) -> str:
    if not isinstance(set_name, str) or not set_name.strip() or len(set_name) > lab.MAX_SET_NAME_CHARS:
        raise InvalidInputError("Pick a golden set.")
    if language not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"language must be one of {', '.join(SOURCE_LANGUAGES)}.")
    return set_name.strip()


def title_of(case: dict) -> str:
    return (case.get("title") or "").strip()


def split_by_title(cases: list) -> tuple:
    """(tuning, holdout): whole titles in each half, the hold-out as close to 30% of the clips as
    whole titles allow. Titles are taken in a fixed order (a hash of the name), so the same set
    always splits the same way and a resumed sweep keeps its halves."""
    groups = {}
    for c in cases:
        groups.setdefault(title_of(c), []).append(c)
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
    tuning = [c for c in cases if title_of(c) not in held]
    holdout = [c for c in cases if title_of(c) in held]
    return tuning, holdout


def is_small_set(plan) -> bool:
    return len(plan.tuning) + len(plan.holdout) < MIN_SET_CASES


# ---------------------------------------------------------------------------
# Coordinate descent, shared by the estimate and the job
# ---------------------------------------------------------------------------

def descend(plan, measure) -> dict:
    """Walks the sweep. measure(setup, half, repeat, step) returns the finished run's
    benchmark_sessions row, or None for a pass not run yet (the estimate). Unmeasured passes
    never win, so the estimate walks the passes a sweep that keeps the default would run."""
    steps = []

    def score(run):
        return None if run is None else run.get("aggregate_score")

    def record(setup, half, run, step):
        steps.append({**step, "half": half, "setup": plan.describe(setup),
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
        kept = plan.describe(best) if best is not None else None
        for st in steps:
            if st["step"] == candidates[0][1]["step"]:
                st["kept"] = st["setup"] == kept
        if best is not None:
            current, current_score = best, best_score

    pick([({"engine": m["engine"], "model": m["model"], "changes": {}},
           {"step": "model", "value": m["label"]})
          for m in plan.models if m["included"] and m["model"] != plan.default["model"]])
    for knob in plan.knobs:
        if not knob["included"] or knob["key"] in plan.ignored_options(current):
            continue
        defaults = plan.defaults_of(current)
        now_value = current["changes"].get(knob["key"], defaults[knob["key"]])
        candidates = []
        for value in knob["values"]:
            if value == now_value:
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


def finished_runs(plan, setup: dict, half: str, sessions: list) -> list:
    """Finished runs of this setup that scored exactly these cases at their current reference
    versions under the plan's markers (scoring rules, app version), oldest first: the passes a
    sweep can skip."""
    key = plan.key(setup)
    identity = plan.identity(setup)
    want = {c["id"]: c.get("ref_version") or 1 for c in plan.cases(half)}
    out = []
    for s in sorted(sessions, key=lambda r: r["id"]):
        if s.get("status") != "done" or s.get("stage") != plan.stage or setup_key(s) != key:
            continue
        ctx = session_out(s)["context_settings"]
        if any(ctx.get(k) != v for k, v in plan.markers.items()):
            continue
        # A re-pinned model or a replaced model folder is a different model under the same name.
        if ctx.get("model_identity") != identity:
            continue
        # A pass with failed cases (a model that ran out of memory, say) is not a measurement.
        if s.get("error_count"):
            continue
        results = db.list_benchmark_results(s["id"])
        if len(results) == len(want) and {r["case_id"]: r.get("ref_version") or 1 for r in results} == want:
            out.append(s)
    return out


def passes_left(plan) -> tuple:
    """(passes still to run as (setup, half), passes already finished) for the estimate."""
    sessions = db.list_benchmark_sessions(10_000, plan.stage)
    todo, done = [], 0

    def measure(setup, half, repeat, step):
        nonlocal done
        found = finished_runs(plan, setup, half, sessions)
        if len(found) > repeat:
            done += 1
            return found[repeat]
        todo.append((setup, half))
        return None

    outcome = descend(plan, measure)
    if outcome["holdout"] is None:
        # A winner is only known once the tuning passes ran: allow for its two hold-out passes.
        todo.extend([(outcome["winner"], "holdout"), (plan.default, "holdout")])
    return todo, done


# ---------------------------------------------------------------------------
# Starting and running
# ---------------------------------------------------------------------------

def launch(store: SweepRecord, record: dict, start_job) -> dict:
    """Starts the sweep's job with start_job() and stores its record. Passes that already have a
    finished run are skipped, so starting a stopped sweep again picks up where it stopped."""
    if lab._job_active():
        raise ConflictError("A benchmark run is already going.")
    if job_launch.exclusive_active() or job_launch.maintenance_active():
        raise ConflictError("A library restore or cleanup is running; try again when it finishes.")
    # Held across the start so the job's first record update waits for this record, and a start
    # that loses the race leaves the stored sweep (and any recommendation waiting on a decision) alone.
    with store.lock:
        if not start_job():
            raise ConflictError("A benchmark run is already going.")
        store.active["id"] = record["id"]
        store.save(record)
    return {"job_id": JOB_ID, "sweep": record}


def run_sweep(job_id, store: SweepRecord, planned: int, prepare, recommend, *, reject=None,
              cleanup=None):
    """The sweep's job body. prepare() returns (plan, run_pass), where run_pass(setup, half, step,
    created, done, planned) makes one Lab run and returns its session id. reject(plan, setup, half,
    run), when given, names why a finished pass can't be scored, or returns None. cleanup() runs
    however the job ends."""
    created = []
    store.update(status="running")
    try:
        plan, run_pass = prepare()
        sessions = db.list_benchmark_sessions(10_000, plan.stage)
        counts = {"run": 0, "reused": 0}

        def measure(setup, half, repeat, step):
            found = finished_runs(plan, setup, half, sessions)
            if len(found) > repeat:
                counts["reused"] += 1
                return found[repeat]
            if background_jobs.is_cancel_requested(job_id):
                raise Stopped()
            sid = run_pass(setup, half, step, created, counts["run"], max(planned, 1))
            run = db.get_benchmark_session(sid) or {}
            if run.get("status") == "cancelled" or background_jobs.is_cancel_requested(job_id):
                raise Stopped()
            if run.get("status") != "done":
                raise PassFailed(f"The pass {plan.describe(setup)} ({half}) did not finish"
                                 + (f": {run['note']}" if run.get("note") else "."))
            problem = reject(plan, setup, half, run) if reject else None
            if problem:
                raise PassFailed(problem)
            counts["run"] += 1
            sessions.append(run)
            store.update(passes_run=counts["run"], passes_reused=counts["reused"])
            return run

        outcome = descend(plan, measure)
        store.update(status="done", finished_at=now(), passes_run=counts["run"],
                     passes_reused=counts["reused"], recommendation=recommend(plan, outcome))
    except Stopped:
        store.update(status="cancelled", finished_at=now(),
                     note="Stopped. Start the sweep again to resume: finished passes are kept.")
    except PassFailed as exc:
        store.update(status="failed", finished_at=now(),
                     note=redact(f"{exc} Finished passes are kept; fix the cause, then start the sweep again "
                                 "to retry it.")[:300])
    except Exception as exc:
        store.update(status="failed", finished_at=now(), note=redact(str(exc))[:300])
        raise
    finally:
        if cleanup:
            cleanup()
        store.active["id"] = None
        for sid in created:
            s = db.get_benchmark_session(sid)
            if s and s.get("status") in ("queued", "running"):
                db.update_benchmark_session(sid, status="failed", finished_at=now(),
                                            note="The run stopped unexpectedly.")
        background_jobs.set_result(job_id, {"session_ids": created, "sweep": True})
    background_jobs.update_progress(job_id, 1.0, "Sweep finished.")


def run_pass(job_id, plan, setup, half, step, created, done, planned, use_gpu, api_key=None,
             job_cap=None) -> int:
    """One pass as an ordinary Lab run under the sweep's job; returns its session id."""
    cfg = plan.config(setup)
    cases = plan.cases(half)
    what = plan.describe(setup)
    sid = lab.create_session(
        plan.stage, cfg, cases, use_gpu, label=f"Sweep {plan.set_name}: {what}"[:lab.MAX_LABEL_CHARS],
        case_filter={"tier": None, "set_name": plan.set_name, "case_ids": sorted(c["id"] for c in cases)},
        extra_context={"sweep": {"half": half, "step": step["step"]}})
    created.append(sid)

    def progress(frac, message):
        background_jobs.update_progress(
            job_id, min(0.99, (done + frac) / planned),
            f"Sweep pass {done + 1} of about {planned} ({what}, {half}): {message}")

    lab._run_plan(job_id, plan.stage, [(sid, cfg, api_key)], [c["id"] for c in cases], use_gpu, job_cap,
                  progress=progress)
    return sid


# ---------------------------------------------------------------------------
# The recommendation and the owner's decision
# ---------------------------------------------------------------------------

def tag_breakdown(cases: list, base_run: dict, win_run: dict) -> list:
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


def recommend(plan, outcome: dict, min_margin: float, margin_rule, unit: str, savable=None) -> dict:
    """The recommendation on a finished descent. margin_rule(noise, margin) words the rule; unit
    names what failed ("clips", "cases"); savable(winner), when given, is False for a winner a
    profile would not change anything for, which is then not offered for approval."""
    noise = outcome["noise"] or 0.0
    margin = max(min_margin, NOISE_FACTOR * noise)
    winner = outcome["winner"]
    out = {"noise": outcome["noise"], "margin": margin, "margin_rule": margin_rule(noise, margin),
           "default": {**plan.default, "label": plan.describe(plan.default)},
           "winner": {**winner, "label": plan.describe(winner)},
           "tuning": {"default_score": outcome["default_score"], "winner_score": outcome["winner_score"]},
           "holdout": None, "tags": [], "steps": outcome["steps"],
           "case_counts": {"tuning": len(plan.tuning), "holdout": len(plan.holdout)},
           "small_set": is_small_set(plan)}
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
    out["tags"] = tag_breakdown(plan.holdout, base, win)
    if delta is not None and delta >= margin and not more_errors and (savable is None or savable(winner)):
        return {**out, "state": "adopt",
                "text": "The winner also beats the default on the held-out titles by the margin. "
                        "Approve to save it as a setting profile."}
    why = f"more {unit} failed" if more_errors else "the gain was smaller than the margin"
    return {**out, "state": "not_confirmed",
            "text": f"The winner did not hold up on the held-out titles ({why}). Keep the default."}


def decide(store: SweepRecord, decision: str, reason, *, applies, save_profile, capability: str,
           source: str) -> dict:
    """The owner's call on the finished sweep's recommendation. "approve" (only for a
    recommendation that held up on the hold-out) saves the winner as a setting profile through
    save_profile(name, winner), which a title gets only when it is picked; "reject" keeps the
    default. Both are recorded in model_decisions with the scores they were made on."""
    if decision not in ("approve", "reject"):
        raise InvalidInputError("decision must be approve or reject.")
    if reason is not None and not isinstance(reason, str):
        raise InvalidInputError("reason must be text.")
    reason = (reason or "").strip()[:300]
    with store.lock:
        record = store.read()
        rec = record.get("recommendation") if record.get("status") == "done" else None
        if not rec or rec.get("state") == "keep_default":
            raise UnsupportedOperationError("There is no sweep winner to decide on.")
        if record.get("decision"):
            raise ConflictError(f"This sweep's winner was already {record['decision']['decision']}.")
        if decision == "approve" and rec["state"] != "adopt":
            raise UnsupportedOperationError("The winner did not hold up on the held-out titles, so it can't be approved.")
        winner = rec["winner"]
        profile = None
        listed = applies(record, winner) if decision == "approve" else None
        if decision == "approve":
            name = f"Sweep {record['set_name']} ({record['language']}) {now()[:16].replace('T', ' ')}"[:100]
            try:
                saved = save_profile(name, winner)
            except ConflictError:
                raise ConflictError("A profile with this name already exists; try again in a minute.") from None
            profile = {"id": saved["preset"]["id"], "name": name}
        candidate_id = _decision_candidate(capability, source, record["language"], winner)
        verdict = "promoted" if decision == "approve" else "rejected"
        scores = {k: rec.get(k) for k in ("noise", "margin", "tuning", "holdout", "tags", "case_counts")}
        # The decision route is PC only, so whoever decided is the owner sitting at this PC.
        decided_by = "owner at this PC"
        db.record_model_decision(candidate_id, verdict, reason, json.dumps({
            **scores, "sweep_id": record.get("id"), "set_name": record["set_name"],
            "language": record["language"], "decided_by": decided_by, "profile": profile, "applies": listed}))
        db.set_model_candidate_status(candidate_id, verdict)
        record["decision"] = {"decision": verdict, "reason": reason, "decided_at": now(),
                              "decided_by": decided_by, "profile": profile, "applies": listed}
        store.save(record)
    return {"sweep": record}


def _decision_candidate(capability: str, source: str, language: str, winner: dict) -> int:
    """The model_candidates row a decision hangs on: one per language and setup, so a setup decided
    before keeps its history and a later decision on it is added beside the earlier one."""
    model = json.dumps({"language": language, "model": winner["model"], "changes": winner["changes"]},
                       sort_keys=True)
    found = db.find_model_candidate(capability, winner["engine"], model)
    if found:
        return found["id"]
    try:
        return db.create_model_candidate(capability, winner["engine"], model, source)
    except sqlite3.IntegrityError:
        return db.find_model_candidate(capability, winner["engine"], model)["id"]


def list_decisions(capability: str, describe) -> dict:
    """Earlier sweep decisions, newest first. describe(engine, setup) names the decided setup from
    its stored {"model", "changes"}."""
    out = []
    for d in db.list_model_decisions(capability):
        c = db.get_model_candidate(d["candidate_id"]) or {}
        try:
            setup = json.loads(c.get("model") or "{}")
        except ValueError:
            setup = {}
        out.append({"decision": d["decision"], "reason": d.get("reason") or "", "decided_at": d.get("decided_at"),
                    "language": setup.get("language"), "setup": describe(c.get("engine"), setup)})
    return {"decisions": out}
