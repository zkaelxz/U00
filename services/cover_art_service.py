"""
services/cover_art_service.py -- a drama's cover art (inventory P14): set it
from an uploaded image, and find the stored file to show it.

Upload safety: the bytes are read with a hard cap, opened with Pillow (the
declared type and file name are ignored; only PNG, JPEG and WebP decode are
accepted), the pixel count is checked from the header before decoding, and the
image is re-encoded from its pixels only, so EXIF (GPS, camera, dates), XMP,
ICC and text chunks are all dropped. It is stored as `cover.<ext>` in the
drama's folder with an atomic replace; a previous cover with another
extension is removed. No path or filename is ever returned.

Serving: only a stored name of the form `cover.<png|jpg|jpeg|webp>` is
served (older uploads kept the client's extension), resolved inside
the drama's own folder.

No FastAPI import.
"""
import contextlib
import io
import os
import tempfile
from typing import TypeGuard

import db
from lib.errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError)

MAX_COVER_BYTES = 10 * 1024 * 1024
MAX_COVER_PIXELS = 25_000_000
# Pillow format name -> (stored extension, media type)
_FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
            "WEBP": ("webp", "image/webp")}
_SERVE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}


def _require_drama(drama_id) -> dict:
    drama = None
    if isinstance(drama_id, int) and not isinstance(drama_id, bool) and 0 < drama_id <= 2**31 - 1:
        drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError("No title with that id.")
    return drama


def _clean_image(data: bytes):
    """Returns (clean_bytes, ext, width, height). Raises InvalidInputError for
    anything that is not a well-formed PNG/JPEG/WebP within the caps."""
    try:
        from PIL import Image
    except ImportError:
        raise DependencyUnavailableError("Pillow is not installed, so covers can't be checked.")
    try:
        # formats= so only these three parsers ever see an upload, never
        # Pillow's others (TIFF, EPS, ...).
        with Image.open(io.BytesIO(data), formats=tuple(_FORMATS)) as img:
            fmt = img.format
            if fmt not in _FORMATS:
                raise InvalidInputError("Upload a PNG, JPEG or WebP image.")
            width, height = img.size
            if width < 1 or height < 1 or width * height > MAX_COVER_PIXELS:
                raise InvalidInputError("That image is too large (at most 25 megapixels).")
            img.load()
            # Apply the EXIF rotation before the tag is dropped, or a phone
            # photo would be stored sideways.
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            if fmt == "JPEG" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")         # CMYK/YCCK etc.
            elif fmt != "JPEG" and img.mode not in ("RGB", "RGBA", "L", "LA"):
                img = img.convert("RGBA")        # palette, 16-bit...: keep transparency
            # A fresh image from the pixels alone: no info/exif/icc carried over.
            clean = Image.frombytes(img.mode, img.size, img.tobytes())
            out = io.BytesIO()
            if fmt == "JPEG":
                clean.save(out, "JPEG", quality=90)
            elif fmt == "PNG":
                clean.save(out, "PNG", optimize=True)
            else:
                clean.save(out, "WEBP", quality=90)
    except InvalidInputError:
        raise
    except Exception:
        raise InvalidInputError("That file is not a readable PNG, JPEG or WebP image.") from None
    return out.getvalue(), _FORMATS[fmt][0], width, height


def save_cover(drama_id: int, fileobj, only_if_missing: bool = False) -> dict:
    """Sets or replaces the drama's cover. Returns {drama_id, has_cover_art,
    format, width, height, size_bytes}."""
    _require_drama(drama_id)
    data = fileobj.read(MAX_COVER_BYTES + 1)
    if len(data) > MAX_COVER_BYTES:
        raise InvalidInputError("That image is too large (at most 10 MB).")
    if not data:
        raise InvalidInputError("The uploaded file is empty.")
    clean, ext, width, height = _clean_image(data)
    folder = db.drama_dir(drama_id)
    if only_if_missing and (db.get_drama(drama_id) or {}).get("cover_art_filename"):
        # An automatic cover spends seconds in ffmpeg; the owner may have uploaded one by hand since.
        raise ConflictError("This title already has a cover.")
    name = f"cover.{ext}"
    fd, tmp = tempfile.mkstemp(prefix=".cover_", suffix=".part", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(clean)
        os.replace(tmp, os.path.join(folder, name))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    old = (db.get_drama(drama_id) or {}).get("cover_art_filename")
    db.update_drama(drama_id, cover_art_filename=name)
    # Case-insensitive: an older "cover.JPG" is the same file as the new
    # "cover.jpg" on Windows/macOS, and removing it would remove the new one.
    if old and old.lower() != name.lower() and _servable_name(old):
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return {"drama_id": drama_id, "has_cover_art": True, "format": ext,
            "width": width, "height": height, "size_bytes": len(clean)}


def _servable_name(name) -> TypeGuard[str]:
    if not isinstance(name, str) or os.path.basename(name) != name:
        return False
    stem, ext = os.path.splitext(name)
    return stem == "cover" and ext.lower() in _SERVE_TYPES


def cover_file(drama_id: int):
    """(absolute path, media type) of the stored cover; NotFoundError if
    there is none, the stored name isn't one this service writes, or the file
    is gone."""
    drama = _require_drama(drama_id)
    name = drama.get("cover_art_filename")
    if not _servable_name(name):
        raise NotFoundError("This title has no cover.")
    folder = os.path.realpath(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    path = os.path.realpath(os.path.join(folder, name))
    if os.path.dirname(path) != folder or not os.path.isfile(path):
        raise NotFoundError("This title has no cover.")
    return path, _SERVE_TYPES[os.path.splitext(name)[1].lower()]


# Short, because the automatic cover runs inside an upload or download job that can't be cancelled meanwhile.
FRAME_TIMEOUT_SECONDS = 30
# A fifth of the way in skips a black start and a studio logo without seeking to a
# point a short clip doesn't have.
_FRAME_FRACTION = 0.2
_FRAME_FALLBACK_SECONDS = 5.0
_FRAME_MAX_WIDTH = 1280


def _frame_second(video_path: str) -> float:
    import media_inspect
    try:
        duration = float(media_inspect.run_ffprobe(video_path, timeout=10)["format"]["duration"])
    except (media_inspect.ProbeError, KeyError, TypeError, ValueError):
        return _FRAME_FALLBACK_SECONDS
    # A NaN or negative length would make a bad -ss; an unusable one falls back.
    return duration * _FRAME_FRACTION if duration > 0 else _FRAME_FALLBACK_SECONDS


def make_cover_from_video(drama_id: int, only_if_missing: bool = False) -> dict:
    """Sets the cover from one frame of the title's own video. Goes through
    save_cover, so the picture is re-encoded with no metadata like an upload."""
    _require_drama(drama_id)
    folder = db.drama_dir(drama_id)
    from services import media_playback_service
    try:
        # The same confinement playback uses: a link pointing out of the folder is not read.
        video, _ = media_playback_service.resolve_media(drama_id, "video")
    except NotFoundError:
        raise InvalidInputError("This title has no video to take a cover from.") from None
    import video_export
    from lib import proc
    from services import media_upload_service
    fd, frame = tempfile.mkstemp(prefix=".frame_", suffix=".jpg", dir=folder)
    os.close(fd)
    try:
        cmd = ["ffmpeg", "-y", *video_export.local_input(), "-ss", f"{_frame_second(video):.2f}", "-i", video,
               "-frames:v", "1", "-vf", f"scale='min({_FRAME_MAX_WIDTH},iw)':-2", "-q:v", "3", frame]
        try:
            run = proc.run_captured(cmd, timeout=FRAME_TIMEOUT_SECONDS)
        except OSError as exc:
            if media_upload_service.ffmpeg_missing(exc, cmd):
                raise DependencyUnavailableError(media_upload_service.FFMPEG_MISSING) from None
            raise
        if run.returncode != 0 or not os.path.getsize(frame):
            raise InvalidInputError("Couldn't take a picture from this video.")
        with open(frame, "rb") as f:
            return save_cover(drama_id, f, only_if_missing=only_if_missing)
    finally:
        with contextlib.suppress(OSError):
            os.remove(frame)


def make_cover_if_missing(drama_id: int) -> bool:
    """Best-effort cover for a title that has none: a failure leaves the title
    as it was, because a missing picture must never fail an upload or import."""
    try:
        if (db.get_drama(drama_id) or {}).get("cover_art_filename"):
            return False
        make_cover_from_video(drama_id, only_if_missing=True)
        return True
    except Exception:
        return False
