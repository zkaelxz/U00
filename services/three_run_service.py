"""
services/three_run_service.py -- "Run 3 times, review differences" in Review:
bookkeeping for translating the same lines three times through the ordinary
Translate run and letting the owner pick, line by line, among the runs that
disagree.

It starts no translation. The client runs the normal Translate run three
times (so glossary, style, context and the cost cap apply as always) and calls
capture_run after each; this module reads each run's English off the lines. The
snapshot taken at begin() is a History undo point; restore_original() is the review's own undo,
which puts back only the English of its lines. Lines where the
three runs agree (ignoring case and punctuation) keep what the last run wrote;
a differing line holds the last run's text until pick_runs() chooses.

The session lives in this process only: after a restart the lines hold the
last run's text and the snapshot is still in History, so nothing is lost but
the chance to pick. pick_runs writes only `en`, as a compare-and-set against
the text this module last wrote, so a line the owner edited meanwhile is
skipped and reported, never overwritten.
"""
import threading
import time
import unicodedata

import background_jobs
import db
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import drama_service, ownership_service

RUNS = 3
MAX_LINES = 5000
SNAPSHOT_LABEL = "before 3-run review"

_lock = threading.Lock()
# drama_id -> {"line_ids", "history_id", "started", "last_job_token", "original": {line id: English before
# the review}, "runs": [{line id: English}], "written": {line id: English}, "picks": {line id: run number}}
_sessions: dict = {}


def _core(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text or "").casefold() if c.isalnum())


def _differs(versions: list) -> bool:
    return len({_core(v) for v in versions}) > 1


def _session(drama_id: int) -> dict:
    session = _sessions.get(drama_id)
    if session is None:
        raise NotFoundError("No 3-run review is open for this title.")
    return session


def _refuse_if_busy(drama_id: int) -> None:
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A job is running on this title. Try again when it finishes.")


def begin(drama_id: int, line_ids=None) -> dict:
    """Snapshots the lines and opens a review over the lines with source text
    (limited to line_ids when given). One review per title: refused while one is open."""
    ownership_service.require_drama(drama_id)
    _refuse_if_busy(drama_id)
    if drama_id in _sessions:
        raise ConflictError("A 3-run review is already open for this title. Finish or close it first.")
    if line_ids is not None and (not isinstance(line_ids, list) or len(line_ids) > MAX_LINES):
        raise InvalidInputError(f"Choose up to {MAX_LINES} lines.")
    wanted = set(line_ids) if line_ids is not None else None
    lines = db.load_line_objects(drama_id)
    chosen = [ln for ln in lines if (ln.zh or "").strip() and (wanted is None or ln.id in wanted)]
    targets = [ln.id for ln in chosen]
    if not targets:
        raise InvalidInputError("None of these lines has source text to translate.")
    if len(targets) > MAX_LINES:
        raise InvalidInputError(f"Choose up to {MAX_LINES} lines; this covers {len(targets)}.")
    history_id = db.save_line_history_snapshot(drama_id, lines, SNAPSHOT_LABEL)
    with _lock:
        if drama_id in _sessions:
            raise ConflictError("A 3-run review is already open for this title. Finish or close it first.")
        _sessions[drama_id] = {"line_ids": targets, "history_id": history_id, "started": time.time(),
                               "last_job_token": None, "runs": [], "picks": {},
                               "original": {ln.id: ln.en or "" for ln in chosen},
                               "written": {ln.id: ln.en or "" for ln in chosen}}
    return {"line_ids": targets, "history_id": history_id, "runs_done": 0}


def capture_run(drama_id: int, run_number: int) -> dict:
    """Records the English the lines hold after the run that just finished. Only
    the next run in order, and only once the title's Translate run ended
    cleanly: a failed batch leaves the earlier text in place, which would
    otherwise read as agreement."""
    ownership_service.require_drama(drama_id)
    _refuse_if_busy(drama_id)
    with _lock:
        session = _session(drama_id)
        if run_number != len(session["runs"]) + 1 or run_number > RUNS:
            raise ConflictError(f"Run {len(session['runs']) + 1} is the next one to record.")
        job = background_jobs.get_status(f"translate_{drama_id}") or {}
        if job.get("status") != "done":
            raise ConflictError("The translation run did not finish. Run it again.")
        result = job.get("result") or {}
        if result.get("errors"):
            raise ConflictError("The translation run had errors on some lines. Run it again.")
        if result.get("cap_reached") is not None:
            raise ConflictError("The translation run stopped at its cost cap, so some lines were not redone. Run it again.")
        # The job record under this id is whichever run finished last; only one that ended after this
        # review began, and that is not the run already recorded, can be this run.
        token = job.get("finished_at")
        if token is None or token <= session["started"] or token == session["last_job_token"]:
            raise ConflictError("That is not a new translation run for this review. Run it again.")
        session["last_job_token"] = token
        current = {ln.id: ln.en or "" for ln in db.load_line_objects(drama_id)}
        session["runs"].append({lid: current.get(lid, "") for lid in session["line_ids"]})
        session["written"] = dict(session["runs"][-1])
        session["picks"] = {}
    return state(drama_id)


def state(drama_id: int) -> dict:
    """The review as the client shows it: runs recorded so far and, once all
    three are in, the differing lines with their versions."""
    ownership_service.require_drama(drama_id)
    with _lock:
        session = _sessions.get(drama_id)
        if session is None:
            return {"active": False, "runs_done": 0, "history_id": None, "line_count": 0, "items": []}
        runs = session["runs"]
        items = []
        if len(runs) == RUNS:
            by_id = {ln.id: ln for ln in db.load_line_objects(drama_id)}
            for lid in session["line_ids"]:
                versions = [r[lid] for r in runs]
                ln = by_id.get(lid)
                if ln is None or not _differs(versions):
                    continue
                items.append({"line_id": lid, "idx": ln.idx, "zh": ln.zh or "", "versions": versions,
                              "picked_run": session["picks"].get(lid),
                              "current_en": ln.en or ""})
        return {"active": True, "runs_done": len(runs), "history_id": session["history_id"],
                "line_count": len(session["line_ids"]), "items": items}


def pick_runs(drama_id: int, picks: list) -> dict:
    """Writes the chosen run's English for each {line_id, run}. Returns
    {applied: [line id], skipped: [line id]}; a skipped line was edited since
    the text this review last wrote, or was deleted."""
    ownership_service.require_drama(drama_id)
    _refuse_if_busy(drama_id)
    if not isinstance(picks, list) or not picks or len(picks) > MAX_LINES:
        raise InvalidInputError("Choose at least one line to pick.")
    with _lock:
        session = _session(drama_id)
        if len(session["runs"]) < RUNS:
            raise ConflictError("Record all three runs before picking.")
        writes, seen = [], set()
        for pick in picks:
            lid, run = pick.get("line_id"), pick.get("run")
            if lid in seen or lid not in session["written"] or run not in range(1, RUNS + 1):
                raise InvalidInputError("Each pick needs one line of this review and a run from 1 to 3.")
            seen.add(lid)
            writes.append((lid, {"en": session["runs"][run - 1][lid]}, {"en": session["written"][lid]}, run))
        missed = set(db.update_lines_fields_if_many(drama_id, [(lid, v, e) for lid, v, e, _r in writes]))
        applied = []
        for lid, values, _e, run in writes:
            if lid not in missed:
                session["written"][lid] = values["en"]
                session["picks"][lid] = run
                applied.append(lid)
    return {"applied": applied, "skipped": sorted(missed)}


def keep_run_for_rest(drama_id: int, run: int) -> dict:
    """pick_runs for every differing line not picked yet."""
    todo = [{"line_id": it["line_id"], "run": run}
            for it in state(drama_id)["items"] if it["picked_run"] is None]
    if not todo:
        return {"applied": [], "skipped": []}
    return pick_runs(drama_id, todo)


def restore_original(drama_id: int) -> dict:
    """Puts back the English the review's lines had before it, as `en` only (timing, speakers,
    flags and added or deleted lines are never touched, so nothing outside the review is undone).
    A line is restored only while it holds the original or one of the runs' text; a line edited
    to something else since is skipped and reported. The review stays open."""
    ownership_service.require_drama(drama_id)
    _refuse_if_busy(drama_id)
    with _lock:
        session = _session(drama_id)
        original = session["original"]
        known = {lid: {original[lid], session["written"][lid], *(r[lid] for r in session["runs"])}
                 for lid in session["line_ids"]}
        current = {ln.id: ln.en or "" for ln in db.load_line_objects(drama_id)}
        writes, skipped = [], []
        for lid in session["line_ids"]:
            if lid not in current:
                skipped.append(lid)
            elif current[lid] == original[lid]:
                continue
            elif current[lid] in known[lid]:
                writes.append((lid, {"en": original[lid]}, {"en": current[lid]}))
            else:
                skipped.append(lid)
        missed = db.update_lines_fields_if_many(drama_id, writes)
        restored = [lid for lid, _v, _e in writes if lid not in set(missed)]
        for lid in restored:
            session["written"][lid] = original[lid]
            session["picks"].pop(lid, None)
    return {"applied": restored, "skipped": sorted(set(skipped) | set(missed))}


def end(drama_id: int) -> None:
    ownership_service.require_drama(drama_id)
    with _lock:
        _sessions.pop(drama_id, None)
