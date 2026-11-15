"""services/page_run_service.py -- the one-button starts on a title page, run as chains on the PC queue.

The page only asks for a run and shows how far it got: the PC carries it on with the page closed.
Which steps a run adds is chosen from what the server holds (lines, the glossary scan), never from
a client flag, and a title with a run still waiting or running gets no second one.
"""

import uuid

import db
from lib.errors import ConflictError, NotFoundError, UnsupportedOperationError
from services import glossary_extract_service, queue_service as q

_ACTIVE = (q.WAITING, q.RUNNING)
_STOPPED = (q.FAILED, q.SKIPPED, q.INTERRUPTED, q.CANCELLED)


def _glossary_status(drama_id: int) -> str:
    return glossary_extract_service.get_lines_glossary_status(drama_id)["status"]


def _title_items(principal, drama_id: int) -> list:
    with q._lock:
        items = q._load()["items"]
    return [i for i in items if i["drama_id"] == drama_id and q._sees(principal, i)]


def _flow_chain(items: list) -> list:
    """The title's latest one-button run: the newest chain that finds names, in step order."""
    chains = [i for i in items if i["chain"] and i["step"] == q.NAMES]
    if not chains:
        return []
    latest = max(chains, key=lambda i: i["id"])["chain"]
    return sorted((i for i in items if i["chain"] == latest), key=lambda i: len(i["needs"]))


def title_flow(principal, drama_id: int) -> dict:
    """How far the audio or video one-button run of this title got: idle (nothing to show),
    waiting (in line), running, stopped (with the reason; pressing again goes on) or review
    (the glossary scan is done and waits for the person)."""
    if db.get_drama(drama_id) is None or not q._owns_title(principal, drama_id):
        raise NotFoundError("Title not found.")
    chain = _flow_chain(_title_items(principal, drama_id))
    active = [i for i in chain if i["status"] in _ACTIVE]
    if active:
        now = next((i for i in active if i["status"] == q.RUNNING), active[0])
        return {"state": "running" if now["status"] == q.RUNNING else "waiting", "step": now["step"],
                "reason": now["reason"] or None}
    glossary = _glossary_status(drama_id)
    if glossary == "done":
        return {"state": "review", "step": None, "reason": None}
    if glossary in ("queued", "running"):
        # Started from the Glossary tab: the run has nothing to add until it ends.
        return {"state": "running", "step": q.NAMES, "reason": None}
    stopped = next((i for i in chain if i["status"] in _STOPPED), None)
    if stopped is not None:
        return {"state": "stopped", "step": stopped["step"], "reason": stopped["reason"] or None}
    return {"state": "idle", "step": None, "reason": None}


def start_title_flow(principal, drama_id: int, holds_paid: bool, *, at_pc: bool) -> dict:
    """Adds the run's steps for this title (Transcribe when it has no lines, then Find names), to
    start at once, and returns title_flow. The caller checks the names engine against the caller's
    paid-engine permission, as the Glossary tab's start does. It stops before translating: names are cheaper to fix
    before a translation than after. A title whose run is already in line gets that run back."""
    view = title_flow(principal, drama_id)
    if view["state"] in ("waiting", "running", "review"):
        return view
    if any(i["status"] in _ACTIVE for i in _title_items(principal, drama_id)):
        raise ConflictError("This title already has a step waiting in the queue. See the Jobs page.")
    steps = []
    if not db.load_line_ids(drama_id):
        drama = db.get_drama(drama_id) or {}
        if (drama.get("transcript_mode") or "have_transcript") == "have_transcript":
            raise UnsupportedOperationError("This title transcribes from a transcript you paste. "
                                            "Paste it on the Transcribe step.")
        q._check_addable(principal, drama_id, "transcribe", holds_paid, at_pc, None, True)
        steps.append(("transcribe", {}))
    steps.append((q.NAMES, {}))
    q._append_items(principal, drama_id, steps, at_pc, chain=uuid.uuid4().hex, now=True)
    return title_flow(principal, drama_id)
