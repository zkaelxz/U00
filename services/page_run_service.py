"""services/page_run_service.py -- the one-button starts on a title page, run as chains on the PC queue.

The page only asks for a run and shows how far it got: the PC carries it on with the page closed.
Which steps a run adds is chosen from what the server holds (chapters, novel text, lines, the
glossary scan), never from a client flag, and a title with a run still waiting or running gets no
second one. Every run stops at the glossary review: names are cheaper to fix before a translation.
"""

import threading
import uuid
from typing import Optional

import db
import novel_text
from engine_backends import engine_registry, local as engine_local
from lib.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError, UnsupportedOperationError
from services import glossary_extract_service, queue_service as q

_ACTIVE = (q.WAITING, q.RUNNING)
_STOPPED = (q.FAILED, q.SKIPPED, q.INTERRUPTED, q.CANCELLED)
_NOVEL = "novel"
_FILE = "file"
# The "already running" check and the append are two steps; without this two starts at once both pass the check.
_after_import_start = threading.Lock()


def _glossary_status(drama_id: int, source: str) -> str:
    get = (glossary_extract_service.get_novel_glossary_status if source == _NOVEL
           else glossary_extract_service.get_lines_glossary_status)
    return get(drama_id)["status"]


def _title_items(principal, drama_id: int) -> list:
    with q._lock:
        items = q._load()["items"]
    return [i for i in items if i["drama_id"] == drama_id and q._sees(principal, i)]


def _flow_chain(items: list, marker: str = q.NAMES) -> list:
    """The title's latest run of one kind: the newest chain holding a `marker` step, in step order."""
    chains = [i for i in items if i["chain"] and i["step"] == marker]
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
                "reason": now["reason"] or None, "job_id": now["job_id"]}
    glossary = _glossary_status(drama_id, source)
    if glossary == "done":
        return {"state": "review", "step": None, "reason": None}
    if glossary in ("queued", "running"):
        # Started from the Glossary tab: the run has nothing to add until it ends.
        return {"state": "running", "step": q.NAMES, "reason": None}
    stopped = next((i for i in chain if i["status"] in _STOPPED), None)
    if stopped is not None:
        # The job, when the step got as far as starting one, is where the page sends the person for its log.
        return {"state": "stopped", "step": stopped["step"], "reason": stopped["reason"] or None,
                "job_id": stopped["job_id"]}
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
    return _view(principal, drama_id, source) if source != _FILE else file_run(principal, drama_id)


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

def lines_choice(drama_id: int, choice: dict) -> dict:
    """The lines step's settings from the page's choice: the engine named (the title's default when
    none is), never the first engine that happens to have a key."""
    from services import narration_service
    out = {"engine": choice.get("engine") or narration_service.get_narration_config(drama_id)["default_engine"],
           "one_narrator": bool(choice.get("one_narrator")), "cost_accepted": bool(choice.get("cost_accepted"))}
    for key in ("model", "chapter_from", "chapter_to"):
        if choice.get(key):
            out[key] = choice[key]
    return out


def _novel_state(drama_id: int) -> dict:
    from services import novel_attach_service, novel_chapters_service
    return {"chapters": novel_chapters_service.list_chapters(drama_id, 0, 1)["total"],
            "text": novel_attach_service.get_status(drama_id)["has_novel_text"],
            "lines": bool(db.load_line_ids(drama_id))}


def _refuse_to_replace_own_text(drama_id: int) -> None:
    """The text step replaces the title's narration text, so a title that has text but no chapters
    (text typed or attached by hand) is never given one by a run. Lines already made are left to the
    callers' own check."""
    state = _novel_state(drama_id)
    if state["text"] and not state["chapters"] and not state["lines"]:
        raise ConflictError("This novel already has its own text, which an import would replace. "
                            "Press Translate on the title to go on.")


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


def start_novel_flow(principal, drama_id: int, url: str, lines: dict, *, at_pc: bool, local: bool,
                     holds_import: bool) -> dict:
    """Adds the novel run (import the link when no chapter is saved, make the text and the lines
    when there are none, then Find names), to start at once, and returns novel_flow. The caller
    checks the lines and names engines against the paid-engine permission. `lines` is the lines
    step's settings (lines_choice). `local` is the import's
    fetch mode (the request came from the PC)."""
    from services import sources_extension_service, sources_url_service
    held = _ready_to_add(principal, drama_id, _NOVEL)
    if held is not None:
        return held
    _refuse_to_replace_own_text(drama_id)
    steps = []
    for step in novel_steps(drama_id):
        if step == q.IMPORT:
            if not holds_import:
                raise ForbiddenError("Importing from a link needs permission to import from sources.")
            url = sources_url_service.check_public_url(url)
            sources_extension_service.require_url_not_extension_only(url)
            steps.append((step, {"url": url, "local": bool(local)}))
        elif step == q.TEXT:
            steps.append((step, {}))
        elif step == q.LINES:
            steps.append((step, lines))
        else:
            steps.append((step, {"source": _NOVEL}))
    return _add(principal, drama_id, steps, at_pc, _NOVEL)


# --- the Add box --------------------------------------------------------------

def file_run(principal, drama_id: int) -> dict:
    """How far the Add box run of this title got: idle, waiting, running (with the step's job),
    stopped (with the reason) or done (translated: the page fetches the subtitles itself)."""
    return _run_view(principal, drama_id, q.RUN_TRANSLATE)


def _run_view(principal, drama_id: int, marker: str) -> dict:
    """The title's latest run that ends in `marker` (its translating step): idle, waiting, running,
    stopped or done."""
    _require_title(principal, drama_id)
    chain = _flow_chain(_title_items(principal, drama_id), marker)
    if not chain:
        return {"state": "idle", "step": None, "reason": None, "job_id": None}
    active = [i for i in chain if i["status"] in _ACTIVE]
    if active:
        now = next((i for i in active if i["status"] == q.RUNNING), active[0])
        return {"state": "running" if now["status"] == q.RUNNING else "waiting", "step": now["step"],
                "reason": now["reason"] or None, "job_id": now["job_id"]}
    stopped = next((i for i in chain if i["status"] in _STOPPED), None)
    if stopped is not None:
        return {"state": "stopped", "step": stopped["step"], "reason": stopped["reason"] or None,
                "job_id": stopped["job_id"]}
    return {"state": "done", "step": None, "reason": None, "job_id": chain[-1]["job_id"]}


def start_file_run(principal, drama_id: int, job_id: str, engine: str, locale: Optional[str], *,
                   at_pc: bool) -> dict:
    """Adds the Add box run for a title whose upload just started `job_id` (its transcription, or
    for a video the audio extraction that runs it): follow that job, then translate with the
    box's engine and English variant. A paid engine translates whether or not a monthly cap is
    set, as the box's own start did (owner, 2026-11-15); a cap that is set still stops it. The
    caller checks the engine against the paid-engine permission."""
    from services import media_upload_service, settings_service
    _require_title(principal, drama_id)
    if job_id not in (f"transcribe_{drama_id}", f"{media_upload_service.EXTRACT_JOB_PREFIX}{drama_id}"):
        raise InvalidInputError("That is not this title's transcription.")
    if engine not in engine_registry.ENGINES:
        raise InvalidInputError("Unknown translation engine.")
    if locale is not None and locale not in settings_service.LOCALE_CHOICES:
        raise InvalidInputError("Unknown English variant.")
    view = file_run(principal, drama_id)
    if view["state"] in ("waiting", "running"):
        return view
    translate = {"engine": engine, **({"locale": locale} if locale else {})}
    return _add(principal, drama_id, [(q.FOLLOW, {"job_id": job_id}), (q.RUN_TRANSLATE, translate)],
                at_pc, _FILE)


# --- after a chapter import ---------------------------------------------------

def after_import_kind(principal, drama_id: int) -> str:
    """"comic" or "novel": what the title's media type says a chapter import produced (404 for a
    title the caller can't use)."""
    from services import sources_import_service as imp
    _require_title(principal, drama_id)
    media = ((db.get_drama(drama_id) or {}).get("media_type") or "").lower()
    if media in imp.COMIC_MEDIA_TYPES:
        return "comic"
    if media in imp.NOVEL_MEDIA_TYPES:
        return "novel"
    raise InvalidInputError("Translating after an import works for comic and novel titles.")


def after_import_run(principal, drama_id: int) -> dict:
    """How far the title's translate-after-import run got (see _run_view)."""
    marker = q.RUN_SCANLATE if after_import_kind(principal, drama_id) == "comic" else q.RUN_TRANSLATE
    return _run_view(principal, drama_id, marker)


_PHONE_REASON_CHARS = 200


def phone_progress(principal, drama_id: int) -> dict:
    """What the phone's status line shows for a title it just saved to. Only comic and novel titles
    have a translate run; any other title reads as idle rather than an error. A stopped run carries
    its reason, so a refusal is never silent on the phone."""
    from services import jobs_service
    from engine_backends.shared import redact_secrets
    try:
        run = after_import_run(principal, drama_id)
    except InvalidInputError:
        return {"state": "idle", "step": None, "kind": None, "reason": None}
    kind = after_import_kind(principal, drama_id)
    reason = None
    if run["state"] == "stopped" and run["reason"]:
        # Stored reasons are already redacted, but this leaves the PC: no URL, path or key, and one line's worth.
        text = redact_secrets(jobs_service.scrub_text(run["reason"])).removeprefix("Skipped: ")
        reason = " ".join(text.split())[:_PHONE_REASON_CHARS] or None
    return {"state": run["state"], "step": run["step"], "kind": kind, "reason": reason}


def start_after_import(principal, drama_id: int, engine: str, lines: Optional[dict], *, at_pc: bool,
                       model: Optional[str] = None, names: Optional[dict] = None,
                       follow_import: bool = True, add_chapters: bool = False,
                       new_chapters: Optional[int] = None) -> dict:
    """Queues the translating that follows the title's running chapter import. A comic gets one
    Scanlate run over the pages still untranslated. A novel with no lines yet gets text, lines,
    names and a translation; `lines` is the lines step's settings (lines_choice). The first step waits
    for the import job, so a failed import stops the rest; `follow_import` is False for a phone save,
    which has no import job. With `add_chapters` (a phone save), a novel that has lines gets its saved
    chapters not yet in the text appended, lines for that text alone, and a translation of the lines
    still untranslated; ConflictError when there is nothing new. `new_chapters` (how many chapters
    that save added) is required then: the append starts at the first of them, so chapters the owner
    cut off the end of the text are not put back. The caller checks every engine against the
    paid-engine permission."""
    with _after_import_start:
        return _start_after_import_locked(principal, drama_id, engine, lines, at_pc=at_pc, model=model,
                                          names=names, follow_import=follow_import, add_chapters=add_chapters,
                                          new_chapters=new_chapters)


def _start_after_import_locked(principal, drama_id: int, engine: str, lines: Optional[dict], *, at_pc: bool,
                               model: Optional[str], names: Optional[dict], follow_import: bool,
                               add_chapters: bool, new_chapters: Optional[int]) -> dict:
    from services import sources_import_service
    kind = after_import_kind(principal, drama_id)
    view = after_import_run(principal, drama_id)
    if view["state"] in ("waiting", "running"):
        return view
    if any(i["status"] in _ACTIVE for i in _title_items(principal, drama_id)):
        raise ConflictError("This title already has a step waiting in the queue. See the Jobs page.")
    steps = [(q.FOLLOW, {"job_id": sources_import_service.import_job_id(drama_id)})] if follow_import else []
    if kind == "comic":
        steps.append((q.RUN_SCANLATE, {"engine": engine}))
    else:
        state = _novel_state(drama_id)
        translate = {"engine": engine, "capped": True, **({"model": model} if model else {})}
        if state["lines"] and add_chapters:
            from services import novel_attach_service
            if not novel_attach_service.has_chapters_to_add(drama_id):
                raise ConflictError("This novel has no saved chapters that are not in its lines yet.")
            if not new_chapters:
                raise InvalidInputError("Update the Baihe app on the phone to translate a novel's new chapters.")
            # The phone's count is a claim: the append can't start before the chapters the phone really
            # saved, or a modified phone could put back chapters the owner cut off.
            saved_by_phone = novel_text.read_phone_added(db.drama_dir(drama_id))
            # Added text still waiting for lines is a retry: the append finds it in the text and adds nothing.
            if not saved_by_phone and novel_text.read_pending_lines(db.drama_dir(drama_id)) is None:
                raise ConflictError("The phone has saved no chapters to this title that are waiting to be added.")
            saved = novel_attach_service.saved_chapter_count(drama_id)
            # No count: start past every saved chapter, so the append only passes when nothing is left to add.
            first_new = (max(1, saved - min(new_chapters, saved_by_phone) + 1) if saved_by_phone else saved + 1)
            # No names step: a new scan would wait for a review nobody at the phone is there to do.
            steps += [(q.APPEND, {"from_chapter": first_new}), (q.LINES, {**(lines or {}), "append": True}),
                      (q.RUN_TRANSLATE, {**translate, "new_lines_only": True})]
            return _add_after_import(principal, drama_id, steps, at_pc)
        if state["lines"]:
            raise ConflictError("This novel already has lines. Press Translate on the title to go on.")
        _refuse_to_replace_own_text(drama_id)
        if not state["chapters"] or not state["text"]:
            steps.append((q.TEXT, {}))
        steps += [(q.LINES, lines or {}), (q.NAMES, {"source": _NOVEL, **(names or {})}), (q.RUN_TRANSLATE, translate)]
    return _add_after_import(principal, drama_id, steps, at_pc)


def require_cap_for_after_import(engine: str, model) -> None:
    """The tick starts its AI runs with nobody watching, so an engine the monthly cap covers needs a
    cap to be set, as for any unattended run."""
    from services import paid_job_service, settings_service
    paid_job_service.require_cap_for_unattended(
        engine, bool(settings_service.get("gemini_free_tier")),
        model or engine_registry.effective_default_model(engine))


def _add_after_import(principal, drama_id: int, steps: list, at_pc: bool) -> dict:
    q._append_items(principal, drama_id, steps, at_pc, chain=uuid.uuid4().hex, now=True)
    return after_import_run(principal, drama_id)


# --- starting one step, for the queue's runner ------------------------------------

def _require_free_or_paid(principal, engine: str, model, what: str) -> None:
    # Checked again at start, as for Translate: the adder's permission and the title's engine may have changed.
    # Ollama is free, but its cloud tags are billed, so they need engines.paid too, or the
    # adder's own Ollama key, which the queue start sends them with (require_cloud_model_allowed).
    from services import member_key_service
    from services.translate_run_service import engine_cap_applies
    if q._holds(principal, "engines.paid"):
        return
    run_model = model or engine_registry.effective_default_model(engine)
    cloud = (engine_registry.open_to_all_accounts(engine) and engine_cap_applies(engine, False, run_model)
             and engine_local.cloud_model_needs_paid(run_model))
    if not engine_registry.open_to_all_accounts(engine) or (
            cloud and not member_key_service.has_usable_key((principal or {}).get("user_id"))):
        raise ForbiddenError(f"This title {what} with a paid engine, which the account that added it can't use.")


def start_step(principal, drama: dict, step: str, settings: dict) -> Optional[str]:
    """Starts one of a page run's steps as its adder; returns the job id, or None for a step done inline."""
    drama_id = drama["id"]
    if step == q.NAMES:
        # An engine and model stored with the step were chosen for the adder when it was added (a
        # member who may not use the title's own engine runs on the one every account may).
        engine = settings.get("engine") or glossary_extract_service.novel_glossary_engine(drama_id)
        model = settings.get("model") if settings.get("engine") else glossary_extract_service.glossary_model(drama_id)
        _require_free_or_paid(principal, engine, model, "finds names")
        start = (glossary_extract_service.start_novel_glossary_run if settings.get("source") == _NOVEL
                 else glossary_extract_service.start_lines_glossary_run)
        return start(drama_id, engine_name=engine, model=model)["job_id"]
    if step == q.IMPORT:
        from services import sources_import_service
        if not q._holds(principal, "sources.import"):
            raise ForbiddenError("The account that added this item can no longer import from sources.")
        return sources_import_service.start_url_import(
            settings.get("url"), drama_id, local=bool(settings.get("local")), principal=principal)["job_id"]
    if step == q.TEXT:
        from services import novel_attach_service
        # A retried item may start after another run already made this title's lines, which a new
        # text would leave out of step with the text.
        if db.load_line_ids(drama_id):
            raise ConflictError("This novel already has lines, so its text is left as it is.")
        novel_attach_service.attach_from_sources(drama_id, "replace")
        return None
    if step == q.APPEND:
        from services import novel_attach_service
        # A retry after the chapters went in adds nothing and the chain goes on.
        novel_attach_service.add_new_chapters_from_sources(drama_id, settings.get("from_chapter"))
        return None
    if step == q.FOLLOW:
        import background_jobs
        job_id = settings.get("job_id") or ""
        # Only the upload's own job, which already runs: this step waits for it and starts nothing.
        if not job_id.endswith(f"_{drama_id}") or background_jobs.get_status(job_id) is None:
            raise NotFoundError("Lost track of the job this step waits for. Open the title to see how far it got.")
        return job_id
    if step == q.RUN_SCANLATE:
        from services import scanlate_run_service
        engine = settings.get("engine") or ""
        _require_free_or_paid(principal, engine, None, "translates pages")
        require_cap_for_after_import(engine, None)
        return scanlate_run_service.start_run(drama_id, mode="missing", engine=engine)["job_id"]
    if step == q.RUN_TRANSLATE:
        from services import translate_run_service, translate_thinking_service
        engine = settings.get("engine") or ""
        model = settings.get("model") or (
            translate_run_service.run_model_and_batch_size(drama_id, engine)[0] if engine == "ollama" else None)
        _require_free_or_paid(principal, engine, model, "translates")
        # Only the after-import chain is unattended: the Add box's run starts a paid translation
        # with no cap set, as its own start did (owner decision 2026-11-15).
        if settings.get("capped"):
            require_cap_for_after_import(engine, model)
        paid = q._holds(principal, "engines.paid")
        scope = {}
        if settings.get("new_lines_only"):
            # Scoped to the untranslated lines, so the glossary's exact-term pass leaves translated English alone.
            ids = [ln["id"] for ln in db.load_lines(drama_id)
                   if (ln.get("zh") or "").strip() and not (ln.get("en") or "").strip()]
            if not ids:
                return None
            scope = {"line_ids": ids, "own_lines_only": True}
        return translate_thinking_service.start_with_thinking(
            translate_run_service.start_translate_run, drama_id, None, False, engine_name=engine,
            locale=settings.get("locale"), model=model, allow_paid_summary=paid, **scope)["job_id"]
    return _start_lines(principal, drama_id, settings)


def _start_lines(principal, drama_id: int, settings: dict) -> Optional[str]:
    import novel_text
    from services import narration_service
    lines = lines_choice(drama_id, settings)
    first, last = lines.get("chapter_from"), lines.get("chapter_to")
    append = bool(settings.get("append"))
    folder = db.drama_dir(drama_id)
    if append and novel_text.read_pending_lines(folder) is None:
        return None     # the append added nothing, or a retry after these lines were saved
    try:
        if not lines["one_narrator"]:
            _require_free_or_paid(principal, lines["engine"], lines.get("model"), "makes lines")
            if not lines["cost_accepted"]:
                # Priced here, not when added: before the text step has run there is no text to price.
                cost = narration_service.estimate_tag_cost(drama_id, lines["engine"], lines.get("model"), first, last,
                                                           append=append)
                if cost["has_key"] and cost["cap_applies"] and not cost["monthly_cap_set"] and cost["estimated_usd"] > 0:
                    raise InvalidInputError(
                        f"Splitting and tagging would cost about ${cost['estimated_usd']:.2f} and no monthly spending "
                        "cap is set. Tick the box to accept the cost, then press Continue.")
        return narration_service.start_narration_run(
            drama_id, engine_name=lines["engine"], model=lines.get("model"), one_narrator=lines["one_narrator"],
            chapter_from=first, chapter_to=last, **({"append": True} if append else {}))["job_id"]
    except InvalidInputError:
        # The pending tail clears its marker when a stopped run had already saved these lines.
        if append and novel_text.read_pending_lines(folder) is None:
            return None
        raise
