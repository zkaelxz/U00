"""
services/scanlate_run_service.py -- the automatic Scanlate run for the API
(docs/specs/scanlate-api-spec.md S5, typesetting via S6): detect text
regions, OCR them, translate them and render the typeset page, one page at
a time, as ONE background job per drama (`scanlate_<drama_id>`).

Modes (user decision 2026-09-29):
- "missing" ("Translate all pages"): pages that already have regions are
  skipped (their typeset image is rendered if it is missing);
- "page" ("Redo this page"): that page's regions are replaced;
- "all" ("Redo all"): every page's regions are replaced; needs confirm.

Safety (spec §4):
- Translation is id-keyed (scanlate.translate_regions_by_id): a short,
  reordered, padded or unparseable answer applies nothing to that page;
  its OCR text is still saved and a note says so.
- A page's regions are replaced only if its region ids and rev are still
  what the job read before detecting (db.replace_bubbles_if_unchanged), so
  a page edited while the job ran keeps the edit; the job notes it.
- A redo never trades a page's translated regions for nothing (no region
  detected) or for OCR-only text (translation failed): the old regions are
  kept and the page's notes say so.
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
import background_jobs
import db
import translate_engines
from engine_backends import llm_tasks
from engine_backends.shared import LLMTaskTimeout, TranslationCancelled
from memory_headroom import HeadroomError
from services import comic_chapters_service
from services import scanlate_pages_service as pages_svc
from services import scanlate_render_service as render_svc
from services import settings_service, translate_service
from services.service_errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    UnsupportedOperationError,
)

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
            free_tier=engine_name == "gemini" and settings_service.get_gemini_free_tier(),
            base_url=(settings_service.resolve_key("ollama_url") or None)
            if engine_name == "ollama" else None)
    except Exception:
        raise DependencyUnavailableError(
            f"The {engine_name} engine could not be started on this PC.") from None


def _require_ocr_backend(drama_id: int) -> None:
    """detect_and_ocr_page turns any OCR failure into empty text, so a
    backend that can't run is refused here, with its plain reason."""
    import ocr
    lang = pages_svc.require_drama(drama_id).get("source_language") or "zh"
    if settings_service.resolve_ocr_backend(lang) == "paddle_vl_manga":
        problem = ocr.paddle_vl_manga_problem()
        if problem:
            raise DependencyUnavailableError(problem)


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
        raise UnsupportedOperationError("This drama has no pages yet. Upload some first.")
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
    _require_ocr_backend(drama_id)
    engine_name = engine or settings_service.get_default_engine()
    # Pure-MT engines are called once per region, outside call_llm_json.
    built = llm_tasks.bound_batches(_build_engine(engine_name))
    started = pages_svc.start_drama_job(
        drama_id, _run_job, pages_svc.job_id(drama_id), drama_id, mode, targets,
        engine_name, built, detect_backend,
        description=f"Scanlate translate (drama {drama_id})")
    return {**started, "engine": engine_name, "mode": mode}


def _predecessor_context(drama_id: int, page_id: int) -> str:
    prev = None
    for p in db.list_pages(drama_id):
        if p["id"] == page_id:
            break
        prev = p
    return (prev or {}).get("context_summary") or ""


def _detector_note(detect_backend: str, notes: list) -> tuple:
    import scanlate
    fell_back = any(n[0] == "warning" for n in notes)
    if detect_backend == "cv" or fell_back:
        why = " (the ML detector could not run)" if fell_back else ""
        return ("info", f"Detector: OpenCV heuristic{why}.")
    if detect_backend == "ml" or scanlate.bubble_ml_weights_cached():
        return ("info", "Detector: ML model.")
    return ("info", "Detector: OpenCV heuristic (the ML model is not downloaded).")


def _translate(bubbles: list, engine, engine_name: str, drama: dict, glossary, context: str,
               drama_id: int, notes: list):
    """Translates the eligible regions in place. Each region is sent under
    an explicit key and its answer is applied through a key -> region map
    built here, never by the position of the answer. Returns (ok, context):
    ok is False when a translation was wanted but not applied (the notes
    say why); context is the new rolling context, or None if none."""
    import scanlate
    by_key = {str(i): b for i, b in enumerate(bubbles)
              if not b.get("skip") and not scanlate.region_excluded_from_auto(b)
              and (b.get("source_text") or "").strip()}
    if not by_key:
        return True, None
    keyed = {key: b["source_text"] for key, b in by_key.items()}

    def usage_cb(inp, out):
        db.log_usage(drama_id, engine_name, getattr(engine, "model", engine_name),
                     "scanlate_translate", inp, out,
                     translate_engines.estimate_cost_for_engine(engine, inp, out))

    try:
        result, new_context = scanlate.translate_regions_by_id(
            keyed, engine, drama, previous_context=context, usage_cb=usage_cb,
            glossary_terms=glossary)
    except (HeadroomError, LLMTaskTimeout):
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
    return True, new_context


def _redo_would_lose_work(existing: list, bubbles: list, translated_ok: bool) -> bool:
    """A redo replaces the page's regions. It must not swap translated
    regions for nothing (detection found none) or for OCR-only text (the
    translation failed); the page then keeps what it has."""
    if not bubbles:
        return True
    return not translated_ok and any((b.get("translated_text") or "").strip()
                                     for b in existing)


def _process_page(drama_id: int, drama: dict, page_id: int, mode: str, engine, engine_name: str,
                  detect_kwargs: dict, glossary) -> str:
    """One page. Returns "skipped", "stale", "kept" (a redo that failed; the
    old regions stay), "done" or "translated"."""
    import scanlate
    page = db.get_page(page_id, drama_id=drama_id)
    if page is None:
        return "skipped"                        # deleted meanwhile
    existing = db.load_bubbles(page_id)
    if mode == "missing" and existing:
        if not page.get("rendered_filename"):
            notes = []
            render_svc.render_page(drama_id, page_id, notes)
            render_svc.append_notes(page_id, notes)
        return "skipped"
    expected_ids, expected_rev = [b["id"] for b in existing], int(page.get("rev") or 0)
    src = render_svc.original_path(drama_id, page)
    lang = drama.get("source_language") or "zh"
    with pages_svc.pipeline_lock():
        bubbles, detect_notes = scanlate.detect_and_ocr_page(src, lang, page_id=page_id,
                                                             **detect_kwargs)
    notes = [_detector_note(detect_kwargs["detect_backend"], detect_notes)]
    notes += detect_notes
    if bubbles and not any((b.get("source_text") or "").strip() for b in bubbles):
        notes.append(("warning", f"OCR ({detect_kwargs['ocr_backend']}) found no text in any "
                                 "region. Check that this OCR backend is installed "
                                 "(Diagnostics)."))
    new_context, translated_ok = None, True
    if bubbles:
        translated_ok, new_context = _translate(
            bubbles, engine, engine_name, drama, glossary,
            _predecessor_context(drama_id, page_id), drama_id, notes)
    if existing and _redo_would_lose_work(existing, bubbles, translated_ok):
        notes.append(("warning", "Redo did not produce a usable result for this page, so its "
                                 "existing regions were kept unchanged. Try Redo this page "
                                 "again."))
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
        render_svc.render_page(drama_id, page_id, render_notes)
    except Exception as exc:
        render_notes.append(("error", f"Render failed: {type(exc).__name__}: "
                             f"{translate_engines.redact_secrets(str(exc))}"))
    render_svc.append_notes(page_id, render_notes)
    return "translated" if new_context is not None else "done"


def _run_job(jid: str, drama_id: int, mode: str, page_ids: list, engine_name: str, engine,
             detect_backend: str):
    drama = db.get_drama(drama_id) or {}
    lang = drama.get("source_language") or "zh"
    glossary = db.list_glossary_terms(drama["series_id"]) if drama.get("series_id") else None
    ocr_backend = settings_service.resolve_ocr_backend(lang)
    detect_kwargs = {"detect_backend": detect_backend,
                     "hf_token": settings_service.resolve_key("hf_token") or None,
                     "ocr_backend": ocr_backend,
                     "tesseract_cmd": settings_service.get_tesseract_cmd()}
    counts = {"translated": 0, "done": 0, "skipped": 0, "stale": 0, "kept": 0, "failed": 0}
    total = len(page_ids)
    # Cancel and a total deadline for every AI call of the run, not only between
    # pages. One request per page, and with thinking on DeepSeek may take the
    # whole client timeout to answer, so the deadline is the per-request one.
    with llm_tasks.bounded_llm_calls(jid, lambda: background_jobs.is_cancel_requested(jid),
                                     deadline=llm_tasks.request_deadline_for(engine)):
        for n, pid in enumerate(page_ids, start=1):
            render_svc.check_cancel(jid)
            background_jobs.update_progress(jid, (n - 1) / total, f"Page {n} of {total}")
            try:
                counts[_process_page(drama_id, drama, pid, mode, engine, engine_name,
                                     detect_kwargs, glossary)] += 1
            except TranslationCancelled:
                raise background_jobs.JobCancelled(jid)
            except (background_jobs.JobCancelled, HeadroomError):
                raise
            except Exception as exc:
                counts["failed"] += 1
                try:
                    db.update_page(pid, run_notes=pages_svc.notes_to_json(
                        [("error", f"This page failed: {type(exc).__name__}: "
                                    f"{translate_engines.redact_secrets(str(exc))}")]))
                except Exception as note_exc:
                    from applog import get_logger
                    get_logger().warning("Could not save the error note for page %s: %s", pid,
                                         translate_engines.redact_secrets(str(note_exc)))
                # The abandoned request may still be running; every later page
                # would be refused, so the run ends with this error.
                if isinstance(exc, LLMTaskTimeout):
                    raise
    parts = [f"{counts['translated']} translated"]
    if counts["done"]:
        parts.append(f"{counts['done']} without translation")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped (already done)")
    if counts["stale"]:
        parts.append(f"{counts['stale']} edited meanwhile")
    if counts["kept"]:
        parts.append(f"{counts['kept']} kept unchanged (redo failed)")
    if counts["failed"]:
        parts.append(f"{counts['failed']} failed")
    background_jobs.update_progress(jid, 1.0, "Pages: " + ", ".join(parts) + ".")
