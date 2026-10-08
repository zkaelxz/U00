"""
services/novel_files_service.py -- the two per-drama novel files (parity
audit B1 #3 and #4):

  - the English novel translation reference (T01): `novel_reference.txt`
    in the drama folder, recorded in `dramas.novel_reference_filename`
    (.txt/.md). Read by translate_run_service,
    workspace_job_service, glossary_service and cli.
  - the raw original-language novel (S04): `raw_novel_context.txt` (no DB
    field; its presence is the flag). Primes the automatic Whisper prompt
    (transcribe_service.build_auto_initial_prompt) and pairs with the
    reference for glossary building. Accepts .txt/.md/.epub.

Both can also be set from pasted text (save_reference_text /
save_raw_novel_text).

Removal of the raw novel already exists (delete_service.remove_raw_novel,
POST /api/novel/dramas/{id}/raw-novel/remove); removal of the reference is
here, with the same confirm/404/409 order.

Safety:
  - Only plain extracted text is stored, always as UTF-8, under the fixed
    names above; the client filename is read only for its whitelisted
    extension and never stored or returned. No path is ever returned.
  - Text uploads are read with a byte cap; EPUBs go through
    novel_attach_service.extract_epub_text (stdlib zip/HTML, bounded, no
    ebooklib). The decoded text is capped on characters.
  - Encoding: BOM first, then strict UTF-8, then the legacy encodings
    usual for the language (GB18030/Big5, CP932, CP949, CP1252), then
    UTF-8 with replacement characters. Decoding UTF-8 with errors="ignore"
    would silently drop a GBK novel to nothing.
  - Writes are atomic (temp file in the drama folder + os.replace), so a
    reader never sees a half-written file.
  - A running drama job (the per-drama job ids in
    background_jobs.DRAMA_JOB_PREFIXES: translate, transcribe, ...)
    refuses the upload/paste/removal with ConflictError (409), like the
    other drama-scoped file writes (novel_attach_service, delete_service).
    A Sources chapter import (`source_import_<source>_<series>`,
    sources/pipeline.py) appends to raw_novel_context.txt but its id names
    no drama, so raw-novel writes are also refused while ANY source import
    is running or queued (conservative: it may be for another drama).

No FastAPI import: takes a binary file-like object.
"""
import codecs
import io
import logging
import os
import tempfile
import time

import background_jobs
import db
from services import drama_service
from services.novel_attach_service import MAX_EPUB_BYTES, extract_epub_text
from services.service_errors import ConflictError, InvalidInputError, NotFoundError
from sources import chapter_manifest

log = logging.getLogger(__name__)

REFERENCE_FILENAME = "novel_reference.txt"
RAW_NOVEL_FILENAME = "raw_novel_context.txt"  # sources/pipeline.py
REFERENCE_EXTENSIONS = (".txt", ".md")
RAW_NOVEL_EXTENSIONS = (".txt", ".md", ".epub")
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_TEXT_CHARS = 10_000_000
_BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
         (codecs.BOM_UTF16_BE, "utf-16"))
_BUSY = "A job is running for this drama. Wait for it to finish or cancel it."
_IMPORT_BUSY = ("A Sources chapter import is running and may be adding to the raw novel. "
                "Wait for it to finish or cancel it.")
SOURCE_IMPORT_PREFIX = "source_import_"  # sources/pipeline.import_job_id
# Same freshness window as drama_service.job_running_for_drama.
_STALE_JOB_RECORD_SECONDS = background_jobs.STALE_JOB_SECONDS
_ACTIVE = ("running", "queued")


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _require_idle(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY)


def _source_import_running() -> bool:
    """Any Sources import job, in this process or (via fresh job_records
    rows) another one. Its id carries no drama id, so this can't be
    narrowed to one drama."""
    for job_id, job in background_jobs.list_all_jobs().items():
        if job_id.startswith(SOURCE_IMPORT_PREFIX) and job.get("status") in _ACTIVE:
            return True
    cutoff = time.time() - _STALE_JOB_RECORD_SECONDS
    for rec in db.list_job_records():
        if (str(rec.get("job_id") or "").startswith(SOURCE_IMPORT_PREFIX)
                and rec.get("status") in _ACTIVE and (rec.get("updated_at") or 0) >= cutoff):
            return True
    return False


def _require_raw_novel_idle(drama_id: int):
    _require_idle(drama_id)
    if _source_import_running():
        raise ConflictError(_IMPORT_BUSY)


def _folder(drama_id: int) -> str:
    # Not db.drama_dir: that creates the folder, and a status read must not.
    return os.path.join(db.DRAMAS_DIR, str(drama_id))


def _plain_file(drama_id: int, filename):
    """The path of a stored filename only if it is a plain name inside the
    drama folder (never follow a stored value out of it); else None."""
    if not isinstance(filename, str) or not filename or filename in (".", ".."):
        return None
    if filename != os.path.basename(filename) or "\\" in filename or "/" in filename:
        return None
    folder = _folder(drama_id)
    path = os.path.join(folder, filename)
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(folder):
        return None
    return path


def _extension(client_filename, allowed) -> str:
    name = str(client_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or ext not in allowed:
        raise InvalidInputError(
            f"Unsupported file type. Upload a {', '.join(allowed)} file.")
    return ext


def _fallbacks(language: str, script: str) -> tuple:
    if language == "zh":
        return ("big5", "gb18030") if script == "traditional" else ("gb18030", "big5")
    return {"ja": ("cp932",), "ko": ("cp949",), "en": ("cp1252",)}.get(language, ())


def decode_text(data: bytes, language: str = "", script: str = "") -> str:
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for encoding in _fallbacks(language, script):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()


def _read_text(fileobj, ext: str, language: str, script: str) -> str:
    cap = MAX_EPUB_BYTES if ext == ".epub" else MAX_TEXT_BYTES
    data = fileobj.read(cap + 1)
    if len(data) > cap:
        raise InvalidInputError("The uploaded file is too large.")
    if not data:
        raise InvalidInputError("The uploaded file is empty.")
    raw = extract_epub_text(io.BytesIO(data)) if ext == ".epub" else decode_text(data, language, script)
    return _checked_text(raw, "No readable text was found in that file.")


def _checked_text(raw: str, empty_message: str) -> str:
    text = _clean(raw)
    if not text:
        raise InvalidInputError(empty_message)
    if len(text) > MAX_TEXT_CHARS:
        raise InvalidInputError("The novel text is too large.")
    return text


def _pasted_text(text) -> str:
    if not isinstance(text, str):
        raise InvalidInputError("The text must be a string.")
    return _checked_text(text, "The pasted text is empty.")


def _write_atomic(drama_id: int, filename: str, text: str):
    final = os.path.join(db.drama_dir(drama_id), filename)
    fd, tmp = tempfile.mkstemp(prefix=".novelfile_", suffix=".part", dir=os.path.dirname(final))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _file_status(path) -> dict:
    if not path or not os.path.isfile(path):
        return {"present": False, "size_bytes": 0, "char_count": 0}
    with open(path, encoding="utf-8", errors="replace") as f:
        chars = len(f.read())
    return {"present": True, "size_bytes": os.path.getsize(path), "char_count": chars}


def _reference_path(drama_id: int, drama: dict):
    return _plain_file(drama_id, drama.get("novel_reference_filename"))


# --- the English novel translation reference (T01) --------------------------

def get_reference_status(drama_id: int) -> dict:
    drama = _require_drama(drama_id)
    return {"drama_id": drama_id, **_file_status(_reference_path(drama_id, drama))}


def upload_reference(drama_id: int, client_filename, fileobj) -> dict:
    """Sets or replaces the reference. Returns the new status plus
    `replaced` (a reference was already present)."""
    drama = _require_drama(drama_id)
    ext = _extension(client_filename, REFERENCE_EXTENSIONS)
    _require_idle(drama_id)
    return _store_reference(drama_id, drama, _read_text(fileobj, ext, "en", ""))


def save_reference_text(drama_id: int, text) -> dict:
    """The paste box: sets or replaces the reference from pasted text, with
    the same caps, 409 and atomic write as the upload."""
    drama = _require_drama(drama_id)
    _require_idle(drama_id)
    return _store_reference(drama_id, drama, _pasted_text(text))


def _store_reference(drama_id: int, drama: dict, text: str) -> dict:
    replaced = _file_status(_reference_path(drama_id, drama))["present"]
    _write_atomic(drama_id, REFERENCE_FILENAME, text)
    # Whitelisted, fixed value: the only field this write owns.
    db.update_drama(drama_id, novel_reference_filename=REFERENCE_FILENAME)
    return {"drama_id": drama_id, "replaced": replaced,
            **_file_status(os.path.join(_folder(drama_id), REFERENCE_FILENAME))}


def remove_reference(drama_id: int, confirm=False) -> dict:
    """Deletes the reference file and clears novel_reference_filename. 404
    when there is none; confirm must be True; 409 while a job runs."""
    drama = _require_drama(drama_id)
    path = _reference_path(drama_id, drama)
    has_file = bool(path and os.path.isfile(path))
    if not has_file and not drama.get("novel_reference_filename"):
        raise NotFoundError("This drama has no novel reference saved.")
    if confirm is not True:
        raise InvalidInputError("Removing the novel reference needs confirm=true.")
    _require_idle(drama_id)
    if has_file:
        try:
            os.remove(path)
        except OSError as e:
            log.exception("Could not remove the novel reference for drama %s", drama_id)
            raise ConflictError("The novel reference is in use and could not be removed. "
                                "Close anything using it and try again.") from e
    db.update_drama(drama_id, novel_reference_filename=None)
    return {"drama_id": drama_id, "removed": True, "present": False}


# --- the raw original-language novel (S04) ----------------------------------

def get_raw_novel_status(drama_id: int) -> dict:
    _require_drama(drama_id)
    return {"drama_id": drama_id,
            **_file_status(os.path.join(_folder(drama_id), RAW_NOVEL_FILENAME))}


def upload_raw_novel(drama_id: int, client_filename, fileobj) -> dict:
    """Sets or replaces raw_novel_context.txt (.txt/.md/.epub)."""
    drama = _require_drama(drama_id)
    ext = _extension(client_filename, RAW_NOVEL_EXTENSIONS)
    _require_raw_novel_idle(drama_id)
    text = _read_text(fileobj, ext, drama.get("source_language") or "zh",
                      drama.get("chinese_script") or "simplified")
    return _store_raw_novel(drama_id, text)


def save_raw_novel_text(drama_id: int, text) -> dict:
    """Pasted raw novel text; same rules as the upload."""
    _require_drama(drama_id)
    _require_raw_novel_idle(drama_id)
    return _store_raw_novel(drama_id, _pasted_text(text))


def _store_raw_novel(drama_id: int, text: str) -> dict:
    path = os.path.join(_folder(drama_id), RAW_NOVEL_FILENAME)
    replaced = os.path.isfile(path)
    _write_atomic(drama_id, RAW_NOVEL_FILENAME, text)
    chapter_manifest.drop(drama_id)
    return {"drama_id": drama_id, "replaced": replaced, **_file_status(path)}
