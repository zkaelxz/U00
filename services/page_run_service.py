"""services/page_run_service.py -- the one-button starts on a title page, run as chains on the PC queue.

The page only asks for a run and shows how far it got: the PC carries it on with the page closed.
Which steps a run adds is chosen from what the server holds (chapters, novel text, lines, the
glossary scan), never from a client flag, and a title with a run still waiting or running gets no
second one. Every run stops at the glossary review: names are cheaper to fix before a translation.
"""

import uuid
from typing import Optional

import db
from engine_backends import engine_registry
from lib.errors import ConflictError, ForbiddenError, NotFoundError, UnsupportedOperationError
from services import glossary_extract_service, queue_service as q

_ACTIVE = (q.WAITING, q.RUNNING)
_STOPPED = (q.FAILED, q.SKIPPED, q.INTERRUPTED, q.CANCELLED)
_NOVEL = "novel"
# Preparing the text was a PC-only action (local_only) before it ran here, and stays one.
_TEXT_AT_PC = "Preparing the imported chapters as the text works only at the PC."


def _glossary_status(drama_id: int, source: str) -> str:
    get = (glossary_extract_service.get_novel_glossary_status if source == _NOVEL
           else glossary_extract_service.get_lines_glossary_status)
    return get(drama_id)["status"]


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


def _require_title(principal, drama_id: int) -> None:
    if db.get_drama(drama_id) is None or not q._owns_title(principal, drama_id):
        raise NotFoundError("Title not found.")


def _view(principal, drama_id: int, source: str) -> dict:
    """idle (nothing to show), waiting (in line), running, stopped (with the reason; pressing again
    goes on) or review (the glossary scan is done and waits for the person)."""
    _require_title(principal, drama_id)
    chain = _flow_chain(_title_items(principal, drama_id))
    active = [i for i in chain if i["status"] in _ACTIVE]
    if active:
        now = next((i for i in active if i["status"] == q.RUNNING), active[0])
        return {"state": "running" if now["status"] == q.RUNNING else "waiting", "step": now["step"],
                "reason": now["reason"] or None}
    glossary = _glossary_status(drama_id, source)
    if glossary == "done":
        return {"state": "review", "step": None, "reason": None}
    if glossary in ("queued", "running"):
        # Started from the Glossary tab: the run has nothing to add until it ends.
        return {"state": "running", "step": q.NAMES, "reason": None}
    stopped = next((i for i in chain if i["status"] in _STOPPED), None)
    if stopped is not None:
        return {"state": "stopped", "step": stopped["step"], "reason": stopped["reason"] or None}
    return {"state": "idle", "step": None, "reason": None}


def _ready_to_add(principal, drama_id: int, source: str) -> Optional[dict]:
    """The run to hand back instead of adding one, or None when a new run may be added."""
    view = _view(principal, drama_id, source)
    if view["state"] in ("waiting", "running", "review"):
        return view
    if any(i["status"] in _ACTIVE for i in _title_items(principal, drama_id)):
        raise ConflictError("This title already has a step waiting in the queue. See the Jobs page.")
    return None


def _add(principal, drama_id: int, steps: list, at_pc: bool, source: str) -> dict:
    q._append_items(principal, drama_id, steps, at_pc, chain=uuid.uuid4().hex, now=True)
    return _view(principal, drama_id, source)


# --- audio and video ---------------------------------------------------------

def title_flow(principal, drama_id: int) -> dict:
    """How far the audio or video one-button run of this title got (see _view)."""
    return _view(principal, drama_id, "lines")


def start_title_flow(principal, drama_id: int, holds_paid: bool, *, at_pc: bool) -> dict:
    """Adds the run's steps for this title (Transcribe when it has no lines, then Find names), to
    start at once, and returns title_flow. The caller checks the names engine against the caller's
    paid-engine permission, as the Glossary tab's start does. A title whose run is already in line
    gets that run back."""
    held = _ready_to_add(principal, drama_id, "lines")
    if held is not None:
        return held
    steps = []
    if not db.load_line_ids(drama_id):
        drama = db.get_drama(drama_id) or {}
        if (drama.get("transcript_mode") or "have_transcript") == "have_transcript":
            raise UnsupportedOperationError("This title transcribes from a transcript you paste. "
                                            "Paste it on the Transcribe step.")
        q._check_addable(principal, drama_id, "transcribe", holds_paid, at_pc, None, True)
        steps.append(("transcribe", {}))
    steps.append((q.NAMES, {}))
    return _add(principal, drama_id, steps, at_pc, "lines")


# --- novels --------------------------------------------------------------------

def lines_engine(drama_id: int) -> str:
    """The engine that cuts a novel into lines: the first with a key, as the novel page picked it."""
    from services import narration_service
    cfg = narration_service.get_narration_config(drama_id)
    return next((e["key"] for e in cfg["engines"] if e["key_configured"]), cfg["default_engine"])


def _novel_state(drama_id: int) -> dict:
    from services import novel_attach_service, novel_chapters_service
    return {"chapters": novel_chapters_service.list_chapters(drama_id, 0, 1)["total"],
            "text": novel_attach_service.get_status(drama_id)["has_novel_text"],
            "lines": bool(db.load_line_ids(drama_id))}


def novel_flow(principal, drama_id: int) -> dict:
    """How far the novel link run of this title got (see _view)."""
    return _view(principal, drama_id, _NOVEL)


def novel_steps(drama_id: int) -> list:
    """The steps a novel run would add now, from what the title holds."""
    s = _novel_state(drama_id)
    steps = [] if s["chapters"] else [q.IMPORT]
    if not s["lines"]:
        if not (s["chapters"] and s["text"]):
            steps.append(q.TEXT)
        steps.append(q.LINES)
    return steps + [q.NAMES]


def start_novel_flow(principal, drama_id: int, url: str, *, at_pc: bool, local: bool,
                     text_allowed: bool, holds_import: bool) -> dict:
    """Adds the novel run (import the link when no chapter is saved, make the text and the lines
    when there are none, then Find names), to start at once, and returns novel_flow. The caller
    checks the lines and names engines against the paid-engine permission. `local` is the import's
    fetch mode (the request came from the PC); `text_allowed` is whether the caller could prepare
    the text themselves."""
    from services import sources_extension_service, sources_url_service
    held = _ready_to_add(principal, drama_id, _NOVEL)
    if held is not None:
        return held
    steps = []
    for step in novel_steps(drama_id):
        if step == q.IMPORT:
            if not holds_import:
                raise ForbiddenError("Importing from a link needs permission to import from sources.")
            url = sources_url_service.check_public_url(url)
            sources_extension_service.require_url_not_extension_only(url)
            steps.append((step, {"url": url, "local": bool(local)}))
        elif step == q.TEXT:
            if not text_allowed:
                raise ForbiddenError(_TEXT_AT_PC)
            steps.append((step, {}))
        elif step == q.LINES:
            steps.append((step, {"engine": lines_engine(drama_id)}))
        else:
            steps.append((step, {"source": _NOVEL}))
    return _add(principal, drama_id, steps, at_pc, _NOVEL)


# --- starting one step, for the queue's runner ------------------------------------

def _require_free_or_paid(principal, engine: str, what: str) -> None:
    # Checked again at start, as for Translate: the adder's permission and the title's engine may have changed.
    if engine not in engine_registry.FREE_ENGINES and not q._holds(principal, "engines.paid"):
        raise ForbiddenError(f"This title {what} with a paid engine, which the account that added it can't use.")


def start_step(principal, drama: dict, step: str, settings: dict) -> Optional[str]:
    """Starts one of a page run's steps as its adder; returns the job id, or None for a step done inline."""
    drama_id = drama["id"]
    if step == q.NAMES:
        engine = glossary_extract_service.novel_glossary_engine(drama_id)
        _require_free_or_paid(principal, engine, "finds names")
        start = (glossary_extract_service.start_novel_glossary_run if settings.get("source") == _NOVEL
                 else glossary_extract_service.start_lines_glossary_run)
        return start(drama_id, engine_name=engine)["job_id"]
    if step == q.IMPORT:
        from services import sources_import_service
        if not q._holds(principal, "sources.import"):
            raise ForbiddenError("The account that added this item can no longer import from sources.")
        return sources_import_service.start_url_import(
            settings.get("url"), drama_id, local=bool(settings.get("local")), principal=principal)["job_id"]
    if step == q.TEXT:
        from services import novel_attach_service
        novel_attach_service.attach_from_sources(drama_id, "replace")
        return None
    from services import narration_service
    engine = settings.get("engine") or lines_engine(drama_id)
    _require_free_or_paid(principal, engine, "makes lines")
    return narration_service.start_narration_run(drama_id, engine_name=engine)["job_id"]
