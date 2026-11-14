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
import contextvars
import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor

import background_jobs
import db
import translation_guide as tguide
from engine_backends import engine_registry
from engine_backends import shared as engine_shared
from engine_backends import llm_tasks, paid_calls
from engine_backends.run_limits import TranslateRunStopped
from engine_backends.vision import VISION_ENGINES
from engine_backends.shared import LLMTaskTimeout, TranslationCancelled
from lib import cancellable_lock
from memory_headroom import HeadroomError
from services import comic_chapters_service, image_text_service, page_import_limits
from services import scanlate_pages_service as pages_svc
from services import scanlate_render_service as render_svc
from services import paid_job_service, settings_service, translate_service
from lib.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    UnsupportedOperationError,
)

_OCR_ERRORED = "OCR failed on this page, so nothing was saved. Check the OCR backend in Diagnostics."
_OCR_NO_TEXT = "OCR found no text on this page, so nothing was saved. Redo it after checking the OCR backend."


class _DetectSkipped(Exception):
    """A read-ahead detection that was not started because the page before it failed."""


class OcrReadFailed(Exception):
    """A page whose OCR produced no text. Its message is fixed text, so the
    job's failure note carries no path or exception detail."""


MODES = ("missing", "page", "pages", "all")
_CONFIRM_PAGES = ("Redo replaces the text regions of the selected pages, including any you "
                  "edited. Confirm to continue.")
_CONFIRM_ALL = ("Redo all replaces the text regions of every page, including any you "
                "edited. Confirm to continue.")


def _build_engine(engine_name: str):
    if engine_name not in engine_registry.ENGINES:
        raise InvalidInputError(engine_registry.unknown_engine_message(engine_name))
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    try:
        return engine_registry.get_engine(
            engine_name, api_key,
            free_tier=engine_name == "gemini" and bool(settings_service.get("gemini_free_tier")),
            base_url=settings_service.ollama_url()
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
                        str(settings_service.get("tesseract_cmd")) or None)


def _require_paddle() -> None:
    """Captions on the art are found by PaddleOCR whatever the page OCR is."""
    import ocr
    ocr.require_backend("paddle", None)


def _require_opencv() -> None:
    """scanlate imports cv2 lazily inside detection and rendering, so without
    it every page would fail and the job would still end "done"."""
    if importlib.util.find_spec("cv2") is None:
        raise DependencyUnavailableError(
            "Scanlate needs opencv-python. Install it in Diagnostics.",
            details={"reason": "missing_package", "package": "opencv-python"})


# Small on purpose: a reader who stops after a page should not have paid for a whole chapter.
READ_AHEAD_PAGES = 3


def start_run(drama_id: int, mode: str = "missing", page_id: int | None = None, confirm: bool = False,
              engine: str | None = None, detect_backend: str = "auto",
              chapter_id: str | None = None, art_captions: bool = False,
              read_ahead_from: int | None = None, page_ids: list | None = None,
              keep_text: bool = False) -> dict:
    """Starts the drama's Scanlate run. 409: a job or import is running,
    or mode "all" without confirm. 503: no key for the engine. 400: no
    pages. `chapter_id` limits "missing"/"all" to one chapter. `art_captions` also
    translates the large text drawn on the art (needs PaddleOCR). `read_ahead_from` is the page
    a reader is on: the run covers it and the READ_AHEAD_PAGES visible pages after it ("missing" skips the translated ones), and (nobody
    having asked for this spend) is refused for a capped engine while no monthly cap is set.
    Mode "pages" redoes the pages in `page_ids` (confirm required, like "all"). `keep_text` (modes
    page, pages, all) skips detection and OCR: the text already read is translated again and
    the pages are typeset again; a page with no text read yet is read normally.
    Returns {job_id, engine, mode}."""
    pages_svc.require_drama(drama_id)
    if mode not in MODES:
        raise InvalidInputError("mode must be 'missing', 'page', 'pages' or 'all'.")
    if keep_text and mode == "missing":
        raise InvalidInputError("keep_text only works when redoing pages.")
    if (mode == "pages") != (page_ids is not None):
        raise InvalidInputError("page_ids is required for mode 'pages' and used only there.")
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
    if mode == "pages":
        if not page_ids:
            raise InvalidInputError("Pick at least one page to redo.")
        owned = {p["id"] for p in pages}
        if any(pid not in owned for pid in page_ids):
            raise InvalidInputError("Some of those pages are not in this title.")
        if not confirm:
            raise ConflictError(_CONFIRM_PAGES)
    if read_ahead_from is not None and (mode != "missing" or chapter_id is not None):
        raise InvalidInputError("read_ahead_from only works with mode 'missing' and no chapter.")
    # Pages marked "not part of the story" are skipped, and a chapter limits the run.
    if mode == "pages":
        # Reading order, and hidden pages stay out of the story.
        wanted = set(page_ids or [])
        targets = [pid for pid in comic_chapters_service.run_page_ids(drama_id) if pid in wanted]
    else:
        targets = ([page_id] if mode == "page"
                   else comic_chapters_service.run_page_ids(drama_id, chapter_id))
    if read_ahead_from is not None:
        if read_ahead_from not in targets:
            raise UnsupportedOperationError("That page is not in the reading order.")
        start = targets.index(read_ahead_from)
        targets = targets[start:start + 1 + READ_AHEAD_PAGES]
    if not targets:
        raise UnsupportedOperationError("No pages to run: they are all hidden.")
    _require_opencv()
    _require_ocr_backend(drama_id)
    if art_captions:
        _require_paddle()
    engine_name = engine or str(settings_service.get("default_engine"))
    if read_ahead_from is not None:
        paid_job_service.require_cap_for_unattended(
            engine_name, bool(settings_service.get("gemini_free_tier")), None)  # runs the default model
    # Pure-MT engines are called once per region, outside call_llm_json.
    built = llm_tasks.bound_batches(_build_engine(engine_name))
    started = pages_svc.start_drama_job(
        drama_id, _run_job, pages_svc.job_id(drama_id), drama_id, mode, targets,
        engine_name, built, detect_backend, art_captions, keep_text,
        description=f"Scanlate translate (title {drama_id})")
    return {**started, "engine": engine_name, "mode": mode}


def _has_text(region: dict) -> bool:
    return bool((region.get("source_text") or "").strip()
                or (region.get("translated_text") or "").strip())


def _predecessor_page(drama_id: int, page_id: int):
    """The page before this one in the run's order. Hidden pages are not part
    of the story, so they are stepped over; a page that is itself hidden (a
    single-page redo) falls back to the full order."""
    visible = {p["id"] for p in db.list_pages(drama_id)} & set(
        comic_chapters_service.run_page_ids(drama_id))
    prev = None
    for p in db.list_pages(drama_id):
        if p["id"] == page_id:
            break
        if page_id not in visible or p["id"] in visible:
            prev = p
    return prev


def _predecessor_context(drama_id: int, page_id: int) -> str:
    """The stored context of the page before this one in the run's order."""
    return (_predecessor_page(drama_id, page_id) or {}).get("context_summary") or ""


_TAIL_REGIONS = 3


def _predecessor_tail(drama_id: int, page_id: int) -> str:
    """The last translated lines of the previous page, source beside English.
    A webtoon sentence often runs across a page break, and the one-sentence
    summary alone leaves the next page's opening fragment with nothing to
    attach to."""
    prev = _predecessor_page(drama_id, page_id)
    if prev is None:
        return ""
    lines = [f"{(b.get('source_text') or '').strip()} => {(b.get('translated_text') or '').strip()}"
             for b in db.load_bubbles(prev["id"])
             if (b.get("translated_text") or "").strip() and not b.get("skip")]
    if not lines:
        return ""
    return ("Last lines of the previous page (source => English); the first lines of this page "
            "may continue the same sentence: " + " | ".join(lines[-_TAIL_REGIONS:]))


def _translation_context(drama_id: int, page_id: int) -> str:
    return " ".join(part for part in (_predecessor_context(drama_id, page_id),
                                       _predecessor_tail(drama_id, page_id)) if part)


_STAMP_LOOKBACK_PAGES = 4

_WAITING_NOTE = "Waiting for another comic job to finish with the OCR and detector models"


def _detector_note(detect_backend: str, notes: list) -> tuple:
    import scanlate_detect
    fell_back = any(scanlate_detect.ML_FALLBACK_SENTENCE in str(n[-1]) for n in notes)
    if detect_backend == "cv" or fell_back:
        why = " (the ML detector could not run)" if fell_back else ""
        return ("info", f"Detector: OpenCV heuristic{why}.")
    if detect_backend == "ml" or scanlate_detect.bubble_ml_weights_cached():
        return ("info", "Detector: ML model.")
    return ("info", "Detector: OpenCV heuristic (the ML model is not downloaded).")


def load_translation_guide(drama: dict) -> tuple:
    """(glossary terms, character notes) for a title's comic translations: the series glossary
    and the series cast's pronouns and voice notes, built as the subtitle path builds them
    (translation_guide.build_character_guidance). A title with no series has neither."""
    series_id = (drama or {}).get("series_id")
    if not series_id:
        return None, ""
    return (db.list_effective_glossary_terms(series_id),
            tguide.build_character_guidance(db.list_series_characters(series_id)))


def _page_image(drama_id: int, page_id: int, engine_name: str):
    """(bytes, mime, scale) of the page as it is sent with its regions, or None
    when the setting is off, the engine takes no pictures, or the file can't be
    prepared; the page is then translated from its text alone. `scale` maps a
    region's box from the stored page to the sent picture, which is cut down
    to the provider's size limit."""
    if not settings_service.get("scanlate_page_image") or engine_name not in VISION_ENGINES:
        return None
    try:
        page = db.get_page(page_id, drama_id=drama_id) or {}
        with open(render_svc.original_path(drama_id, page), "rb") as handle:
            content = handle.read()
    except Exception:
        return None
    return page_picture(content)


def page_picture(content: bytes):
    """_page_image's (bytes, mime, scale) for a page already in memory, or None
    when it can't be prepared. The caller has checked the setting and the engine."""
    try:
        _fmt, width, _height = page_import_limits.check_image(content)
        data, mime, sent_width, _sent_height = image_text_service.prepare_picture(content)
    except Exception:
        return None
    return data, mime, sent_width / width


# Measured on 16 comic pages: with the picture, Haiku 5.5 reads dialogue as well
# as Sonnet 5.5 at about a fifteenth of the cost.
PAGE_IMAGE_CLAUDE_MODEL = "claude-haiku-5-5"


def _picture_engine(engine, engine_name: str):
    """The engine that reads a page's picture: the chosen Claude engine is swapped
    for Haiku 5.5 here only, so text-only translation keeps the model the user
    picked. Any other engine, or a Haiku that can't be built, is used as chosen."""
    if engine_name != "claude" or getattr(engine, "model", None) == PAGE_IMAGE_CLAUDE_MODEL:
        return engine
    try:
        return llm_tasks.bound_batches(engine_registry.get_engine(
            "claude", translate_service.resolve_api_key("claude"), model=PAGE_IMAGE_CLAUDE_MODEL))
    except Exception:
        return engine


def _translate_with_image(by_key: dict, page_image, engine, drama: dict, context: str, usage_cb,
                          glossary, character_notes: str, chapter_brief: str, notes: list):
    """(result, new_context) of the page-with-image request, or (None, context)
    after a note when it failed or its answer could not be matched by id, so
    the caller translates the page from its text instead."""
    import scanlate_translate
    data, mime, scale = page_image
    regions = [{"id": key, "text": b["source_text"],
                "box": {name: round(b[name] * scale) for name in ("x", "y", "w", "h")},
                **({"sound_effect": True} if b.get("kind") == "sfx" else {})}
               for key, b in by_key.items()]
    try:
        return scanlate_translate.translate_regions_with_image(
            regions, data, mime, engine, drama, previous_context=context, usage_cb=usage_cb,
            glossary_terms=glossary, character_notes=character_notes, chapter_brief=chapter_brief)
    except (HeadroomError, LLMTaskTimeout, TranslateRunStopped):
        raise
    except Exception as exc:
        reason = f"{type(exc).__name__}: {engine_shared.redact_secrets(str(exc))}"
        notes.append(("info", f"Reading this page's picture failed ({reason}); it was translated "
                              "from its text alone."))
        return None, context


def _translate(bubbles: list, engine, engine_name: str, drama: dict, glossary, context: str,
               drama_id: int, notes: list, character_notes: str = "", chapter_brief: str = "",
               page_image=None):
    """Translates the eligible regions in place. Each region is sent under
    an explicit key and its answer is applied through a key -> region map
    built here, never by the position of the answer. Returns (ok, context):
    ok is False when a translation was wanted but not applied (the notes
    say why); context is the new rolling context, or None if none.
    `page_image` is _page_image's result: the page is then sent with its
    regions, and the text-only request below is the fallback when that fails."""
    import scanlate_regions
    import scanlate_translate
    # A sound effect is translated only to be glossed beside the art, so it is
    # asked for only when the person turned that on.
    gloss_sfx = bool(settings_service.get("scanlate_sfx_gloss"))
    by_key = {str(i): b for i, b in enumerate(bubbles)
              if not b.get("skip")
              and (gloss_sfx or not scanlate_regions.region_excluded_from_auto(b))
              and (b.get("source_text") or "").strip()}
    if not by_key:
        return True, None
    keyed = {key: b["source_text"] for key, b in by_key.items()}

    usage_cb = paid_calls.usage_logger(drama_id, engine_name, engine, "scanlate_translate")

    result, new_context = None, context
    if page_image is not None and len(keyed) <= scanlate_translate.REGIONS_PER_CALL:
        picture_engine = _picture_engine(engine, engine_name)
        result, new_context = _translate_with_image(
            by_key, page_image, picture_engine, drama, context,
            paid_calls.usage_logger(drama_id, engine_name, picture_engine, "scanlate_translate"), glossary, character_notes,
            chapter_brief, notes)
    try:
        if result is None:
            result, new_context = scanlate_translate.translate_regions_by_id(
                keyed, engine, drama, previous_context=context, usage_cb=usage_cb,
                glossary_terms=glossary, character_notes=character_notes,
                chapter_brief=chapter_brief)
    except (HeadroomError, LLMTaskTimeout, TranslateRunStopped):
        # The Ollama check refuses on every page, and a timed-out request is
        # still running and would refuse the next page's call: stop the job once.
        raise
    except Exception as exc:
        notes.append(("warning", f"Translation failed ({type(exc).__name__}: {exc}). The OCR "
                                 "text was saved; use Redo this page to try again."))
        return False, None
    if result is None:
        result = scanlate_translate.retry_missing_regions(
            keyed, {}, engine, drama, previous_context=context, usage_cb=usage_cb,
            glossary_terms=glossary, character_notes=character_notes,
            chapter_brief=chapter_brief) or None
        new_context = context
    if result is None:
        notes.append(("warning", "The translation answer could not be matched to this page's "
                                 "regions, so none of it was applied. The OCR text was saved; "
                                 "use Redo this page to try again."))
        return False, None
    if len(result) < len(by_key):
        result = scanlate_translate.retry_missing_regions(
            keyed, result, engine, drama, previous_context=context, usage_cb=usage_cb,
            glossary_terms=glossary, character_notes=character_notes,
            chapter_brief=chapter_brief)
    for key, text in result.items():
        by_key[key]["translated_text"] = text
    # The picture request is told to answer "" for a watermark; a model can also do
    # that for a real balloon, and the page would then show the original with no
    # sign that anything went wrong.
    blank = sum(1 for key in result if not (result[key] or "").strip())
    if blank:
        notes.append(("warning", f"{blank} of {len(by_key)} regions came back with an empty "
                                 "translation and were left as they are. If one is real "
                                 "dialogue, type it in the Review screen or use Redo this page."))
    if len(result) < len(by_key):
        # A rejected chunk of a dense page is simply absent from the result.
        # What was translated is kept on the regions (a first run saves it); the
        # False return is what stops a redo from replacing a complete page.
        notes.append(("warning", f"{len(by_key) - len(result)} of {len(by_key)} regions weren't "
                                 "translated: part of the answer couldn't be matched to them "
                                 "by id. Use Redo this page to try again."))
        return False, new_context
    return True, new_context


def _translated_since(bubbles: list, english_before: list) -> list:
    """The regions whose English differs from `english_before` (their English when the
    translation call began), i.e. the ones that call translated."""
    return [b for b, before in zip(bubbles, english_before) if b.get("translated_text") != before]


def _condense_signs(drama_id: int, page_id: int, bubbles: list, engine, engine_name: str,
                    drama: dict, notes: list) -> None:
    """Shortens the English of a sign that would be set below the readable size on its band.
    A page whose picture can't be read, or an answer that can't be used, leaves the full
    translation as it was; only the stop signals the run lets through are raised."""
    import scanlate_condense
    import scanlate_detect
    import scanlate_render
    from core import LANGUAGE_NAMES
    if not any(b.get("kind") == "sign" and b.get("translated_text") for b in bubbles):
        return
    try:
        page = scanlate_detect.decode_page(render_svc.original_path(
            drama_id, db.get_page(page_id, drama_id=drama_id) or {}))
        custom_fonts = render_svc._custom_fonts(drama_id)
        changed = scanlate_condense.condense_long_signs(
            bubbles, lambda b: scanlate_render.sign_fitted_size(page, b, b["translated_text"], custom_fonts),
            lambda wanted: scanlate_condense.ask_for_shorter(
                engine, LANGUAGE_NAMES.get((drama or {}).get("source_language") or "zh", "source-language"),
                wanted, paid_calls.usage_logger(drama_id, engine_name, engine, "scanlate_translate")))
    except (HeadroomError, LLMTaskTimeout, TranslateRunStopped, TranslationCancelled,
            background_jobs.JobCancelled):
        raise
    except Exception:
        return
    if changed:
        notes.append(("info", f"{len(changed)} sign text{'s were' if len(changed) > 1 else ' was'} "
                              "shortened to fit the band it sits on. Retype the full text in the "
                              "Review screen if you prefer it."))


def _ocr_lost_text(existing: list, notes) -> bool:
    """OCR failed on some regions while the page already holds text (OCR'd,
    hand-corrected or translated): replacing it would blank those regions."""
    return (any(level == "error" for level, *_ in notes)
            and any(_has_text(b) for b in existing))


def _is_translated_caption(region: dict) -> bool:
    return (region.get("kind") == "caption" and _has_text(region)
            and bool((region.get("translated_text") or "").strip()))


def _redo_would_lose_work(existing: list, bubbles: list, translated_ok: bool,
                          notes: list | tuple = ()) -> bool:
    """A redo replaces the page's regions. It must not swap translated
    regions for nothing (detection found none), for OCR-only text (the
    translation failed or was partial), for a page where OCR lost some
    regions, or for one where no region ended up translated; the page then
    keeps what it has."""
    if not bubbles or _ocr_lost_text(existing, notes):
        return True
    # Captions on the art come from a separate detector: when it fails, or the
    # run has the option off, their earlier translations are not replaced by nothing.
    if (any(_is_translated_caption(b) for b in existing)
            and not any(b.get("kind") == "caption" and _has_text(b) for b in bubbles)):
        return True
    if not any((b.get("translated_text") or "").strip() for b in existing):
        return False
    return not translated_ok or not any((b.get("translated_text") or "").strip()
                                        for b in bubbles)


def _detect_stage(drama_id: int, drama: dict, page_id: int, mode: str, detect_kwargs: dict,
                  jid: str, keep_text: bool = False):
    """Detection and OCR for one page: no AI call, so the run can do it for the
    next page while this one is being translated. Returns "skipped" (final
    outcome) or the detected page for _process_page to translate and save."""
    import scanlate
    page = db.get_page(page_id, drama_id=drama_id)
    if page is None:
        return "skipped"                        # deleted meanwhile
    existing = db.load_bubbles(page_id)
    # Regions that hold no text came from a failed or empty OCR; counting them
    # as done would make "Translate all" skip the page forever.
    if mode == "missing" and any(_has_text(b) for b in existing):
        if not page.get("rendered_filename"):
            render_svc.check_cancel(jid)        # a read-ahead may start after a cancel
            notes = []
            render_svc.render_page(drama_id, page_id, notes, lambda: render_svc.check_cancel(jid))
            render_svc.append_notes(page_id, notes)
        return "skipped"
    expected_ids, expected_rev = [b["id"] for b in existing], int(page.get("rev") or 0)
    if keep_text and any((b.get("source_text") or "").strip() for b in existing):
        # Detection and OCR are most of a page's time and cost nothing to skip when the text
        # is already read; the regions go back through translate and typeset as they are.
        return existing, expected_ids, expected_rev, [dict(b) for b in existing], []
    render_svc.check_cancel(jid)                # a read-ahead may start after a cancel
    src = render_svc.original_path(drama_id, page)
    lang = drama.get("source_language") or "zh"
    waiting = []

    def check_while_waiting():
        render_svc.check_cancel(jid)
        # Detection holds the pipeline lock behind another comic job's model run; without a
        # note the job reads "Page N of M" and looks hung. The next page's message replaces it.
        if not waiting:
            waiting.append(True)
            progress = (background_jobs.get_status(jid) or {}).get("progress")
            background_jobs.update_progress(jid, progress or 0.0, _WAITING_NOTE)

    with cancellable_lock.hold(pages_svc.pipeline_lock(), check_while_waiting):
        bubbles, detect_notes = scanlate.detect_and_ocr_page(src, lang, page_id=page_id,
                                                             **detect_kwargs)
    if bubbles and not any((b.get("source_text") or "").strip() for b in bubbles):
        ocr_errored = any(level == "error" for level, _ in detect_notes)
        raise OcrReadFailed(_OCR_ERRORED if ocr_errored else _OCR_NO_TEXT)
    return existing, expected_ids, expected_rev, bubbles, detect_notes


def _flag_repeated_site_marks(drama_id: int, page_id: int, bubbles: list) -> None:
    """Skips a region that repeats, in place, a region of the few pages before
    this one: a site's stamp, which the per-page check misses when OCR drops
    the address. Runs when the page is processed, not when it is detected: the
    read-ahead detects the next page before this one's regions are saved."""
    import scanlate_watermark
    page = db.get_page(page_id) or {}
    width, height = int(page.get("width") or 0), int(page.get("height") or 0)
    earlier = []
    for p in db.list_pages(drama_id):
        if p["idx"] >= int(page.get("idx") or 0):
            break
        if (int(p.get("width") or 0), int(p.get("height") or 0)) == (width, height):
            earlier.append(p["id"])
    # The nearest few pages: a stamp is on every page, and loading all would
    # cost a query per page of a long chapter.
    earlier_regions = [db.load_bubbles(pid) for pid in earlier[-_STAMP_LOOKBACK_PAGES:]]
    scanlate_watermark.flag_site_marks(bubbles, earlier_regions, width, height)
    scanlate_watermark.flag_edge_fragments(bubbles, width, height)
    scanlate_watermark.flag_page_numbers(bubbles, width, height)


def _rerender(drama_id: int, page_id: int, jid: str) -> None:
    """Typesets a page again after its text changed. A failed render clears the
    page's typeset image, which shows the old text, so the original is shown."""
    notes = []
    try:
        render_svc.render_page(drama_id, page_id, notes, lambda: render_svc.check_cancel(jid))
    except background_jobs.JobCancelled:
        raise
    except Exception as exc:
        db.update_page(page_id, rendered_filename=None)
        notes.append(("error", f"Render failed: {type(exc).__name__}: "
                      f"{engine_shared.redact_secrets(str(exc))}"))
    render_svc.append_notes(page_id, notes)


def _join_page_break_caption(drama_id: int, drama: dict, previous_id: int, page_id: int,
                             engine, engine_name: str, guide: tuple, jid: str) -> None:
    """A caption the page break cut between `previous_id` and `page_id` (both
    translated and typeset earlier in this run) is translated as one line and
    its English split between them (scanlate.join_caption_across_pages). Each
    half is written only if that region still holds the text read here, the
    previous page's only after this page's, so a caption edited meanwhile is
    never left half joined; a page whose half was written is typeset again."""
    import scanlate
    previous = db.get_page(previous_id, drama_id=drama_id)
    if previous is None or not int(previous.get("height") or 0):
        return
    upper, lower = db.load_bubbles(previous_id), db.load_bubbles(page_id)
    before = {b["id"]: b.get("translated_text") or "" for b in upper + lower}
    scanlate.join_caption_across_pages(
        {"id": previous_id, "bubbles": upper, "height": int(previous["height"])}, lower,
        engine, drama, _predecessor_context(drama_id, page_id), guide[0],
        paid_calls.usage_logger(drama_id, engine_name, engine, "scanlate_translate"),
        character_notes=guide[1])
    for pid, regions in ((page_id, lower), (previous_id, upper)):
        changed = [b for b in regions if (b.get("translated_text") or "") != before[b["id"]]]
        if not changed:
            continue
        if not all(db.update_bubble_fields(b["id"], {"translated_text": b["translated_text"]},
                                           expected={"translated_text": before[b["id"]]},
                                           page_id=pid) for b in changed):
            return
        _rerender(drama_id, pid, jid)


def _process_page(drama_id: int, drama: dict, page_id: int, mode: str, engine, engine_name: str,
                  detect_kwargs: dict, guide: tuple, jid: str, detected=None,
                  previous_id: int | None = None, chapter_brief: str = "",
                  keep_text: bool = False) -> str:
    """One page. Returns "skipped", "stale", "kept" (a redo that failed; the
    old regions stay), "done" or "translated". `detected` is the page's
    _detect_stage result when the run already started it (a Future), else it
    runs here. `previous_id` is the page just before it in the run, when this
    run translated that page: a caption cut between the two is then joined."""
    started = time.monotonic()
    result = (detected.result() if detected is not None
              else _detect_stage(drama_id, drama, page_id, mode, detect_kwargs, jid, keep_text))
    detect_wait = time.monotonic() - started
    if isinstance(result, str):
        return result
    existing, expected_ids, expected_rev, bubbles, detect_notes = result
    _flag_repeated_site_marks(drama_id, page_id, bubbles)
    reused = keep_text and not detect_notes and bool(existing)
    notes = ([("info", "Detector: skipped, the text already read was reused.")] if reused
             else [_detector_note(detect_kwargs["detect_backend"], detect_notes)])
    notes += detect_notes
    new_context, translated_ok = None, True
    # A result that will be thrown away is not worth a paid translation call.
    ai_started = time.monotonic()
    english_before = [b.get("translated_text") for b in bubbles]
    if bubbles and not _ocr_lost_text(existing, detect_notes):
        translated_ok, new_context = _translate(
            bubbles, engine, engine_name, drama, guide[0],
            _translation_context(drama_id, page_id), drama_id, notes, guide[1], chapter_brief,
            page_image=_page_image(drama_id, page_id, engine_name))
    if translated_ok:
        # Only the English this call just wrote: a region kept from an earlier run, or typed by
        # hand, is the person's text and is not rewritten.
        _condense_signs(drama_id, page_id, _translated_since(bubbles, english_before), engine,
                        engine_name, drama, notes)
    ai_seconds = time.monotonic() - ai_started
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
    # The old typeset image shows the replaced text: if the render below fails,
    # the reader and the export must fall back to the original, not to it.
    render_started = time.monotonic()
    fields = {"run_notes": pages_svc.notes_to_json(notes), "rendered_filename": None}
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
                             f"{engine_shared.redact_secrets(str(exc))}"))
    # Says which step makes a slow run slow. Detection runs ahead, so its figure is the wait for it.
    render_notes.append(("info", f"Time: detect and OCR wait {detect_wait:.1f}s, AI call "
                                 f"{ai_seconds:.1f}s, typeset {time.monotonic() - render_started:.1f}s."))
    render_svc.append_notes(page_id, render_notes)
    # After this page is typeset, so a refusal or timeout in the extra call
    # cannot leave the page's new text without its image.
    if previous_id is not None and new_context is not None:
        _join_page_break_caption(drama_id, drama, previous_id, page_id, engine, engine_name,
                                 guide, jid)
    return "translated" if new_context is not None else "done"


def _failure_note(exc: Exception) -> str:
    if isinstance(exc, OcrReadFailed):
        return str(exc)
    return f"This page failed: {type(exc).__name__}: {engine_shared.redact_secrets(str(exc))}"


# The share of the progress bar the read-through takes when a run has one: it OCRs (and may
# typeset) every page before the first translation, which is about half the run's time.
_READ_SHARE = 0.5


def _page_texts_when_read(drama_id: int, page_ids: list, ahead: dict, jid: str) -> list:
    """The OCR text of each page, in order, as the page's detect futures finish.
    A page whose detection failed or was skipped as already done contributes
    what is stored for it. Checks for a cancel while it waits."""
    from concurrent.futures import wait
    texts = []
    for i, pid in enumerate(page_ids):
        background_jobs.update_progress(jid, _READ_SHARE * i / len(page_ids),
                                        f"Reading page {i + 1} of {len(page_ids)}")
        future = ahead[pid]
        while not future.done():
            render_svc.check_cancel(jid)
            wait([future], timeout=1)
        result = None if future.exception() else future.result()
        regions = result[3] if isinstance(result, tuple) else db.load_bubbles(pid)
        texts.append(" ".join((b.get("source_text") or "").strip() for b in regions
                              if not b.get("skip") and (b.get("source_text") or "").strip()))
    return texts


def _chapter_brief(jid: str, drama_id: int, drama: dict, page_ids: list, ahead: dict,
                   start_detect, engine, engine_name: str) -> str:
    """Reads the whole run's pages first, then returns one brief of the chapter
    for every page's translation call to carry. The brief is cached beside the
    title under a hash of the pages' text, so running the same chapter again
    costs no call. Never stops the run: any failure but a cancel, a memory
    refusal or a timeout (which end the run for every page anyway) gives ""."""
    import hashlib
    import scanlate_translate
    from engine_backends.run_limits import TranslateRunStopped
    background_jobs.update_progress(jid, 0.0, "Reading the whole chapter first")
    previous = None
    for pid in page_ids:
        # setdefault would call start_detect even for the page already started,
        # detecting the first page twice.
        if pid not in ahead:
            ahead[pid] = start_detect(pid, previous)
        previous = ahead[pid]
    texts = _page_texts_when_read(drama_id, page_ids, ahead, jid)
    background_jobs.update_progress(jid, _READ_SHARE, "Writing the chapter brief")
    key = hashlib.sha1(chr(10).join(texts).encode("utf-8")).hexdigest()[:16]
    cache = os.path.join(db.drama_dir(drama_id), f"chapter_brief_{key}.txt")
    try:
        with open(cache, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        pass
    try:
        brief = scanlate_translate.build_chapter_brief(
            texts, engine, drama, paid_calls.usage_logger(drama_id, engine_name, engine,
                                                          "scanlate_translate"))
    except TranslationCancelled:
        raise background_jobs.JobCancelled(jid)
    except TranslateRunStopped:
        return ""
    except (HeadroomError, LLMTaskTimeout, background_jobs.JobCancelled):
        raise
    except Exception as exc:
        from applog import get_logger
        get_logger().warning("Chapter brief failed: %s", engine_shared.redact_secrets(str(exc)))
        return ""
    if brief:
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(brief)
    return brief


def _run_job(*args, **kwargs):
    try:
        return _run_job_pages(*args, **kwargs)
    finally:
        # The run's OCR models would otherwise sit on the GPU until the app exits.
        import ocr
        ocr.release_models()


def _run_job_pages(jid: str, drama_id: int, mode: str, page_ids: list, engine_name: str, engine,
                   detect_backend: str, art_captions: bool = False, keep_text: bool = False):
    import scanlate_detect
    scanlate_detect.forget_ml_failure()
    drama = db.get_drama(drama_id) or {}
    lang = drama.get("source_language") or "zh"
    guide = load_translation_guide(drama)
    ocr_backend = settings_service.resolve_ocr_backend(lang)
    detect_kwargs = {"detect_backend": detect_backend,
                     "hf_token": settings_service.resolve_key("hf_token") or None,
                     "ocr_backend": ocr_backend,
                     "tesseract_cmd": settings_service.get("tesseract_cmd") or None,
                     "art_captions": art_captions}
    counts = {"translated": 0, "done": 0, "skipped": 0, "stale": 0, "kept": 0, "failed": 0}
    total = len(page_ids)
    # Cancel and a total deadline for every AI call of the run, not only between
    # pages. One request per page, and with thinking on DeepSeek may take the
    # whole client timeout to answer, so the deadline is the per-request one.
    with paid_job_service.job_calls(jid, drama_id, engine, engine_name, "scanlate_translate",
                                    deadline=llm_tasks.request_deadline_for(engine),
                                    lenient_empty=False, no_thinking=True) as limits, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="scanlate-detect") as detector:
        # Detection of the next page runs while this page waits on the AI engine.
        # Only the AI call needs the previous page's context, so that chain stays in order.
        # A page whose predecessor's detection ended the run (a memory refusal, a cancel) is not
        # started, so a refusal is not followed by a second model load. Other failures are per page.
        def start_detect(pid, prev=None):
            def run():
                if prev is not None and isinstance(
                        prev.exception(), (HeadroomError, background_jobs.JobCancelled)):
                    raise _DetectSkipped()
                return _detect_stage(drama_id, drama, pid, mode, detect_kwargs, jid, keep_text)
            # A worker thread starts with an empty context; copy this job's so the read-ahead
            # keeps the offline switch (a remote run never downloads a model) and the rest.
            return detector.submit(contextvars.copy_context().run, run)
        ahead = {page_ids[0]: start_detect(page_ids[0])} if page_ids else {}
        saved_before = None          # the page before this one, if this run translated it
        read_first = total > 1 and settings_service.get("scanlate_chapter_brief")
        brief = (_chapter_brief(jid, drama_id, drama, page_ids, ahead, start_detect, engine,
                                engine_name) if read_first else "")
        # The bar continues from where the read-through left it instead of dropping back to 0%.
        base = _READ_SHARE if read_first else 0.0
        for n, pid in enumerate(page_ids, start=1):
            render_svc.check_cancel(jid)
            if n < total and page_ids[n] not in ahead:
                ahead[page_ids[n]] = start_detect(page_ids[n], ahead.get(pid))
            background_jobs.update_progress(jid, base + (1 - base) * (n - 1) / total,
                                            f"Page {n} of {total}")
            previous_id, saved_before = saved_before, None
            try:
                outcome = _process_page(drama_id, drama, pid, mode, engine, engine_name,
                                        detect_kwargs, guide, jid,
                                        detected=ahead.pop(pid, None), previous_id=previous_id,
                                        chapter_brief=brief, keep_text=keep_text)
                counts[outcome] += 1
                if outcome == "translated":
                    saved_before = pid
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
                                         engine_shared.redact_secrets(str(note_exc)))
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
