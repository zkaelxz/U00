"""
sources/ai_extract.py -- content extraction with an LLM as the *fallback*,
plus the independent confidence checks every result goes through (roadmap
Step 23g items 1, 2, 3 and 5).

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
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urljoin, urlsplit

from . import chapter_order
from .generic_import import MIN_NOVEL_CHARS, MIN_PAGE_HEIGHT, MIN_PAGE_WIDTH  # same floors

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

def _squash(s: str) -> str:
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


def _file_number(url: str):
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
        self.squashed = _squash(self.text)

    def _build_blocks(self):
        from bs4 import NavigableString, Tag
        buf, state = [], {"owner": None, "link_chars": 0}

        def flush():
            text = re.sub(r"\s+", " ", "".join(buf)).strip()
            buf.clear()
            if text and len(self.blocks) < MAX_BLOCKS:
                self.blocks.append(Block(id=f"b{len(self.blocks)}", tag=state["owner"].name,
                                         text=text, el=state["owner"],
                                         link_only=state["link_chars"] >= 0.9 * len(_squash(text))))
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
                state["link_chars"] += len(_squash(s))
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
        return bool(text) and _squash(text) in self.squashed


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


# Step 23k: a page read through a signed-in browser can carry
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
_SENSITIVE_PATH_SEGMENT = re.compile(
    r"((?:^|/)(?:token|sign(?:ed)?|auth|session|sess|secret|credential|ticket|jwt|access|"
    r"policy|hmac|nonce|cookie|passport|key|priv(?:ate)?|secure)/)"
    r"[\w-]{16,}(?=/|$|[?#])", re.I)


def prompt_safe(prompt: str) -> str:
    """The prompt with credential-shaped URL parameter values blanked and
    anything else secret-shaped redacted."""
    from translate_engines import redact_secrets
    text = _SENSITIVE_PARAM.sub(r"\1\2[REDACTED]", prompt or "")
    text = _SENSITIVE_PATH_SEGMENT.sub(r"\1[REDACTED]", text)
    return redact_secrets(text)


def ask_json(engine, prompt: str, max_tokens: int = 2000):
    """One call through call_llm_json -- the only one in the sources
    package, and always through prompt_safe(). Returns the parsed JSON
    object or None. Network/provider errors propagate to the caller, which
    records them (redacted) as the reason."""
    from translate_engines import call_llm_json
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
# Novel chapters
# ---------------------------------------------------------------------------

_NEXT_WORDS = ("下一章", "下一页", "下一頁", "下一话", "下一話", "下章", "后一章", "next chapter",
               "next", "次へ", "次の話", "次話", "다음화", "다음")
_PREV_WORDS = ("上一章", "上一页", "上一頁", "上一话", "上一話", "上章", "前一章", "previous chapter",
               "prev", "previous", "前へ", "前の話", "前話", "이전화", "이전")
_TOC_WORDS = ("目录", "目錄", "章节目录", "table of contents", "contents", "index", "目次", "목록")


def _keyword_link(page: PageModel, words, current: str):
    """First short link whose text is one of `words` and that is shaped
    like another chapter; else the first keyword match (validation then
    decides)."""
    matches = []
    for l in page.links:
        t = l.text.strip().lower()
        if t and len(t) <= 20 and any(w in t for w in words):
            matches.append(l)
    for l in matches:
        if link_resemblance(l.url, current)[0] > 0:
            return l
    return matches[0] if matches else None


def _is_dialogue(text: str) -> bool:
    return bool(_DIALOGUE_START.match(text or ""))


def _chapter_num_str(title: str):
    n = chapter_order.chapter_number(title or "") if title else None
    if n is None:
        return None
    return str(int(n)) if float(n).is_integer() else str(n)


def novel_data(page: PageModel, body_blocks, *, method: str, heading_ids=(), dialogue_ids=(),
               chapter_title=None, chapter_title_id=None, title=None, author=None,
               chapter_number=None, next_url=None, previous_url=None, toc=()) -> dict:
    """Builds the result from blocks already chosen (by id), copying their
    original text."""
    heading_ids, dialogue_ids = set(heading_ids), set(dialogue_ids)
    paras = []
    for b in body_blocks:
        kind = "heading" if (b.id in heading_ids or b.tag in HEADING_TAGS
                             or b.id == chapter_title_id) else "paragraph"
        paras.append({"block": b.id, "text": b.text, "kind": kind,
                      "dialogue": kind == "paragraph" and (b.id in dialogue_ids or _is_dialogue(b.text))})
    return {"content_type": "novel", "url": page.url, "method": method,
            "title": title, "author": author, "chapter_title": chapter_title,
            "chapter_title_block": chapter_title_id, "chapter_number": chapter_number,
            "paragraphs": paras, "content": "\n".join(p["text"] for p in paras),
            "next_url": next_url, "previous_url": previous_url,
            "toc_links": [{"text": l.text, "url": l.url} for l in toc],
            "rejected": {}}


def blocks_for_text(page: PageModel, text: str) -> list:
    """The page blocks a deterministic extractor's output came from, in
    page order (so a trafilatura/heuristic result gets real block ids and
    paragraph boundaries, and can seed a profile). [] unless every line
    maps to a block -- a partial mapping would silently drop text."""
    wanted = {_squash(l) for l in (text or "").splitlines() if l.strip()}
    found = [b for b in page.blocks if _squash(b.text) in wanted]
    if not wanted or {_squash(b.text) for b in found} != wanted:
        return []
    return found


def deterministic_novel(page: PageModel) -> dict:
    """Step 23's deterministic tier (trafilatura, else the largest-text-
    block heuristic), reshaped into the same result as every other tier."""
    from .generic_import import extract_main_text
    text, method = extract_main_text(page.html, page.url)
    body = blocks_for_text(page, text)
    if not body and text.strip():
        # Couldn't map every line back to a block (e.g. trafilatura
        # normalized some); keep the extractor's own lines, all of them.
        body = [Block(id=f"t{i}", tag="p", text=l.strip())
                for i, l in enumerate(text.splitlines()) if l.strip()]
    heading = next((b for b in page.blocks if b.tag in ("h1", "h2") and b in body), None) or \
        next((b for b in page.blocks if b.tag in ("h1", "h2")), None)
    chapter_title = heading.text if heading else (page.page_title or None)
    nxt = _keyword_link(page, _NEXT_WORDS, page.url)
    prv = _keyword_link(page, _PREV_WORDS, page.url)
    toc = [l for l in page.links if l.text.strip().lower() in _TOC_WORDS][:3]
    return novel_data(page, body, method=method, chapter_title=chapter_title,
                      chapter_title_id=heading.id if heading else None,
                      chapter_number=_chapter_num_str(chapter_title),
                      next_url=nxt.url if nxt else None, previous_url=prv.url if prv else None,
                      toc=toc)


NOVEL_PROMPT = (
    "Below is one web page, split into numbered text blocks [b0], [b1], ... in page order "
    "(each tagged with the HTML element it sits in) and numbered links [L0], [L1], .... "
    "It should contain one chapter of a novel. Work out which blocks ARE the chapter. "
    "Do NOT rewrite, summarize, translate or correct anything, and don't copy text out -- "
    "answer only with block and link ids; the app copies the original text itself.\n\n"
    "Return ONLY a JSON object with these keys (null, or [] for lists, for anything you "
    "can't identify confidently -- never guess):\n"
    '{"title_block": id of the block holding the book/work title, "author_block": id, '
    '"chapter_title_block": id, "chapter_number": "the chapter number exactly as written in '
    'the chapter title", "body_range": [first body block id, last body block id], '
    '"exclude_blocks": [ids inside that range that are NOT chapter text: ads, nav, notices, '
    'comments], "heading_blocks": [ids of headings inside the chapter], '
    '"dialogue_blocks": [ids of spoken-dialogue paragraphs], "next_link": link id of the '
    'next chapter, "previous_link": link id of the previous chapter, "toc_links": [link ids '
    'of the table of contents / chapter list], "confidence": {"chapter_title": 0-1, '
    '"content": 0-1, "next_url": 0-1, "previous_url": 0-1}}. '
    "No preamble, no markdown fences.\n\n")


def novel_from_picks(page: PageModel, picks: dict) -> dict:
    """Turns the model's id-keyed answer into a result. Every id is looked
    up by name; unknown ids are ignored -- nothing is matched by position."""
    picks = picks if isinstance(picks, dict) else {}
    index = {b.id: i for i, b in enumerate(page.blocks)}
    body_ids = [i for i in (picks.get("body_blocks") or []) if i in index]
    rng = picks.get("body_range")
    if not body_ids and isinstance(rng, list) and len(rng) == 2 and all(r in index for r in rng):
        lo, hi = sorted((index[rng[0]], index[rng[1]]))
        body_ids = [b.id for b in page.blocks[lo:hi + 1]]
    exclude = set(picks.get("exclude_blocks") or [])
    body = sorted((page.blocks[index[i]] for i in set(body_ids) - exclude),
                  key=lambda b: index[b.id])

    def text_of(key):
        b = page.block(picks.get(key)) if isinstance(picks.get(key), str) else None
        return (b.text if b else None), (b.id if b else None)

    chapter_title, ct_id = text_of("chapter_title_block")
    title, _ = text_of("title_block")
    author, _ = text_of("author_block")
    number = picks.get("chapter_number")
    number = str(number).strip() if isinstance(number, (str, int, float)) and str(number).strip() else None

    def link_of(key):
        l = page.link(picks.get(key)) if isinstance(picks.get(key), str) else None
        return l.url if l else None

    toc = [page.link(i) for i in (picks.get("toc_links") or []) if isinstance(i, str) and page.link(i)]
    return novel_data(page, body, method="llm", heading_ids=picks.get("heading_blocks") or [],
                      dialogue_ids=picks.get("dialogue_blocks") or [], chapter_title=chapter_title,
                      chapter_title_id=ct_id, title=title, author=author, chapter_number=number,
                      next_url=link_of("next_link"), previous_url=link_of("previous_link"), toc=toc)


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
    got = chapter_order._num(unicodedata.normalize("NFKC", str(number)).strip())
    from_title = chapter_order.chapter_number(chapter_title or "") if chapter_title else None
    if got is not None and from_title is not None and got == from_title:
        return 0.9
    if _squash(str(number)) and (_squash(str(number)) in _squash(chapter_title or "")):
        return 0.8
    if got is not None and re.search(rf"(?<!\d){int(got) if got.is_integer() else got}(?!\d)",
                                     urlsplit(url or "").path):
        checks.append("matches the URL, not the chapter title")
        return 0.6
    checks.append("doesn't match the chapter title or URL")
    return 0.0


def validate_novel(data: dict, page: PageModel, model_conf: dict = None) -> dict:
    """Independent checks on a novel result; fills data["confidence"],
    data["overall"], data["valid"], data["problems"]. Fields that fail are
    set to None (the rejected value kept under data["rejected"])."""
    mc = model_conf if isinstance(model_conf, dict) else {}
    conf = {}
    paras = data.get("paragraphs") or []
    content = (data.get("content") or "").strip()

    checks, score = [], 1.0
    n = len(content)
    if n == 0:
        score = 0.0
        checks.append("no text was extracted")
    elif n < MIN_NOVEL_CHARS:
        score = 0.0
        checks.append(f"only {n} characters -- too short to be a chapter")
    elif n > MAX_NOVEL_CHARS:
        score = 0.0
        checks.append(f"{n:,} characters -- implausibly long for one chapter "
                      "(probably the whole page or a whole book)")
    else:
        texts = [p["text"] for p in paras]
        if len(texts) < 3:
            score -= 0.35
            checks.append(f"only {len(texts)} paragraph(s)")
        longish = [_squash(t) for t in texts if len(_squash(t)) >= 4]
        if longish:
            dup = 1 - len(set(longish)) / len(longish)
            if dup > 0.3:
                score -= 0.6
                checks.append(f"{dup:.0%} of the paragraphs are repeats (looks like navigation)")
        if texts:
            short = sum(1 for t in texts if len(_squash(t)) <= 12) / len(texts)
            if short > 0.6:
                score -= 0.4
                checks.append(f"{short:.0%} of it is short fragments (menus/links, not prose)")
            link_texts = {_squash(l.text) for l in page.links if l.text}
            linky = sum(1 for t in texts if _squash(t) in link_texts) / len(texts)
            if linky > 0.3:
                score -= 0.4
                checks.append(f"{linky:.0%} of the paragraphs are link text")
            sample = texts[:: max(1, len(texts) // 20)]
            present = sum(1 for t in sample if page.contains(t)) / len(sample)
            if present < 0.8:
                score = 0.0
                checks.append("the extracted text isn't actually on the page")
    conf["content"] = _field(score, checks, mc.get("content"))

    for key in ("chapter_title", "title", "author"):
        c = []
        s = _presence(page, data.get(key), c)
        conf[key] = _field(s, c, mc.get(key))
        if s < 0.5 and data.get(key) is not None:
            data["rejected"][key] = data[key]
            data[key] = None

    c = []
    s = _number_check(data.get("chapter_number"), data.get("chapter_title"), page.url, c)
    conf["chapter_number"] = _field(s, c, mc.get("chapter_number"))
    if s == 0 and data.get("chapter_number") is not None:
        data["rejected"]["chapter_number"] = data["chapter_number"]
        data["chapter_number"] = None

    for key in ("next_url", "previous_url"):
        s, why = link_resemblance(data.get(key), page.url)
        conf[key] = _field(s, [why] if s < 0.8 else [], mc.get(key))
        if s == 0 and data.get(key):
            data["rejected"][key] = data[key]
            data[key] = None
    if data.get("next_url") and data.get("next_url") == data.get("previous_url"):
        conf["previous_url"] = _field(0, ["same link as 'next'"])
        data["rejected"]["previous_url"] = data["previous_url"]
        data["previous_url"] = None

    data["confidence"] = conf
    data["overall"] = {"score": conf["content"]["score"], "bucket": conf["content"]["bucket"]}
    data["valid"] = conf["content"]["bucket"] in (HIGH, MEDIUM)
    data["problems"] = list(conf["content"]["checks"]) + [
        f"{k.replace('_', ' ')} rejected: {conf[k]['checks'][0]}" for k in data["rejected"]
        if conf.get(k, {}).get("checks")]
    return data


# ---------------------------------------------------------------------------
# Comic chapters
# ---------------------------------------------------------------------------

_CHROME_HINT = re.compile(r"cover|banner|\bads?\b|advert|sponsor|promo|recommend|related|thumb|"
                          r"logo|avatar|icon|share|qrcode|广告|推荐", re.I)


def comic_candidates(html: str, url: str) -> list:
    """Every candidate the deterministic pass surfaces: <img>/<source>
    (incl. srcset, data-src and other lazy attributes, <picture>) plus
    image URLs listed in inline scripts/JSON manifests."""
    from .generic_import import image_candidates, manifest_candidates
    cands = image_candidates(html, url)
    extra = manifest_candidates(html, url, skip={c.url for c in cands})
    for c in extra:
        c.order = len(cands)
        cands.append(c)
    return cands


def _desc(c, cid) -> dict:
    d = {"id": cid, "url": c.url, "attr": c.attr or "src", "context": c.hint}
    if c.width_attr:
        d["w"] = c.width_attr
    if c.height_attr:
        d["h"] = c.height_attr
    return d


def comic_payload(page: PageModel, candidates) -> str:
    heads = [b.text for b in page.blocks if b.tag in HEADING_TAGS][:4]
    lines = [f"URL: {page.url}", f"PAGE TITLE: {page.page_title}",
             "HEADINGS: " + " / ".join(heads), "IMAGES:"]
    for i, c in enumerate(candidates):
        lines.append(json.dumps(_desc(c, f"i{i}"), ensure_ascii=False))
    return "\n".join(lines)


COMIC_PROMPT = (
    "Below are the candidate images found on one comic-chapter web page, in page order. Each "
    "has an id, the attribute it came from (src, srcset, data-src and other lazy-load "
    "attributes, or \"manifest\" = listed inside a script/JSON block), its declared "
    "width/height if any, and context (alt text, the id/class chain of its container, a link "
    "it sits inside). Decide what each one is. Roles: \"content\" (an actual page of this "
    "chapter), \"cover\", \"thumbnail\" (small preview), \"ad\", \"recommendation\" (promo for "
    "another work), \"icon\" (logo/button/avatar), \"duplicate\" (the same page as another "
    "candidate, e.g. a low-res or lazy placeholder version), \"other\".\n\n"
    "Return ONLY a JSON object: {\"title\": work title exactly as written on the page or null, "
    "\"chapter_title\": exactly as written or null, \"chapter_number\": as written or null, "
    "\"images\": [{\"id\": \"i0\", \"role\": \"...\", \"page\": reading-order position "
    "starting at 1 for content pages (null otherwise), \"confidence\": 0-1}]}. Use only the "
    "ids given -- never invent URLs. null for anything you can't identify confidently. No "
    "preamble, no markdown fences.\n\n")


def comic_data(page: PageModel, candidates, roles: dict, *, method: str, order: dict = None,
               reasons: dict = None, confidences: dict = None, title=None, chapter_title=None,
               chapter_number=None) -> dict:
    """Result in the roadmap's shape. `roles`, `order`, `reasons` and
    `confidences` are keyed by candidate URL (never list position)."""
    order, reasons, confidences = order or {}, reasons or {}, confidences or {}
    content = [c for c in candidates if roles.get(c.url) == "content"]
    content.sort(key=lambda c: (order.get(c.url) if isinstance(order.get(c.url), (int, float))
                                else 10 ** 6, c.order))
    pages = [{"index": i, "resource_url": c.url, "role": "content",
              "confidence": confidences.get(c.url), "source_attr": c.attr or "src"}
             for i, c in enumerate(content)]
    for c in candidates:
        if roles.get(c.url) != "content":
            pages.append({"index": None, "resource_url": c.url, "role": roles.get(c.url) or "other",
                          "confidence": confidences.get(c.url), "source_attr": c.attr or "src",
                          "reason": reasons.get(c.url, "")})
    return {"content_type": "comic", "url": page.url, "method": method, "title": title,
            "chapter_title": chapter_title, "chapter_number": chapter_number, "pages": pages,
            "rejected": {}}


def dedupe_content(candidates, roles: dict, reasons: dict):
    """Independent duplicate pass: two content candidates that are the same
    picture at another size / via another attribute keep only the fuller
    one. Applied whatever the model said."""
    by_key = {}
    for c in candidates:
        if roles.get(c.url) != "content":
            continue
        by_key.setdefault(dedup_key(c.url), []).append(c)
    for group in by_key.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda c: (_looks_thumb(c.url), c.attr == "manifest", c.order))
        for c in group[1:]:
            roles[c.url] = "duplicate"
            reasons[c.url] = f"same picture as {group[0].url}"
    for c in candidates:
        if roles.get(c.url) == "content" and c.width_attr and c.height_attr and \
                (c.width_attr < MIN_PAGE_WIDTH or c.height_attr < MIN_PAGE_HEIGHT):
            roles[c.url] = "thumbnail"
            reasons[c.url] = f"declared size {c.width_attr}x{c.height_attr} is too small for a page"


def comic_from_picks(page: PageModel, candidates, picks: dict) -> dict:
    picks = picks if isinstance(picks, dict) else {}
    by_id = {f"i{i}": c for i, c in enumerate(candidates)}
    roles, order, conf, reasons = {}, {}, {}, {}
    for item in picks.get("images") or []:
        if not isinstance(item, dict) or item.get("id") not in by_id:
            continue
        c = by_id[item["id"]]
        role = item.get("role") if item.get("role") in COMIC_ROLES else "other"
        roles[c.url] = role
        if isinstance(item.get("page"), (int, float)) and not isinstance(item.get("page"), bool):
            order[c.url] = item["page"]
        if isinstance(item.get("confidence"), (int, float)):
            conf[c.url] = float(item["confidence"])
    for c in candidates:
        if c.url not in roles:
            roles[c.url] = "other"
            reasons[c.url] = "the model didn't classify it"
    dedupe_content(candidates, roles, reasons)

    def text(key):
        v = picks.get(key)
        return str(v).strip() if isinstance(v, (str, int, float)) and str(v).strip() else None

    return comic_data(page, candidates, roles, method="llm", order=order, reasons=reasons,
                      confidences=conf, title=text("title"), chapter_title=text("chapter_title"),
                      chapter_number=text("chapter_number"))


_REJECT_ROLE = (("too small", "thumbnail"), ("other chapters", "ad"), ("duplicate", "duplicate"),
                ("third-party", "ad"), ("shape", "other"), ("readable", "other"),
                ("download", "other"))


def comic_from_filter(page: PageModel, candidates, kept, rejected, method="filter") -> dict:
    """The existing content-vs-chrome filter's verdict in the same shape."""
    roles, reasons = {}, {}
    for c in kept:
        roles[c.url] = "content"
    for c in rejected:
        roles[c.url] = next((r for k, r in _REJECT_ROLE if k in c.reject_reason), "other")
        reasons[c.url] = c.reject_reason
    heading = next((b.text for b in page.blocks if b.tag in ("h1", "h2")), None)
    return comic_data(page, candidates, roles, method=method,
                      order={c.url: i for i, c in enumerate(kept)}, reasons=reasons,
                      chapter_title=heading or page.page_title or None,
                      chapter_number=_chapter_num_str(heading or page.page_title))


def comic_needs_review(data: dict, candidates) -> list:
    """Why a deterministic comic result is ambiguous (empty = it isn't)."""
    why = []
    if not data.get("valid"):
        why.extend(data.get("problems") or ["no usable pages"])
    by_url = {c.url: c for c in candidates}
    chrome = [p["resource_url"] for p in data["pages"] if p["role"] == "content"
              and _CHROME_HINT.search((by_url.get(p["resource_url"]).hint if
                                       by_url.get(p["resource_url"]) else "") + " " +
                                      p["resource_url"].rsplit("/", 1)[-1])]
    if chrome:
        why.append(f"{len(chrome)} kept image(s) look like covers/ads/thumbnails by their context")
    return why


def validate_comic(data: dict, page: PageModel, measured: dict = None, model_conf: dict = None) -> dict:
    """Independent checks on a comic result. `measured` maps resource URL
    -> {"width", "height", "sha256"} for images the downloader has
    fetched (never fetched here)."""
    measured = measured or {}
    mc = model_conf if isinstance(model_conf, dict) else {}
    conf = {}
    pages = [p for p in data["pages"] if p["role"] == "content"]
    checks, score = [], 1.0
    bad = [p for p in pages if not well_formed(p["resource_url"])]
    if bad:
        for p in bad:
            p["role"], p["index"], p["reason"] = "other", None, "not a well-formed URL"
        pages = [p for p in pages if p not in bad]
        for i, p in enumerate(pages):
            p["index"] = i
        checks.append(f"{len(bad)} page URL(s) weren't well-formed and were dropped")
        score -= 0.2
    n = len(pages)
    if n == 0:
        score = 0.0
        checks.append("no content pages identified")
    else:
        keys = [dedup_key(p["resource_url"]) for p in pages]
        shas = [measured.get(p["resource_url"], {}).get("sha256") for p in pages]
        known = [s for s in shas if s]
        dup = max(1 - len(set(keys)) / len(keys),
                  (1 - len(set(known)) / len(known)) if known else 0.0)
        if dup > 0.25:
            score = 0.0
            checks.append(f"{dup:.0%} of the pages are the same image -- not a real page set")
        elif dup > 0:
            score -= 0.3
            checks.append(f"{dup:.0%} of the pages repeat")
        dims = [(measured[p["resource_url"]].get("width", 0), measured[p["resource_url"]].get("height", 0))
                for p in pages if p["resource_url"] in measured]
        small = sum(1 for w, h in dims if w < MIN_PAGE_WIDTH or h < MIN_PAGE_HEIGHT)
        if dims and small / len(dims) > 0.3:
            score -= 0.5
            checks.append(f"{small} page(s) are too small to be pages")
        if n > MAX_PAGES:
            score -= 0.5
            checks.append(f"{n} pages -- implausibly many for one chapter")
    conf["page_images"] = _field(score, checks, mc.get("page_images"))

    oc, os_ = [], 0.0
    if n:
        os_ = 0.9
        nums = [_file_number(p["resource_url"]) for p in pages]
        if all(x is not None for x in nums) and len(set(nums)) == n and n > 1:
            if nums == sorted(nums):
                os_ = 0.95
            else:
                os_ = 0.4
                oc.append("page order disagrees with the numbers in the file names")
    else:
        oc.append("no pages to order")
    conf["page_order"] = _field(os_, oc, mc.get("page_order"))

    for key in ("chapter_title", "title"):
        c = []
        s = _presence(page, data.get(key), c)
        conf[key] = _field(s, c, mc.get(key))
        if s < 0.5 and data.get(key) is not None:
            data["rejected"][key] = data[key]
            data[key] = None
    c = []
    s = _number_check(data.get("chapter_number"), data.get("chapter_title"), page.url, c)
    conf["chapter_number"] = _field(s, c, mc.get("chapter_number"))
    if s == 0 and data.get("chapter_number") is not None:
        data["rejected"]["chapter_number"] = data["chapter_number"]
        data["chapter_number"] = None

    data["confidence"] = conf
    data["overall"] = {"score": conf["page_images"]["score"], "bucket": conf["page_images"]["bucket"]}
    data["valid"] = conf["page_images"]["bucket"] in (HIGH, MEDIUM)
    data["problems"] = list(conf["page_images"]["checks"]) + list(conf["page_order"]["checks"])
    return data


# ---------------------------------------------------------------------------
# Video / audio / subtitle resources on an unrecognized page
# ---------------------------------------------------------------------------

_MEDIA_KIND = {"m3u8": "manifest", "mpd": "manifest", "mp4": "video", "webm": "video",
               "mkv": "video", "mov": "video", "m4v": "video", "m4a": "audio", "mp3": "audio",
               "aac": "audio", "ogg": "audio", "opus": "audio", "flac": "audio", "wav": "audio",
               "vtt": "subtitle", "srt": "subtitle", "ass": "subtitle", "ssa": "subtitle"}
_MEDIA_URL = re.compile(
    r"""(?:https?:)?(?:\\?/){2}[^\s"'<>()]+?\.(%s)(?:\?[^\s"'<>()]*)?""" % "|".join(_MEDIA_KIND),
    re.I)


@dataclass
class MediaCandidate:
    url: str
    kind: str                     # video / audio / manifest / subtitle / embed
    attr: str = ""
    language: str = ""
    label: str = ""
    hint: str = ""


def _kind_for(url: str, default: str = "") -> str:
    ext = urlsplit(url).path.rsplit(".", 1)[-1].lower() if "." in urlsplit(url).path else ""
    return _MEDIA_KIND.get(ext, default)


def media_candidates(html: str, url: str) -> list:
    """Every media/subtitle resource the page itself exposes: <video>/
    <audio>/<source>/<track>, og:video metadata, embedded players, and
    media URLs sitting as text in inline scripts/JSON. Identify only --
    nothing is fetched, decoded or played."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    out, seen = [], set()

    def add(raw, kind, attr, language="", label="", hint=""):
        raw = (raw or "").strip().replace("\\/", "/")
        if not raw or raw.startswith(("data:", "blob:", "javascript:")):
            return
        if raw.startswith("//"):
            raw = (urlsplit(url).scheme or "https") + ":" + raw
        absolute = urljoin(url, raw)
        if absolute in seen:
            return
        seen.add(absolute)
        out.append(MediaCandidate(absolute, kind or _kind_for(absolute, "video"), attr,
                                  language, label, hint))

    for tag in soup.find_all(["video", "audio"]):
        if tag.get("src"):
            add(tag["src"], _kind_for(tag["src"], tag.name), f"<{tag.name} src>")
        for s in tag.find_all("source"):
            add(s.get("src"), _kind_for(s.get("src") or "", tag.name), f"<{tag.name}><source>",
                label=s.get("label") or s.get("res") or s.get("size") or "",
                hint=s.get("type") or "")
        for t in tag.find_all("track"):
            add(t.get("src"), "subtitle", f"<track kind={t.get('kind') or 'subtitles'}>",
                language=t.get("srclang") or "", label=t.get("label") or "")
    for prop in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"):
        for m in soup.find_all("meta", attrs={"property": prop}) + \
                soup.find_all("meta", attrs={"name": prop}):
            add(m.get("content"), _kind_for(m.get("content") or "", "video"), f"<meta {prop}>")
    for f in soup.find_all("iframe", src=True):
        if re.search(r"player|embed|video", f["src"], re.I):
            add(f["src"], "embed", "<iframe>", hint="embedded player from another page")
    for script in soup.find_all("script"):
        body = script.string or script.get_text() or ""
        for m in _MEDIA_URL.finditer(body):
            add(m.group(0), _kind_for(m.group(0).replace("\\/", "/")), "script/JSON")
    return out


def resource_types(html: str, url: str) -> list:
    """Step 23k item 6: which resource types the page actually exposes to
    the session that read it (ContentAccess values), from the same
    deterministic detectors the extraction tiers use -- nothing fetched."""
    from .generic_import import extract_main_text_heuristic
    from .models import ContentAccess
    found = []
    if len(extract_main_text_heuristic(html)) >= MIN_NOVEL_CHARS:
        found.append(ContentAccess.TEXT.value)
    if len(comic_candidates(html, url)) >= 3:
        found.append(ContentAccess.IMAGES.value)
    kinds = {c.kind for c in media_candidates(html, url)}
    for kind_set, value in (({"video", "manifest", "embed"}, ContentAccess.VIDEO.value),
                            ({"audio"}, ContentAccess.AUDIO.value),
                            ({"subtitle"}, ContentAccess.SUBTITLES.value)):
        if kinds & kind_set:
            found.append(value)
    return found


def content_access_for(types) -> str:
    from .models import ContentAccess
    types = list(types or ())
    if not types:
        return ContentAccess.UNKNOWN.value
    return types[0] if len(types) == 1 else ContentAccess.MIXED.value


def media_payload(page: PageModel, candidates) -> str:
    lines = [f"URL: {page.url}", f"PAGE TITLE: {page.page_title}", "RESOURCES:"]
    for i, c in enumerate(candidates):
        d = {"id": f"m{i}", "url": c.url, "kind": c.kind, "found_in": c.attr}
        if c.language:
            d["srclang"] = c.language
        if c.label:
            d["label"] = c.label
        if c.hint:
            d["hint"] = c.hint
        lines.append(json.dumps(d, ensure_ascii=False))
    return "\n".join(lines)


MEDIA_PROMPT = (
    "Below are the media resources found on one web page (video, audio, streaming manifests, "
    "subtitle files, embedded players), in page order. Decide what each one is. Roles: "
    "\"main\" (the page's actual video/audio), \"alternate\" (the same content in another "
    "quality/format), \"subtitle\", \"audio_track\", \"trailer\", \"ad\", \"preview\", "
    "\"unrelated\".\n\nReturn ONLY a JSON object: {\"title\": the video's title exactly as "
    "written on the page or null, \"resources\": [{\"id\": \"m0\", \"role\": \"...\", "
    "\"language\": subtitle/audio language code or null, \"confidence\": 0-1}]}. Use only "
    "the ids given -- never invent URLs. null for anything you can't identify confidently. "
    "No preamble, no markdown fences.\n\n")


def media_data(page: PageModel, candidates, roles: dict, *, method: str, languages: dict = None,
               confidences: dict = None, title=None, protection=()) -> dict:
    languages, confidences = languages or {}, confidences or {}
    resources = [{"index": i, "resource_url": c.url, "kind": c.kind,
                  "role": roles.get(c.url, "unrelated"),
                  "language": languages.get(c.url) or c.language or None, "label": c.label or None,
                  "found_in": c.attr, "confidence": confidences.get(c.url)}
                 for i, c in enumerate(candidates)]
    return {"content_type": "video", "url": page.url, "method": method, "title": title,
            "resources": resources, "protection": list(protection), "rejected": {}}


def deterministic_media(page: PageModel, candidates, protection=()) -> tuple:
    """(data, ambiguous_reasons). Unambiguous = exactly one playable
    main-looking resource; subtitles keep their declared language."""
    main_like = [c for c in candidates if c.kind in ("video", "manifest", "audio")]
    roles = {}
    for c in candidates:
        if c.kind == "subtitle":
            roles[c.url] = "subtitle"
    why = []
    if len(main_like) == 1:
        roles[main_like[0].url] = "main"
    elif not main_like:
        why.append("no playable video/audio resource found on the page")
    else:
        why.append(f"{len(main_like)} possible video/audio resources -- which one is the "
                   "actual content isn't clear")
    for c in candidates:
        roles.setdefault(c.url, "unrelated" if c.kind != "embed" else "alternate")
    title = next((b.text for b in page.blocks if b.tag in ("h1", "h2")), None) or page.page_title or None
    return media_data(page, candidates, roles, method="deterministic", title=title,
                      protection=protection), why


def media_from_picks(page: PageModel, candidates, picks: dict, protection=()) -> dict:
    picks = picks if isinstance(picks, dict) else {}
    by_id = {f"m{i}": c for i, c in enumerate(candidates)}
    roles, langs, conf = {}, {}, {}
    for item in picks.get("resources") or []:
        if not isinstance(item, dict) or item.get("id") not in by_id:
            continue
        c = by_id[item["id"]]
        roles[c.url] = item.get("role") if item.get("role") in MEDIA_ROLES else "unrelated"
        if isinstance(item.get("language"), str) and item["language"].strip():
            langs[c.url] = item["language"].strip()
        if isinstance(item.get("confidence"), (int, float)):
            conf[c.url] = float(item["confidence"])
    # A subtitle file is a subtitle whatever the model called it.
    for c in candidates:
        if c.kind == "subtitle":
            roles[c.url] = "subtitle"
    t = picks.get("title")
    return media_data(page, candidates, roles, method="llm", languages=langs, confidences=conf,
                      title=str(t).strip() if isinstance(t, str) and t.strip() else None,
                      protection=protection)


def validate_media(data: dict, page: PageModel) -> dict:
    conf = {}
    res = data["resources"]
    checks, score = [], 0.9
    for r in res:
        if not well_formed(r["resource_url"]):
            r["role"] = "unrelated"
            checks.append(f"dropped a malformed URL ({r['resource_url'][:60]})")
    main = [r for r in res if r["role"] == "main"]
    if not main:
        score = 0.0
        checks.append("no main video/audio resource identified")
    elif len(main) > 1:
        score = 0.6
        checks.append(f"{len(main)} resources marked as the main one")
    if data.get("protection"):
        checks.append("DRM/protection markers on this page -- the app won't decrypt or work "
                      "around anything; a protected stream will fail to download")
    conf["media_resources"] = _field(score, checks)
    c = []
    conf["title"] = _field(_presence(page, data.get("title"), c), c)
    if conf["title"]["score"] < 0.5 and data.get("title") is not None:
        data["rejected"]["title"] = data["title"]
        data["title"] = None
    data["confidence"] = conf
    data["overall"] = {"score": conf["media_resources"]["score"],
                       "bucket": conf["media_resources"]["bucket"]}
    data["valid"] = conf["media_resources"]["bucket"] in (HIGH, MEDIUM)
    data["problems"] = list(conf["media_resources"]["checks"])
    return data
