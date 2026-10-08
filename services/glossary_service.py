"""
services/glossary_service.py -- series glossary, project/series
instructions, and the read-only option catalogues for the Translate
stage's config ("Project instructions" and "Series glossary & term
handling").

Glossary terms belong to a SERIES (`drama.series_id`), never a drama: a
drama with no series reads as an empty glossary and refuses term writes
and series instructions (UnsupportedOperationError). Every term write or
delete verifies the term belongs to the drama's own series first, since
the id-only db helpers (`db.delete_glossary_term`, `db.update_glossary_term`)
have no ownership check; a term of another series reads as NotFoundError.

aliases / banned_translations are lists of strings in this API and are
stored pipe-separated (db convention), so a "|" inside one is rejected.

Glossary from the attached novel ("Build a glossary from this novel"):
start_novel_glossary_run is a background job
calling `tguide.extract_glossary_from_novel` on the drama's own saved
novel files (raw_novel_context.txt as the original, the
novel_reference_filename translation as the paired English rendering).
The engine and key are resolved
server-side from the drama's translation_engine; the key is never
returned or stored in a job result. The job only PROPOSES terms (keyed by
term); apply_novel_glossary adds the ones the caller names, by term text
(never by list position). A proposal whose term already exists in the
series glossary (possibly user-edited) is skipped unless
overwrite_existing=True is passed explicitly. See PAID_ENGINE_FUNCTIONS.

Glossary from the drama's own source lines ("Auto-extract terms from the
source text", parity X10): start_lines_glossary_run is the same
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

No FastAPI import.
"""
import csv
from typing import Optional

import db
import translation_guide as tguide
from translate_engines import WORKFLOW_TIERS, effective_tier
from services.service_errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
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
    corrected); without one it is keyed on (series, term_original). Omitted
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
    """"Import glossary file": `text` is the file's
    contents (CSV, TSV or JSON; `filename`, the upload's name, only hints
    the format), parsed by tguide.parse_glossary_file.
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
    """"Export glossary as CSV" (tguide.glossary_to_csv): the
    series glossary as CSV text; just the header row when there is none.
    A cell that a spreadsheet would run as a formula is prefixed with '."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=False)
    terms = db.list_glossary_terms(sid) if sid else []
    return tguide.glossary_to_csv([{k: _csv_safe(v) for k, v in t.items()} for t in terms])


def bulk_delete_glossary_terms(drama_id: int, term_ids: list, confirm: bool = False) -> dict:
    """"Delete N selected term(s)" (confirm=true):
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


# Extraction jobs live in glossary_extract_service; re-exported so routers,
# the CLI and tests keep importing them from here.
from services.glossary_extract_service import (  # noqa: E402,F401
    RAW_NOVEL_FILENAME,
    _EXTRACT_FAILED,
    novel_glossary_job_id,
    _read_drama_file,
    _default_engine,
    novel_glossary_engine,
    HIGH_CONFIDENCE_MIN_OCCURRENCES,
    MAX_ALTERNATIVES,
    _confidence,
    _normalize_proposals,
    _novel_glossary_cache,
    _run_novel_glossary_job,
    _run_ids_lock,
    _start_extraction_job,
    _cancel_extraction,
    cancel_novel_glossary_run,
    cancel_lines_glossary_run,
    _current_run_id_locked,
    _current_run_id,
    _glossary_engine,
    start_novel_glossary_run,
    _PROPOSAL_FIELDS,
    MAX_DISMISSALS_PER_SERIES,
    _dismissals,
    _extraction_status,
    get_novel_glossary_status,
    _OVERRIDE_KEYS,
    _clean_overrides,
    PROPOSALS_CHANGED,
    _apply_extraction,
    list_glossary_dismissals,
    _clean_dismiss_terms,
    dismiss_glossary_proposals,
    restore_glossary_proposals,
    apply_novel_glossary,
    lines_glossary_job_id,
    lines_glossary_engine,
    _run_lines_glossary_job,
    start_lines_glossary_run,
    get_lines_glossary_status,
    apply_lines_glossary,
)
