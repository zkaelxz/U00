"""
services/extension_title_service.py -- the extension bridge's "Auto" target: the
title (and series) a page belongs to, found or created from what the page says,
so the person need not pick a drama before sending pages.

A page is matched to an existing title by its source link, or else by the same
site and the same original name, because a chapter's address differs from the
series' own. A matched title only has blank fields filled (names, source link,
series), so nothing the person typed is overwritten; a new one gets the names,
the cleaned link and a series of the same name, which is where merging or moving
titles continues in the app.

The answer goes back through `page_server.py`, so it carries ids, booleans and
the stored display name only, never the page address or a filesystem path.
"""

import re
import threading
from urllib.parse import urlsplit

import db
import page_server
from lib.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError, ServiceError
from services import (drama_service, extension_novel_service, link_details_service, ownership_service,
                      source_link_service)
from services.series_service import term_key
from translate_engines import redact_secrets

MAX_NAME_CHARS = drama_service.MAX_NAME_LEN
MAX_CANDIDATES = 12
MAX_CANDIDATE_CHARS = 1000

# Same principal as the other bridge saves: the PC's own token is the local owner's access.
_BRIDGE_PRINCIPAL = extension_novel_service._BRIDGE_PRINCIPAL

# One send at a time: two popups (or a double click) on the same new series would
# otherwise both find nothing and each create it.
_resolve_lock = threading.Lock()

_FAILED = "Could not set up a title for this page; pick one by hand."
_NO_TITLE = ("No existing title matches this page. Pick a drama, or turn on creating new titles.")
_KINDS = ("comic", "novel")

_NUMBER = r"[\d０-９一二三四五六七八九十百零]+"
_CHAPTER_MARK = (rf"第\s*{_NUMBER}\s*[章话話回集卷期]|(?:chapter|chap|ch|episode|ep|vol|volume)\.?\s*[\d.]+"
                 r"|[\d.]+\s*[화話话章]|#\s*\d+")
_CHAPTER_ONLY = re.compile(rf"^(?:{_CHAPTER_MARK}|\d+)$", re.IGNORECASE)
_CHAPTER_TAIL = re.compile(rf"\s*(?:{_CHAPTER_MARK}).*$", re.IGNORECASE)
# Page titles read "Name chapter - Site" or "chapter_Name_Site"; a hyphen with no spaces
# belongs to the name ("One-Punch Man").
_SEPARATORS = re.compile(r"\s+[-–—|·»«]\s+|\s*[|｜_]\s*")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_LATIN_PHRASE = re.compile(r"[A-Za-z][A-Za-z0-9'’.,:!?\- ]*[A-Za-z0-9.!?]")

# A page only matches titles of its own kind: a comic site's page never lands in an audio or video title.
_MEDIA_BY_KIND = {"comic": ("manga", "manhwa", "manhua"), "novel": ("novel",)}

_MEDIA_BY_LANGUAGE = {"zh": "manhua", "ja": "manga", "ko": "manhwa"}


def _line(value) -> str:
    return re.sub(r"\s+", " ", extension_novel_service.clean_text(str(value or ""))).strip()


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _site_words(host: str) -> set:
    """What a page title calls the site: the host and its name without the domain."""
    parts = host.split(".")
    words = {host, host.replace(".", "")}
    if len(parts) > 1:
        words.add(parts[-2])
    return {term_key(w) for w in words if w}


def _clean_candidate(text: str, site: set) -> str:
    """The work's name out of one page title: the first piece that is not a
    chapter number or the site's name, with a trailing chapter marker cut off."""
    for part in _SEPARATORS.split(_line(text)):
        part = part.strip()
        if not part or _CHAPTER_ONLY.match(part) or term_key(part).replace(" ", "") in site:
            continue
        name = _CHAPTER_TAIL.sub("", part).strip()
        if name:
            return name[:MAX_NAME_CHARS]
    return ""


def _split_combined(name: str) -> tuple:
    """(original, translated) when one string holds a CJK name and an English
    phrase of two words or more; otherwise (name, "")."""
    phrases = [p for p in _LATIN_PHRASE.findall(name) if len(p.split()) >= 2]
    if not (phrases and _CJK.search(name)):
        return name, ""
    rest = name
    for phrase in phrases:
        rest = rest.replace(phrase, " ")
    return _line(rest), " ".join(phrases)


def pick_names(candidates: list, host: str) -> tuple:
    """(original, english): the first page title written in CJK script is the
    original name, the first in Latin script the English one. Never the other
    way round, so a translation cannot land in the Original title field."""
    site = _site_words(host)
    original = english = ""
    for text in candidates:
        name = _clean_candidate(text, site)
        if not name:
            continue
        if _CJK.search(name):
            name, translated = _split_combined(name)
            original = original or name
            english = english or translated
        else:
            english = english or name
    return original[:MAX_NAME_CHARS], english[:MAX_NAME_CHARS]


def _language(hint, original: str) -> str:
    if _KANA.search(original):
        return "ja"
    if _HANGUL.search(original):
        return "ko"
    if original:
        return "zh"
    hint = str(hint or "").lower()[:2]
    return hint if hint in _MEDIA_BY_LANGUAGE else "zh"


def _flag(payload: dict, name: str) -> bool:
    value = payload.get(name, True)
    if not isinstance(value, bool):
        raise InvalidInputError(f"{name} must be true or false.")
    return value


def _read(payload: dict) -> dict:
    url = payload.get("url") or ""
    if not isinstance(url, str) or len(url) > drama_service.MAX_URL_LEN or (
            url and not url.startswith(("http://", "https://"))):
        raise InvalidInputError("The page address must be an http or https link.")
    raw = payload.get("titles") or []
    if not isinstance(raw, list) or len(raw) > MAX_CANDIDATES or not all(
            isinstance(t, str) and len(t) <= MAX_CANDIDATE_CHARS for t in raw):
        raise InvalidInputError("titles must be a short list of text.")
    kind = payload.get("kind") or "comic"
    if kind not in _KINDS:
        raise InvalidInputError("kind must be comic or novel.")
    return {"url": url, "titles": raw, "kind": kind, "language": payload.get("language"),
            "create": _flag(payload, "create"), "fill": _flag(payload, "fill"),
            "series": _flag(payload, "series")}


def _find_match(clean_url: str, host: str, original: str, english: str, kind: str):
    wanted = {term_key(n) for n in (original, english) if n}
    for drama in db.list_dramas():
        if drama.get("media_type") not in _MEDIA_BY_KIND[kind] or db.has_custom_tag(drama, db.MERGED_TAG):
            continue
        # Another user's private title (or one in their private series) is theirs to fill.
        if link_details_service._others_private(_BRIDGE_PRINCIPAL, drama["id"]):
            continue
        link = (drama.get("source_url") or "").strip()
        if clean_url and link == clean_url:
            return drama
        names = {term_key(drama.get(f)) for f in ("title_zh", "title_en")} - {""}
        if host and link and _host(link) == host and names & wanted:
            return drama
    return None


def _join_series(drama_id: int, name: str):
    """The series the title joins, only while it is in none, by the rule the link
    details use for a name taken from a site: a series with the same owner and
    privacy as the title, else a new one made with them. Anything else (a series
    someone else owns, or more or less private) leaves the title out of any series."""
    try:
        series_id = link_details_service._series_for(drama_id, name)
    except ConflictError:
        series_id = None
    if series_id is not None:
        db.dramas.assign_series_if_none(drama_id, series_id)
    return (db.get_item_ownership("drama", drama_id) or {}).get("series_id")


def _answer(drama_id: int, created: bool, filled: list) -> dict:
    drama = db.get_drama(drama_id) or {}
    return {"drama_id": drama_id, "created": created, "matched": not created,
            "series_id": drama.get("series_id"), "filled": filled,
            "title": drama.get("title_en") or drama.get("title_zh") or f"drama #{drama_id}",
            "media_type": drama.get("media_type") or ""}


def resolve_title(payload: dict) -> dict:
    """Finds or creates the title for a page. `create` off only finds (422 when
    nothing matches), `fill` off creates a placeholder named after the site with
    the page link but no names, `series` off leaves the title out of any series."""
    if not isinstance(payload, dict):
        raise InvalidInputError("The request must be a JSON object.")
    page = _read(payload)
    kind = page["kind"]
    clean_url = source_link_service.split_source_url(page["url"])[0] if page["url"] else ""
    host = _host(page["url"])
    original, english = pick_names(page["titles"], host) if page["fill"] else ("", "")
    series_name = original or english
    with _resolve_lock:
        # With Fill off the title is named after the site, so that name is what a later chapter matches.
        match = _find_match(clean_url, host, original or ("" if page["fill"] else host), english, kind)
        if match is not None:
            drama_id = match["id"]
            ownership_service.require_editable(_BRIDGE_PRINCIPAL, "drama", drama_id)
            filled = []
            if page["fill"]:
                filled = db.dramas.fill_blank_fields(drama_id, {"title_zh": original, "title_en": english})
                if clean_url and drama_service.set_source_url_once(drama_id, page["url"]):
                    filled.append("source_url")
            if page["series"] and series_name:
                _join_series(drama_id, series_name)
            return _answer(drama_id, False, filled)
        if not page["create"]:
            raise InvalidInputError(_NO_TITLE)
        language = _language(page["language"], original)
        fields = {"source_language": language, "title_zh": original or ("" if english else host or "Untitled page"),
                  "title_en": english, "principal": _BRIDGE_PRINCIPAL,
                  "media_type": "novel" if kind == "novel" else _MEDIA_BY_LANGUAGE[language]}
        created = drama_service.create_drama(**fields)
        drama_id = created["id"]
        # The link is kept even with Fill off, or the next send could never find this title again.
        if clean_url:
            drama_service.set_source_url_once(drama_id, page["url"])
        if page["series"] and series_name:
            _join_series(drama_id, series_name)
        return _answer(drama_id, True, [f for f in ("title_zh", "title_en") if created.get(f)]
                       + (["source_url"] if clean_url else []))


_STATUS = ((InvalidInputError, 422), (NotFoundError, 404), (ForbiddenError, 403), (ConflictError, 409))


def resolve_from_bridge(payload: dict) -> dict:
    """page_server's POST /title: `resolve_title` with its errors turned into
    the bridge's own, since a raw exception could carry a path."""
    try:
        return resolve_title(payload)
    except ServiceError as e:
        status = next((code for cls, code in _STATUS if isinstance(e, cls)), 500)
        raise page_server.EndpointError(status, redact_secrets(e.message)) from None
    except Exception:
        import applog
        applog.get_logger().warning("Setting up an extension title failed", exc_info=True)
        raise page_server.EndpointError(500, _FAILED) from None
