"""
services/glossary_name_report_service.py -- the "name check across all titles" report.

The per-title scan (glossary_variants_service.discover_names) finds names that recur
under several spellings inside one title. The same streamer is often heard or rendered
differently from one title to the next, so this runs that scan over every title the
caller may see and merges the clusters that are one name into a single group, with
per-title counts, a few short example lines and what each title's glossary already says.

The scan is read-only and bounded: a cap on titles per run, on groups, on examples and
on the size of the answer, plus a time budget. It runs as a cancellable background job,
one at a time server-wide, and each person's result is private to them (the job id
carries their user id and the title list is filtered to what they could see when they
started it).

The owner's marks ("right", "wrong", "skip") are kept per person in app_settings, keyed
by the name so a group stays resolved on later scans. Marking changes no glossary: a
group marked right is applied by the UI through the normal term routes. No FastAPI import.
"""
import json
import logging
import threading
import time
from typing import Callable, Dict, List, Optional

import background_jobs
import db
import glossary_phonetic as ph
from services import glossary_service, ownership_service
from services import glossary_variants_service as gv
from services.service_errors import ConflictError, InvalidInputError

log = logging.getLogger(__name__)

# --- Tunables (the one place for the numbers) -------------------------------
MAX_TITLES_SCANNED = 60       # newest first; each title is itself capped by the per-title scan
MAX_SCAN_SECONDS = 240        # a slow disk or a huge library stops here and says so
MAX_GROUPS = 150              # more than a person works through in one sitting
MAX_GROUP_TITLES = 20         # titles listed per group; the real number is `titles_total`
MAX_GROUP_VARIANTS = 12
MAX_GROUP_EXAMPLES = 3
MAX_TOTAL_EXAMPLES = 300
MAX_SNIPPET_CHARS = 80
MAX_TITLE_CHARS = 120
MAX_RESPONSE_BYTES = 250_000
MAX_MERGE_COMPARISONS = 40_000   # sound-alike checks between groups; exact matches never need them
MAX_REASONS = 5
MAX_MARKS = 2000              # per person; each mark is a few short strings
MAX_KEY_CHARS = 80

JOB_PREFIX = "glossary_name_report_"
MARKS = ("right", "wrong", "skip")
FAILED_MESSAGE = "The name check failed. Nothing was changed; try again."
IDLE = {"job_id": "", "status": "idle", "progress": 0.0, "message": "", "result": None}

# One scan at a time: the check and the start must not interleave, or two people
# starting together would both pass the "nothing running" test.
_start_lock = threading.Lock()
# Marks are a read-modify-write of one JSON setting.
_marks_lock = threading.Lock()


def _noop(*_args) -> None:
    return None


def _who(principal) -> str:
    uid = ownership_service.user_id(principal)
    return "pc" if uid is None else str(uid)


def job_id_for(principal) -> str:
    return f"{JOB_PREFIX}{_who(principal)}"


# --- The scan ------------------------------------------------------------------

def _title_label(drama: dict) -> str:
    label = (drama.get("title_en") or drama.get("title_zh") or f"Title {drama['id']}").strip()
    return label[:MAX_TITLE_CHARS]


def _known_forms(drama_id: int) -> Dict[str, tuple]:
    """normalised term text or alias -> (term_original, English) for the title's glossary."""
    known: Dict[str, tuple] = {}
    for term in glossary_service.list_glossary_terms(drama_id):
        for text in [term["term_original"], *term["aliases"]]:
            known.setdefault(gv._norm(text), (term["term_original"], term.get("term_translation") or ""))
    return known


def _record(drama: dict, cluster: dict) -> Optional[dict]:
    """One title's cluster in the shape the merge works on, or None when it has no sound."""
    language = gv._language(drama)
    spellings: Dict[str, list] = {}
    for s in cluster["spellings"]:
        entry = spellings.setdefault(gv._norm(s["text"]), [s["text"], 0])
        entry[1] += s["count"]
    if not spellings:
        return None
    canonical = cluster["suggested_original"]
    try:
        key = ph.phonetic_key(canonical, language)
    except ph.PhoneticUnavailable:
        key = ""
    return {"drama_id": drama["id"], "title": _title_label(drama), "language": language,
            "spellings": spellings, "total": sum(v[1] for v in spellings.values()),
            "canonical": canonical, "key": key, "examples": list(cluster.get("examples") or [])}


def _new_group(rec: dict) -> dict:
    return {"language": rec["language"], "key": rec["key"], "variants": {}, "titles": {},
            "examples": {}, "total": 0}


def _add(group: dict, rec: dict) -> None:
    for norm, (text, count) in rec["spellings"].items():
        entry = group["variants"].setdefault(norm, [text, 0])
        entry[1] += count
    title = group["titles"].setdefault(rec["drama_id"], {"title": rec["title"], "count": 0})
    title["count"] += rec["total"]
    group["examples"].setdefault(rec["drama_id"], []).extend(rec["examples"])
    group["total"] += rec["total"]


def _absorb(into: dict, other: dict) -> None:
    for norm, (text, count) in other["variants"].items():
        entry = into["variants"].setdefault(norm, [text, 0])
        entry[1] += count
    for did, title in other["titles"].items():
        mine = into["titles"].setdefault(did, {"title": title["title"], "count": 0})
        mine["count"] += title["count"]
    for did, examples in other["examples"].items():
        into["examples"].setdefault(did, []).extend(examples)
    into["total"] += other["total"]


def _merge(records: List[dict]):
    """Groups of clusters that are one name. Two clusters are one name when they share a
    spelling, have the same sound key, or (within a comparison budget) sound alike in the
    same language. Returns (groups, ran_out_of_budget)."""
    groups: List[dict] = []
    by_norm: Dict[str, dict] = {}
    by_key: Dict[tuple, dict] = {}
    comparisons = 0
    spent = False
    for rec in sorted(records, key=lambda r: (-r["total"], r["canonical"])):
        shared = {id(by_norm[n]): by_norm[n] for n in rec["spellings"] if n in by_norm}
        target = max(shared.values(), key=lambda g: g["total"]) if shared else None
        if target is None and rec["key"]:
            target = by_key.get((rec["language"], rec["key"]))
        if target is None and rec["key"] and not spent:
            for g in groups:
                if g["language"] != rec["language"] or not g["key"]:
                    continue
                comparisons += 1
                if comparisons > MAX_MERGE_COMPARISONS:
                    spent = True
                    break
                if ph.similar(rec["key"], g["key"], rec["language"]):
                    target = g
                    break
        if target is None:
            target = _new_group(rec)
            groups.append(target)
        for other in shared.values():
            if other is not target:
                _absorb(target, other)
                groups[:] = [g for g in groups if g is not other]
                for norm in other["variants"]:
                    by_norm[norm] = target
                for key, held in list(by_key.items()):
                    if held is other:
                        by_key[key] = target
        _add(target, rec)
        for norm in target["variants"]:
            by_norm[norm] = target
        if rec["key"]:
            by_key.setdefault((rec["language"], rec["key"]), target)
    return groups, spent


def _pick_examples(group: dict) -> List[dict]:
    """Up to MAX_GROUP_EXAMPLES lines, one per title first so they show the name in
    different places rather than three lines from one episode."""
    order = sorted(group["titles"], key=lambda did: (-group["titles"][did]["count"], did))
    queues = [list(group["examples"].get(did, [])) for did in order]
    out: List[dict] = []
    while len(out) < MAX_GROUP_EXAMPLES and any(queues):
        for did, queue in zip(order, queues):
            if queue and len(out) < MAX_GROUP_EXAMPLES:
                ex = queue.pop(0)
                snippet = " ".join(str(ex["snippet"]).split())
                if len(snippet) > MAX_SNIPPET_CHARS:
                    snippet = snippet[:MAX_SNIPPET_CHARS - 1] + "…"
                out.append({"drama_id": did, "line_idx": ex["line_idx"], "snippet": snippet})
    return out


def _finalize(group: dict, known_by_title: Dict[int, dict]) -> dict:
    variants = sorted(group["variants"].values(), key=lambda v: (-v[1], v[0]))
    in_glossary = {gv._norm(v[0]) for v in variants
                   if any(gv._norm(v[0]) in known for known in known_by_title.values())}
    canonical = next((v[0] for v in variants if gv._norm(v[0]) in in_glossary), variants[0][0])
    titles = []
    for did, title in group["titles"].items():
        known = known_by_title.get(did, {})
        match = next((known[gv._norm(v[0])] for v in variants if gv._norm(v[0]) in known), None)
        titles.append({"drama_id": did, "title": title["title"], "count": title["count"],
                       "in_glossary": match is not None,
                       "glossary_term": match[0] if match else None,
                       "glossary_english": (match[1] or None) if match else None})
    titles.sort(key=lambda t: (-t["count"], t["title"], t["drama_id"]))
    return {"key": gv._norm(canonical), "canonical": canonical, "total": group["total"],
            "variants": [{"text": t, "count": c} for t, c in variants[:MAX_GROUP_VARIANTS]],
            "titles_total": len(titles), "titles": titles[:MAX_GROUP_TITLES],
            "examples": _pick_examples(group)}


def scan_titles(visible_to, check: Callable[[], None] = _noop,
                progress: Callable[[float, str], None] = _noop) -> dict:
    """Runs the per-title name scan over the titles `visible_to` may see (None: all) and
    merges the results. Read-only. `check` is called between titles and may raise to stop
    (a cancelled job); `progress(fraction, message)` reports how far along it is."""
    dramas = db.list_dramas(visible_to=visible_to)
    batch = dramas[:MAX_TITLES_SCANNED]
    truncated = len(dramas) > len(batch)
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    records: List[dict] = []
    known_by_title: Dict[int, dict] = {}
    unavailable = failed = scanned = 0
    reasons: List[str] = []
    for i, drama in enumerate(batch):
        check()
        if time.monotonic() >= deadline:
            truncated = True
            break
        scanned += 1
        progress(0.05 + 0.85 * i / max(1, len(batch)), f"Checking title {i + 1} of {len(batch)}...")
        try:
            found = gv.discover_names(drama["id"])
            known_by_title[drama["id"]] = _known_forms(drama["id"])
        except Exception:
            # One unreadable title must not stop the others; its error text can name a
            # file, so only the count leaves the server.
            log.exception("name report: title %s skipped", drama["id"])
            failed += 1
            continue
        if not found["available"]:
            unavailable += 1
            reason = str(found.get("reason") or "")[:200]
            if reason and reason not in reasons and len(reasons) < MAX_REASONS:
                reasons.append(reason)
            continue
        truncated = truncated or bool(found.get("truncated"))
        for cluster in [*found["clusters"], *found.get("known_vtuber_names", [])]:
            rec = _record(drama, cluster)
            if rec:
                records.append(rec)
    progress(0.92, "Grouping names...")
    check()
    merged, spent = _merge(records)
    truncated = truncated or spent
    groups = [_finalize(g, known_by_title) for g in merged]
    kept = [g for g in groups if len(g["variants"]) > 1 or g["titles_total"] > 1]
    singletons = len(groups) - len(kept)
    kept.sort(key=lambda g: (-g["titles_total"], -len(g["variants"]), -g["total"], g["key"]))
    groups_omitted = max(0, len(kept) - MAX_GROUPS)
    kept = kept[:MAX_GROUPS]
    remaining = MAX_TOTAL_EXAMPLES
    for g in kept:
        g["examples"] = g["examples"][:remaining]
        remaining -= len(g["examples"])
    within, used = [], 0
    for g in kept:
        size = len(json.dumps(g))
        if used + size > MAX_RESPONSE_BYTES:
            break
        within.append(g)
        used += size
    groups_omitted += len(kept) - len(within)
    return {"titles_total": len(dramas), "titles_scanned": scanned, "titles_unavailable": unavailable,
            "titles_failed": failed, "reasons": reasons,
            "truncated": truncated or groups_omitted > 0, "singletons_omitted": singletons,
            "groups_omitted": groups_omitted, "groups": within}


# --- The job -------------------------------------------------------------------

def _run(job_id: str, visible_to) -> None:
    def check() -> None:
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled()

    try:
        result = scan_titles(visible_to, check, lambda frac, msg: background_jobs.update_progress(job_id, frac, msg))
    except background_jobs.JobCancelled:
        raise
    except Exception:
        log.exception("name report scan failed")
        raise RuntimeError(FAILED_MESSAGE) from None
    background_jobs.set_result(job_id, result)


def start_scan(principal) -> dict:
    """Starts the scan for `principal` (ConflictError when any scan is already running).
    Poll get_report."""
    job_id = job_id_for(principal)
    visible_to = ownership_service.visible_to_filter(principal)
    with _start_lock:
        if background_jobs.count_active_jobs(JOB_PREFIX):
            raise ConflictError("A name check is already running.")
        if not background_jobs.start_job(job_id, _run, job_id, visible_to,
                                         description="Name check across titles"):
            raise ConflictError("A name check can't start right now.")
    return {"job_id": job_id}


def cancel_scan(principal) -> dict:
    job_id = job_id_for(principal)
    job = background_jobs.get_status(job_id)
    if not job or job.get("status") not in ("running", "queued"):
        raise ConflictError("No name check is running.")
    background_jobs.request_cancel(job_id)
    return {"job_id": job_id, "cancel_requested": True, "status": job["status"]}


def get_report(principal) -> dict:
    """{job_id, status, progress, message, result}; the result (with the caller's marks
    applied) only once the scan is done. "idle" before any scan in this process: a finished
    report is held in memory, so a restart asks for a new scan."""
    job_id = job_id_for(principal)
    job = background_jobs.get_status(job_id)
    if not job:
        return dict(IDLE)
    status = job.get("status")
    result = None
    if status == "done" and job.get("result"):
        result = apply_marks(principal, job["result"])
    # The job's own error text can carry a file name, so an error shows a fixed message.
    message = FAILED_MESSAGE if status == "error" else (job.get("message") or "")
    return {"job_id": job_id, "status": status, "progress": job.get("progress") or 0.0,
            "message": message, "result": result}


def _held_result(principal) -> dict:
    job = background_jobs.get_status(job_id_for(principal))
    if not job or job.get("status") != "done" or not job.get("result"):
        raise ConflictError("Run the name check first.")
    return job["result"]


# --- Marks -----------------------------------------------------------------------

def _marks_setting(principal) -> str:
    return f"glossary.name_marks.{_who(principal)}"


def _load_marks(principal) -> dict:
    stored = db.get_app_setting(_marks_setting(principal), {})
    return stored if isinstance(stored, dict) else {}


def _group_norms(group: dict) -> set:
    return {group["key"], *(gv._norm(v["text"]) for v in group["variants"])}


def _mark_index(marks: dict) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for key, entry in sorted(marks.items()):
        if isinstance(entry, dict) and entry.get("mark") in MARKS:
            for norm in [key, *(entry.get("keys") or [])]:
                index.setdefault(norm, entry["mark"])
    return index


def apply_marks(principal, result: dict) -> dict:
    """A copy of `result` whose groups carry the person's mark (or None) and a `counts`
    summary. A group is resolved when any of its spellings was part of a marked group, so
    a mark outlives a later scan that picks another spelling as the canonical one."""
    index = _mark_index(_load_marks(principal))
    groups = []
    counts = {"unresolved": 0, "right": 0, "wrong": 0, "skip": 0}
    for g in result["groups"]:
        mark = index.get(g["key"]) or next((index[n] for n in sorted(_group_norms(g)) if n in index), None)
        counts[mark or "unresolved"] += 1
        groups.append({**g, "mark": mark})
    return {**result, "groups": groups, "counts": counts}


def set_mark(principal, key, mark, result: Optional[dict] = None) -> dict:
    """Marks the group `key` of the caller's held report "right", "wrong" or "skip", or
    removes its mark with "clear". Stored per person; changes nothing else."""
    if mark not in (*MARKS, "clear"):
        raise InvalidInputError("mark must be right, wrong, skip or clear.")
    if not isinstance(key, str) or not key or len(key) > MAX_KEY_CHARS:
        raise InvalidInputError("That name is not in the current report.")
    result = result if result is not None else _held_result(principal)
    group = next((g for g in result["groups"] if g["key"] == key), None)
    with _marks_lock:
        marks = _load_marks(principal)
        if mark == "clear":
            norms = _group_norms(group) if group else {key}
            for k in [k for k, e in marks.items()
                      if k in norms or (isinstance(e, dict) and norms & set(e.get("keys") or []))]:
                del marks[k]
        else:
            if group is None:
                raise InvalidInputError("That name is not in the current report.")
            norms = _group_norms(group)
            for k in [k for k, e in marks.items() if k != key and (
                    k in norms or (isinstance(e, dict) and norms & set(e.get("keys") or [])))]:
                del marks[k]   # one mark per name: a regrouped scan must not leave two
            if key not in marks and len(marks) >= MAX_MARKS:
                raise ConflictError("Too many marked names. Clear some first.")
            marks[key] = {"mark": mark, "keys": sorted(norms)}
        db.set_app_setting(_marks_setting(principal), marks)
    return {"key": key, "mark": None if mark == "clear" else mark}


# --- Export ----------------------------------------------------------------------

def export_report(principal) -> str:
    return export_markdown(apply_marks(principal, _held_result(principal)))


def export_markdown(result: dict) -> str:
    """The unresolved groups as plain text to paste elsewhere: names, counts and example
    lines only (no title names, ids or paths)."""
    open_groups = [g for g in result["groups"] if g.get("mark") is None]
    lines = ["# Name check across titles", ""]
    if not open_groups:
        lines.append("No unresolved names.")
        return "\n".join(lines) + "\n"
    lines.append(f"{len(open_groups)} unresolved name{'' if len(open_groups) == 1 else 's'} "
                 f"from {result['titles_scanned']} of {result['titles_total']} titles.")
    for n, g in enumerate(open_groups, 1):
        spellings = ", ".join(f"{v['text']} ×{v['count']}" for v in g["variants"])
        lines += ["", f"## {n}. {g['canonical']}",
                  f"- In {g['titles_total']} title{'' if g['titles_total'] == 1 else 's'}, {g['total']} mentions",
                  f"- Spellings: {spellings}"]
        if g["examples"]:
            lines.append("- Example lines:")
            lines += [f"  - {e['snippet']}" for e in g["examples"]]
    return "\n".join(lines) + "\n"
