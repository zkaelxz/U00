"""
services/artifact_service.py -- job-output file convention and safe lookup.

Convention: a job that produces a downloadable file writes it to
`<drama folder>/exports/<kind>/<filename>` (get it from `output_path`,
which validates the name and creates the folder). The download endpoint
serves the newest regular file in that folder. The only other thing kept is
a `.download.json` label beside it (the content language, for the download
name), which `get_artifact` reports.

Security: callers never supply a path. `kind` is whitelisted, filenames
must be a bare name, and every resolved path must stay inside the kind
folder (symlinks are rejected). Errors use fixed text with no path echo.
"""

import json
import os
import stat
import tempfile
from typing import Dict

import db
from services.service_errors import InvalidInputError, NotFoundError, ServiceError

ARTIFACT_KINDS = ("subtitle", "epub", "audio", "video", "softsub_video", "dubbed_video",
                  "archive", "scanlate_zip", "scanlate_pdf")

_BAD_NAME = "Invalid artifact filename."
_MISSING = "No artifact available."


def _kind_dir(drama_id: int, kind: str, create: bool) -> str:
    if kind not in ARTIFACT_KINDS:
        raise InvalidInputError("Unknown artifact kind.")
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    root = db.drama_dir(drama_id)
    path = os.path.join(root, "exports", kind)
    if create:
        os.makedirs(path, exist_ok=True)
    # The kind folder (or "exports") may itself be a symlink pointing elsewhere.
    if os.path.lexists(path) and not _inside(root, path):
        raise NotFoundError(_MISSING)
    return path


def _inside(base: str, path: str) -> bool:
    base_real = os.path.realpath(base)
    real = os.path.realpath(path)
    return os.path.commonpath([base_real, real]) == base_real and real != base_real


def output_path(drama_id: int, kind: str, filename: str) -> str:
    """Absolute path a job should write its output to (server-side use only)."""
    if (not filename or filename != os.path.basename(filename) or filename in (".", "..")
            or "\\" in filename or "\x00" in filename or os.path.isabs(filename)):
        raise InvalidInputError(_BAD_NAME)
    base = _kind_dir(drama_id, kind, create=True)
    path = os.path.join(base, filename)
    if not _inside(base, path):
        raise InvalidInputError(_BAD_NAME)
    return path


# Hidden, so get_artifact's "newest file" scan never mistakes it for an output.
_LABEL_FILE = ".download.json"


def set_download_language(drama_id: int, kind: str, filename: str, language: str,
                          source: str = None) -> None:
    """Remembers the content language of the file a job writes, because the
    stored name stays ID-only and the download name needs the language chosen
    at export time. The label carries the file's size and mtime so it only
    describes that exact file; `source` is the finished file before it is
    moved into place (a move keeps size and mtime), so the label can exist
    before the new file does. Best-effort: a failure leaves the plain name."""
    tmp = None
    try:
        base = os.path.dirname(output_path(drama_id, kind, filename))
        path = os.path.join(base, _LABEL_FILE)
        # Opening a planted symlink for writing would follow it out of the folder.
        if os.path.islink(path):
            return
        st = os.stat(source or os.path.join(base, filename))
        # mkstemp creates its file exclusively, and the replace swaps the
        # label in whole so a reader never sees half of it.
        fd, tmp = tempfile.mkstemp(prefix=".download-", suffix=".tmp", dir=base)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"file": filename, "language": language,
                       "size": st.st_size, "mtime_ns": st.st_mtime_ns}, f)
        os.replace(tmp, path)
        tmp = None
    except (OSError, ServiceError):
        pass
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


def clear_download_language(drama_id: int, kind: str, filename: str) -> None:
    """Drops the label written ahead of a move that then failed."""
    try:
        base = os.path.dirname(output_path(drama_id, kind, filename))
        path = os.path.join(base, _LABEL_FILE)
        if os.path.islink(path):
            return
        if _read_label(path).get("file") == filename:
            os.remove(path)
    except (OSError, ServiceError):
        pass


# A label is a few dozen bytes; the cap keeps a planted huge file from filling memory.
_LABEL_MAX_BYTES = 4096


def _read_label(path: str) -> dict:
    try:
        # lstat first: opening a FIFO blocks and a symlink can point at /dev/zero.
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return {}
        with open(path, encoding="utf-8") as f:
            data = json.loads(f.read(_LABEL_MAX_BYTES))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _download_language(base: str, name: str) -> str:
    data = _read_label(os.path.join(base, _LABEL_FILE))
    try:
        st = os.stat(os.path.join(base, name))
    except OSError:
        return ""
    # A re-export reuses the file name, so the name alone would let a stale
    # label describe the newer file; size and mtime pin it to one file.
    if (data.get("file") == name and isinstance(data.get("language"), str)
            and data.get("size") == st.st_size and data.get("mtime_ns") == st.st_mtime_ns):
        return data["language"]
    return ""


def get_artifact(drama_id: int, kind: str) -> Dict:
    """Newest artifact of `kind`: {path (server-side only), name, size, kind,
    language (the label recorded by the job, "" if none)}."""
    base = _kind_dir(drama_id, kind, create=False)
    try:
        names = os.listdir(base)
    except OSError:
        raise NotFoundError(_MISSING)
    best = None
    for name in names:
        if name.startswith(".") or name.endswith(".part"):
            continue                       # an unfinished write
        path = os.path.join(base, name)
        if os.path.islink(path) or not os.path.isfile(path) or not _inside(base, path):
            continue
        mtime = os.path.getmtime(path)
        if best is None or mtime > best[0]:
            best = (mtime, name, path)
    if best is None:
        raise NotFoundError(_MISSING)
    return {"path": best[2], "name": best[1], "size": os.path.getsize(best[2]), "kind": kind,
            "language": _download_language(base, best[1])}
