"""
services/scanlate_pages_service.py -- Scanlate pages for the API
(docs/archive/scanlate-api-spec.md S1 and S2): the panel's config, one page's
detail (regions keyed by their stable id, the page rev and its run notes),
page import (upload or a link import, SO06) with fixed limits, and the comic
editor's one-text-box edit, add, delete and undo (compare-and-set on the
page rev).

Also holds what the Scanlate job services share: the per-drama job id
(`scanlate_<drama_id>`, one job at a time per drama), the per-drama upload
claim a job refuses to start under (and vice versa), the pipeline lock
shared with the extension bridge, and the note cleaner (secrets redacted,
paths stripped) every stored or returned note goes through.

Import limits live in services/page_import_limits.py (user-set
2026-09-29, shared with the URL comic imports; change them there): PNG/JPEG/
WebP/PDF only (the file's own bytes decide, the extension must agree); the
per-image, per-PDF and per-request caps are checked from headers before any
decode. Pages are prepared by page_import_limits.prepare_page (EXIF applied,
tall strips sliced by default) and written ONLY through
sources.pipeline.add_page_images (the one page writer: per-drama lock,
exclusive index claim, exclusive file create). A bad file adds nothing.

No FastAPI import. Plain dicts; errors from lib.errors.
"""
import contextlib
import importlib.util
import json
import os
import re
import threading
import warnings

import background_jobs
from jobs import job_launch
import db
import storage
from services import comic_view_service
from services import page_import_limits as limits
from lib.errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)
from services.ownership_service import require_drama

SLICE_STRIPS_DEFAULT = True

JOB_PREFIX = "scanlate_"
DETECT_BACKENDS = ("auto", "cv", "ml")
_CHUNK = 1024 * 1024
MAX_NOTES = 20
_MAX_NOTE_CHARS = 500

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_BUSY_JOB = "A Scanlate job is running for this title. Wait for it or cancel it first."
_BUSY_UPLOAD = "Pages are being added to this title. Try again when that finishes."


# --- Shared by the job services ------------------------------------------

def job_id(drama_id: int) -> str:
    return f"{JOB_PREFIX}{drama_id}"


def pipeline_lock():
    """The lock every Scanlate model run holds: scanlate's and ocr's model
    caches are unlocked module globals (spec §4). It is the extension
    bridge's own _PIPELINE_LOCK, so the bridge and the API jobs in this
    process queue behind each other instead of racing on first load."""
    import page_server
    return page_server.PIPELINE_LOCK


_claims_lock = threading.Lock()
_uploading = set()


def job_busy(drama_id: int) -> bool:
    job = background_jobs.get_status(job_id(drama_id))
    return bool(job and job["status"] in ("running", "queued"))


@contextlib.contextmanager
def upload_claim(drama_id: int):
    """Held for the whole import: refused (409) while this drama's Scanlate
    job runs or another import is adding pages to it."""
    with _claims_lock:
        if job_busy(drama_id):
            raise ConflictError(_BUSY_JOB)
        if drama_id in _uploading:
            raise ConflictError(_BUSY_UPLOAD)
        _uploading.add(drama_id)
    try:
        yield
    finally:
        with _claims_lock:
            _uploading.discard(drama_id)


def start_drama_job(drama_id: int, target, *args, description: str) -> dict:
    """Starts the drama's one Scanlate job (gpu_touching: OCR and the ML
    detector/LaMa may use the GPU). 409 while it or an import runs."""
    jid = job_id(drama_id)
    with _claims_lock:
        if drama_id in _uploading:
            raise ConflictError(_BUSY_UPLOAD)
        if not job_launch.start_job(jid, target, *args, gpu_touching=True,
                                         description=description, line_fields=()):
            raise ConflictError(_BUSY_JOB)
    return {"job_id": jid}


_WIN_PATH_RE = re.compile(          # folders may hold spaces ("C:\\Users\\Alex Example\\...")
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\\/:'\"<>|*?;\r\n]+[\\/])*[^\\/:'\"<>|*?;\s]*")
_POSIX_SPACED_RE = re.compile(r"(?<![\w.:/\\])/(?:[^/:'\"<>|*?;\r\n]+/)+[^/:'\"<>|*?;\s]*")
_PATH_RE = re.compile(r"(?<![\w.:/\\])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s'\"<>()]+")


def clean_note(text) -> str:
    """Redacts anything key-like and replaces absolute paths and URL queries, so a note is
    safe to store and return (spec §4 secrets and paths)."""
    from engine_backends.shared import redact_secrets, strip_url_queries
    # Hugging Face downloads redirect to signed CDN URLs whose query is a credential.
    text = strip_url_queries(redact_secrets(str(text or "")))
    text = _WIN_PATH_RE.sub("<path>", text)                  # these two allow spaces in folders
    text = _POSIX_SPACED_RE.sub("<path>", text)
    text = _PATH_RE.sub("<path>", text).replace("**", "")
    return text.strip()[:_MAX_NOTE_CHARS]


def notes_to_json(notes) -> str:
    """[(level, message) | (level, code, message)] -> stored run_notes JSON."""
    out = []
    for n in list(notes or [])[:MAX_NOTES]:
        level, message = n[0], n[-1]
        out.append({"level": level if level in ("info", "warning", "error") else "warning",
                    "message": clean_note(message)})
    return json.dumps(out, ensure_ascii=False)


def parse_notes(raw) -> list:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [{"level": str(n.get("level", "warning")), "message": clean_note(n.get("message"))}
            for n in data if isinstance(n, dict)][:MAX_NOTES] if isinstance(data, list) else []


# --- S1: read -------------------------------------------------------------

def require_page(drama_id: int, page_id: int) -> dict:
    page = db.get_page(page_id, drama_id=drama_id)
    if page is None:
        raise NotFoundError("No such page in this title.")
    return page


def ocr_backend_installed(backend: str, tesseract_cmd: str | None = None) -> bool:
    import ocr
    return ocr.backend_problem(backend, tesseract_cmd) is None


def _weights_cached():
    try:
        import scanlate_detect
        import scanlate_inpaint
        return scanlate_detect.bubble_ml_weights_cached(), scanlate_inpaint.lama_ml_weights_cached()
    except Exception:
        return False, False


def default_engine(engines: list | None = None) -> str:
    """The engine a Scanlate run starts with: the Settings default when the
    person saved one that can run, otherwise the cheapest engine that can --
    the stock default is Claude, the dearest, and a page run translates a
    whole chapter. Cheapest by the price of each engine's default model; a
    free or local engine costs nothing. The Settings default when none can run, so
    the run is refused for the missing key as before."""
    import types
    from engine_backends.engine_registry import FREE_ENGINES, effective_default_model
    from engine_backends.pricing import estimate_cost_for_engine
    from services import settings_service, translate_service
    engines = translate_service.list_engines() if engines is None else engines
    usable = [e["name"] for e in engines if e["key_configured"]]
    chosen = str(settings_service.get("default_engine"))
    if settings_service.is_saved("default_engine") and chosen in usable:
        return chosen
    gemini_free = bool(settings_service.get("gemini_free_tier"))

    def price(name):
        if name in FREE_ENGINES:
            return 0.0
        stand_in = types.SimpleNamespace(
            model=effective_default_model(name) or "",
            free_tier=name == "gemini" and gemini_free)
        return estimate_cost_for_engine(stand_in, 1_000_000, 1_000_000)

    return min(usable, key=price, default=chosen)


def get_config(drama_id: int) -> dict:
    """What the React panel needs: engines with key_configured booleans
    (never keys), the default engine, whether the ML detector and LaMa
    weights are cached, the saved OCR backend (and whether it is
    installed), the upload limits, page counts and the job state."""
    from services import settings_service, translate_service
    drama = require_drama(drama_id)
    lang = drama.get("source_language") or "zh"
    ocr_backend = settings_service.resolve_ocr_backend(lang)
    ml_cached, lama_cached = _weights_cached()
    engines = translate_service.list_engines()
    pages = db.list_pages(drama_id)
    with_regions = {b["page_id"] for b in db.list_bubbles_for_drama(drama_id)}
    job = background_jobs.get_status(job_id(drama_id))
    return {
        "drama_id": drama_id,
        "source_language": lang,
        "engines": [{"name": e["name"], "label": e["label"], "free": e["free"],
                     "key_configured": e["key_configured"]} for e in engines],
        "default_engine": default_engine(engines),
        "detect_backends": list(DETECT_BACKENDS),
        "ml_weights_cached": ml_cached,
        "lama_weights_cached": lama_cached,
        "ocr_backend": ocr_backend,
        "ocr_backend_installed": ocr_backend_installed(
            ocr_backend, str(settings_service.get("tesseract_cmd")) or None),
        "page_count": len(pages),
        "pages_with_regions": sum(1 for p in pages if p["id"] in with_regions),
        "pages_rendered": sum(1 for p in pages if p.get("rendered_filename")),
        "job_id": job_id(drama_id),
        "job_running": bool(job and job["status"] in ("running", "queued")),
        "upload_limits": upload_limits(),
    }


def upload_limits() -> dict:
    return {"image_types": ["png", "jpg", "jpeg", "webp"], "pdf": importlib.util.find_spec("pypdf") is not None,
            "max_image_mb": limits.MAX_IMAGE_BYTES // (1024 * 1024),
            "max_image_megapixels": limits.MAX_IMAGE_PIXELS // 1_000_000,
            "max_pdf_mb": limits.MAX_PDF_BYTES // (1024 * 1024),
            "max_pdf_pages": limits.MAX_PDF_PAGES,
            "max_files": limits.MAX_FILES_PER_IMPORT,
            "max_total_mb": limits.MAX_IMPORT_BYTES // (1024 * 1024),
            "strip_slice_ratio": int(limits.STRIP_SLICE_RATIO),
            "slice_strips_default": SLICE_STRIPS_DEFAULT}


def _region_view(b: dict) -> dict:
    return {"id": b["id"], "idx": int(b.get("idx") or 0),
            "x": int(b.get("x") or 0), "y": int(b.get("y") or 0),
            "w": int(b.get("w") or 0), "h": int(b.get("h") or 0),
            "source_text": b.get("source_text") or "",
            "translated_text": b.get("translated_text") or "",
            "font_size": int(b.get("font_size") or 18),
            "font_category": b.get("font_category") or "regular",
            "kind": b.get("kind") or "bubble",
            "skip": bool(b.get("skip")), "include_sfx": bool(b.get("include_sfx")),
            "language": b.get("language"), "orientation": b.get("orientation"),
            "text_color": b.get("text_color") or None}


def get_page_detail(drama_id: int, page_id: int) -> dict:
    """One page: every region (skipped and SFX included, keyed by stable
    id), rev, whether it has a typeset image, and its last run notes. No
    file names or paths."""
    require_drama(drama_id)
    page = require_page(drama_id, page_id)
    ordinal = next((i + 1 for i, p in enumerate(db.list_pages(drama_id))
                    if p["id"] == page_id), 0)
    bubbles = db.load_bubbles(page_id)
    return {"id": page["id"], "drama_id": drama_id, "ordinal": ordinal,
            "width": int(page.get("width") or 0), "height": int(page.get("height") or 0),
            "rev": int(page.get("rev") or 0),
            "undo_depth": db.edit_history_depth(page, [b["id"] for b in bubbles]),
            "has_rendered": bool(page.get("rendered_filename")),
            "regions": [_region_view(b) for b in bubbles],
            "run_notes": parse_notes(page.get("run_notes"))}


def list_run_notes(drama_id: int) -> dict:
    """Every page's last run notes (pages with none left out)."""
    require_drama(drama_id)
    out = []
    for i, p in enumerate(db.list_pages(drama_id)):
        notes = parse_notes(p.get("run_notes"))
        if notes:
            out.append({"page_id": p["id"], "ordinal": i + 1, "notes": notes})
    return {"drama_id": drama_id, "pages": out}


# --- Comic editor: one text box at a time ------------------------------------

REGION_EDIT_FIELDS = ("translated_text", "x", "y", "w", "h", "font_size", "font_category",
                      "kind", "text_color")
_NULLABLE_EDIT_FIELDS = ("text_color",)          # None = the render picks the colour
_STALE = ("This page changed since you opened it (a job or another edit wrote it). "
          "Reload the page and make the change again.")


def _stale(reason: str, rev, message: str = _STALE):
    return ConflictError(message, details={"reason": reason, "rev": rev})


def _check_region_values(values: dict, label: str):
    unknown = [k for k in values if k not in REGION_EDIT_FIELDS]
    if unknown:
        raise InvalidInputError(f"{label}: these fields can't be edited here: "
                                f"{', '.join(sorted(unknown))}.")
    nulls = [k for k, v in values.items() if v is None and k not in _NULLABLE_EDIT_FIELDS]
    if nulls:
        raise InvalidInputError(f"{label}: {', '.join(sorted(nulls))} can't be empty.")
    if values.get("font_category") is not None:
        import scanlate_render
        if values["font_category"] not in scanlate_render.FONT_CATEGORIES:
            raise InvalidInputError("font_category must be one of "
                                    f"{', '.join(scanlate_render.FONT_CATEGORIES)}.")
    if values.get("kind") is not None:
        import scanlate_regions
        if values["kind"] not in scanlate_regions.REGION_KINDS:
            raise InvalidInputError("kind must be one of "
                                    f"{', '.join(scanlate_regions.REGION_KINDS)}.")
    if values.get("text_color") is not None:
        values["text_color"] = values["text_color"].lower()


def _include_chosen_sfx(values: dict):
    # The render and the translate call skip an sfx box unless include_sfx is
    # set, so a box the user turns into sfx would vanish from the page; picking
    # the kind by hand is that per-box override.
    if values.get("kind") == "sfx":
        values["include_sfx"] = True


def _check_inside_page(page: dict, box: dict):
    width, height = int(page.get("width") or 0), int(page.get("height") or 0)
    if (width and box["x"] + box["w"] > width) or (height and box["y"] + box["h"] > height):
        raise InvalidInputError("The text box must stay inside the page.")


def _require_rev(page: dict, expected_rev: int):
    # Early answer only: a job that replaced the page also reissued the box ids,
    # which must read as stale, not as an unknown box. The db write re-checks.
    if int(page.get("rev") or 0) != int(expected_rev):
        raise _stale("stale", int(page.get("rev") or 0))


def _raise_for_write(status: str, rev):
    if status == "missing":
        raise NotFoundError("No such text box on this page.")
    if status == "no_page":
        raise NotFoundError("No such page in this title.")
    if status == "stale":
        raise _stale("stale", rev)
    if status == "full":
        raise InvalidInputError(f"A page can hold at most {db.MAX_REGIONS_PER_PAGE} text boxes.")


def edit_region(drama_id: int, page_id: int, region_id: int, changes: dict,
                expected_rev: int, expected: dict | None = None) -> dict:
    """Changes one text box (its English, box, font size and style, kind,
    colour) only if the page is still at `expected_rev` and, for each field in
    `expected`, the box still holds that value; 409 (details: reason
    "stale" and the current rev) otherwise, so a job that rewrote the page
    is never overwritten blind. The box must stay inside the page. The edit
    goes on the page's undo list. Returns the page detail."""
    require_drama(drama_id)
    page = require_page(drama_id, page_id)
    changes, expected = dict(changes or {}), dict(expected or {})
    if not changes:
        raise InvalidInputError("Send at least one change.")
    _check_region_values(changes, "changes")
    _check_region_values(expected, "expected")
    _include_chosen_sfx(changes)
    _require_rev(page, expected_rev)
    current = next((b for b in db.load_bubbles(page_id) if b["id"] == region_id), None)
    if current is None:
        raise NotFoundError("No such text box on this page.")
    _check_inside_page(page, {k: int(changes.get(k, current.get(k) or 0))
                              for k in ("x", "y", "w", "h")})
    status, rev = db.edit_region(page_id, region_id, changes, expected_rev, expected)
    _raise_for_write(status, rev)
    return get_page_detail(drama_id, page_id)


def create_region(drama_id: int, page_id: int, values: dict, expected_rev: int) -> dict:
    """Adds one text box the user drew (x, y, w, h inside the page; English,
    font size and style, kind and colour optional) at the end of the page's
    reading order, under the same rev check and on the same undo list. 422
    once the page holds MAX_REGIONS_PER_PAGE boxes. Returns the page detail,
    whose last region is the new box."""
    require_drama(drama_id)
    page = require_page(drama_id, page_id)
    values = dict(values or {})
    _check_region_values(values, "region")
    _include_chosen_sfx(values)
    missing = [k for k in ("x", "y", "w", "h") if k not in values]
    if missing:
        raise InvalidInputError(f"region: {', '.join(missing)} required.")
    _require_rev(page, expected_rev)
    _check_inside_page(page, values)
    if len(db.load_bubbles(page_id)) >= db.MAX_REGIONS_PER_PAGE:
        _raise_for_write("full", None)
    status, rev, _new_id = db.create_region(page_id, values, expected_rev)
    _raise_for_write(status, rev)
    return get_page_detail(drama_id, page_id)


def delete_region(drama_id: int, page_id: int, region_id: int, expected_rev: int) -> dict:
    """Removes one text box under the same rev check; undo puts it back with
    every value it had. Returns the page detail."""
    require_drama(drama_id)
    page = require_page(drama_id, page_id)
    _require_rev(page, expected_rev)
    status, rev = db.delete_region(page_id, region_id, expected_rev)
    _raise_for_write(status, rev)
    return get_page_detail(drama_id, page_id)


def undo_region_edit(drama_id: int, page_id: int, expected_rev: int) -> dict:
    """Reverses the page's latest text-box edit, new box or deletion, under
    the same rev check. 409 when the caller's rev is old (reason "stale"),
    when there is nothing to undo ("nothing_to_undo"), or when something
    else wrote the page after that change ("history_changed": undoing would
    overwrite that write, so the undo list is cleared instead). Returns the
    page detail."""
    require_drama(drama_id)
    require_page(drama_id, page_id)
    status, rev = db.undo_region_edit(page_id, expected_rev)
    if status == "no_page":
        raise NotFoundError("No such page in this title.")
    if status == "stale":
        raise _stale("stale", rev)
    if status == "nothing":
        raise _stale("nothing_to_undo", rev, "There is no edit on this page to undo.")
    if status == "broken":
        raise _stale("history_changed", rev,
                     "This page was changed after your last edit (by a job or another "
                     "edit), so that edit can't be undone safely. The undo list was cleared.")
    return get_page_detail(drama_id, page_id)


# --- S2: import -------------------------------------------------------------

def _safe_extension(name) -> str:
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if any(ord(c) < 32 or ord(c) == 127 for c in base):
        raise InvalidInputError("Unsupported file type. Upload PNG, JPEG, WebP or PDF files.")
    ext = os.path.splitext(base)[1].lower()
    if ext not in limits.ALLOWED_EXTENSIONS:
        raise InvalidInputError("Unsupported file type. Upload PNG, JPEG, WebP or PDF files.")
    return ext


def _sniff(head: bytes):
    if head.startswith(_PNG):
        return "png"
    if head.startswith(_JPEG):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF-"):
        return "pdf"
    return None


def _check_pixels(width: int, height: int, what: str):
    if width < 1 or height < 1:
        raise InvalidInputError(f"{what} is empty or corrupt.")
    if width * height > limits.MAX_IMAGE_PIXELS:
        raise InvalidInputError(
            f"{what} is too large (at most {limits.MAX_IMAGE_PIXELS // 1_000_000} megapixels).")


def _normalise(img):
    """EXIF orientation applied, then a fresh pixel-only image (no EXIF,
    ICC or text chunks) in a mode PNG/JPEG can store."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
    return Image.frombytes(img.mode, img.size, img.tobytes())


def _stage_image(src: str, out_dir: str, stem: str, slice_strips: bool) -> list:
    """Checked page file(s) in out_dir, in reading order: the shared page
    rules (limits.prepare_page: caps from the header before any decode,
    EXIF applied, a tall strip sliced when asked)."""
    with open(src, "rb") as f:
        content = f.read()
    try:
        prepared = limits.prepare_page(content, slice_strips)
    except limits.ImageRejected as exc:
        raise InvalidInputError(f"An image was rejected: {exc}.") from None
    outs = []
    for i, (data, ext) in enumerate(prepared):
        out = os.path.join(out_dir, f"{stem}_{i:04d}{ext}")
        with open(out, "wb") as f:
            f.write(data)
        outs.append(out)
    return outs


def _pdf_best_image(page):
    """(pixels, name, w, h) of a PDF page's largest image XObject, read from
    its dictionary without decoding it; None if it has none."""
    best = None
    res = page.get("/Resources")
    res = res.get_object() if res is not None else None
    xobjs = res.get("/XObject") if res is not None else None
    xobjs = (xobjs.get_object() if xobjs is not None else None) or {}
    for name in xobjs:
        obj = xobjs[name].get_object()
        if obj.get("/Subtype") != "/Image":
            continue
        w, h = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
        if best is None or w * h > best[0]:
            best = (w * h, name, w, h)
    return best


_PDF_INFLATE_CAP = 256 * 1024 * 1024


def _limit_pdf_inflation():
    """pypdf inflates a whole stream before anyone can look at it, so a tiny
    PDF can expand to gigabytes. pypdf 6 has explicit output caps
    (pypdf.filters.*_MAX_OUTPUT_LENGTH); set every one that exists to a
    fixed ceiling (never raising a library default that is already lower)."""
    try:
        from pypdf import filters
    except ImportError:
        return
    for name in dir(filters):
        value = getattr(filters, name)
        if name.endswith("_MAX_OUTPUT_LENGTH") and isinstance(value, int) \
                and value > _PDF_INFLATE_CAP:
            setattr(filters, name, _PDF_INFLATE_CAP)


def _stage_pdf(src: str, out_dir: str, stem: str) -> tuple:
    """(page files, pages skipped for having no image). A scanned-manga PDF
    is one raster image per page; the largest one is the page (a small
    logo or watermark may ride along)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DependencyUnavailableError(
            "PDF import needs the pypdf package (install it from Diagnostics).") from None
    _limit_pdf_inflation()
    try:
        reader = PdfReader(src)
        if reader.is_encrypted:
            raise InvalidInputError("That PDF is password-protected.")
        count = len(reader.pages)
    except InvalidInputError:
        raise
    except Exception:
        raise InvalidInputError("A PDF could not be read (corrupt or unsupported).") from None
    if count > limits.MAX_PDF_PAGES:
        raise InvalidInputError(f"A PDF has too many pages (at most {limits.MAX_PDF_PAGES}).")
    outputs, skipped = [], 0
    for i in range(count):
        try:
            page = reader.pages[i]
            best = _pdf_best_image(page)
        except Exception:
            best = None
        if best is None:
            skipped += 1
            continue
        _check_pixels(best[2], best[3], f"PDF page {i + 1}'s image")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                img = page.images[best[1]].image
                width, height = img.size
                _check_pixels(width, height, f"PDF page {i + 1}'s image")
                clean = _normalise(img)
        except InvalidInputError:
            raise
        except Exception:
            skipped += 1
            continue
        out = os.path.join(out_dir, f"{stem}_p{i:04d}.png")
        clean.convert("RGB").save(out, "PNG")
        outputs.append(out)
    return outputs, skipped


def _copy_capped(fileobj, dest: str, limit: int, budget: list) -> int:
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = fileobj.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            budget[0] -= len(chunk)
            if size > limit:
                raise InvalidInputError(
                    f"A file is too large (at most {limit // (1024 * 1024)} MB).")
            if budget[0] < 0:
                raise InvalidInputError(
                    f"The upload is too large (at most {limits.MAX_IMPORT_BYTES // (1024 * 1024)} MB "
                    "in total).")
            out.write(chunk)
    if size == 0:
        raise InvalidInputError("A file is empty.")
    return size


def add_page_images(drama_id: int, files, slice_strips: bool = SLICE_STRIPS_DEFAULT) -> dict:
    """Adds `files` as the drama's next pages, in order. `files` is a list
    of (client_name, binary file-like) pairs; only the name's extension is
    read (and must be a whitelisted one agreeing with the file's bytes).
    Every file is checked and converted before any page is added, so a bad
    file adds nothing (422). 409 while this drama's Scanlate job or another
    import runs. Returns {added, page_ids, pdf_pages_skipped, strips_sliced}.
    The shared entry point for the upload route and link imports (SO06)."""
    require_drama(drama_id)
    files = list(files or [])
    if not files:
        raise InvalidInputError("Choose at least one page image or PDF.")
    if len(files) > limits.MAX_FILES_PER_IMPORT:
        raise InvalidInputError(f"Too many files (at most {limits.MAX_FILES_PER_IMPORT} at once).")
    exts = [_safe_extension(name) for name, _f in files]
    with upload_claim(drama_id), storage.job_workdir("scanlate_import") as staging:
        staged, pdf_skipped, sliced = [], 0, 0
        budget = [limits.MAX_IMPORT_BYTES]
        for n, ((_name, fileobj), ext) in enumerate(zip(files, exts)):
            is_pdf = ext == ".pdf"
            raw = os.path.join(staging, f"in_{n:04d}{'.pdf' if is_pdf else '.img'}")
            _copy_capped(fileobj, raw, limits.MAX_PDF_BYTES if is_pdf
                         else limits.MAX_IMAGE_BYTES, budget)
            with open(raw, "rb") as f:
                kind = _sniff(f.read(16))
            if kind is None or (kind == "pdf") != is_pdf:
                raise InvalidInputError(
                    "A file's contents don't match its type. Upload PNG, JPEG, WebP or PDF.")
            if is_pdf:
                outs, skipped = _stage_pdf(raw, staging, f"f{n:04d}")
                pdf_skipped += skipped
            else:
                outs = _stage_image(raw, staging, f"f{n:04d}", slice_strips)
                sliced += 1 if len(outs) > 1 else 0
            os.remove(raw)
            for out in outs:                      # the viewer refuses bigger files later
                if os.path.getsize(out) > comic_view_service.MAX_IMAGE_BYTES:
                    raise InvalidInputError(
                        "A page is too large once prepared (at most "
                        f"{comic_view_service.MAX_IMAGE_BYTES // (1024 * 1024)} MB).")
            staged.extend(outs)
        page_ids = _commit_pages(drama_id, staged)
    return {"added": len(page_ids), "page_ids": page_ids,
            "pdf_pages_skipped": pdf_skipped, "strips_sliced": sliced}


def _commit_pages(drama_id: int, staged: list) -> list:
    """Writes the staged files as the drama's next pages through the one
    page writer (sources.pipeline.add_page_images), one file in memory at a
    time. Returns the new page ids in order."""
    from sources import pipeline

    def images():
        for path in staged:
            with open(path, "rb") as f:
                yield f.read(), os.path.splitext(path)[1].lower()
    page_ids = []
    pipeline.add_page_images(drama_id, images(), ids_out=page_ids)
    return page_ids
