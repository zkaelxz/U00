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
import html
import io
import os
import re
import tempfile
import threading
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


# Cover tiles are drawn 150-250 px wide (two columns on a phone); 480 stays sharp
# on a high-density screen.
_THUMB_WIDTH = 480
_THUMB_QUALITY = 82
_THUMB_FOLDER = "reader_cache"
# A first Library visit asks for every cover at once; each decode of a 25-megapixel
# cover takes a few hundred MB.
_THUMB_SLOTS = threading.BoundedSemaphore(2)


def cover_thumb(drama_id: int):
    """(path, media type) of a small JPEG copy of the cover for lists, made once
    beside the title and remade when the cover changes; the cover itself when the
    copy can't be made or isn't smaller. A cover is kept at full size (up to 25
    megapixels), and a Library page drew every one of them at list size."""
    path, media_type = cover_file(drama_id)
    st = os.stat(path)
    folder = os.path.join(os.path.dirname(path), _THUMB_FOLDER)
    stamp = f"cover.{st.st_mtime_ns}.{st.st_size}"
    thumb = os.path.join(folder, f"{stamp}.thumb.jpg")
    try:
        made = os.path.isfile(thumb)
        if not made:
            with _THUMB_SLOTS:
                # Another request may have made it while this one waited.
                made = os.path.isfile(thumb) or _write_thumb(path, folder, stamp, thumb)
        if made and os.path.getsize(thumb) < st.st_size:
            return thumb, "image/jpeg"
    except Exception:
        pass
    return path, media_type


def _write_thumb(path: str, folder: str, stamp: str, thumb: str) -> bool:
    """False, writing nothing, for a cover already no wider than a thumb."""
    from PIL import Image
    # A restored library puts cover files back unchecked, so only the parsers an upload may use.
    with Image.open(path, formats=tuple(_FORMATS)) as img:
        if img.width <= _THUMB_WIDTH:
            return False
        os.makedirs(folder, exist_ok=True)
        img.load()
        if img.mode != "RGB":
            rgba = img.convert("RGBA")
            img = Image.new("RGB", rgba.size, "white")
            img.paste(rgba, mask=rgba.getchannel("A"))
        img = img.resize((_THUMB_WIDTH, max(1, round(img.height * _THUMB_WIDTH / img.width))),
                         Image.Resampling.LANCZOS)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                img.save(fh, "JPEG", quality=_THUMB_QUALITY, optimize=True)
            os.replace(tmp, thumb)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise
    for name in os.listdir(folder):
        if name.startswith("cover.") and name.endswith(".thumb.jpg") and not name.startswith(stamp + "."):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(folder, name))
    return True


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


def use_site_thumbnail(drama_id: int, path: str) -> bool:
    """Best-effort cover from the picture the downloader saved at `path`
    (video_download.save_thumbnail). Goes through save_cover (type, size and
    pixel caps, re-encode), and never replaces a cover the title already has.
    True only when this call set the cover, so a caller whose job then fails
    can take it back with remove_cover. Any failure leaves the title as it
    was: a missing picture must not fail a download."""
    if (db.get_drama(drama_id) or {}).get("cover_art_filename"):
        return False
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            save_cover(drama_id, f, only_if_missing=True)
        return True
    except Exception:
        return False


def cover_stamp(drama_id: int):
    """Identifies the stored cover (name and modified time), or None when there is none."""
    name = (db.get_drama(drama_id) or {}).get("cover_art_filename")
    if not _servable_name(name):
        return None
    try:
        return name, os.stat(os.path.join(os.path.realpath(db.drama_dir(drama_id)), name)).st_mtime_ns
    except OSError:
        return None


def remove_cover(drama_id: int, only_if_stamp=None) -> None:
    """Takes the cover away again: the stored file, then the title's field. For a job that set
    one automatically and then failed, so a title left with no media shows no picture either.
    With `only_if_stamp` (from cover_stamp) it does nothing when the cover is no longer the one
    the job made, so a picture the user uploaded meanwhile is kept."""
    drama = db.get_drama(drama_id) or {}
    name = drama.get("cover_art_filename")
    if not drama or (only_if_stamp is not None and cover_stamp(drama_id) != only_if_stamp):
        return
    with contextlib.suppress(OSError):
        if _servable_name(name):
            os.remove(os.path.join(os.path.realpath(db.drama_dir(drama_id)), name))
    db.update_drama(drama_id, cover_art_filename=None)


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


def make_cover_from_first_page(drama_id: int, only_if_missing: bool = False) -> dict:
    """Sets the cover from the first visible page of a comic title. Goes through save_cover, so
    the page is re-encoded with no metadata like an upload."""
    _require_drama(drama_id)
    from services import comic_view_service
    from comic_chapters import load as load_manifest
    hidden = set(load_manifest(drama_id)["hidden"])
    for page in db.list_pages(drama_id):
        if page.get("filename") in hidden:
            continue
        found = comic_view_service.safe_file(drama_id, page.get("filename"))
        if found is None:
            continue
        with open(found[0], "rb") as f:
            return save_cover(drama_id, f, only_if_missing=only_if_missing)
    raise InvalidInputError("This title has no page to take a cover from.")


def cover_referer(cover_url: str, page_url: str) -> str:
    """The page address for a picture on the page's own host; only its origin for another host,
    as a browser sends, so a CDN never sees the chapter's path or query."""
    from urllib.parse import urlsplit
    cover, page = urlsplit(cover_url), urlsplit(page_url)
    if not page.scheme or not page.hostname:
        return ""
    return page_url if cover.hostname == page.hostname else f"{page.scheme}://{page.netloc}/"


def fetch_site_cover(drama_id: int, cover_url: str, page_url: str, replace: bool = False, client=None) -> bool:
    """Best-effort cover from a picture a site shows for the series. lib.http checks every hop is a
    public address and save_cover re-encodes the bytes, so only a clean image is kept. `replace`
    is for a caller that has already decided the current cover may go."""
    if not cover_url:
        return False
    from services import quick_read_fetch
    try:
        referer = cover_referer(cover_url, page_url or "")
        if client is not None:
            # The source's own paced client, so a pass over many titles keeps its per-site gap.
            data = client.get(cover_url, classify_body=False, headers={"Referer": referer} if referer else {}).content
        else:
            data, _reason = quick_read_fetch.fetch_one(cover_url, {"referer": referer}, MAX_COVER_BYTES)
        if data and replace and not _is_portrait(data):
            # A page's og:image is often a site logo or banner; only a cover-shaped picture may displace page one.
            return False
        if data:
            save_cover(drama_id, io.BytesIO(data), only_if_missing=not replace)
            return True
    except Exception:
        pass
    return False


def _is_portrait(data: bytes) -> bool:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data), formats=tuple(_FORMATS)) as img:
            return img.height >= img.width * 0.9
    except Exception:
        return False


_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_PAGE_BYTES = 1024 * 1024
_PAGE_TIMEOUT = (5, 15)
# og:image first: sites name the series picture there, and twitter:image is the same picture on the rest.
_COVER_META = ("og:image", "og:image:url", "og:image:secure_url", "twitter:image")


def cover_url_from_html(page_html: str, page_url: str) -> str:
    """The picture a page names for itself in its og:image / twitter:image tag, as an absolute
    http(s) address, or "". Only meta tags are read, so a script or comment can't supply one."""
    from urllib.parse import urljoin, urlsplit
    found = {}
    for tag in _META_TAG.findall(page_html or ""):
        attrs = {m[0].lower(): html.unescape(m[1] or m[2]).strip() for m in _META_ATTR.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in _COVER_META and attrs.get("content"):
            found.setdefault(key, attrs["content"])
    for key in _COVER_META:
        if key in found:
            try:
                url = urljoin(page_url or "", found[key])
                if urlsplit(url).scheme in ("http", "https"):
                    return url
            except ValueError:
                continue
    return ""


def paced_client(link: str):
    """The source's own paced client for `link` (its per-host gap, slowdown and health record apply),
    with the page and picture caps this service uses."""
    from sources import generic_import
    client = generic_import.http_client(None, link)
    client.max_page_bytes = _PAGE_BYTES
    client.max_image_bytes = MAX_COVER_BYTES
    return client


def site_cover_url(page_url: str, client=None) -> str | None:
    """The cover address a stored page link names; "" when the page names none, None when it can't be read."""
    from lib import http
    from services import quick_read_fetch
    try:
        if client is not None:
            return cover_url_from_html(client.get(page_url, classify_body=False).text, page_url)
        response = http.get(page_url, timeout=_PAGE_TIMEOUT, max_bytes=_PAGE_BYTES,
                            headers={"User-Agent": quick_read_fetch.FALLBACK_USER_AGENT,
                                     "Accept": "text/html,*/*;q=0.8"}, max_error_bytes=1024)
    except Exception:
        return None
    return cover_url_from_html(response.text(), page_url) if response.ok else None


# Mean grey difference (0-255) under which two pictures count as the same page.
_SAME_PICTURE_TOLERANCE = 6


def cover_is_first_page(drama_id: int) -> bool:
    """True when the stored cover is page one of the comic: what the automatic cover made before
    a site picture was tried, so it may be replaced, unlike a picture the person chose."""
    from PIL import Image, ImageChops, ImageStat
    from comic_chapters import load as load_manifest
    from services import comic_view_service
    try:
        cover_path, _ = cover_file(drama_id)
        hidden = set(load_manifest(drama_id)["hidden"])
        for page in db.list_pages(drama_id):
            found = None if page.get("filename") in hidden else \
                comic_view_service.safe_file(drama_id, page.get("filename"))
            if found is None:
                continue
            with Image.open(cover_path, formats=tuple(_FORMATS)) as cover, Image.open(found[0]) as first:
                if cover.size != first.size:
                    return False
                a, b = (im.convert("L").resize((32, 32)) for im in (cover, first))
                return ImageStat.Stat(ImageChops.difference(a, b)).mean[0] < _SAME_PICTURE_TOLERANCE
    except Exception:
        return False
    return False


def fill_missing_cover(drama_id: int, cover_url: str = "", page_url: str = "",
                       from_network: bool = True) -> bool:
    """Best-effort cover for a title that has none, from the nearest picture: the site's own for
    the series, then a frame of the title's video, then page one of a comic. Never replaces a
    cover and never raises: a missing picture must not fail an import or a library pass. True only
    when this call set the cover."""
    try:
        if (db.get_drama(drama_id) or {}).get("cover_art_filename"):
            return False
        if from_network and fetch_site_cover(drama_id, cover_url, page_url):
            return True
        for make in (make_cover_from_video, make_cover_from_first_page):
            try:
                make(drama_id, only_if_missing=True)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def refresh_site_cover(drama_id: int, client=None, only_first_page: bool = False) -> str:
    """Replaces the cover with the picture the title's stored link names for the series. Returns
    "fetched"; "kept" when the page names no cover-shaped picture (the cover stays); "unreachable"
    when the page couldn't be read; "skipped" when `only_first_page` and the cover is one the person
    chose; "no_link" when the title has no stored link."""
    drama = _require_drama(drama_id)
    link = (drama.get("source_url_full") or drama.get("source_url") or "").strip()
    if not link.startswith(("http://", "https://")):
        return "no_link"
    if only_first_page and drama.get("cover_art_filename") and not cover_is_first_page(drama_id):
        return "skipped"
    client = client or paced_client(link)
    picture = site_cover_url(link, client)
    if picture is None:
        return "unreachable"
    if picture and fetch_site_cover(drama_id, picture, link, replace=True, client=client):
        return "fetched"
    return "kept"
