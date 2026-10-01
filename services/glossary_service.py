"""
services/glossary_service.py -- series glossary, project/series
instructions, and the read-only option catalogues for the Translate
stage's config (`tabs/workspace_tab.py`, Translate config's "Project
instructions" box and "Series glossary & term handling" expander).

Glossary terms belong to a SERIES (`drama.series_id`), never a drama: a
drama with no series reads as an empty glossary and refuses term writes
and series instructions (UnsupportedOperationError). Every term write or
delete verifies the term belongs to the drama's own series first, since
the id-only db helpers (`db.delete_glossary_term`, `db.update_glossary_term`)
have no ownership check; a term of another series reads as NotFoundError.

aliases / banned_translations are lists of strings in this API and are
stored pipe-separated (db convention), so a "|" inside one is rejected.

Glossary from the attached novel (the tab's "📕 Build a glossary from this
novel" expander, Step 7b): start_novel_glossary_run is a background job
calling `tguide.extract_glossary_from_novel` on the drama's own saved
novel files (raw_novel_context.txt as the original, the
novel_reference_filename translation as the paired English rendering --
the same source/pairing rule as the tab). The engine and key are resolved
server-side from the drama's translation_engine; the key is never
returned or stored in a job result. The job only PROPOSES terms (keyed by
term); apply_novel_glossary adds the ones the caller names, by term text
(never by list position). A proposal whose term already exists in the
series glossary (possibly user-edited) is skipped unless
overwrite_existing=True is passed explicitly. See PAID_ENGINE_FUNCTIONS.

Glossary from the drama's own source lines (the tab's "Auto-extract terms
from the source text", parity X10): start_lines_glossary_run is the same
kind of background job calling `tguide.extract_terms_llm` on the saved
lines' source text, with its own job id, status and apply. Both applies
take optional per-term overrides (translation/category/policy the user
edited in review), keyed by term text like the terms themselves.

Run ids: every extraction run gets a fresh run_id, shown in its status and
stored in its result. An apply names the run_id of the proposals the user
reviewed; if the held run is a different one (another tab or device ran
the extraction again), the apply is refused with ConflictError and nothing
is written -- otherwise un-overridden fields would be filled from proposals
the user never saw. The lines apply requires it; the novel apply accepts it
optionally (older callers), and the React client always sends it.

The pre-translate glossary review (parity X28) needs no service of its
own: the client runs one of the two extractions, applies what the user
keeps, then starts the translate run through translate_run_service.

Parity T03/T04/X13: import_glossary_text takes a glossary file's TEXT
(CSV/TSV/JSON, parsed by tguide.parse_glossary_file) in the request body --
no file is uploaded or stored, so like any other term write it is a
household edit (lines.edit); existing terms are skipped unless
overwrite_existing. glossary_csv exports the series glossary, and
bulk_delete_glossary_terms deletes named term ids of the drama's series.

Deliberately NOT here:
  - Presets CRUD (`db.save_preset` etc.), series/drama creation,
    characters (services/characters_service.py).

No Streamlit or FastAPI import.
"""
import csv
import os
import threading
import uuid
from typing import Optional

import background_jobs
import db
import translate_engines
import translation_guide as tguide
from services import job_checkpoint_service
from translate_engines import WORKFLOW_TIERS, effective_tier
from services.service_errors import (
    ConflictError, DependencyUnavailableError, InvalidInputError, NotFoundError,
    UnsupportedOperationError,
)

# Functions that call an LLM engine (and so may spend on a paid one, i.e.
# any engine outside translate_engines.FREE_ENGINES).
PAID_ENGINE_FUNCTIONS = ("start_novel_glossary_run", "start_lines_glossary_run")

MAX_TERM_LEN = 200
MAX_NOTES_LEN = 1000
MAX_LIST_ITEMS = 50
MAX_ITEM_LEN = 200
MAX_INSTRUCTIONS_LEN = 5000


def _drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError("Drama not found.")
    return drama


def _series_id(drama: dict, *, required: bool) -> Optional[int]:
    sid = drama.get("series_id")
    if not sid and required:
        raise UnsupportedOperationError(
            "This drama isn't part of a series; add it to a series first.")
    return sid or None


def _split(value) -> list:
    return [p.strip() for p in (value or "").split("|") if p.strip()]


def _serialize(row: dict) -> dict:
    return {
        "id": row["id"],
        "term_original": row.get("term_original") or "",
        "term_translation": row.get("term_translation") or "",
        "notes": row.get("notes") or "",
        "category": row.get("category") or None,
        "policy": row.get("policy") or None,
        "enforce_exact": bool(row.get("enforce_exact")),
        "aliases": _split(row.get("aliases")),
        "banned_translations": _split(row.get("banned_translations")),
    }


def _text(value, field: str, max_len: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InvalidInputError(f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise InvalidInputError(f"{field} is required.")
    if len(value) > max_len:
        raise InvalidInputError(f"{field} is too long (max {max_len} characters).")
    return value


def _str_list(value, field: str) -> str:
    """Validate a list of non-empty strings; return the pipe-joined form."""
    if not isinstance(value, (list, tuple)):
        raise InvalidInputError(f"{field} must be a list of strings.")
    if len(value) > MAX_LIST_ITEMS:
        raise InvalidInputError(f"{field} has too many entries (max {MAX_LIST_ITEMS}).")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise InvalidInputError(f"{field} must be a list of strings.")
        item = item.strip()
        if not item:
            raise InvalidInputError(f"{field} entries must not be empty.")
        if len(item) > MAX_ITEM_LEN:
            raise InvalidInputError(f"A {field} entry is too long (max {MAX_ITEM_LEN} characters).")
        if "|" in item:
            raise InvalidInputError(f"{field} entries must not contain '|'.")
        out.append(item)
    return "|".join(out)


def _owned_term(series_id: int, term_id) -> dict:
    if isinstance(term_id, bool) or not isinstance(term_id, int):
        raise NotFoundError("Glossary term not found.")
    for row in db.list_glossary_terms(series_id):
        if row["id"] == term_id:
            return row
    raise NotFoundError("Glossary term not found.")


def list_glossary_terms(drama_id: int) -> list:
    drama = _drama(drama_id)
    sid = _series_id(drama, required=False)
    if sid is None:
        return []
    return [_serialize(r) for r in db.list_glossary_terms(sid)]


def upsert_glossary_term(drama_id: int, term_fields: dict) -> dict:
    """Create or update a term in the drama's series glossary.

    With an "id" the term is updated in place (its original text may be
    corrected, like the tab's edit form); without one it is keyed on
    (series, term_original) exactly like the tab's add form. Omitted
    optional fields keep their stored value on update -- including when no
    "id" is given but a term with that original text already exists in the
    series (it is then an update of that term, not a reset to defaults);
    a brand-new term gets fresh defaults. Returns the saved term."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    if not isinstance(term_fields, dict):
        raise InvalidInputError("Term fields must be an object.")

    existing = _owned_term(sid, term_fields["id"]) if term_fields.get("id") is not None else None
    base = _serialize(existing) if existing else {}
    if existing is None and isinstance(term_fields.get("term_original"), str):
        key = term_fields["term_original"].strip()
        match = next((r for r in db.list_glossary_terms(sid) if r["term_original"] == key), None)
        if match:
            base = _serialize(match)

    def pick(key, default=None):
        return term_fields[key] if key in term_fields else base.get(key, default)

    original = _text(pick("term_original"), "term_original", MAX_TERM_LEN, required=True)
    translation = _text(pick("term_translation"), "term_translation", MAX_TERM_LEN, required=True)
    notes = _text(pick("notes"), "notes", MAX_NOTES_LEN)

    category = pick("category")
    if category is not None and category not in tguide.TERM_CATEGORIES:
        raise InvalidInputError("Unknown category.")
    policy = pick("policy")
    if policy is not None and policy not in tguide.TERM_POLICIES:
        raise InvalidInputError("Unknown policy.")
    enforce = pick("enforce_exact", False)
    if not isinstance(enforce, bool):
        raise InvalidInputError("enforce_exact must be true or false.")
    aliases = _str_list(pick("aliases", []), "aliases")
    banned = _str_list(pick("banned_translations", []), "banned_translations")

    if existing:
        clash = next((r for r in db.list_glossary_terms(sid)
                      if r["term_original"] == original and r["id"] != existing["id"]), None)
        if clash:
            raise ConflictError("Another term already uses that original text.")
        db.update_glossary_term(existing["id"], original, translation, notes, category, policy,
                                enforce, aliases, banned)
        saved_id = existing["id"]
    else:
        db.upsert_glossary_term(sid, original, translation, notes, category, policy, enforce,
                                aliases, banned)
        saved_id = next(r["id"] for r in db.list_glossary_terms(sid)
                        if r["term_original"] == original)
    return _serialize(_owned_term(sid, saved_id))


# ---------------------------------------------------------------------------
# Parity T03/T04/X13: import a glossary file's text, export as CSV, bulk delete
# ---------------------------------------------------------------------------

MAX_IMPORT_CHARS = 1_000_000
MAX_IMPORT_TERMS = 2000
MAX_BULK_DELETE = 1000
_MAX_WARNINGS = 20
# A spreadsheet reads a cell starting with one of these as a formula, so the
# CSV export prefixes such a cell with ' and the import drops that one '.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def _csv_unescape(value):
    if isinstance(value, str) and value.startswith("'") and value[1:].startswith(_FORMULA_STARTS):
        return value[1:]
    return value


def import_glossary_text(drama_id: int, text: str, filename: str = "",
                         overwrite_existing: bool = False) -> dict:
    """The Translate tab's "Import glossary file": `text` is the file's
    contents (CSV, TSV or JSON; `filename` only hints the format, as the
    tab passed the upload's name), parsed by tguide.parse_glossary_file.
    Nothing is written to disk. A term already in the series glossary is
    left untouched and reported in "skipped_existing" unless
    overwrite_existing is True (then its translation, notes, category,
    policy and enforce flag are replaced; its aliases and banned list are
    kept). Rows that fail the same limits as a hand-added term are reported
    in "invalid". Returns {"added", "overwritten", "skipped_existing",
    "invalid", "warnings"}."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    if not isinstance(text, str):
        raise InvalidInputError("text must be text.")
    if len(text) > MAX_IMPORT_CHARS:
        raise InvalidInputError(f"The glossary is too long (max {MAX_IMPORT_CHARS} characters).")
    if not isinstance(filename, str) or len(filename) > 255:
        raise InvalidInputError("filename must be a short name.")
    if not isinstance(overwrite_existing, bool):
        raise InvalidInputError("overwrite_existing must be true or false.")
    try:
        entries, warnings = tguide.parse_glossary_file(text, filename)
    except (AttributeError, TypeError, ValueError, csv.Error, RecursionError):
        # A JSON row whose values aren't text (a number, a list), a CSV field
        # over the csv module's size limit or deeply nested JSON trips the parser.
        raise InvalidInputError("That glossary has a row in an unexpected shape.") from None
    if not entries:
        raise InvalidInputError("Nothing could be imported from that glossary.",
                                details={"warnings": [str(w)[:200] for w in warnings[:_MAX_WARNINGS]]})
    if len(entries) > MAX_IMPORT_TERMS:
        raise InvalidInputError(f"Too many terms in one import (max {MAX_IMPORT_TERMS}).")

    existing = {r["term_original"]: r for r in db.list_glossary_terms(sid)}
    report = {"added": [], "overwritten": [], "skipped_existing": [], "invalid": []}
    seen = set()
    for e in entries:
        original = e["term_original"]
        try:
            original = _text(_csv_unescape(original), "term_original", MAX_TERM_LEN,
                             required=True)
            translation = _text(_csv_unescape(e["term_translation"]), "term_translation",
                                MAX_TERM_LEN)
            notes = _text(_csv_unescape(e["notes"]), "notes", MAX_NOTES_LEN)
        except InvalidInputError:
            report["invalid"].append(original[:MAX_TERM_LEN])
            continue
        if original in seen:
            continue   # a later duplicate row in the same file adds nothing new
        seen.add(original)
        current = existing.get(original)
        if not overwrite_existing:
            # Insert-only, so a term added since `existing` was read is not replaced.
            added = current is None and db.insert_glossary_term_if_absent(
                sid, original, translation, notes=notes, category=e["category"],
                policy=e["policy"], enforce_exact=bool(e["enforce_exact"]))
            report["added" if added else "skipped_existing"].append(original)
            continue
        db.upsert_glossary_term(sid, original, translation, notes=notes,
                                category=e["category"], policy=e["policy"],
                                enforce_exact=bool(e["enforce_exact"]))
        report["overwritten" if current is not None else "added"].append(original)
    report["warnings"] = [str(w)[:200] for w in warnings[:_MAX_WARNINGS]]
    return report


def glossary_csv(drama_id: int) -> str:
    """The tab's "Export glossary as CSV" (tguide.glossary_to_csv): the
    series glossary as CSV text; just the header row when there is none.
    A cell that a spreadsheet would run as a formula is prefixed with '."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=False)
    terms = db.list_glossary_terms(sid) if sid else []
    return tguide.glossary_to_csv([{k: _csv_safe(v) for k, v in t.items()} for t in terms])


def bulk_delete_glossary_terms(drama_id: int, term_ids: list, confirm: bool = False) -> dict:
    """The tab's "Delete N selected term(s)" (confirm checkbox, Step 71):
    deletes each named term by id (never by position). Ids that are not in
    this drama's series glossary are reported in "not_found" and nothing
    else happens to them. Returns {"deleted", "not_found"} id lists."""
    drama = _drama(drama_id)
    if (not isinstance(term_ids, (list, tuple)) or not term_ids
            or len(term_ids) > MAX_BULK_DELETE
            or not all(isinstance(t, int) and not isinstance(t, bool) for t in term_ids)):
        raise InvalidInputError("term_ids must be a non-empty list of term ids.")
    if confirm is not True:
        raise InvalidInputError("Deleting glossary terms needs confirm=true.")
    sid = _series_id(drama, required=False)
    owned = {r["id"] for r in db.list_glossary_terms(sid)} if sid else set()
    report = {"deleted": [], "not_found": []}
    for term_id in dict.fromkeys(term_ids):
        if term_id in owned:
            db.delete_glossary_term(term_id)
            report["deleted"].append(term_id)
        else:
            report["not_found"].append(term_id)
    return report


def get_instructions(drama_id: int) -> dict:
    drama = _drama(drama_id)
    return {
        "project_instructions": drama.get("project_instructions") or "",
        "series_instructions": drama.get("series_instructions") or "",
    }


def set_project_instructions(drama_id: int, text: str) -> dict:
    _drama(drama_id)
    text = _text(text, "project_instructions", MAX_INSTRUCTIONS_LEN)
    db.update_drama(drama_id, project_instructions=text)
    return get_instructions(drama_id)


def set_series_instructions(drama_id: int, text: str) -> dict:
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    text = _text(text, "series_instructions", MAX_INSTRUCTIONS_LEN)
    db.update_series_instructions(sid, text)
    return get_instructions(drama_id)


def get_catalogues() -> dict:
    """Static option lists for the Translate config UI (no secrets: the
    workflow tiers expose only key/label/engine/model/flags)."""
    return {
        "style_presets": [{"key": k, "label": v["label"]}
                          for k, v in tguide.STYLE_PRESETS.items()],
        "term_categories": [{"key": k, "label": v} for k, v in tguide.TERM_CATEGORIES.items()],
        "term_policies": [{"key": k, "label": v["label"], "example": v.get("example", "")}
                          for k, v in tguide.TERM_POLICIES.items()],
        "workflow_tiers": [{"key": k, "label": v["label"],
                            "translation_engine": v["translation_engine"],
                            "engine_model": v["engine_model"],
                            "reflect": bool(v["reflect"]), "auto_qc": bool(v["auto_qc"])}
                           for k in WORKFLOW_TIERS
                           for v in [effective_tier(k)]],
    }


# ---------------------------------------------------------------------------
# Glossary from the attached novel (Step 7b)
# ---------------------------------------------------------------------------

RAW_NOVEL_FILENAME = "raw_novel_context.txt"
_EXTRACT_FAILED = "Glossary extraction failed."


def novel_glossary_job_id(drama_id: int) -> str:
    return f"novel_glossary_{drama_id}"


def _read_drama_file(drama_id: int, filename: Optional[str]) -> str:
    if not filename:
        return ""
    path = os.path.join(db.drama_dir(drama_id), filename)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _default_engine() -> str:
    from services import settings_service
    return settings_service.get_default_engine()


def novel_glossary_engine(drama_id: int) -> str:
    """The engine a run would use (the drama's translation_engine, else
    the Settings default engine) -- for the router's engines.paid gate."""
    return _drama(drama_id).get("translation_engine") or _default_engine()


def _normalize_proposals(proposals, known_terms) -> list:
    """Allowlisted proposal dicts keyed by term text (a repeated term keeps
    the last one); already_in_glossary is the snapshot at extraction time."""
    known = {t["term_original"] for t in known_terms}
    out = {}
    for p in proposals or []:
        term = p.get("term") if isinstance(p, dict) else None
        term = term.strip() if isinstance(term, str) else ""
        if not term or len(term) > MAX_TERM_LEN:
            continue
        out[term] = {
            "term": term,
            "suggested_translation": str(p.get("suggested_translation") or "").strip()[:MAX_TERM_LEN],
            "category": p.get("category") if p.get("category") in tguide.TERM_CATEGORIES else None,
            "policy": p.get("policy") if p.get("policy") in tguide.TERM_POLICIES else None,
            "reason": str(p.get("reason") or "")[:MAX_NOTES_LEN],
            "already_in_glossary": term in known,
        }
    return list(out.values())


def _novel_glossary_cache(engine, engine_name, fresh=False):
    """Step 41 item 1: each passage's reply is cached on (prompt, engine and
    model, max_tokens), so re-running an interrupted extraction only pays
    for the passages it never finished. fresh: skip the cached replies (new
    ones still replace them)."""
    model = f"{engine_name}:{getattr(engine, 'model', '') or ''}"
    settings = {"max_tokens": 4000}

    # A cache failure (a locked or full disk) must never fail a paid run.
    def get(prompt):
        if fresh:
            return None
        try:
            return job_checkpoint_service.cache_get(
                "glossary_from_novel", job_checkpoint_service.hash_text(prompt), model,
                settings)
        except Exception:
            return None

    def put(prompt, text):
        try:
            job_checkpoint_service.cache_put(
                "glossary_from_novel", job_checkpoint_service.hash_text(prompt), model,
                settings, text)
        except Exception:
            pass
    return get, put


def _run_novel_glossary_job(job_id, run_id, drama_id, engine, engine_name, src_text, en_text,
                            source_language, known_terms, fresh=False):
    try:
        proposals = tguide.extract_glossary_from_novel(
            src_text, engine, source_language=source_language, english_translation=en_text,
            known_terms=known_terms,
            response_cache=_novel_glossary_cache(engine, engine_name, fresh),
            progress_cb=lambda f: background_jobs.update_progress(
                job_id, f, f"Reading... {f * 100:.0f}%"),
            usage_cb=lambda inp, out: db.log_usage(
                drama_id, engine_name, getattr(engine, "model", engine_name),
                "glossary_from_novel", inp, out,
                translate_engines.estimate_cost_for_engine(engine, inp, out)))
    except Exception as exc:
        # Engine errors can echo request details; never surface them raw.
        raise RuntimeError(
            _EXTRACT_FAILED + " " + translate_engines.redact_secrets(str(exc))) from None
    background_jobs.set_result(job_id, {"proposals": _normalize_proposals(proposals, known_terms),
                                        "run_id": run_id})


# The run_id of each extraction job id's latest started run, for the status
# of a run that isn't done yet (a done run's own result carries its run_id,
# which is what an apply is checked against).
_run_ids: dict = {}
_run_ids_lock = threading.Lock()


def _start_extraction_job(job_id: str, target, *args, **kwargs) -> bool:
    run_id = uuid.uuid4().hex
    # Start and record the run_id under one lock, so a run-scoped cancel
    # (_cancel_extraction) never sees the new job with the old run_id.
    with _run_ids_lock:
        started = background_jobs.start_job(job_id, target, job_id, run_id, *args, **kwargs)
        if started:
            _run_ids[job_id] = run_id
    return started


def _cancel_extraction(drama_id: int, job_id: str, run_id) -> dict:
    """Cancels this drama's extraction only when the held run is `run_id`
    (the one the caller is looking at), so a stale Cancel can't stop a newer
    run. ConflictError when another run is held or the run has finished."""
    _drama(drama_id)
    if not isinstance(run_id, str) or not run_id or len(run_id) > 64:
        raise InvalidInputError("run_id must be the run_id from the extraction's status.")
    from services import jobs_service
    with _run_ids_lock:
        job = background_jobs.get_status(job_id)
        if _current_run_id_locked(job_id, job) != run_id:
            raise ConflictError("That extraction was replaced by a newer run; nothing was "
                                "cancelled.")
        if not job or job.get("status") not in ("running", "queued"):
            raise ConflictError("That extraction has already finished.")
        return jobs_service.cancel_job(job_id)


def cancel_novel_glossary_run(drama_id: int, run_id: str) -> dict:
    """Cancel this drama's glossary-from-novel run `run_id` (see
    _cancel_extraction). Returns jobs_service.cancel_job's result."""
    return _cancel_extraction(drama_id, novel_glossary_job_id(drama_id), run_id)


def cancel_lines_glossary_run(drama_id: int, run_id: str) -> dict:
    """Cancel this drama's glossary-from-lines run `run_id`."""
    return _cancel_extraction(drama_id, lines_glossary_job_id(drama_id), run_id)


def _current_run_id_locked(job_id: str, job: Optional[dict]) -> Optional[str]:
    """_current_run_id for a caller already holding _run_ids_lock."""
    if not job:
        return None
    if job.get("status") == "done":
        return (job.get("result") or {}).get("run_id") or None
    return _run_ids.get(job_id)


def _current_run_id(job_id: str, job: Optional[dict]) -> Optional[str]:
    with _run_ids_lock:
        return _current_run_id_locked(job_id, job)


def _glossary_engine(drama: dict, engine_name: Optional[str]):
    """(engine_name, engine) for an extraction run on the drama's stored
    translation_engine. engine_name is the one the caller's gate checked;
    ConflictError if the stored engine changed since."""
    from services import settings_service, translate_service

    stored_engine = drama.get("translation_engine") or settings_service.get_default_engine()
    if engine_name is not None and engine_name != stored_engine:
        raise ConflictError("This drama's engine changed; check it and start again.")
    engine_name = stored_engine
    cls = translate_engines.ENGINES.get(engine_name)
    if cls is None or not getattr(cls, "supports_reference", False):
        raise UnsupportedOperationError("This drama's engine can't extract a glossary.")
    api_key = translate_service.resolve_api_key(engine_name)
    if not api_key:
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    engine = translate_engines.get_engine(
        engine_name, api_key,
        free_tier=engine_name == "gemini" and settings_service.get_gemini_free_tier(),
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    return engine_name, engine


def start_novel_glossary_run(drama_id: int, engine_name: Optional[str] = None,
                             fresh: bool = False) -> dict:
    """Starts proposing glossary terms from this drama's saved novel text.
    Uses the saved original-language novel (raw_novel_context.txt) as the
    source and the saved novel translation as the paired rendering when
    both exist; either alone is used as the source (the tab's rule).
    Paid-engine spend: yes, unless the engine is in FREE_ENGINES.

    engine_name: the engine a caller already authorized (the API route's
    engines.paid gate). When given and the drama's stored engine no longer
    matches it (changed in between), ConflictError -- the run, and the key
    resolved for it, never use an engine the caller didn't check.

    Poll get_novel_glossary_status(drama_id) (GET /api/glossary/dramas/{id}/
    from-novel); its "done" result is {"proposals": [{term,
    suggested_translation, category, policy, reason, already_in_glossary}]}.

    NotFoundError, UnsupportedOperationError (no series / no novel saved /
    engine can't do this / monthly cap used up, capped engines only, as
    start_lines_glossary_run), DependencyUnavailableError (no key),
    ConflictError (already running).

    fresh (Step 41): ignore replies cached by an earlier run over the same
    text and engine and ask the model again (the new replies replace them)."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    orig = _read_drama_file(drama_id, RAW_NOVEL_FILENAME)
    novel = _read_drama_file(drama_id, drama.get("novel_reference_filename"))
    if not (orig.strip() or novel.strip()):
        raise UnsupportedOperationError("Attach a novel to this drama first.")
    src_text = orig if orig.strip() else novel
    en_text = novel if orig.strip() else ""

    engine_name, engine = _glossary_engine(drama, engine_name)
    from services import settings_service, translate_run_service
    translate_run_service.refuse_when_cap_spent(engine_name,
                                                settings_service.get_gemini_free_tier())
    job_id = novel_glossary_job_id(drama_id)
    started = _start_extraction_job(
        job_id, _run_novel_glossary_job, drama_id, engine, engine_name, src_text,
        en_text, drama.get("source_language") or "zh", db.list_glossary_terms(sid),
        gpu_touching=engine_name == "ollama",
        description=f"Glossary from novel (drama #{drama_id})", fresh=bool(fresh))
    if not started:
        raise ConflictError("A glossary extraction is already running for this drama.")
    return {"job_id": job_id, "engine": engine_name, "paired": bool(en_text)}


_PROPOSAL_FIELDS = ("term", "suggested_translation", "category", "policy", "reason",
                    "already_in_glossary")


def _extraction_status(drama_id: int, job_id: str) -> dict:
    _drama(drama_id)
    job = background_jobs.get_status(job_id)
    if not job:
        raise NotFoundError("No glossary extraction for this drama in this app session.")
    status = job.get("status")
    result = None
    if status == "done":
        result = {"proposals": [{k: p.get(k) for k in _PROPOSAL_FIELDS}
                                for p in (job.get("result") or {}).get("proposals") or []
                                if isinstance(p, dict)]}
    message = job.get("error") if status == "error" else job.get("message")
    return {"job_id": job_id, "status": status, "progress": job.get("progress"),
            "message": translate_engines.redact_secrets(str(message)) if message else "",
            "result": result, "run_id": _current_run_id(job_id, job)}


def get_novel_glossary_status(drama_id: int) -> dict:
    """{job_id, status, progress, message, result, run_id} for this drama's
    glossary-from-novel job; result is {"proposals": [...]} only when done
    (else None). run_id names this run (None if unknown); pass it back to
    the apply. The message (or a failed job's error) is redacted; no key
    is ever in a job result. NotFoundError when the drama doesn't exist or
    no such job is resident in this process (results live only in
    background_jobs memory)."""
    return _extraction_status(drama_id, novel_glossary_job_id(drama_id))


_OVERRIDE_KEYS = ("translation", "category", "policy")


def _clean_overrides(overrides) -> dict:
    """{term: {translation?, category?, policy?}} -> validated copy. A key
    present means "use this value"; an absent key keeps the proposal's."""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict) or len(overrides) > 1000:
        raise InvalidInputError("overrides must be an object keyed by term.")
    out = {}
    for term, edit in overrides.items():
        if not isinstance(term, str) or not isinstance(edit, dict) \
                or not set(edit) <= set(_OVERRIDE_KEYS):
            raise InvalidInputError("Each override must be {translation, category, policy}.")
        clean = {}
        if "translation" in edit:
            clean["translation"] = _text(edit["translation"], "translation", MAX_TERM_LEN,
                                         required=True)
        if "category" in edit:
            if edit["category"] is not None and edit["category"] not in tguide.TERM_CATEGORIES:
                raise InvalidInputError("Unknown category.")
            clean["category"] = edit["category"]
        if "policy" in edit:
            if edit["policy"] is not None and edit["policy"] not in tguide.TERM_POLICIES:
                raise InvalidInputError("Unknown policy.")
            clean["policy"] = edit["policy"]
        out[term.strip()] = clean
    return out


PROPOSALS_CHANGED = "The proposals changed since you reviewed them — review again."


def _apply_extraction(drama_id: int, job_id: str, terms: list, overwrite_existing: bool,
                      overrides, run_id: Optional[str], *, run_id_required: bool) -> dict:
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    if not isinstance(overwrite_existing, bool):
        raise InvalidInputError("overwrite_existing must be true or false.")
    if (not isinstance(terms, (list, tuple)) or not terms or len(terms) > 1000
            or not all(isinstance(t, str) for t in terms)):
        raise InvalidInputError("terms must be a non-empty list of term strings.")
    if run_id is None and run_id_required:
        raise InvalidInputError("run_id is required.")
    if run_id is not None and (not isinstance(run_id, str) or not run_id or len(run_id) > 64):
        raise InvalidInputError("run_id must be the run_id from the extraction's status.")
    edits = _clean_overrides(overrides)
    job = background_jobs.get_status(job_id)
    # A different run than the one reviewed (or none held any more): its
    # proposals would fill fields the user never saw.
    if run_id is not None and _current_run_id(job_id, job) != run_id:
        raise ConflictError(PROPOSALS_CHANGED)
    if not job or job.get("status") != "done":
        raise UnsupportedOperationError("No finished glossary extraction for this drama.")
    by_term = {p["term"]: p for p in (job.get("result") or {}).get("proposals") or []}
    existing = {r["term_original"]: r for r in db.list_glossary_terms(sid)}

    report = {"added": [], "overwritten": [], "skipped_existing": [], "unknown": []}
    for term in dict.fromkeys(t.strip() for t in terms):
        p = by_term.get(term)
        edit = edits.get(term, {})
        translation = edit.get("translation", p["suggested_translation"] if p else "")
        if p is None or not translation:
            report["unknown"].append(term)
            continue
        current = existing.get(term)
        if current is not None and not overwrite_existing:
            report["skipped_existing"].append(term)
            continue
        db.upsert_glossary_term(
            sid, term, translation, notes=p["reason"],
            category=edit.get("category", p["category"]),
            policy=edit.get("policy", p["policy"]),
            enforce_exact=bool(current.get("enforce_exact")) if current else False)
        report["overwritten" if current is not None else "added"].append(term)
    return report


def apply_novel_glossary(drama_id: int, terms: list, overwrite_existing: bool = False,
                         overrides: Optional[dict] = None, run_id: Optional[str] = None) -> dict:
    """Adds the named proposals (by term text, never list position) from
    this drama's finished extraction to the series glossary. A term already
    in the glossary is left untouched and reported in "skipped_existing"
    unless overwrite_existing is True (then its translation/notes/category/
    policy are replaced; its enforce_exact, aliases and banned list are
    kept). Terms not among the proposals (or proposed with no translation
    and none given in overrides) are reported in "unknown". overrides:
    optional {term: {translation?, category?, policy?}} the user edited in
    review; keys for terms not in `terms` are ignored. run_id: optional,
    the run_id of the status the user reviewed; when given and the held run
    is another one (or none), ConflictError and nothing is written. Returns
    {"added", "overwritten", "skipped_existing", "unknown"} lists of terms."""
    return _apply_extraction(drama_id, novel_glossary_job_id(drama_id), terms,
                             overwrite_existing, overrides, run_id, run_id_required=False)


# ---------------------------------------------------------------------------
# Glossary from the drama's source lines (parity X10)
# ---------------------------------------------------------------------------

def lines_glossary_job_id(drama_id: int) -> str:
    return f"lines_glossary_{drama_id}"


def lines_glossary_engine(drama_id: int) -> str:
    """The engine a lines extraction would use (the same rule as the novel
    one: the drama's translation_engine, default claude)."""
    return novel_glossary_engine(drama_id)


def _run_lines_glossary_job(job_id, run_id, drama_id, engine, engine_name, source_lines,
                            source_language, known_terms):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled()
    background_jobs.update_progress(job_id, 0.1, f"Scanning {len(source_lines)} lines...")
    try:
        proposals = tguide.extract_terms_llm(
            source_lines, engine, source_language=source_language, known_terms=known_terms,
            usage_cb=lambda inp, out: db.log_usage(
                drama_id, engine_name, getattr(engine, "model", engine_name),
                "extract_terms", inp, out,
                translate_engines.estimate_cost_for_engine(engine, inp, out)))
    except Exception as exc:
        raise RuntimeError(
            _EXTRACT_FAILED + " " + translate_engines.redact_secrets(str(exc))) from None
    # One LLM call can't be interrupted; a cancel during it drops the result.
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled()
    background_jobs.set_result(job_id, {"proposals": _normalize_proposals(proposals, known_terms),
                                        "run_id": run_id})


def start_lines_glossary_run(drama_id: int, engine_name: Optional[str] = None) -> dict:
    """Starts proposing glossary terms from this drama's saved source lines
    (`tguide.extract_terms_llm`, which samples up to 400 lines across the
    whole drama). One LLM call on the drama's translation_engine; the
    engine_name rule is start_novel_glossary_run's. Refused before starting
    when this month's spending cap is already used up (capped engines only,
    as translate_run_service). Paid-engine spend: yes, unless the engine is
    in FREE_ENGINES.

    Poll get_lines_glossary_status; apply with apply_lines_glossary.
    NotFoundError, UnsupportedOperationError (no series / no source lines /
    engine can't do this / monthly cap used up), DependencyUnavailableError
    (no key), ConflictError (already running, or the engine changed)."""
    from services import settings_service, translate_run_service

    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    source_lines = [r["zh"] for r in db.load_lines(drama_id) if (r.get("zh") or "").strip()]
    if not source_lines:
        raise UnsupportedOperationError(
            "This drama has no source lines yet; transcribe or import them first.")
    engine_name, engine = _glossary_engine(drama, engine_name)
    translate_run_service.refuse_when_cap_spent(engine_name,
                                                settings_service.get_gemini_free_tier())

    job_id = lines_glossary_job_id(drama_id)
    started = _start_extraction_job(
        job_id, _run_lines_glossary_job, drama_id, engine, engine_name, source_lines,
        drama.get("source_language") or "zh", db.list_glossary_terms(sid),
        gpu_touching=engine_name == "ollama",
        description=f"Glossary from lines (drama #{drama_id})")
    if not started:
        raise ConflictError("A glossary extraction is already running for this drama.")
    return {"job_id": job_id, "engine": engine_name, "line_count": len(source_lines)}


def get_lines_glossary_status(drama_id: int) -> dict:
    """get_novel_glossary_status's contract, for the lines extraction."""
    return _extraction_status(drama_id, lines_glossary_job_id(drama_id))


def apply_lines_glossary(drama_id: int, terms: list, overwrite_existing: bool = False,
                         overrides: Optional[dict] = None, run_id: Optional[str] = None) -> dict:
    """apply_novel_glossary's contract, for the lines extraction's proposals,
    except that run_id is required (InvalidInputError when missing)."""
    return _apply_extraction(drama_id, lines_glossary_job_id(drama_id), terms,
                             overwrite_existing, overrides, run_id, run_id_required=True)
