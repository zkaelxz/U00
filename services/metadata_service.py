"""
services/metadata_service.py -- Media analysis and metadata auto-fill for one
drama (Migration Slice 37), the API counterpart of the New-drama form's
"Analyze a media file" and "Auto-fill from a public listing page" expanders.

  - analyze_media: ffprobe read of the drama's stored media. Numbers and
    booleans only -- no path, filename or track title is returned.
  - autofill_suggestion: fetches a public page (or takes pasted text), asks
    the chosen LLM engine for bibliographic fields and RETURNS them; nothing
    is written.
  - apply_autofill: writes only the whitelisted fields, through
    drama_service.update_drama_metadata (which re-validates them).

Safety: a client-supplied URL must be http(s) with a public host -- every
address the host resolves to (and every redirect hop, followed manually) must
be global (not loopback/private/link-local/reserved). Residual risk: DNS
rebinding between the check and requests' own lookup. All fetches carry
timeout=, keys are resolved server-side and never sent by clients, and
exception text is never echoed (fixed messages only), so no secret can reach
an error.

No Streamlit or FastAPI import: plain dicts in, plain dicts out.
"""
import ipaddress
import os
import socket
from typing import Optional
from urllib.parse import urljoin, urlsplit

import db
import media_inspect
import metadata_lookup
import translate_engines
from services import drama_service, settings_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                     NotFoundError)

SUGGEST_FIELDS = ("title_en", "title_zh", "author", "studio", "director", "voice_actors",
                  "summary")
APPLY_FIELDS = SUGGEST_FIELDS + ("source_url",)
MAX_PAGE_TEXT_CHARS = 200_000
MAX_FETCH_BYTES = 2_000_000
MAX_REDIRECTS = 3
FETCH_TIMEOUT = 20
DEFAULT_ENGINE = "claude"

_FETCH_FAILED = "The page could not be fetched. Paste the page text instead."
_BAD_URL = "url must be a valid http:// or https:// address."


def _require_drama(drama_id) -> dict:
    drama = None
    if isinstance(drama_id, int) and not isinstance(drama_id, bool) \
            and 0 < drama_id <= drama_service.MAX_ID:
        drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError("No drama with that id.")
    return drama


def _stored_media_path(drama_id, drama) -> Optional[str]:
    """Video first, else audio; a bare filename inside the drama folder that
    is a regular file whose real path stays inside it."""
    base = os.path.realpath(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    for col in ("source_video_filename", "audio_filename"):
        name = drama.get(col)
        if not name or name != os.path.basename(name):
            continue
        path = os.path.realpath(os.path.join(base, name))
        if os.path.dirname(path) == base and os.path.isfile(path):
            return path
    return None


def analyze_media(drama_id: int) -> dict:
    """NotFoundError unknown drama; InvalidInputError no stored media;
    DependencyUnavailableError if ffprobe is missing or cannot read it."""
    drama = _require_drama(drama_id)
    path = _stored_media_path(drama_id, drama)
    if path is None:
        raise InvalidInputError("This drama has no media file yet.")
    try:
        probe = media_inspect._run_ffprobe(path)
    except media_inspect.ProbeError as e:
        raise DependencyUnavailableError(
            "Media analysis is unavailable: ffprobe is missing or cannot read this file.") from e
    fmt = probe.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    has_video = False
    audio = []
    for s in probe.get("streams") or []:
        kind = s.get("codec_type")
        if kind == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            has_video = True
        elif kind == "audio":
            audio.append(s)
    sample_rate = None
    if audio:
        try:
            sample_rate = int(audio[0].get("sample_rate"))
        except (TypeError, ValueError):
            sample_rate = None
    return {"drama_id": drama_id, "duration_seconds": duration, "has_video": has_video,
            "has_audio": bool(audio), "audio_track_count": len(audio),
            "sample_rate": sample_rate}


def _check_public_url(url: str):
    """http(s) only, with a host whose every resolved address is public."""
    if not isinstance(url, str) or len(url) > drama_service.MAX_URL_LEN:
        raise InvalidInputError(_BAD_URL)
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise InvalidInputError(_BAD_URL) from None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise InvalidInputError(_BAD_URL)
    try:
        infos = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except (UnicodeError, OSError):
        raise DependencyUnavailableError(_FETCH_FAILED) from None
    if not infos:
        raise DependencyUnavailableError(_FETCH_FAILED)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise InvalidInputError("url must point to a public web address.")


def _fetch_page_text(url: str) -> str:
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise DependencyUnavailableError("Fetching pages needs requests and beautifulsoup4 "
                                         "installed.") from e
    headers = {"User-Agent": "Mozilla/5.0 (compatible; BaiheStudio/1.0)"}
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _check_public_url(current)
            resp = requests.get(current, headers=headers, timeout=FETCH_TIMEOUT,
                                allow_redirects=False, stream=True)
            try:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise DependencyUnavailableError(_FETCH_FAILED)
                    current = urljoin(current, location)
                    continue
                resp.raise_for_status()
                raw = resp.raw.read(MAX_FETCH_BYTES + 1, decode_content=True)
                encoding = resp.encoding or "utf-8"
            finally:
                resp.close()
            html = raw[:MAX_FETCH_BYTES].decode(encoding, errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            return "\n".join(ln.strip() for ln in soup.get_text("\n").splitlines()
                             if ln.strip())[:MAX_PAGE_TEXT_CHARS]
    except (InvalidInputError, DependencyUnavailableError):
        raise
    except Exception as e:
        raise DependencyUnavailableError(_FETCH_FAILED) from e
    raise DependencyUnavailableError(_FETCH_FAILED)  # too many redirects


def _api_key(engine_name: str) -> Optional[str]:
    if engine_name == "ollama":
        return "local"
    return settings_service.resolve_key(engine_name)


def autofill_suggestion(drama_id: int, url: Optional[str] = None,
                        page_text: Optional[str] = None,
                        engine_name: Optional[str] = None) -> dict:
    """Returns {"drama_id", "suggestion": {whitelisted fields found},
    "found": bool}; writes nothing. Exactly one of `url` / `page_text`.
    Unknown drama 404; bad input 422; no key / fetch failure / LLM failure
    503 (fixed text)."""
    _require_drama(drama_id)
    if bool(url) == bool(page_text and page_text.strip()):
        raise InvalidInputError("Pass exactly one of url or page_text.")
    engine_name = engine_name or DEFAULT_ENGINE
    supported = [e for e, cls in translate_engines.ENGINES.items()
                 if getattr(cls, "supports_reference", False)]
    if engine_name not in supported:
        raise InvalidInputError("That engine cannot extract metadata.",
                                details={"allowed": supported})
    if url:
        _check_public_url(url)
    api_key = _api_key(engine_name)
    if not api_key:
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    text = _fetch_page_text(url) if url else page_text[:MAX_PAGE_TEXT_CHARS]
    if not text.strip():
        return {"drama_id": drama_id, "suggestion": {}, "found": False}
    try:
        engine = translate_engines.get_engine(engine_name, api_key)
        found = metadata_lookup.extract_metadata_llm(text, engine)
    except Exception as e:
        raise DependencyUnavailableError("The metadata lookup service is unavailable.") from e
    suggestion = {k: v.strip() for k, v in (found or {}).items()
                  if k in SUGGEST_FIELDS and isinstance(v, str) and v.strip()}
    if suggestion and url:
        suggestion["source_url"] = url
    return {"drama_id": drama_id, "suggestion": suggestion, "found": bool(suggestion)}


def apply_autofill(drama_id: int, fields: dict) -> dict:
    """Writes only APPLY_FIELDS through update_drama_metadata; returns the
    drama detail. Unknown keys or an empty set are InvalidInputError."""
    _require_drama(drama_id)
    if any(k not in APPLY_FIELDS for k in fields):
        raise InvalidInputError("Only autofill suggestion fields can be applied.",
                                details={"allowed": list(APPLY_FIELDS)})
    chosen = {k: v for k, v in fields.items() if v is not None}
    if not chosen:
        raise InvalidInputError("No fields to apply.")
    return drama_service.update_drama_metadata(drama_id, **chosen)
