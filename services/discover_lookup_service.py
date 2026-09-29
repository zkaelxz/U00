"""
services/discover_lookup_service.py -- the Discover tab's network helpers
without Streamlit (spec slice D-2): query translation, baihehub search,
import suggestion from a page, bulk listing extraction, bulk commit and
navigation help.

Rules (spec sections 3 and 5):
  - The engine is chosen by name only, whitelisted on `supports_reference`,
    and its key is resolved server-side. No key is ever accepted from or
    returned to a client.
  - Every user-supplied URL is fetched through `services.safe_fetch`
    (public hosts only, pinned, capped, static). Never `page_fetch`,
    `smart_fetch` or a rendered browser.
  - `ladder.check_terms` is called at the fetch boundary (currently a
    no-op by user decision; re-enabling it covers this service too).
  - Errors are `service_errors` with fixed messages; any user-visible text
    passes `redact_secrets`.
  - Nothing here writes except `bulk_commit` (known_titles only, deduped).

Paid-engine spending: see PAID_ENGINE_FUNCTIONS. They spend only when the
chosen engine is not in `translate_engines.FREE_ENGINES`; `spends_on_paid_engine`
answers that for a given engine name.
"""
import threading
from typing import Optional

import background_jobs
import bulk_import
import db
import metadata_lookup
import navigator
import title_library
import translate_engines
from services import discover_catalog_service as _catalog
from services import safe_fetch, settings_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                     NotFoundError, RateLimitedError,
                                     UnsupportedOperationError)

DEFAULT_ENGINE = "claude"
MAX_QUERY_LEN = _catalog.MAX_QUERY_LEN
MAX_URL_LEN = 2000
MAX_BULK_URLS = 10
MAX_COMMIT_ENTRIES = 500
MAX_GOAL_LEN = 500
MAX_LABELS = 150
TARGET_LANGUAGES = ("English", "Vietnamese", "Chinese", "Japanese", "Korean")

BULK_JOB_ID = "discover_bulk_extract"
NAV_JOB_ID = "discover_navigation_help"

# Functions that call an LLM engine (and so may spend on a paid one).
PAID_ENGINE_FUNCTIONS = ("translate_query", "import_suggestion", "bulk_extract",
                         "navigation_help")

_SUGGEST_FIELDS = ("title_en", "title_zh", "author", "studio", "director",
                   "voice_actors", "summary")
_ENTRY_CAPS = {"title": 300, "author": 300, "tags": 500, "source_url": MAX_URL_LEN}
_ENGINE_FAILED = "The AI engine could not complete this request."
_TERMS_BLOCKED = "This site's recorded terms do not allow automatic reading."
_JOB_BUSY = "A {} is already running. Wait for it to finish."
_start_lock = threading.Lock()


def _redact(text) -> str:
    return translate_engines.redact_secrets(text or "")


# ----- engine ---------------------------------------------------------------

def allowed_engines() -> list:
    return [e for e, cls in translate_engines.ENGINES.items()
            if getattr(cls, "supports_reference", False)]


def spends_on_paid_engine(engine_name: Optional[str]) -> bool:
    return (engine_name or DEFAULT_ENGINE) not in translate_engines.FREE_ENGINES


def _check_engine(engine_name) -> str:
    engine_name = engine_name or DEFAULT_ENGINE
    allowed = allowed_engines()
    if not isinstance(engine_name, str) or engine_name not in allowed:
        raise InvalidInputError("That engine cannot be used here.", details={"allowed": allowed})
    return engine_name


def _build_engine(engine_name: str):
    """Server-side key resolution. The key stays inside the engine object."""
    if engine_name == "ollama":
        return translate_engines.get_engine(
            "ollama", "local", base_url=settings_service.resolve_key("ollama_url") or None)
    if engine_name in translate_engines.FREE_ENGINES:
        return translate_engines.get_engine(engine_name, None)
    api_key = settings_service.resolve_key(engine_name)
    if not api_key:
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    return translate_engines.get_engine(engine_name, api_key)


# ----- input checks ---------------------------------------------------------

def _check_text(name, value, cap, required=True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > cap:
        raise InvalidInputError(f"{name} must be text of at most {cap} characters.")
    value = value.strip()
    if required and not value:
        raise InvalidInputError(f"{name} is required.")
    return value


def _check_url(url) -> str:
    url = _check_text("url", url, MAX_URL_LEN)
    if not url.lower().startswith(("http://", "https://")):
        raise InvalidInputError("url must be a valid http:// or https:// address.")
    return url


def _check_terms(url: str):
    from sources import ladder
    try:
        ladder.check_terms("discover", url=url)
    except ladder.TermsProhibited:
        raise UnsupportedOperationError(_TERMS_BLOCKED) from None


def _fetch(url: str) -> safe_fetch.FetchResult:
    _check_terms(url)
    return safe_fetch.fetch_public_text(url)


# ----- sync helpers ---------------------------------------------------------

def translate_query(q, engine_name: Optional[str] = None) -> dict:
    """Button-driven only. A query that already has Chinese characters is
    returned as-is without building an engine (no spend)."""
    q = _check_text("q", q, MAX_QUERY_LEN)
    engine_name = _check_engine(engine_name)
    if any("一" <= ch <= "鿿" for ch in q):
        return {"query": q, "translated": q, "engine": engine_name}
    engine = _build_engine(engine_name)
    try:
        translated = title_library.translate_query_to_zh(q, engine)
    except Exception:
        raise DependencyUnavailableError(_ENGINE_FAILED) from None
    translated = _redact(translated if isinstance(translated, str) else q)[:MAX_QUERY_LEN]
    return {"query": q, "translated": translated or q, "engine": engine_name}


def baihehub_search(q) -> dict:
    """Fixed host (title_library.BAIHEHUB_API) with its existing timeouts.
    No results -> an empty list plus the human search link."""
    q = _check_text("q", q, MAX_QUERY_LEN)
    try:
        found = title_library.search_baihehub(q) or []
    except Exception:
        raise DependencyUnavailableError("baihehub search is unavailable right now.") from None
    results = [{"title": _redact(str(r.get("title") or ""))[:300],
                "url": str(r.get("url") or "")[:MAX_URL_LEN],
                "snippet": _redact(str(r.get("snippet") or ""))[:200]}
               for r in found if isinstance(r, dict)]
    return {"results": results, "fallback_url": title_library.search_url_fallback(q)}


def import_suggestion(url, engine_name: Optional[str] = None) -> dict:
    """Fetch statically, extract bibliographic fields, return a suggestion.
    Writes nothing; the client applies it through the catalog create route."""
    url = _check_url(url)
    engine_name = _check_engine(engine_name)
    engine = _build_engine(engine_name)
    page = _fetch(url)
    if page.needs_manual or not page.text.strip():
        return {"suggestion": {}, "found": False, "needs_manual": True,
                "message": _redact(page.message)}
    try:
        found = metadata_lookup.extract_metadata_llm(page.text, engine)
    except Exception:
        raise DependencyUnavailableError(_ENGINE_FAILED) from None
    suggestion = {k: _redact(v.strip()) for k, v in (found or {}).items()
                  if k in _SUGGEST_FIELDS and isinstance(v, str) and v.strip()}
    if suggestion:
        suggestion["source_url"] = url
    return {"suggestion": suggestion, "found": bool(suggestion), "needs_manual": False,
            "message": "" if suggestion else "Read the page but found no metadata in it."}


# ----- jobs -----------------------------------------------------------------

def _start(job_id: str, label: str, target, *args) -> dict:
    with _start_lock:
        if background_jobs.is_running(job_id):
            raise RateLimitedError(_JOB_BUSY.format(label))
        if not background_jobs.start_job(job_id, target, job_id, *args,
                                         description=label.capitalize()):
            raise RateLimitedError(_JOB_BUSY.format(label))
    return {"job_id": job_id, "started": True}


def _job_result(job_id: str) -> dict:
    status = background_jobs.get_status(job_id)
    if status is None:
        raise NotFoundError("No such job has run in this process.")
    return {"job_id": job_id, "status": status.get("status"),
            "progress": status.get("progress") or 0.0,
            "message": _redact(status.get("message")),
            "result": status.get("result")}


def _clean_entry(e) -> Optional[dict]:
    if not isinstance(e, dict):
        return None
    title = e.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    out = {}
    for k in ("title", "author", "tags"):
        v = e.get(k)
        out[k] = _redact(v.strip() if isinstance(v, str) else "")[:_ENTRY_CAPS[k]]
    out["has_audio_drama"] = e.get("has_audio_drama") is True
    return out


def _run_bulk_extract(job_id, urls, source_label, engine):
    rows, entries, seen = [], [], set()
    for i, url in enumerate(urls):
        if background_jobs.is_cancel_requested(job_id):
            break
        row = {"url": url, "ok": False, "needs_manual": False, "count": 0, "message": ""}
        try:
            page = _fetch(url)
            if page.needs_manual or not page.text.strip():
                row.update(needs_manual=True, message=_redact(page.message))
            else:
                found = bulk_import.extract_listing_entries_llm(
                    page.text, engine, source_name=source_label)
                for raw in found or []:
                    e = _clean_entry(raw)
                    if e is None or e["title"] in seen:
                        continue
                    seen.add(e["title"])
                    e["source_url"] = url
                    entries.append(e)
                    row["count"] += 1
                row["ok"] = row["count"] > 0
                row["message"] = "" if row["ok"] else "Read the page but found no titles in it."
        except (InvalidInputError, UnsupportedOperationError, DependencyUnavailableError) as err:
            row["message"] = _redact(err.message)
        except Exception:
            row["message"] = _ENGINE_FAILED
        rows.append(row)
        background_jobs.update_progress(job_id, (i + 1) / len(urls),
                                        f"Read {i + 1} of {len(urls)} pages")
    background_jobs.set_result(job_id, {"entries": entries, "pages": rows,
                                        "source_label": source_label})


def bulk_extract(urls, source_label="", engine_name: Optional[str] = None) -> dict:
    """Job: fetch each listing page (static only) and extract catalog
    entries. Result: {"entries", "pages": per-URL status, "source_label"}.
    One URL failing never stops the rest. Writes nothing."""
    if not isinstance(urls, list) or not urls:
        raise InvalidInputError("urls must be a non-empty list.")
    if len(urls) > MAX_BULK_URLS:
        raise InvalidInputError(f"At most {MAX_BULK_URLS} URLs per run.")
    clean = []
    for u in urls:
        u = _check_url(u)
        if u not in clean:
            clean.append(u)
    source_label = _check_text("source_label", source_label, 100, required=False)
    engine_name = _check_engine(engine_name)
    engine = _build_engine(engine_name)
    return _start(BULK_JOB_ID, "bulk listing extraction", _run_bulk_extract,
                  clean, source_label, engine)


def bulk_extract_result() -> dict:
    return _job_result(BULK_JOB_ID)


def bulk_commit(entries, source_label="") -> dict:
    """Insert reviewed entries into known_titles. Skips entries whose title
    or source_url is already in the catalog (or repeated in this batch).
    Each entry is validated by the catalog service's own rules."""
    if not isinstance(entries, list) or not entries:
        raise InvalidInputError("entries must be a non-empty list.")
    if len(entries) > MAX_COMMIT_ENTRIES:
        raise InvalidInputError(f"At most {MAX_COMMIT_ENTRIES} entries per commit.")
    source_label = _check_text("source_label", source_label, 100, required=False) or "manual"
    existing = db.list_known_titles()
    titles = {(r.get("title_original") or "").strip() for r in existing} - {""}
    urls = {(r.get("source_url") or "").strip() for r in existing} - {""}
    prepared = []
    for e in entries:
        if not isinstance(e, dict):
            raise InvalidInputError("Each entry must be an object.")
        fields = {
            "title_original": e.get("title"), "author": e.get("author") or "",
            "tags": e.get("tags") or "", "source_url": e.get("source_url") or "",
            "source_name": source_label,
            "language": e.get("language") or "zh",
            "media_type": "audio_drama" if e.get("has_audio_drama") is True else "novel",
        }
        for k in ("title_original", "author", "tags", "source_url"):
            if fields[k] is not None and not isinstance(fields[k], str):
                raise InvalidInputError(f"{k} must be text.")
        prepared.append(fields)
    added, skipped = [], 0
    for fields in prepared:
        title = (fields["title_original"] or "").strip()
        url = fields["source_url"].strip()
        # A listing page URL is shared by every entry from it; dedup by URL
        # only when it is unique to one catalog row (a detail-page URL).
        if title in titles or (url and url in urls and _url_is_detail(url, prepared)):
            skipped += 1
            continue
        added.append(_catalog.create_title(dict(fields, title_original=title))["id"])
        titles.add(title)
        if url:
            urls.add(url)
    return {"added": len(added), "skipped": skipped, "ids": added}


def _url_is_detail(url: str, prepared: list) -> bool:
    return sum(1 for f in prepared if f["source_url"].strip() == url) == 1


def _labels_from_text(text: str) -> list:
    """Static stand-in for navigator.fetch_visible_labels: short visible
    lines of the page text (menu items, buttons, headings), deduped."""
    labels, seen = [], set()
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and len(ln) <= 40 and ln not in seen:
            labels.append(ln)
            seen.add(ln)
        if len(labels) >= MAX_LABELS:
            break
    return labels


def _run_navigation_help(job_id, url, goal, target_language, engine):
    result = {"labels": {}, "steps": "", "needs_manual": False, "message": ""}
    try:
        page = _fetch(url)
        if page.needs_manual:
            result.update(needs_manual=True, message=_redact(page.message))
        else:
            background_jobs.update_progress(job_id, 0.3, "Translating page labels")
            translated = navigator.translate_labels(
                _labels_from_text(page.text), target_language, engine) or {}
            result["labels"] = {_redact(str(k))[:40]: _redact(str(v))[:200]
                                for k, v in list(translated.items())[:MAX_LABELS]}
        background_jobs.update_progress(job_id, 0.7, "Writing steps")
        steps = navigator.generate_navigation_steps(
            url, goal, target_language, engine, translated_labels=result["labels"] or None)
        result["steps"] = _redact(steps if isinstance(steps, str) else "")[:20_000]
    except (InvalidInputError, UnsupportedOperationError, DependencyUnavailableError) as err:
        result["message"] = _redact(err.message)
    except Exception:
        result["message"] = _ENGINE_FAILED
    background_jobs.set_result(job_id, result)
    background_jobs.update_progress(job_id, 1.0, "Done")


def navigation_help(url, goal, target_language="English",
                    engine_name: Optional[str] = None) -> dict:
    """Job: static fetch, translate visible labels, then numbered steps
    (2+ LLM calls). Describes the site's own interface only."""
    url = _check_url(url)
    goal = _check_text("goal", goal, MAX_GOAL_LEN)
    if target_language not in TARGET_LANGUAGES:
        raise InvalidInputError("Unknown target_language.",
                                details={"allowed": list(TARGET_LANGUAGES)})
    engine_name = _check_engine(engine_name)
    engine = _build_engine(engine_name)
    return _start(NAV_JOB_ID, "navigation help", _run_navigation_help,
                  url, goal, target_language, engine)


def navigation_help_result() -> dict:
    return _job_result(NAV_JOB_ID)
