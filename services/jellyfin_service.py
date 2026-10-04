"""
services/jellyfin_service.py -- the optional Jellyfin connector (roadmap
Step 39). Off by default; API + filesystem only, never a Jellyfin plugin.

  - get_config / set_config / clear_key: the server URL, the library folder
    on this PC and an on/off switch are stored in app_settings; the API key
    goes to .env (BAIHE_JELLYFIN_API_KEY) like the engine keys. Nothing here
    returns, logs or puts the key in an error; callers see `key_configured`.
  - test_connection: GET /System/Info (server name and version only).
  - scan: a read-only report of the library's movies and episodes, with
    counts of the ones missing a subtitle in the target language. Changes
    nothing.
  - send_to_jellyfin: writes one drama's subtitle file (and, in the folder
    layout, optionally its video) where Jellyfin auto-detects it, then
    optionally asks Jellyfin to refresh. Next to an existing item it is
    "<media stem>.<lang>.<ext>" beside the media file; as a new title it is
    "<library>/<Title>/<Title>.<lang>.<ext>" (plus "<Title>.<video ext>").
    An existing file is never overwritten unless `overwrite` is true, and
    nothing is ever written outside the configured library folder (a path
    Jellyfin reports is checked against it after resolving links).

Network: the URL must be http(s) with a host, no user name/password, query or
fragment. Every address it resolves to is checked at call time: loopback and
private LAN addresses are fine (Jellyfin usually runs on this PC or the LAN),
but link-local (cloud metadata), multicast, reserved and unspecified
addresses are refused, and so are Baihe's own ports on this PC
(settings_service.baihe_own_ports). The check is not pinned to the connection (the address
is the PC owner's own choice, and a key-write-gated setting). Requests carry
timeout=, follow no redirects, ignore proxy settings and read at most
MAX_RESPONSE_BYTES within READ_DEADLINE. Errors are fixed text: never the URL, a path or the key.

No FastAPI import: plain dicts in, plain dicts out.
"""
import contextlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import tempfile
from typing import Optional
from urllib.parse import urlsplit

import db
from services import (artifact_service, capped_body, drama_service, export_service,
                      settings_service)
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

log = logging.getLogger(__name__)

SETTING = "jellyfin"
KEY_ENV = ("BAIHE_JELLYFIN_API_KEY",)
HTTP_TIMEOUT = (3.05, 20)
PAGE_SIZE = 200
MAX_RESPONSE_BYTES = 20_000_000
READ_DEADLINE = 60.0
MAX_SCAN_ITEMS = 5000
MAX_REPORT_ITEMS = 500
MAX_LIBRARY_DIR_LEN = 1000
FORMATS = ("srt", "ass")
FIELDS = ("en", "zh", "bilingual")
MEDIA = ("none", "source", "dubbed")
# Jellyfin matches "<stem>.<lang>.<ext>" against ISO 639 codes; the
# three-letter (ISO 639-2/B) forms are the ones its own language list uses.
LANG_CODES = {"en": "eng", "zh": "chi", "ja": "jpn", "ko": "kor"}
_LANG_ALIASES = {"en": {"en", "eng"}, "zh": {"zh", "chi", "zho"}, "ja": {"ja", "jpn"},
                 "ko": {"ko", "kor"}}
_KEY_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ITEM_REFRESH = {"metadataRefreshMode": "Default", "imageRefreshMode": "Default",
                 "replaceAllMetadata": "false", "replaceAllImages": "false"}
_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")

_UNREACHABLE = "Couldn't reach the Jellyfin server. Check the address and that it is running."
_BAD_KEY = "Jellyfin refused the API key. Create one in Jellyfin's Dashboard > API Keys."
_BAD_REPLY = "The Jellyfin server sent a reply this app could not read."
_DISABLED = "The Jellyfin connector is off. Turn it on in Settings first."
_WRITE_FAILED = "Could not write the file into the Jellyfin library folder."


# --- settings ----------------------------------------------------------------

def _stored() -> dict:
    saved = db.get_app_setting(SETTING) or {}
    return saved if isinstance(saved, dict) else {}


def _api_key() -> Optional[str]:
    """Server-side only."""
    return settings_service.resolve_env_names(KEY_ENV)


def get_config() -> dict:
    s = _stored()
    return {"enabled": bool(s.get("enabled")), "server_url": s.get("server_url") or None,
            "library_dir": s.get("library_dir") or None,
            "key_configured": bool(_api_key())}


def _check_library_dir(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError("Enter the library folder Jellyfin reads.")
    value = value.strip()
    if len(value) > MAX_LIBRARY_DIR_LEN or any(ord(c) < 32 for c in value):
        raise InvalidInputError("That folder path is not valid.")
    if not os.path.isabs(value):
        raise InvalidInputError("Use a full folder path (for example D:\\Media\\Dramas).")
    if not os.path.isdir(value):
        raise InvalidInputError("That folder does not exist on this PC.")
    return os.path.realpath(value)


def set_config(enabled: Optional[bool] = None, server_url: Optional[str] = None,
               library_dir: Optional[str] = None) -> dict:
    """Fields left as None keep their saved value; "" clears a text field."""
    s = _stored()
    if server_url is not None:
        s["server_url"] = settings_service.validate_endpoint_url(server_url) \
            if server_url.strip() else None
    if library_dir is not None:
        s["library_dir"] = _check_library_dir(library_dir) if library_dir.strip() else None
    if enabled is not None:
        s["enabled"] = bool(enabled)
    db.set_app_setting(SETTING, s)
    return get_config()


def set_key(value, env_path: Optional[str] = None) -> dict:
    """Write-only: stores the key in .env; never echoes it back."""
    key = value.strip() if isinstance(value, str) else ""
    if not _KEY_RE.match(key):
        raise InvalidInputError("That does not look like a Jellyfin API key "
                                "(letters and digits only).")
    settings_service.write_env_var(KEY_ENV[0], key, env_path)
    return get_config()


def clear_key(env_path: Optional[str] = None) -> dict:
    settings_service.remove_env_vars(KEY_ENV, env_path)
    return get_config()


# --- HTTP ----------------------------------------------------------------------

def _check_target(url: str):
    """Refuses a server address that resolves anywhere a Jellyfin server
    cannot sensibly be (see the module docstring). The lookup itself has no
    timeout of its own: it relies on the OS resolver's timeout."""
    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except (OSError, ValueError, UnicodeError):
        raise DependencyUnavailableError(_UNREACHABLE) from None
    if not infos:
        raise DependencyUnavailableError(_UNREACHABLE)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if (ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or (ip.is_reserved and not ip.is_private)):
            raise InvalidInputError("That server address is not allowed.")
        if ip.is_loopback and port in settings_service.baihe_own_ports():
            raise InvalidInputError("That address is this app's own port, not Jellyfin.")


def _server() -> tuple:
    cfg = _stored()
    if not cfg.get("enabled"):
        raise ConflictError(_DISABLED, details={"reason": "disabled"})
    url, key = cfg.get("server_url"), _api_key()
    if not url or not key:
        raise InvalidInputError("Set the Jellyfin server address and API key in Settings first.")
    return url, key


def _request(method: str, path: str, params: Optional[dict] = None,
             url: Optional[str] = None, key: Optional[str] = None):
    import requests
    if url is None:
        url, key = _server()
    _check_target(url)
    session = requests.Session()
    session.trust_env = False  # a proxy would reach the LAN server on our behalf
    try:
        resp = session.request(method, url + path, params=params,
                               headers={"Authorization": f'MediaBrowser Token="{key}"',
                                        "Accept": "application/json"},
                               timeout=HTTP_TIMEOUT, allow_redirects=False, stream=True)
        try:
            if resp.status_code in (401, 403):
                raise DependencyUnavailableError(_BAD_KEY)
            if resp.status_code >= 300:
                log.info("Jellyfin answered HTTP %s", resp.status_code)
                raise DependencyUnavailableError(_UNREACHABLE)
            return _read_capped(resp)
        finally:
            resp.close()
    except requests.RequestException:
        raise DependencyUnavailableError(_UNREACHABLE) from None
    finally:
        session.close()


def _read_capped(resp) -> bytes:
    """The body, at most MAX_RESPONSE_BYTES and READ_DEADLINE seconds in all."""
    return capped_body.read_capped(resp, MAX_RESPONSE_BYTES, READ_DEADLINE,
                                   lambda: DependencyUnavailableError(_BAD_REPLY))


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8")) if body else None
    except (ValueError, UnicodeDecodeError):
        raise DependencyUnavailableError(_BAD_REPLY) from None
    if not isinstance(data, dict):
        raise DependencyUnavailableError(_BAD_REPLY)
    return data


def test_connection() -> dict:
    """Works while the connector is off, so it can be checked before
    turning it on."""
    cfg = _stored()
    url, key = cfg.get("server_url"), _api_key()
    if not url or not key:
        raise InvalidInputError("Set the Jellyfin server address and API key first.")
    info = _json(_request("GET", "/System/Info", url=url, key=key))
    return {"ok": True, "server_name": str(info.get("ServerName") or "")[:200],
            "version": str(info.get("Version") or "")[:50]}


# --- scan ------------------------------------------------------------------------

def _inside(base: str, path: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(base), os.path.realpath(path)]) \
            == os.path.realpath(base)
    except ValueError:  # different drives on Windows
        return False


def _has_language(item: dict, language: str) -> bool:
    wanted = _LANG_ALIASES[language]
    for stream in item.get("MediaStreams") or []:
        if isinstance(stream, dict) and stream.get("Type") == "Subtitle" \
                and str(stream.get("Language") or "").lower() in wanted:
            return True
    return False


def scan(language: str = "en") -> dict:
    """Read-only: counts of movies/episodes with and without a subtitle in
    `language`, plus the missing ones (id, name, series, whether Baihe can
    write next to it). Nothing is changed on the server or on disk."""
    if language not in LANG_CODES:
        raise InvalidInputError("Unknown subtitle language.", details={"allowed": list(LANG_CODES)})
    library_dir = _stored().get("library_dir")
    total = with_subs = 0
    missing, start, truncated = [], 0, False
    while True:
        data = _json(_request("GET", "/Items", params={
            "Recursive": "true", "IncludeItemTypes": "Movie,Episode",
            "Fields": "MediaStreams,Path", "StartIndex": start, "Limit": PAGE_SIZE}))
        items = data.get("Items") or []
        if not isinstance(items, list):
            raise DependencyUnavailableError(_BAD_REPLY)
        for item in items:
            if not isinstance(item, dict):
                continue
            total += 1
            if _has_language(item, language):
                with_subs += 1
                continue
            path = item.get("Path")
            if len(missing) < MAX_REPORT_ITEMS:
                missing.append({
                    "id": str(item.get("Id") or "")[:64],
                    "name": str(item.get("Name") or "")[:300],
                    "series": (str(item["SeriesName"])[:300] if item.get("SeriesName") else None),
                    "season": item.get("ParentIndexNumber") if isinstance(
                        item.get("ParentIndexNumber"), int) else None,
                    "episode": item.get("IndexNumber") if isinstance(
                        item.get("IndexNumber"), int) else None,
                    "type": "episode" if item.get("Type") == "Episode" else "movie",
                    "writable": bool(library_dir and isinstance(path, str)
                                     and _inside(library_dir, path)),
                })
            else:
                truncated = True
        start += len(items)
        record_total = data.get("TotalRecordCount")
        if not items or not isinstance(record_total, int) or start >= record_total:
            break
        if start >= MAX_SCAN_ITEMS:
            truncated = True
            break
    return {"language": language, "total": total, "with_subtitles": with_subs,
            "missing": total - with_subs, "items": missing, "truncated": truncated}


# --- send --------------------------------------------------------------------------

def _safe_name(text: str) -> str:
    name = re.sub(r"\s+", " ", _UNSAFE_NAME.sub(" ", text or ""))
    name = re.sub(r"(^|\s)\.+(?=\s|$)", " ", name)  # no "." / ".." parts
    return name[:120].strip(" .")


def _subtitle_text(drama_id: int, fmt: str, field: str) -> str:
    if fmt == "ass":
        return export_service.generate_ass_text(drama_id, field=field)
    return export_service.generate_subtitle_text(drama_id, "srt", field)


def _item_media_path(item_id: str, library_dir: str) -> str:
    data = _json(_request("GET", "/Items", params={"Ids": item_id, "Fields": "Path"}))
    wanted = item_id.replace("-", "").lower()
    match = [i for i in (data.get("Items") or []) if isinstance(i, dict)
             and str(i.get("Id") or "").replace("-", "").lower() == wanted]
    path = match[0].get("Path") if match else None  # by id, never by list position
    if not isinstance(path, str) or not path:
        raise NotFoundError("Jellyfin has no media file for that item.")
    if not _inside(library_dir, path):
        raise InvalidInputError(
            "That item's file is outside the library folder set in Settings (or Jellyfin sees "
            "it under a different path), so the subtitle can't be placed next to it.")
    return os.path.realpath(path)


def _source_media(drama_id: int, drama: dict, media: str) -> Optional[str]:
    if media == "none":
        return None
    if media == "dubbed":
        try:
            path = artifact_service.get_artifact(drama_id, "dubbed_video")["path"]
        except NotFoundError:
            path = None
        if not path or not os.path.isfile(path):
            raise InvalidInputError("There is no dubbed video yet. Export one first.")
        return path
    name = drama.get("source_video_filename") or ""
    path = os.path.join(db.drama_dir(drama_id), name) if name else ""
    if not name or name != os.path.basename(name) or not os.path.isfile(path):
        raise InvalidInputError("This drama has no source video.")
    return path


def _check_free(dest: str, library_dir: str, overwrite: bool):
    if not _inside(library_dir, os.path.dirname(dest)):
        raise InvalidInputError("The destination is outside the library folder.")
    if os.path.lexists(dest) and not overwrite:
        raise ConflictError(f"{os.path.basename(dest)} already exists in the library. "
                            "Allow replacing it to overwrite.",
                            details={"reason": "exists", "file": os.path.basename(dest)})


def _place(src_or_text, dest: str, library_dir: str, overwrite: bool, *, text: bool):
    """Writes through a fresh, exclusively created temp file in the target
    folder (mkstemp: never an existing name or link someone planted), then
    re-checks the folder is still inside the library before the rename. A
    video is copied, never hard-linked, so the library file and Baihe's own
    file stay independent."""
    _check_free(dest, library_dir, overwrite)
    folder = os.path.dirname(dest)
    tmp = None
    try:
        os.makedirs(folder, exist_ok=True)
        if not _inside(library_dir, folder):  # swapped for a link after the check
            raise InvalidInputError("The destination is outside the library folder.")
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".baihe-", suffix=".tmp")
        with os.fdopen(fd, "wb") as out:
            if text:
                out.write(src_or_text.replace("\r\n", "\n").encode("utf-8"))
            else:
                with open(src_or_text, "rb") as src:
                    shutil.copyfileobj(src, out, 1024 * 1024)
        _check_free(dest, library_dir, overwrite)
        if os.path.realpath(os.path.dirname(tmp)) != os.path.realpath(folder):
            raise InvalidInputError("The destination is outside the library folder.")
        os.replace(tmp, dest)
        tmp = None
    except OSError:
        raise DependencyUnavailableError(_WRITE_FAILED) from None
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                os.remove(tmp)


def send_to_jellyfin(drama_id: int, item_id: Optional[str] = None, fmt: str = "srt",
                     field: str = "en", language: Optional[str] = None, media: str = "none",
                     overwrite: bool = False, refresh: bool = True) -> dict:
    """Returns {"drama_id", "files": [names relative to the library folder],
    "refresh": "done" | "failed" | "skipped"}."""
    drama = db.get_drama(drama_id) if isinstance(drama_id, int) and drama_id > 0 else None
    if drama is None:
        raise NotFoundError("No drama with that id.")
    if fmt not in FORMATS:
        raise InvalidInputError("Format must be srt or ass.", details={"allowed": list(FORMATS)})
    if field not in FIELDS:
        raise InvalidInputError("Unknown subtitle text.", details={"allowed": list(FIELDS)})
    if media not in MEDIA:
        raise InvalidInputError("Unknown media choice.", details={"allowed": list(MEDIA)})
    language = language or ("en" if field != "zh" else (drama.get("source_language") or "zh"))
    if language not in LANG_CODES:
        raise InvalidInputError("Unknown subtitle language.", details={"allowed": list(LANG_CODES)})
    cfg = _stored()
    if not cfg.get("enabled"):
        raise ConflictError(_DISABLED, details={"reason": "disabled"})
    library_dir = cfg.get("library_dir")
    if not library_dir or not os.path.isdir(library_dir):
        raise InvalidInputError("Set the Jellyfin library folder in Settings first.")
    if item_id is not None and not _ITEM_ID_RE.match(item_id):
        raise InvalidInputError("That Jellyfin item id is not valid.")
    if item_id and media != "none":
        raise InvalidInputError("Media is only copied when adding a new title folder.")
    if drama_service.job_running_for_drama(drama_id):  # e.g. a dubbed video still being written
        raise ConflictError("A background job is still running for this drama. Wait for it to "
                            "finish, then send it to Jellyfin.", details={"reason": "job_running"})

    text = _subtitle_text(drama_id, fmt, field)
    if not text.strip():
        raise InvalidInputError("There are no subtitle lines to send yet.")
    code = LANG_CODES[language]
    files = []
    if item_id:
        media_path = _item_media_path(item_id, library_dir)
        stem = os.path.splitext(os.path.basename(media_path))[0]
        dest = os.path.join(os.path.dirname(media_path), f"{stem}.{code}.{fmt}")
        _place(text, dest, library_dir, overwrite, text=True)
        files.append(dest)
    else:
        title = _safe_name(drama.get("title_en") or "") or _safe_name(drama.get("title_zh") or "") \
            or f"Drama {drama_id}"
        folder = os.path.join(library_dir, title)
        src = _source_media(drama_id, drama, media)
        dest = os.path.join(folder, f"{title}.{code}.{fmt}")
        video_dest = None
        if src:
            ext = os.path.splitext(src)[1].lower()
            if ext not in _VIDEO_EXTS:
                raise InvalidInputError("That video type can't be added to Jellyfin.")
            video_dest = os.path.join(folder, f"{title}{ext}")
        for d in filter(None, (video_dest, dest)):  # refuse before writing anything
            _check_free(d, library_dir, overwrite)
        if video_dest:
            _place(src, video_dest, library_dir, overwrite, text=False)
            files.append(video_dest)
        _place(text, dest, library_dir, overwrite, text=True)
        files.append(dest)

    status = "skipped"
    if refresh:
        try:
            if item_id:  # "Default" runs the file scan that finds new external subtitles
                _request("POST", f"/Items/{item_id}/Refresh", params=_ITEM_REFRESH)
            else:
                _request("POST", "/Library/Refresh")
            status = "done"
        except (DependencyUnavailableError, InvalidInputError, ConflictError):
            status = "failed"
    return {"drama_id": drama_id, "refresh": status,
            "files": [os.path.relpath(p, library_dir) for p in files]}
