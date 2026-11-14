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
from urllib.parse import parse_qsl, urlsplit

import comic_chapter_links
import comic_chapters
import db
import db.dramas
import page_server
from lib.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError, ServiceError
from services import (drama_service, extension_novel_service, link_details_service, ownership_service,
                      source_link_service)
from lib import clock
from services.series_service import term_key
from engine_backends.shared import redact_secrets

MAX_NAME_CHARS = drama_service.MAX_NAME_LEN
MAX_CANDIDATES = 12
MAX_CANDIDATE_CHARS = 1000

# Same principal as the other bridge saves: the PC's own token is the local owner's access.
_BRIDGE_PRINCIPAL = extension_novel_service._BRIDGE_PRINCIPAL

# One send at a time: two popups (or a double click) on the same new series would
# otherwise both find nothing and each create it.
_resolve_lock = threading.Lock()
_title_spend_lock = threading.Lock()
# Titles whose creator is still translating the name; guarded by _resolve_lock. The series is named
# from that translation, so a second capture matching the title meanwhile must not join one first.
_series_pending: set = set()

_FAILED = "Could not set up a title for this page; pick one by hand."
_NO_TITLE = ("No existing title matches this page. Pick a drama, or turn on creating new titles.")
_KINDS = ("comic", "novel")

_NUMBER = r"[\d０-９一二三四五六七八九十百零]+"
_CHAPTER_MARK = (rf"第\s*{_NUMBER}\s*[章话話回集卷巻期]|(?:chapter|chap|ch|episode|ep|vol|volume)\.?\s*[\d.]+"
                 r"|[\d.]+\s*[화話话章권巻卷]|#\s*\d+")
_CHAPTER_ONLY = re.compile(rf"^(?:{_CHAPTER_MARK}|\d+)$", re.IGNORECASE)
# A chapter mark inside brackets ("【第1話】Name") would otherwise be cut together with
# everything after it, leaving only the opening bracket.
_BRACKETED_CHAPTER = re.compile(
    rf"[【\[（(「『〈《][^】\]）)」』〉》]*?(?:{_CHAPTER_MARK})[^】\]）)」』〉》]*[】\]）)」』〉》]", re.IGNORECASE)
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
    return extension_novel_service.one_line_name(value)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _site_words(host: str, site_name: str = "") -> set:
    """What a page title calls the site: the host, its name without the domain, and the
    name the page declares for itself (a CJK site name cannot be guessed from the host)."""
    parts = host.split(".")
    words = {host, host.replace(".", ""), _line(site_name)}
    if len(parts) > 1:
        words.add(parts[-2])
    return {term_key(w) for w in words if w}


def _clean_candidate(text: str, site: set) -> str:
    """The work's name out of one page title: the first piece that is not a
    chapter number or the site's name, with a trailing chapter marker cut off."""
    for part in _SEPARATORS.split(_BRACKETED_CHAPTER.sub(" ", _line(text))):
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


def pick_names(candidates: list, host: str, site_name: str = "") -> tuple:
    """(original, english): the first page title written in CJK script is the
    original name, the first in Latin script the English one. Never the other
    way round, so a translation cannot land in the Original title field."""
    site = _site_words(host, site_name)
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
    site_name = payload.get("site_name") or ""
    if not isinstance(site_name, str) or len(site_name) > MAX_CANDIDATE_CHARS:
        raise InvalidInputError("site_name must be short text.")
    kind = payload.get("kind") or "comic"
    if kind not in _KINDS:
        raise InvalidInputError("kind must be comic or novel.")
    return {"url": url, "titles": raw, "site_name": site_name, "kind": kind, "language": payload.get("language"),
            "create": _flag(payload, "create"), "fill": _flag(payload, "fill"),
            "series": _flag(payload, "series"), "chapter_links": payload.get("chapter_links")}


_NOT_THE_PAGE = ("utm", "fbclid", "gclid", "ref", "source", "token", "sig", "key", "session", "sid")


def _link_names_the_page(url: str, clean_url: str) -> bool:
    """Whether the cleaned link still tells this page's series from another on the site.
    Not when cleaning left only the site root, or dropped a query that is more than tracking
    or a secret (`/read?id=12`): both series would then share one link."""
    if not clean_url or urlsplit(clean_url).path in ("", "/"):
        return False
    return all(k.lower().startswith(_NOT_THE_PAGE) for k, _ in parse_qsl(urlsplit(url).query))


def _find_match(clean_url: str, host: str, original: str, english: str, kind: str,
                principal=_BRIDGE_PRINCIPAL):
    wanted = {term_key(n) for n in (original, english) if n}
    for drama in db.list_dramas():
        if drama.get("media_type") not in _MEDIA_BY_KIND[kind] or db.has_custom_tag(drama, db.MERGED_TAG):
            continue
        # Another user's private title (or one in their private series) is theirs to fill.
        # A title the caller can't change is skipped too, not matched and then
        # refused: a refusal would tell a member that a private title they can't
        # see has this link or name, and stop them making their own.
        if (link_details_service._others_private(principal, drama["id"])
                or not ownership_service.can_edit_drama(principal, drama["id"])):
            continue
        link = (drama.get("source_url") or "").strip()
        if clean_url and link == clean_url:
            return drama
        names = {term_key(drama.get(f)) for f in ("title_zh", "title_en")} - {""}
        if host and link and _host(link) == host and names & wanted:
            return drama
    return None


def _series_display_name(original: str, english: str, drama_id: int) -> str:
    """The name a series is joined or made under. First the series of another title with the same
    original name whose series has this title's owner and privacy (what naming the series after the original
    always did, and what keeps one work from different sites in one series even when each
    translation reads differently), then a series of the same owner and privacy whose name matches
    the English or original name ignoring case and width (the check a new series gets), else the
    English name, else the original."""
    row = db.get_item_ownership("drama", drama_id) or {}

    def same_scope(item: dict) -> bool:
        return (item.get("owner_user_id") == row.get("owner_user_id")
                and bool(item.get("is_private")) == bool(row.get("is_private")))

    english = english or _line((db.get_drama(drama_id) or {}).get("title_en"))
    series = {s["id"]: s for s in db.list_series() if same_scope(s)}
    if original:
        key = term_key(original)
        for drama in db.list_dramas():
            if (drama["id"] != drama_id and drama.get("series_id") in series
                    and term_key(drama.get("title_zh")) == key):
                return series[drama["series_id"]]["name"]
    for name in (english, original):
        for item in series.values():
            if name and term_key(item["name"]) == term_key(name):
                return item["name"]
    return english or original


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
        if db.dramas.assign_series_if_none(drama_id, series_id):
            drama_service.inherit_series_defaults_on_join(drama_id, series_id)
    return (db.get_item_ownership("drama", drama_id) or {}).get("series_id")


def _remember_links(drama_id: int, page: dict, host: str) -> None:
    """The chapter links the page listed, for the reader's chapter list. Comics only,
    and only links on the page's own site: a link elsewhere is not a chapter of it."""
    if page["kind"] == "comic" and host:
        comic_chapter_links.remember(drama_id, comic_chapter_links.clean(page["chapter_links"], host))


def _answer(drama_id: int, created: bool, filled: list) -> dict:
    drama = db.get_drama(drama_id) or {}
    return {"drama_id": drama_id, "created": created, "matched": not created,
            "series_id": drama.get("series_id"), "filled": filled,
            "title": drama.get("title_en") or drama.get("title_zh") or f"drama #{drama_id}",
            "media_type": drama.get("media_type") or ""}


def resolve_title(payload: dict, principal=_BRIDGE_PRINCIPAL, translate_title: bool = True) -> dict:
    """Finds or creates the title for a page. `create` off only finds (422 when
    nothing matches), `fill` off creates a placeholder named after the site with
    the page link but no names, `series` off leaves the title out of any series. `translate_title`
    is False for a caller who may not spend the owner's engine (see api/routers/extension_bridge_routes.py)."""
    if not isinstance(payload, dict):
        raise InvalidInputError("The request must be a JSON object.")
    page = _read(payload)
    kind = page["kind"]
    clean_url = source_link_service.split_source_url(page["url"])[0] if page["url"] else ""
    host = _host(page["url"])
    link_key = clean_url if _link_names_the_page(page["url"], clean_url) else ""
    original, english = pick_names(page["titles"], host, page["site_name"]) if page["fill"] else ("", "")
    series_name = original or english
    with _resolve_lock:
        # With Fill off the title is named after the site, so that name is what a later chapter matches.
        match = _find_match(link_key, host, original or ("" if page["fill"] else host), english, kind,
                            principal)
        if match is not None:
            drama_id = match["id"]
            ownership_service.require_editable(principal, "drama", drama_id)
            filled = []
            if page["fill"]:
                filled = db.dramas.fill_blank_fields(drama_id, {"title_zh": original, "title_en": english})
                if clean_url and drama_service.set_source_url_once(drama_id, page["url"]):
                    filled.append("source_url")
            if page["series"] and series_name and drama_id not in _series_pending:
                _join_series(drama_id, _series_display_name(original, english, drama_id))
            _remember_links(drama_id, page, host)
            return _answer(drama_id, False, filled)
        if not page["create"]:
            raise InvalidInputError(_NO_TITLE)
        language = _language(page["language"], original)
        fields = {"source_language": language, "title_zh": original or ("" if english else host or "Untitled page"),
                  "title_en": english, "principal": principal,
                  "media_type": "novel" if kind == "novel" else _MEDIA_BY_LANGUAGE[language]}
        created = drama_service.create_drama(**fields)
        drama_id = created["id"]
        # The link is kept even with Fill off, or the next send could never find this title again.
        if clean_url:
            drama_service.set_source_url_once(drama_id, page["url"])
        _remember_links(drama_id, page, host)
        filled_names = ([f for f in ("title_zh", "title_en") if created.get(f)]
                        + (["source_url"] if clean_url else []))
        translate = bool(page["fill"] and original and not english and translate_title)
        wants_series = bool(page["series"] and series_name)
        if translate and wants_series:
            _series_pending.add(drama_id)
    try:
        # Outside the lock: an AI call must not hold up the next title's lookup.
        if translate and _translate_title_once(drama_id, original, language):
            filled_names.append("title_en")
        # After the translation, so the series is named in English like the title.
        if wants_series:
            _join_series(drama_id, _series_display_name(original, english, drama_id))
    finally:
        with _resolve_lock:
            _series_pending.discard(drama_id)
    return _answer(drama_id, True, filled_names)


_TITLE_PROMPT = ("Translate this {language} comic or novel title into natural English, as a published "
                 "title would read. Return ONLY a JSON object: {{\"title\": \"<the English title>\"}}. "
                 "Title: {title}")


def _translate_title_once(drama_id: int, original: str, language: str) -> bool:
    """Puts an English title on a title the capture just created from a page that gave only the
    original name, with one small AI call on the engine the extension is set up with (the same one
    its page translations use, so a member is held to the same permission check by the caller). Runs only at creation, so a
    later chapter never spends again, and only fills a blank English title, so a name the person
    typed stays. Skipped (False) when the monthly cap is used up, the engine has no key or the
    reply is unusable; it never fails the import."""
    try:
        from core import LANGUAGE_NAMES
        from engine_backends import engine_registry, llm_tasks, paid_calls
        from engine_backends.shared import extract_first_json_value
        import page_server
        from services import translate_run_service
        config = page_server.get_translation_config()
        engine_name = (config.get("engine") or "").strip()
        engine_cls = engine_registry.ENGINES.get(engine_name)
        if engine_cls is None or not getattr(engine_cls, "supports_reference", False):
            return False
        engine = page_server._build_engine(config)
        if engine is None:
            return False
        # The spend is logged only once the reply is back, so the cap check and the call share one
        # hold: otherwise members creating titles at once could each pass a cap with room for one.
        with _title_spend_lock:
            translate_run_service.refuse_when_cap_spent(
                engine_name, bool(config.get("free_tier")) and engine_name == "gemini",
                config.get("model"))
            reply = llm_tasks.call_llm_json(
                engine, _TITLE_PROMPT.format(language=LANGUAGE_NAMES.get(language, "source-language"),
                                             title=original[:300]),
                max_tokens=150, fallback=None,
                usage_cb=paid_calls.usage_logger(drama_id, engine_name, engine, "extension_title_translate"))
        data = extract_first_json_value(re.sub(r"^```json|^```|```$", "", (reply or "").strip(),
                                               flags=re.MULTILINE).strip())
        # The reply becomes a series name too, so it gets the same character cleaning as page names.
        english = _line(data.get("title")) if isinstance(data, dict) else ""
        english = redact_secrets(english)[:drama_service.MAX_NAME_LEN]
        return bool(english) and bool(db.dramas.fill_blank_fields(drama_id, {"title_en": english}))
    except Exception as exc:
        import applog
        applog.get_logger().warning("Translating a new title failed: %s", redact_secrets(str(exc)))
        return False


_STATUS = ((InvalidInputError, 422), (NotFoundError, 404), (ForbiddenError, 403), (ConflictError, 409))


def resolve_from_bridge(payload: dict, principal=_BRIDGE_PRINCIPAL, translate_title: bool = True) -> dict:
    """page_server's POST /title: `resolve_title` with its errors turned into
    the bridge's own, since a raw exception could carry a path."""
    try:
        return resolve_title(payload, principal, translate_title)
    except ServiceError as e:
        status = next((code for cls, code in _STATUS if isinstance(e, cls)), 500)
        raise page_server.EndpointError(status, redact_secrets(e.message)) from None
    except Exception:
        import applog
        applog.get_logger().warning("Setting up an extension title failed", exc_info=True)
        raise page_server.EndpointError(500, _FAILED) from None


# Query keys and path words that carry a chapter's own number. A bare `id` is not here: on many
# sites it names the series, and a number taken from it would file every chapter under one label.
_CHAPTER_QUERY_KEYS = ("no", "episode_no", "episode", "chapter", "chapter_no", "ep", "cno")
_CHAPTER_PATH = re.compile(r"(?:^|/)(?:chapter|chap|episode|ep|ch)[-_/]?(\d+(?:\.\d+)?)(?:/|$)", re.IGNORECASE)


def capture_chapter_ref(source_url: str, run_id: str | None):
    """The chapter label for pages a capture just saved, or None when nothing names one.
    The number comes from the page address when it carries one. Otherwise the whole capture
    (every batch of one run shares `run_id`) is one chapter stamped with its import time, so
    imported chapters stay in the order they were captured and never fall into "unknown"."""
    parts = urlsplit(str(source_url or ""))
    host = (parts.hostname or "")[:40]
    number = next((v for k, v in parse_qsl(parts.query) if k.lower() in _CHAPTER_QUERY_KEYS
                   and re.fullmatch(r"\d+(?:\.\d+)?", v)), None)
    if number is None:
        found = _CHAPTER_PATH.search(parts.path)
        number = found.group(1) if found else None
    if number is not None:
        return comic_chapters.chapter_ref(chapter_id=number, title=f"Chapter {number}",
                                          source=host, url=source_url)
    if not run_id:
        return None
    stamp = clock.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return comic_chapters.chapter_ref(chapter_id=f"run-{run_id}", title=f"Imported {stamp}",
                                      source=host, url=source_url)
