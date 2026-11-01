"""
services/novel_files_service.py -- the two per-drama novel files:

  - the English novel translation reference: `novel_reference.txt`
    in the drama folder, recorded in `dramas.novel_reference_filename`
    (.txt/.md). Read by translate_run_service,
    workspace_job_service and glossary_service.
  - the raw original-language novel: `raw_novel_context.txt` (no DB
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
    A Sources chapter import (`sourceimport_<drama_id>`) is one of those
    per-drama jobs, so it blocks raw-novel writes for its drama the same way.

No FastAPI import: takes a binary file-like object.
"""
import codecs
import io
import logging
import os
from typing import Optional

import db
import novel_text
import subtitle_parse
from lib.atomic_write import atomic_write
from lib.safe_paths import plain_file_in
from services import drama_service, media_upload_service, ownership_service
from services.novel_attach_service import MAX_EPUB_BYTES, extract_epub_text
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from sources import chapter_manifest, pipeline

log = logging.getLogger(__name__)

REFERENCE_FILENAME = "novel_reference.txt"
RAW_NOVEL_FILENAME = "raw_novel_context.txt"  # sources/pipeline.py
REFERENCE_EXTENSIONS = (".txt", ".md")
RAW_NOVEL_EXTENSIONS = (".txt", ".md", ".epub")
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_TEXT_CHARS = 10_000_000
_BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
         (codecs.BOM_UTF16_BE, "utf-16"))
_BUSY = "A job is running for this title. Wait for it to finish or cancel it."


def _require_idle(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY)


def _folder(drama_id: int) -> str:
    # Not db.drama_dir: that creates the folder, and a status read must not.
    return os.path.join(db.DRAMAS_DIR, str(drama_id))


def _plain_file(drama_id: int, filename):
    """The path of a stored filename only if it is a plain name inside the
    drama folder (never follow a stored value out of it); else None."""
    return plain_file_in(_folder(drama_id), filename)


def _readable_path(drama_id: int, filename, capped: bool = True):
    path = _plain_file(drama_id, filename)
    if path is None or not os.path.isfile(path):
        return None
    if capped and os.path.getsize(path) > MAX_TEXT_BYTES:
        return None
    return path


def read_stored_text(drama_id: int, filename, capped: bool = True) -> Optional[str]:
    """The UTF-8 text of a stored novel file, or None when the name is not a
    plain file in the title folder, is missing, or (when capped) is over
    MAX_TEXT_BYTES. A restored or imported row can hold any filename, and
    this text goes to the translation engine. The raw novel is read uncapped:
    Sources chapter imports append to it with no total limit, and treating a
    long one as absent would make glossary extraction use the other file."""
    path = _readable_path(drama_id, filename, capped)
    if path is None:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def has_reference(drama_id: int, drama: dict) -> bool:
    """True only when read_reference_text would return the file."""
    return _readable_path(drama_id, drama.get("novel_reference_filename")) is not None


def read_reference_text(drama_id: int, drama: dict) -> Optional[str]:
    return read_stored_text(drama_id, drama.get("novel_reference_filename"))


def _extension(client_filename, allowed) -> str:
    name = str(client_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or ext not in allowed:
        raise InvalidInputError(
            f"Unsupported file type. Upload a {', '.join(allowed)} file.")
    return ext


def _fallbacks(language: str, script: str, data: bytes) -> tuple:
    if language == "zh":
        return ("big5", "gb18030") if script == "traditional" else ("gb18030", "big5")
    if language == "en":
        return ("cp1252",)
    # A Japanese or Korean title can still hold a GBK file, so the other CJK
    # codecs stay available behind its own.
    return subtitle_parse.legacy_codec_order(data, language)


def decode_text_guessing(data: bytes, language: str = "", script: str = "") -> tuple:
    """(text, guessed_encoding): the encoding name only when a legacy codec
    was picked for lack of any proof, else None."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors="replace"), None
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        pass
    fallbacks = _fallbacks(language, script, data)
    if "cp932" not in fallbacks[:1] and subtitle_parse.reads_as_shift_jis(data):
        fallbacks = ("cp932",) + fallbacks
    text, encoding = novel_text.best_decoding(data, fallbacks)
    if text is not None:
        return text, encoding
    return data.decode("utf-8", errors="replace"), "utf-8"


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()


def _read_text(fileobj, ext: str, language: str, script: str) -> tuple:
    """(text, guessed_encoding or None)."""
    cap = MAX_EPUB_BYTES if ext == ".epub" else MAX_TEXT_BYTES
    data = fileobj.read(cap + 1)
    if len(data) > cap:
        raise InvalidInputError("The uploaded file is too large.")
    if not data:
        raise InvalidInputError("The uploaded file is empty.")
    if ext == ".epub":
        raw, guessed = extract_epub_text(io.BytesIO(data)), None
    else:
        raw, guessed = decode_text_guessing(data, language, script)
    return _checked_text(raw, "No readable text was found in that file."), guessed


def _guess_warning(encoding) -> Optional[str]:
    if not encoding:
        return None
    return (f"The file isn't UTF-8; it was read as {encoding}. Check the text, and re-save "
            "the file as UTF-8 if the characters look wrong.")


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


def _with_warning(result: dict, guessed) -> dict:
    warning = _guess_warning(guessed)
    return {**result, "warning": warning} if warning else result


def _write_atomic(drama_id: int, filename: str, text: str):
    # .part, not .tmp: the "Leftover temp files" cleanup deletes any *.tmp in the title's folder.
    atomic_write(os.path.join(db.drama_dir(drama_id), filename), text,
                 prefix=".novelfile_", suffix=".part")


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
    drama = ownership_service.require_drama(drama_id)
    return {"drama_id": drama_id, **_file_status(_reference_path(drama_id, drama))}


def upload_reference(drama_id: int, client_filename, fileobj) -> dict:
    """Sets or replaces the reference. Returns the new status plus
    `replaced` (a reference was already present)."""
    drama = ownership_service.require_drama(drama_id)
    ext = _extension(client_filename, REFERENCE_EXTENSIONS)
    with media_upload_service.folder_write(drama_id):
        _require_idle(drama_id)
        text, guessed = _read_text(fileobj, ext, "en", "")
        return _with_warning(_store_reference(drama_id, drama, text), guessed)


def save_reference_text(drama_id: int, text) -> dict:
    """The paste box: sets or replaces the reference from pasted text, with
    the same caps, 409 and atomic write as the upload."""
    drama = ownership_service.require_drama(drama_id)
    with media_upload_service.folder_write(drama_id):
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
    drama = ownership_service.require_drama(drama_id)
    path = _reference_path(drama_id, drama)
    has_file = bool(path and os.path.isfile(path))
    if not has_file and not drama.get("novel_reference_filename"):
        raise NotFoundError("This title has no novel reference saved.")
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


# --- the raw original-language novel ----------------------------------

def get_raw_novel_status(drama_id: int) -> dict:
    ownership_service.require_drama(drama_id)
    return {"drama_id": drama_id,
            **_file_status(os.path.join(_folder(drama_id), RAW_NOVEL_FILENAME))}


def upload_raw_novel(drama_id: int, client_filename, fileobj) -> dict:
    """Sets or replaces raw_novel_context.txt (.txt/.md/.epub)."""
    drama = ownership_service.require_drama(drama_id)
    ext = _extension(client_filename, RAW_NOVEL_EXTENSIONS)
    with media_upload_service.folder_write(drama_id):
        _require_idle(drama_id)
        text, guessed = _read_text(fileobj, ext, drama.get("source_language") or "zh",
                                   drama.get("chinese_script") or "simplified")
        return _with_warning(_store_raw_novel(drama_id, text), guessed)


def save_raw_novel_text(drama_id: int, text) -> dict:
    """Pasted raw novel text; same rules as the upload."""
    ownership_service.require_drama(drama_id)
    with media_upload_service.folder_write(drama_id):
        _require_idle(drama_id)
        return _store_raw_novel(drama_id, _pasted_text(text))


def _store_raw_novel(drama_id: int, text: str) -> dict:
    path = os.path.join(_folder(drama_id), RAW_NOVEL_FILENAME)
    # The page lock every raw-novel writer holds (chapter appends, moving
    # chapters between titles), so a replace never lands mid-move.
    with pipeline._page_lock(drama_id):
        replaced = os.path.isfile(path)
        # Dropped first: if the drop fails after the file was replaced, an
        # equal-size new file would be read through the old manifest.
        if not chapter_manifest.drop(drama_id):
            raise ConflictError("The saved chapter list is in use and could not be reset. "
                                "Close anything using it and try again.")
        _write_atomic(drama_id, RAW_NOVEL_FILENAME, text)
        chapter_manifest.record_book(drama_id, text, novel_text.chapter_starts(text))
    return {"drama_id": drama_id, "replaced": replaced, **_file_status(path)}
