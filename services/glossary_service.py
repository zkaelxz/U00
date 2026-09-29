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

Deliberately NOT here:
  - Dialogue-based LLM extraction (`extract_terms_llm`).
  - Presets CRUD (`db.save_preset` etc.), series/drama creation,
    characters (services/characters_service.py), and the pre-translate
    glossary review gate.

No Streamlit or FastAPI import.
"""
import os
from typing import Optional

import background_jobs
import db
import translate_engines
import translation_guide as tguide
from translate_engines import WORKFLOW_TIERS
from services.service_errors import (
    ConflictError, DependencyUnavailableError, InvalidInputError, NotFoundError,
    UnsupportedOperationError,
)

# Functions that call an LLM engine (and so may spend on a paid one, i.e.
# any engine outside translate_engines.FREE_ENGINES).
PAID_ENGINE_FUNCTIONS = ("start_novel_glossary_run",)

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


def delete_glossary_term(drama_id: int, term_id: int, confirm: bool = False) -> None:
    """Delete one term. Mirrors the tab's confirm-before-delete checkbox
    (Step 71): confirm must be True."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=False)
    if sid is None:
        raise NotFoundError("Glossary term not found.")
    term = _owned_term(sid, term_id)
    if confirm is not True:
        raise InvalidInputError("Deleting a glossary term needs confirm=true.")
    db.delete_glossary_term(term["id"])


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
                           for k, v in WORKFLOW_TIERS.items()],
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


def novel_glossary_engine(drama_id: int) -> str:
    """The engine a run would use (the drama's translation_engine, default
    claude, as the tab) -- for the router's engines.paid gate."""
    return _drama(drama_id).get("translation_engine") or "claude"


def spends_on_paid_engine(engine_name: Optional[str]) -> bool:
    return (engine_name or "claude") not in translate_engines.FREE_ENGINES


def _run_novel_glossary_job(job_id, drama_id, engine, engine_name, src_text, en_text,
                            source_language, known_terms):
    try:
        proposals = tguide.extract_glossary_from_novel(
            src_text, engine, source_language=source_language, english_translation=en_text,
            known_terms=known_terms,
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
    background_jobs.set_result(job_id, {"proposals": list(out.values())})


def start_novel_glossary_run(drama_id: int) -> dict:
    """Starts proposing glossary terms from this drama's saved novel text.
    Uses the saved original-language novel (raw_novel_context.txt) as the
    source and the saved novel translation as the paired rendering when
    both exist; either alone is used as the source (the tab's rule).
    Paid-engine spend: yes, unless the engine is in FREE_ENGINES.

    Poll GET /api/jobs/{job_id}; the "done" result is {"proposals": [{term,
    suggested_translation, category, policy, reason, already_in_glossary}]}.

    NotFoundError, UnsupportedOperationError (no series / no novel saved /
    engine can't do this), DependencyUnavailableError (no key),
    ConflictError (already running)."""
    from services import settings_service, translate_service

    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    orig = _read_drama_file(drama_id, RAW_NOVEL_FILENAME)
    novel = _read_drama_file(drama_id, drama.get("novel_reference_filename"))
    if not (orig.strip() or novel.strip()):
        raise UnsupportedOperationError("Attach a novel to this drama first.")
    src_text = orig if orig.strip() else novel
    en_text = novel if orig.strip() else ""

    engine_name = novel_glossary_engine(drama_id)
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

    job_id = novel_glossary_job_id(drama_id)
    started = background_jobs.start_job(
        job_id, _run_novel_glossary_job, job_id, drama_id, engine, engine_name, src_text,
        en_text, drama.get("source_language") or "zh", db.list_glossary_terms(sid),
        gpu_touching=engine_name == "ollama",
        description=f"Glossary from novel (drama #{drama_id})")
    if not started:
        raise ConflictError("A glossary extraction is already running for this drama.")
    return {"job_id": job_id, "engine": engine_name, "paired": bool(en_text)}


def apply_novel_glossary(drama_id: int, terms: list, overwrite_existing: bool = False) -> dict:
    """Adds the named proposals (by term text, never list position) from
    this drama's finished extraction to the series glossary. A term already
    in the glossary is left untouched and reported in "skipped_existing"
    unless overwrite_existing is True (then its translation/notes/category/
    policy are replaced; its enforce_exact, aliases and banned list are
    kept). Terms not among the proposals (or proposed with no translation)
    are reported in "unknown". Returns {"added", "overwritten",
    "skipped_existing", "unknown"} lists of terms."""
    drama = _drama(drama_id)
    sid = _series_id(drama, required=True)
    if not isinstance(overwrite_existing, bool):
        raise InvalidInputError("overwrite_existing must be true or false.")
    if (not isinstance(terms, (list, tuple)) or not terms or len(terms) > 1000
            or not all(isinstance(t, str) for t in terms)):
        raise InvalidInputError("terms must be a non-empty list of term strings.")
    job = background_jobs.get_status(novel_glossary_job_id(drama_id))
    if not job or job.get("status") != "done":
        raise UnsupportedOperationError("No finished glossary extraction for this drama.")
    by_term = {p["term"]: p for p in (job.get("result") or {}).get("proposals") or []}
    existing = {r["term_original"]: r for r in db.list_glossary_terms(sid)}

    report = {"added": [], "overwritten": [], "skipped_existing": [], "unknown": []}
    for term in dict.fromkeys(t.strip() for t in terms):
        p = by_term.get(term)
        if p is None or not p["suggested_translation"]:
            report["unknown"].append(term)
            continue
        current = existing.get(term)
        if current is not None and not overwrite_existing:
            report["skipped_existing"].append(term)
            continue
        db.upsert_glossary_term(
            sid, term, p["suggested_translation"], notes=p["reason"],
            category=p["category"], policy=p["policy"],
            enforce_exact=bool(current.get("enforce_exact")) if current else False)
        report["overwritten" if current is not None else "added"].append(term)
    return report
