"""
services/artifact_service.py -- job-output file convention and safe lookup.

Convention: a job that produces a downloadable file writes it to
`<drama folder>/exports/<kind>/<filename>` (get it from `output_path`,
which validates the name and creates the folder). The download endpoint
serves the newest regular file in that folder, so nothing needs to be
persisted besides the file itself.

Security: callers never supply a path. `kind` is whitelisted, filenames
must be a bare name, and every resolved path must stay inside the kind
folder (symlinks are rejected). Errors use fixed text with no path echo.
"""

import json
import os
from typing import Dict

import db
from services.service_errors import InvalidInputError, NotFoundError

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


def set_download_language(drama_id: int, kind: str, filename: str, language: str) -> None:
    """Remembers the content language of the file a job just wrote, because
    the stored name stays ID-only and the download name needs the language
    chosen at export time. Best-effort: a failure leaves the plain name."""
    try:
        path = os.path.join(os.path.dirname(output_path(drama_id, kind, filename)), _LABEL_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"file": filename, "language": language}, f)
    except (OSError, InvalidInputError):
        pass


def _download_language(base: str, name: str) -> str:
    try:
        with open(os.path.join(base, _LABEL_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    # Matching on the name keeps a stale label from describing a newer file.
    if isinstance(data, dict) and data.get("file") == name and isinstance(data.get("language"), str):
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
