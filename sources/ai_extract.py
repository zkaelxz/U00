"""
sources/ai_extract.py -- content extraction with an LLM as the *fallback*,
plus the independent confidence checks every result goes through.

This is metadata_lookup.py's pattern (one strict "return ONLY JSON, null
for anything not found" prompt through translate_engines.call_llm_json)
applied to a different job: the chapter itself rather than its catalog
entry. Two rules make that safe for real content:

  * The model never writes content. The page is split into numbered text
    blocks and links; the model answers only with block/link ids, and the
    text is copied out of the page by the app. So it can't summarize,
    rewrite, translate or "correct" anything, the original language comes
    through untouched, and results are matched back by id -- never by
    list position.
  * The model's own confidence is never trusted on its own. Every field
    is re-checked here (text actually on the page, plausible length,
    repeat rate, well-formed URLs, next/previous links shaped like this
    chapter's URL, duplicate and undersized images...). A model score can
    only *lower* a field's confidence, never raise it past what the checks
    allow.

Nothing in this module fetches or downloads anything: it reads HTML it's
handed and candidate lists the deterministic pass already surfaced.
Downloading stays with generic_import's downloader and the existing video
download path.

This module holds what every kind shares: confidence scoring, URL helpers,
the page model and the one LLM call. The per-kind logic lives in
ai_extract_novel.py, ai_extract_comic.py and ai_extract_media.py.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urljoin, urlsplit

from . import chapter_order

# Bump when the shape of a stored extraction (or of the model's id-keyed
# answer) changes -- cached entries from another version are discarded,
# never returned stale-shaped.
EXTRACTION_SCHEMA_VERSION = 1

HIGH, MEDIUM, LOW, FAILED = "HIGH", "MEDIUM", "LOW", "FAILED"
FIELDS = ("title", "author", "chapter_title", "chapter_number", "content", "next_url",
          "previous_url", "page_images", "page_order")

MAX_NOVEL_CHARS = 200_000        # a whole book on one page, not a chapter
MAX_PAGES = 500

COMIC_ROLES = ("content", "cover", "thumbnail", "ad", "recommendation", "icon", "duplicate",
               "other")
MEDIA_ROLES = ("main", "alternate", "subtitle", "audio_track", "trailer", "ad", "preview",
               "unrelated")


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

def bucket(score: float) -> str:
    if score >= 0.8:
        return HIGH
    if score >= 0.5:
        return MEDIUM
    if score > 0:
        return LOW
    return FAILED


def _field(score: float, checks=(), model=None) -> dict:
    """One field's confidence. `model` (the model's own 0-1 claim, if it
    gave one) can pull the score down, never up."""
    score = max(0.0, min(1.0, float(score)))
    if isinstance(model, (int, float)) and not isinstance(model, bool) and 0 <= model <= 1:
        score = min(score, float(model))
    else:
        model = None
    return {"score": round(score, 3), "bucket": bucket(score), "checks": list(checks),
            "model_confidence": model}


def overall(data: dict) -> dict:
    return data.get("overall") or {"score": 0.0, "bucket": FAILED}


# ---------------------------------------------------------------------------
# Small text / URL helpers
# ---------------------------------------------------------------------------

def squash(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def well_formed(url) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    parts = urlsplit(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.hostname) and " " not in url.strip()


def _site(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _path_shape(url: str):
    parts = urlsplit(url or "")
    segs = [s for s in parts.path.split("/") if s]
    return [re.sub(r"\d+", "#", s.lower()) for s in segs], sorted(k for k, _ in parse_qsl(parts.query))


def link_resemblance(link: str, current: str) -> tuple:
    """(score, reason): how much a next/previous link looks like another
    chapter of the same book, judged from URL structure alone."""
    if not link:
        return 0.0, "not found on the page"
    if not well_formed(link):
        return 0.0, f"not a well-formed http(s) URL ({str(link)[:80]})"
    if _site(link) != _site(current):
        return 0.0, f"points to a different site ({urlsplit(link).hostname})"
    if link.split("#")[0].rstrip("/") == (current or "").split("#")[0].rstrip("/"):
        return 0.0, "points back to this same page"
    (a, qa), (b, qb) = _path_shape(link), _path_shape(current)
    if a == b and qa == qb:
        return 0.9, "same URL pattern as this chapter"
    if len(a) == len(b) and a[:-1] == b[:-1] and qa == qb and a and \
            not re.search(r"\d", urlsplit(link).path.split("/")[-1] or "x") and \
            not re.search(r"\d", urlsplit(current).path.split("/")[-1] or "x"):
        return 0.55, "same section of the site (no chapter number in either URL to compare)"
    return 0.0, (f"doesn't look like another chapter of this book (URL pattern "
                 f"/{'/'.join(a)} vs this chapter's /{'/'.join(b)})")


def url_skeleton(url: str) -> str:
    """host + directory with digit runs normalized: the part of a page
    image's URL that stays the same from chapter to chapter."""
    parts = urlsplit(url or "")
    directory = parts.path.rsplit("/", 1)[0] + "/"
    return (parts.hostname or "").lower() + re.sub(r"\d+", "#", directory.lower())


_SIZE_SUFFIX = re.compile(r"[-_.@](thumb|thumbnail|small|sm|s|m|t|mini|\d{2,4}x\d{2,4}|\d{2,4}w|2x|3x)$",
                          re.I)
_THUMB_DIR = re.compile(r"/(thumbs?|thumbnails?|small|preview|resize[^/]*)/", re.I)


def dedup_key(url: str) -> str:
    """Same picture at another size/through another attribute -> same key."""
    parts = urlsplit(url or "")
    path = _THUMB_DIR.sub("/", parts.path.lower())
    stem, dot, ext = path.rpartition(".")
    if not dot:
        stem, ext = path, ""
    stem = _SIZE_SUFFIX.sub("", stem)
    return f"{(parts.hostname or '').lower()}{stem}.{ext}"


def _looks_thumb(url: str) -> bool:
    parts = urlsplit(url or "")
    stem = parts.path.lower().rpartition(".")[0] or parts.path.lower()
    return bool(_THUMB_DIR.search(parts.path.lower()) or _SIZE_SUFFIX.search(stem))


def file_number(url: str):
    stem = urlsplit(url or "").path.rsplit("/", 1)[-1].rpartition(".")[0]
    m = re.findall(r"\d+", stem)
    return int(m[-1]) if m else None


def content_hash(kind: str, payload: str) -> str:
    """Hash of exactly what the model would read for this page -- the
    extraction cache's key. Stable across cosmetic markup churn that
    doesn't change the text/links/candidates the prompt carries."""
    return hashlib.sha256(f"{kind}\n{payload}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The page as numbered blocks and links
# ---------------------------------------------------------------------------

_BLOCKISH = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "dd", "dt", "pre",
             "td", "th", "figcaption", "caption", "div", "section", "article", "main", "body",
             "header", "footer", "nav", "aside", "form", "center", "ul", "ol", "dl", "table",
             "tr", "tbody", "title"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_DIALOGUE_START = re.compile(r"^\s*[“「『\"'‘《〈]")
MAX_BLOCKS = 3000
MAX_LINKS = 400


@dataclass
class Block:
    id: str
    tag: str
    text: str
    el: object = field(repr=False, default=None, compare=False)
    link_only: bool = False


@dataclass
class Link:
    id: str
    text: str
    url: str
    el: object = field(repr=False, default=None, compare=False)


class PageModel:
    """A page split into text blocks (paragraph-sized, in reading order,
    split at block elements and <br>) and links. Built once per page and
    shared by the deterministic tier, the LLM prompt, profile inference
    and the review screen."""

    def __init__(self, html: str, url: str):
        from bs4 import BeautifulSoup
        from .detect import page_title
        self.url = url
        self.html = html or ""
        self.page_title = page_title(self.html)
        soup = BeautifulSoup(self.html, "html.parser")
        for t in soup(["script", "style", "noscript", "template"]):
            t.decompose()
        self.soup = soup
        self.blocks = []
        self.links = []
        self._build_blocks()
        self._build_links()
        self._blocks_by_id = {b.id: b for b in self.blocks}
        self._links_by_id = {l.id: l for l in self.links}
        self.text = "\n".join(b.text for b in self.blocks)
        self.squashed = squash(self.text)

    def _build_blocks(self):
        from bs4 import NavigableString, Tag
        buf, state = [], {"owner": None, "link_chars": 0}

        def flush():
            text = re.sub(r"\s+", " ", "".join(buf)).strip()
            buf.clear()
            if text and len(self.blocks) < MAX_BLOCKS:
                self.blocks.append(Block(id=f"b{len(self.blocks)}", tag=state["owner"].name,
                                         text=text, el=state["owner"],
                                         link_only=state["link_chars"] >= 0.9 * len(squash(text))))
            state["link_chars"] = 0

        for node in self.soup.descendants:
            if isinstance(node, Tag):
                if node.name in ("br", "hr"):
                    flush()
                continue
            if type(node) is not NavigableString:
                continue          # comments, doctype, CDATA
            s = str(node)
            if not s.strip():
                if buf:
                    buf.append(" ")
                continue
            owner = node.parent
            while owner is not None and owner.name not in _BLOCKISH and owner.parent is not None:
                owner = owner.parent
            if owner is not state["owner"]:
                flush()
                state["owner"] = owner
            buf.append(s)
            if node.find_parent("a") is not None:
                state["link_chars"] += len(squash(s))
        flush()

    def _build_links(self):
        for a in self.soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
                continue
            self.links.append(Link(id=f"L{len(self.links)}", text=a.get_text(" ", strip=True)[:80],
                                   url=urljoin(self.url, href), el=a))
            if len(self.links) >= MAX_LINKS:
                break

    def block(self, bid):
        return self._blocks_by_id.get(bid) if isinstance(bid, str) else None

    def link(self, lid):
        return self._links_by_id.get(lid) if isinstance(lid, str) else None

    def contains(self, text: str) -> bool:
        return bool(text) and squash(text) in self.squashed


def blocks_payload(page: PageModel, budget: int = 14000) -> str:
    """The block/link listing the model reads. Long blocks are shortened
    in the prompt only -- the app always copies the full original text."""
    n = max(1, len(page.blocks))
    snip = max(40, min(160, budget // n))
    lines = [f"URL: {page.url}", f"PAGE TITLE: {page.page_title}", "BLOCKS:"]
    for b in page.blocks:
        t = b.text if len(b.text) <= snip else b.text[:snip] + f"…(+{len(b.text) - snip} chars)"
        lines.append(f"[{b.id}|{b.tag}{'|link' if b.link_only else ''}] {t}")
    lines.append("LINKS:")
    for l in page.links[:150]:
        lines.append(f"[{l.id}] {l.text or '(no text)'} -> {l.url}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The one LLM call shape (metadata_lookup's pattern)
# ---------------------------------------------------------------------------

def llm_available(engine) -> bool:
    return engine is not None and bool(getattr(engine, "supports_reference", False))


# A page read through a signed-in browser can carry
# session-derived credentials inside ordinary URLs (signed image tokens,
# auth_key=, access_token=...). The model only ever needs the page's
# content and the ids it answers with, so those values are blanked before
# any prompt leaves the app. Session cookies never get this far at all --
# nothing in the app reads them (sources/auth_browser.py).
_SENSITIVE_PARAM = re.compile(
    r"([?&#;])((?:[\w.\-]*(?:token|sign|auth|session|sess|secret|credential|ticket|jwt|"
    r"access|policy|expires|hmac|nonce|cookie|passport|key)[\w.\-]*|sid|uid|x-amz-[\w.\-]+)"
    r"=)[^&#\s\"'<>\\]*", re.I)

# The same kind of session-derived credential also shows up as a raw path
# segment with no `=` at all (a real CDN shape, e.g.
# https://cdn.example/priv/<signed-token>/page1.jpg) -- _SENSITIVE_PARAM
# above only catches the query-string-shaped case. Gated on a plausible
# marker segment immediately before the token (the same keyword set as
# above, plus priv/private/secure) so an ordinary chapter/page slug -- which
# never sits right after a segment literally named "token"/"auth"/"priv" --
# is never touched.
SENSITIVE_PATH_SEGMENT = re.compile(
    r"((?:^|/)(?:token|sign(?:ed)?|auth|session|sess|secret|credential|ticket|jwt|access|"
    r"policy|hmac|nonce|cookie|passport|key|priv(?:ate)?|secure)/)"
    r"[\w-]{16,}(?=/|$|[?#])", re.I)


def prompt_safe(prompt: str) -> str:
    """The prompt with credential-shaped URL parameter values blanked and
    anything else secret-shaped redacted."""
    from translate_engines import redact_secrets
    text = _SENSITIVE_PARAM.sub(r"\1\2[REDACTED]", prompt or "")
    text = SENSITIVE_PATH_SEGMENT.sub(r"\1[REDACTED]", text)
    return redact_secrets(text)


def ask_json(engine, prompt: str, max_tokens: int = 2000):
    """The sources package's one call_llm_json: via prompt_safe(), logged to the
    usage log. Returns the parsed JSON object or None."""
    from engine_backends.paid_calls import logged_calls
    from translate_engines import call_llm_json
    with logged_calls(engine, "sources_ai_fallback"):
        text = call_llm_json(engine, prompt_safe(prompt), max_tokens=max_tokens, fallback=None)
    if not text:
        return None
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def engine_info(engine) -> dict:
    return {"provider": type(engine).__name__ if engine is not None else None,
            "model": getattr(engine, "model", None),
            "model_version": getattr(engine, "model_version", None)}


# ---------------------------------------------------------------------------
# Checks shared by the novel, comic and media validators
# ---------------------------------------------------------------------------


def chapter_num_str(title: str):
    n = chapter_order.chapter_number(title or "") if title else None
    if n is None:
        return None
    return str(int(n)) if float(n).is_integer() else str(n)


def _presence(page: PageModel, value, checks) -> float:
    if value is None or not str(value).strip():
        checks.append("not found on the page")
        return 0.0
    if len(str(value)) > 200:
        checks.append("too long to be this field")
        return 0.3
    if not page.contains(str(value)):
        checks.append("isn't actually in the page's text")
        return 0.25
    return 0.9


def _number_check(number, chapter_title, url, checks) -> float:
    if number is None:
        checks.append("not found on the page")
        return 0.0
    got = chapter_order.number_value(unicodedata.normalize("NFKC", str(number)).strip())
    from_title = chapter_order.chapter_number(chapter_title or "") if chapter_title else None
    if got is not None and from_title is not None and got == from_title:
        return 0.9
    if squash(str(number)) and (squash(str(number)) in squash(chapter_title or "")):
        return 0.8
    if got is not None and re.search(rf"(?<!\d){int(got) if got.is_integer() else got}(?!\d)",
                                     urlsplit(url or "").path):
        checks.append("matches the URL, not the chapter title")
        return 0.6
    checks.append("doesn't match the chapter title or URL")
    return 0.0
