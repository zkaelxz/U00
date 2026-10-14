"""
services/scanlate_run_service.py -- the automatic Scanlate run for the API
(docs/archive/scanlate-api-spec.md S5, typesetting via S6): detect text
regions, OCR them, translate them and render the typeset page, one page at
a time, as ONE background job per drama (`scanlate_<drama_id>`).

Modes (user decision 2026-09-29):
- "missing" ("Translate all pages"): pages that already have regions are
  skipped (their typeset image is rendered if it is missing);
- "page" ("Redo this page"): that page's regions are replaced;
- "all" ("Redo all"): every page's regions are replaced; needs confirm.

Safety (spec §4):
- Translation is id-keyed (scanlate_translate.translate_regions_by_id): a short,
  reordered, padded or unparseable answer applies nothing to that page;
  its OCR text is still saved and a note says so.
- A page's regions are replaced only if its region ids and rev are still
  what the job read before detecting (db.replace_bubbles_if_unchanged), so
  a page edited while the job ran keeps the edit; the job notes it.
- A redo never trades a page's translated regions for nothing (no region
  detected), for OCR-only text (translation failed), or for an incomplete
  result (OCR lost some regions, or a chunk of a dense page was rejected so
  some regions stayed untranslated): the old regions are kept and the page's
  notes say so. Hand-corrected source text is kept the same way when a redo's
  OCR fails on some regions.
- Rolling context comes from the predecessor page's stored
  context_summary; each page stores its own afterwards.
- SFX regions are not sent to the engine (region_excluded_from_auto).
- Cancel is checked between pages. Spend is logged with db.log_usage
  ("scanlate_translate"); there is no spend cap (user decision Q4).
- Keys, the HF token, the OCR backend and the Tesseract program come from
  the saved settings on the server, never from the request.
- Every note is redacted and path-stripped before it is stored.
Model runs hold the pipeline lock shared with the extension bridge.
No FastAPI import.
"""
import importlib.util

import background_jobs
import db
import translate_engines
from engine_backends import llm_tasks, paid_calls
from engine_backends.run_limits import TranslateRunStopped
from engine_backends.shared import LLMTaskTimeout, TranslationCancelled
from lib import cancellable_lock
from memory_headroom import HeadroomError
from services import comic_chapters_service
from services import scanlate_pages_service as pages_svc
from services import scanlate_render_service as render_svc
from services import paid_job_service, settings_service, translate_service
from services.service_errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    UnsupportedOperationError,
)

_OCR_ERRORED = "OCR failed on this page, so nothing was saved. Check the OCR backend in Diagnostics."
_OCR_NO_TEXT = "OCR found no text on this page, so nothing was saved. Redo it after checking the OCR backend."


class OcrReadFailed(Exception):
    """A page whose OCR produced no text. Its message is fixed text, so the
    job's failure note carries no path or exception detail."""


MODES = ("missing", "page", "all")
_CONFIRM_ALL = ("Redo all replaces the text regions of every page, including any you "
                "edited. Confirm to continue.")


def _build_engine(engine_name: str):
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    try:
        return translate_engines.get_engine(
            engine_name, api_key,
            free_tier=engine_name == "gemini" and settings_service.get("gemini_free_tier"),
            base_url=(settings_service.resolve_key("ollama_url") or None)
            if engine_name == "ollama" else None)
    except Exception:
        raise DependencyUnavailableError(
            f"The {engine_name} engine could not be started on this PC.") from None


def _require_ocr_backend(drama_id: int) -> None:
    """detect_and_ocr_page reports an OCR failure per page, so a backend that
    can't run is refused here, naming what is missing."""
    import ocr
    lang = pages_svc.require_drama(drama_id).get("source_language") or "zh"
    ocr.require_backend(settings_service.resolve_ocr_backend(lang),
                        settings_service.get("tesseract_cmd") or None)


def _require_opencv() -> None:
    """scanlate imports cv2 lazily inside detection and rendering, so without
    it every page would fail and the job would still end "done"."""
    if importlib.util.find_spec("cv2") is None:
        raise DependencyUnavailableError(
            "Scanlate needs opencv-python. Install it in Diagnostics.",
            details={"reason": "missing_package", "package": "opencv-python"})


def start_run(drama_id: int, mode: str = "missing", page_id: int = None, confirm: bool = False,
              engine: str = None, detect_backend: str = "auto",
              chapter_id: str = None) -> dict:
    """Starts the drama's Scanlate run. 409: a job or import is running,
    or mode "all" without confirm. 503: no key for the engine. 400: no
    pages. `chapter_id` limits "missing"/"all" to one chapter. Returns {job_id, engine, mode}."""
    pages_svc.require_drama(drama_id)
    if mode not in MODES:
        raise InvalidInputError("mode must be 'missing', 'page' or 'all'.")
    if detect_backend not in pages_svc.DETECT_BACKENDS:
        raise InvalidInputError("detect_backend must be 'auto', 'cv' or 'ml'.")
    pages = db.list_pages(drama_id)
    if not pages:
        raise UnsupportedOperationError("This title has no pages yet. Upload some first.")
    if mode == "page":
        if page_id is None:
            raise InvalidInputError("page_id is required to redo one page.")
        pages_svc.require_page(drama_id, page_id)
    elif page_id is not None:
        raise InvalidInputError("page_id is only used to redo one page.")
    if chapter_id is not None and mode == "page":
        raise InvalidInputError("chapter_id can't be combined with a single page.")
    if mode == "all" and not confirm:
        raise ConflictError(_CONFIRM_ALL)
    # Pages marked "not part of the story" are skipped, and a chapter limits the run.
    targets = ([page_id] if mode == "page"
               else comic_chapters_service.run_page_ids(drama_id, chapter_id))
    if not targets:
        raise UnsupportedOperationError("No pages to run: they are all hidden.")
    _require_opencv()
    _require_ocr_backend(drama_id)
    engine_name = engine or settings_service.get("default_engine")
    # Pure-MT engines are called once per region, outside call_llm_json.
    built = llm_tasks.bound_batches(_build_engine(engine_name))
    started = pages_svc.start_drama_job(
        drama_id, _run_job, pages_svc.job_id(drama_id), drama_id, mode, targets,
        engine_name, built, detect_backend,
        description=f"Scanlate translate (title {drama_id})")
    return {**started, "engine": engine_name, "mode": mode}


def _has_text(region: dict) -> bool:
    return bool((region.get("source_text") or "").strip()
                or (region.get("translated_text") or "").strip())


def _predecessor_context(drama_id: int, page_id: int) -> str:
    """The stored context of the page before this one in the run's order.
    Hidden pages are not part of the story, so they are stepped over; a page
    that is itself hidden (a single-page redo) falls back to the full order."""
    visible = {p["id"] for p in db.list_pages(drama_id)} & set(
        comic_chapters_service.run_page_ids(drama_id))
    prev = None
    for p in db.list_pages(drama_id):
        if p["id"] == page_id:
            break
        if page_id not in visible or p["id"] in visible:
            prev = p
    return (prev or {}).get("context_summary") or ""


def _detector_note(detect_backend: str, notes: list) -> tuple:
    import scanlate_detect
    fell_back = any(n[0] == "warning" for n in notes)
    if detect_backend == "cv" or fell_back:
        why = " (the ML detector could not run)" if fell_back else ""
        return ("info", f"Detector: OpenCV heuristic{why}.")
    if detect_backend == "ml" or scanlate_detect.bubble_ml_weights_cached():
        return ("info", "Detector: ML model.")
    return ("info", "Detector: OpenCV heuristic (the ML model is not downloaded).")


def _translate(bubbles: list, engine, engine_name: str, drama: dict, glossary, context: str,
               drama_id: int, notes: list):
    """Translates the eligible regions in place. Each region is sent under
    an explicit key and its answer is applied through a key -> region map
    built here, never by the position of the answer. Returns (ok, context):
    ok is False when a translation was wanted but not applied (the notes
    say why); context is the new rolling context, or None if none."""
    import scanlate_regions
    import scanlate_translate
    by_key = {str(i): b for i, b in enumerate(bubbles)
              if not b.get("skip") and not scanlate_regions.region_excluded_from_auto(b)
              and (b.get("source_text") or "").strip()}
    if not by_key:
        return True, None
    keyed = {key: b["source_text"] for key, b in by_key.items()}

    usage_cb = paid_calls.usage_logger(drama_id, engine_name, engine, "scanlate_translate")

    try:
        result, new_context = scanlate_translate.translate_regions_by_id(
            keyed, engine, drama, previous_context=context, usage_cb=usage_cb,
            glossary_terms=glossary)
    except (HeadroomError, LLMTaskTimeout, TranslateRunStopped):
        # The Ollama check refuses on every page, and a timed-out request is
        # still running and would refuse the next page's call: stop the job once.
        raise
    except Exception as exc:
        notes.append(("warning", f"Translation failed ({type(exc).__name__}: {exc}). The OCR "
                                 "text was saved; use Redo this page to try again."))
        return False, None
    if result is None:
        notes.append(("warning", "The translation answer could not be matched to this page's "
                                 "regions, so none of it was applied. The OCR text was saved; "
                                 "use Redo this page to try again."))
        return False, None
    for key, text in result.items():
        by_key[key]["translated_text"] = text
    if len(result) < len(by_key):
        # A rejected chunk of a dense page is simply absent from the result.
        # What was translated is kept on the regions (a first run saves it); the
        # False return is what stops a redo from replacing a complete page.
        notes.append(("warning", f"{len(by_key) - len(result)} of {len(by_key)} regions weren't "
                                 "translated: part of the answer couldn't be matched to them "
                                 "by id. Use Redo this page to try again."))
        return False, new_context
    return True, new_context


def _ocr_lost_text(existing: list, notes) -> bool:
    """OCR failed on some regions while the page already holds text (OCR'd,
    hand-corrected or translated): replacing it would blank those regions."""
    return (any(level == "error" for level, *_ in notes)
            and any(_has_text(b) for b in existing))


def _redo_would_lose_work(existing: list, bubbles: list, translated_ok: bool,
                          notes: list = ()) -> bool:
    """A redo replaces the page's regions. It must not swap translated
    regions for nothing (detection found none), for OCR-only text (the
    translation failed or was partial), for a page where OCR lost some
    regions, or for one where no region ended up translated; the page then
    keeps what it has."""
    if not bubbles or _ocr_lost_text(existing, notes):
        return True
    if not any((b.get("translated_text") or "").strip() for b in existing):
        return False
    return not translated_ok or not any((b.get("translated_text") or "").strip()
                                        for b in bubbles)


def _process_page(drama_id: int, drama: dict, page_id: int, mode: str, engine, engine_name: str,
                  detect_kwargs: dict, glossary, jid: str) -> str:
    """One page. Returns "skipped", "stale", "kept" (a redo that failed; the
    old regions stay), "done" or "translated"."""
    import scanlate
    page = db.get_page(page_id, drama_id=drama_id)
    if page is None:
        return "skipped"                        # deleted meanwhile
    existing = db.load_bubbles(page_id)
    # Regions that hold no text came from a failed or empty OCR; counting them
    # as done would make "Translate all" skip the page forever.
    if mode == "missing" and any(_has_text(b) for b in existing):
        if not page.get("rendered_filename"):
            notes = []
            render_svc.render_page(drama_id, page_id, notes, lambda: render_svc.check_cancel(jid))
            render_svc.append_notes(page_id, notes)
        return "skipped"
    expected_ids, expected_rev = [b["id"] for b in existing], int(page.get("rev") or 0)
    src = render_svc.original_path(drama_id, page)
    lang = drama.get("source_language") or "zh"
    with cancellable_lock.hold(pages_svc.pipeline_lock(), lambda: render_svc.check_cancel(jid)):
        bubbles, detect_notes = scanlate.detect_and_ocr_page(src, lang, page_id=page_id,
                                                             **detect_kwargs)
    if bubbles and not any((b.get("source_text") or "").strip() for b in bubbles):
        ocr_errored = any(level == "error" for level, _ in detect_notes)
        raise OcrReadFailed(_OCR_ERRORED if ocr_errored else _OCR_NO_TEXT)
    notes = [_detector_note(detect_kwargs["detect_backend"], detect_notes)]
    notes += detect_notes
    new_context, translated_ok = None, True
    # A result that will be thrown away is not worth a paid translation call.
    if bubbles and not _ocr_lost_text(existing, detect_notes):
        translated_ok, new_context = _translate(
            bubbles, engine, engine_name, drama, glossary,
            _predecessor_context(drama_id, page_id), drama_id, notes)
    if existing and _redo_would_lose_work(existing, bubbles, translated_ok, notes):
        notes.append(("warning", "Redo did not produce a complete result for this page (no "
                                 "regions, OCR or translation incomplete), so its existing "
                                 "regions were kept unchanged. Try Redo this page again."))
        db.update_page(page_id, run_notes=pages_svc.notes_to_json(notes))
        return "kept"
    new_ids = db.replace_bubbles_if_unchanged(page_id, expected_ids, bubbles,
                                              expected_rev=expected_rev)
    if new_ids is None:
        db.update_page(page_id, run_notes=pages_svc.notes_to_json(
            [("warning", "This page was edited while the job ran, so the new results were "
                         "not saved. Use Redo this page to run it again.")]))
        return "stale"
    fields = {"run_notes": pages_svc.notes_to_json(notes)}
    if new_context is not None:
        fields["context_summary"] = new_context
    db.update_page(page_id, **fields)
    render_notes = []
    try:
        render_svc.render_page(drama_id, page_id, render_notes,
                               lambda: render_svc.check_cancel(jid))
    except background_jobs.JobCancelled:
        raise                                    # a cancel is not a render failure
    except Exception as exc:
        render_notes.append(("error", f"Render failed: {type(exc).__name__}: "
                             f"{translate_engines.redact_secrets(str(exc))}"))
    render_svc.append_notes(page_id, render_notes)
    return "translated" if new_context is not None else "done"


def _failure_note(exc: Exception) -> str:
    if isinstance(exc, OcrReadFailed):
        return str(exc)
    return f"This page failed: {type(exc).__name__}: {translate_engines.redact_secrets(str(exc))}"


def _run_job(jid: str, drama_id: int, mode: str, page_ids: list, engine_name: str, engine,
             detect_backend: str):
    drama = db.get_drama(drama_id) or {}
    lang = drama.get("source_language") or "zh"
    glossary = db.list_glossary_terms(drama["series_id"]) if drama.get("series_id") else None
    ocr_backend = settings_service.resolve_ocr_backend(lang)
    detect_kwargs = {"detect_backend": detect_backend,
                     "hf_token": settings_service.resolve_key("hf_token") or None,
                     "ocr_backend": ocr_backend,
                     "tesseract_cmd": settings_service.get("tesseract_cmd") or None}
    counts = {"translated": 0, "done": 0, "skipped": 0, "stale": 0, "kept": 0, "failed": 0}
    total = len(page_ids)
    # Cancel and a total deadline for every AI call of the run, not only between
    # pages. One request per page, and with thinking on DeepSeek may take the
    # whole client timeout to answer, so the deadline is the per-request one.
    with paid_job_service.job_calls(jid, drama_id, engine, engine_name, "scanlate_translate",
                                    deadline=llm_tasks.request_deadline_for(engine),
                                    lenient_empty=False) as limits:
        for n, pid in enumerate(page_ids, start=1):
            render_svc.check_cancel(jid)
            background_jobs.update_progress(jid, (n - 1) / total, f"Page {n} of {total}")
            try:
                counts[_process_page(drama_id, drama, pid, mode, engine, engine_name,
                                     detect_kwargs, glossary, jid)] += 1
            except TranslationCancelled:
                raise background_jobs.JobCancelled(jid)
            except TranslateRunStopped:
                break  # the pages still to do stay as they are
            except (background_jobs.JobCancelled, HeadroomError):
                raise
            except Exception as exc:
                counts["failed"] += 1
                try:
                    db.update_page(pid, run_notes=pages_svc.notes_to_json(
                        [("error", _failure_note(exc))]))
                except Exception as note_exc:
                    from applog import get_logger
                    get_logger().warning("Could not save the error note for page %s: %s", pid,
                                         translate_engines.redact_secrets(str(note_exc)))
                # The abandoned request may still be running; every later page
                # would be refused, so the run ends with this error.
                if isinstance(exc, LLMTaskTimeout):
                    raise
    # Problems lead so a long "6 translated" tail cannot hide them.
    parts = []
    if counts["failed"]:
        parts.append(f"{counts['failed']} failed")
    if counts["kept"]:
        parts.append(f"{counts['kept']} kept unchanged (redo failed)")
    if counts["stale"]:
        parts.append(f"{counts['stale']} edited meanwhile")
    if counts["done"]:
        parts.append(f"{counts['done']} with no translation (no text found or translation failed)")
    parts.append(f"{counts['translated']} translated")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped (already done)")
    background_jobs.update_progress(jid, 1.0, "Pages: " + ", ".join(parts) + "."
                                    + (f" {limits.stop_reason}." if limits.stop_reason else ""))
    if limits.stop_reason:
        background_jobs.set_result(jid, paid_job_service.stop_fields(limits))
