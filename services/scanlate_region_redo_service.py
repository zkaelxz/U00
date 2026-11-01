"""Redo one text box of a Scanlate page: read it again, translate it again, typeset the page."""
import db
from engine_backends import llm_tasks, paid_calls
from services import scanlate_pages_service as pages_svc
from services import scanlate_render_service as render_svc
from services import scanlate_run_service as run_svc
from services import settings_service
from lib.errors import ConflictError, DependencyUnavailableError, InvalidInputError, NotFoundError

MODES = ("both", "translate")
_BUSY = "A Scanlate job is running on this title. Try again when it finishes."
_NO_TEXT = "No text could be read in this box. Nothing was changed."
_NO_ANSWER = "The translation could not be read. Nothing was changed."


def redo_region(drama_id: int, page_id: int, region_id: int, expected_rev: int,
                mode: str = "both", engine_name: str = None) -> dict:
    """Reads the box again (mode "both") and translates its text again, then saves both in one
    compare-and-set write and starts a re-render of the page. Nothing is written when the read
    or the translation fails. 409: the page moved on since `expected_rev`, or a job runs on the
    title. Returns the page detail."""
    import scanlate
    import scanlate_translate
    if mode not in MODES:
        raise InvalidInputError("mode must be 'both' or 'translate'.")
    drama = pages_svc.require_drama(drama_id)
    page = pages_svc.require_page(drama_id, page_id)
    pages_svc._require_rev(page, expected_rev)
    if pages_svc.job_busy(drama_id):
        raise ConflictError(_BUSY)
    region = next((b for b in db.load_bubbles(page_id) if b["id"] == region_id), None)
    if region is None:
        raise NotFoundError("No such text box on this page.")
    lang = drama.get("source_language") or "zh"
    source = region.get("source_text") or ""
    if mode == "both":
        run_svc._require_opencv()
        run_svc._require_ocr_backend(drama_id)
        with pages_svc.pipeline_lock():
            source = scanlate.ocr_box_region(
                render_svc.original_path(drama_id, page), region, lang,
                backend=settings_service.resolve_ocr_backend(lang),
                tesseract_cmd=settings_service.get("tesseract_cmd") or None)
    if not source.strip():
        raise InvalidInputError(_NO_TEXT)

    name = engine_name or settings_service.get("default_engine")
    engine = llm_tasks.bound_batches(run_svc._build_engine(name))
    glossary = db.list_glossary_terms(drama["series_id"]) if drama.get("series_id") else None
    result, _context = scanlate_translate.translate_regions_by_id(
        {str(region_id): source}, engine, drama,
        previous_context=run_svc._predecessor_context(drama_id, page_id),
        usage_cb=paid_calls.usage_logger(drama_id, name, engine, "scanlate_translate"),
        glossary_terms=glossary)
    translated = (result or {}).get(str(region_id))
    if not isinstance(translated, str) or not translated.strip():
        raise DependencyUnavailableError(_NO_ANSWER)

    status, rev = db.edit_region(page_id, region_id,
                                 {"source_text": source, "translated_text": translated.strip()},
                                 expected_rev)
    pages_svc._raise_for_write(status, rev)
    try:
        render_svc.start_render(drama_id, page_id)
    except ConflictError:
        # A job claimed the title after the check above; the text is saved and the page
        # shows the old typeset image until the next render.
        pass
    return pages_svc.get_page_detail(drama_id, page_id)
