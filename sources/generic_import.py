"""
sources/generic_import.py -- one-off "paste a URL" imports for sites with
no registered adapter (Step 23 items 8, 9, 14).

Deliberately not an adapter: no search, no chapter list, no tracking.
It fetches the one URL it's given (through the same paced client and
access ladder as everything else), and either finds the content or says
plainly that it couldn't -- never a crash, never a silent empty import.

Comic pages: every <img> candidate (incl. lazy-load attributes and
srcset) in reading order, then a content-vs-chrome filter before
anything is kept:
  1. too short to be a page (icons, buttons, avatars);
  2. off the dominant shape/width cluster (a banner or logo among pages);
  3. the same image already seen on a *different* chapter of this site
     (logos, nav art, recurring ad creatives);
  4. served from a third-party domain that's neither the page's own nor
     the domain the real pages are coming from (a common ad signal).
None of that is per-site knowledge.

Novel text: trafilatura when installed (a maintained main-content
extractor), else a plain largest-text-block heuristic.
"""

import hashlib
import io
import re
from dataclasses import dataclass, field
from statistics import median
from urllib.parse import urljoin, urlsplit

from . import ladder, store
from .http import SourceClient
from .models import AccessTier, SourceError

GENERIC_SOURCE = "generic"

MIN_PAGE_HEIGHT = 400
MIN_PAGE_WIDTH = 200
MAX_CANDIDATES = 300
MIN_NOVEL_CHARS = 200

_LAZY_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-lazy", "data-url",
               "data-echo", "src")


class NoContentFound(SourceError):
    pass


@dataclass
class ImageCandidate:
    url: str
    order: int
    content: bytes = b""
    ext: str = ""
    width: int = 0
    height: int = 0
    sha256: str = ""
    reject_reason: str = ""


@dataclass
class ComicImportResult:
    page_url: str
    images: list = field(default_factory=list)        # kept ImageCandidates, reading order
    rejected: list = field(default_factory=list)      # ImageCandidates with reject_reason
    ladder: object = None


@dataclass
class NovelImportResult:
    page_url: str
    title: str
    text: str
    method: str                                       # "trafilatura" / "heuristic"
    ladder: object = None


# ---------------------------------------------------------------------------
# Comic: candidate discovery and filtering
# ---------------------------------------------------------------------------

def _best_from_srcset(srcset: str) -> str:
    best, best_w = "", -1.0
    for part in (srcset or "").split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0.0
        if len(bits) > 1:
            m = re.match(r"([\d.]+)[wx]", bits[1])
            w = float(m.group(1)) if m else 0.0
        if w >= best_w:
            best, best_w = bits[0], w
    return best


def image_candidates(html: str, page_url: str) -> list:
    """Every plausible page image URL in document (reading) order."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    seen, out = set(), []
    for tag in soup.find_all(["img", "source"]):
        url = ""
        srcset = tag.get("srcset") or tag.get("data-srcset")
        if srcset:
            url = _best_from_srcset(srcset)
        if not url:
            for attr in _LAZY_ATTRS:
                v = (tag.get(attr) or "").strip()
                if v and not v.startswith("data:"):
                    url = v
                    break
        if not url or url.startswith("data:") or url.lower().endswith(".svg"):
            continue
        absolute = urljoin(page_url, url)
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(ImageCandidate(url=absolute, order=len(out)))
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def _measure(c: ImageCandidate):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(c.content)) as im:
            c.width, c.height = im.size
            c.ext = "." + (im.format or "png").lower().replace("jpeg", "jpg")
    except Exception:
        c.reject_reason = "not a readable image"
    c.sha256 = hashlib.sha256(c.content).hexdigest()


def _domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _cluster(values, tolerance: float):
    """Indices of the largest group of values within ±tolerance of a
    shared centre (centre = each value in turn; small n, so O(n²) is fine)."""
    best = []
    for v in values:
        group = [i for i, x in enumerate(values) if v and abs(x - v) / v <= tolerance]
        if len(group) > len(best):
            best = group
    return set(best)


def filter_page_images(candidates, page_url: str, seen_elsewhere=frozenset()) -> tuple:
    """Splits measured candidates into (kept, rejected). Pure: no network,
    no database -- `seen_elsewhere` is the set of hashes already seen on
    other chapters of this site."""
    kept, rejected = [], []
    within = set()
    for c in candidates:
        if c.reject_reason:
            rejected.append(c)
            continue
        if c.height < MIN_PAGE_HEIGHT or c.width < MIN_PAGE_WIDTH:
            c.reject_reason = f"too small to be a page ({c.width}x{c.height})"
        elif c.sha256 in seen_elsewhere:
            c.reject_reason = "same image appears on other chapters (logo/ad/nav)"
        elif c.sha256 in within:
            c.reject_reason = "duplicate of an earlier image on this page"
        if c.reject_reason:
            rejected.append(c)
        else:
            within.add(c.sha256)
            kept.append(c)

    # Third-party domains: keep the page's own domain and whichever domain
    # most of the surviving page-sized images come from (a site's own CDN).
    if kept:
        page_dom = _domain(page_url)
        counts = {}
        for c in kept:
            counts[_domain(c.url)] = counts.get(_domain(c.url), 0) + 1
        dominant = max(counts, key=counts.get)
        allowed = {page_dom, dominant}
        still = []
        for c in kept:
            if _domain(c.url) not in allowed:
                c.reject_reason = f"served from a third-party domain ({_domain(c.url)})"
                rejected.append(c)
            else:
                still.append(c)
        kept = still

    # Shape: real pages from one chapter share a width or an aspect ratio.
    if len(kept) >= 3:
        ratios = [c.height / c.width for c in kept]
        widths = [float(c.width) for c in kept]
        by_ratio = _cluster(ratios, 0.15)
        by_width = _cluster(widths, 0.10)
        ok = by_ratio | by_width
        if len(ok) >= max(2, len(kept) // 2):
            still = []
            for i, c in enumerate(kept):
                if i in ok:
                    still.append(c)
                else:
                    c.reject_reason = (f"shape doesn't match the other pages "
                                       f"({c.width}x{c.height}, median ratio {median(ratios):.2f})")
                    rejected.append(c)
            kept = still

    kept.sort(key=lambda c: c.order)
    return kept, rejected


def _client(client=None, **kw) -> SourceClient:
    return client or SourceClient(GENERIC_SOURCE, **kw)


def fetch_page(url: str, client=None, rendered_fetch=None, user_html: str = None):
    """Runs the ladder for one URL: static HTTP, then a real browser if
    that failed (unless a challenge stopped everything). With `user_html`
    -- the page source the person saved after completing a verification
    themselves -- the USER_ASSISTED tier is used instead of any request."""
    client = _client(client)
    if user_html is not None:
        tiers = {AccessTier.USER_ASSISTED_BROWSER: ladder.user_assisted_tier(user_html)}
    else:
        tiers = {AccessTier.STATIC_HTTP: ladder.static_tier(client),
                 AccessTier.RENDERED_BROWSER: ladder.rendered_tier(client, rendered_fetch),
                 AccessTier.AUTHENTICATED_BROWSER: ladder.not_built_tier("23k")}
    result = ladder.run_ladder(url, tiers, source=GENERIC_SOURCE)
    return result


def import_comic_page(url: str, client=None, rendered_fetch=None, user_html: str = None,
                      remember: bool = True) -> ComicImportResult:
    """Fetches a chapter URL and returns its page images, filtered. Raises
    NoContentFound (with the ladder's per-tier lines in the message) when
    nothing usable is there; ChallengeDetected-shaped hand-offs come back
    via `result.ladder.handoff` with no images."""
    client = _client(client)
    lr = fetch_page(url, client, rendered_fetch, user_html)
    out = ComicImportResult(page_url=url, ladder=lr)
    if lr.handoff:
        return out
    if not lr.ok:
        raise NoContentFound("Couldn't load this page:\n" + "\n".join(lr.summary_lines()))

    candidates = image_candidates(lr.html, url)
    if not candidates:
        raise NoContentFound("Couldn't find page images here -- the page has no image tags "
                             "this importer recognizes. Upload the pages manually instead.")
    for c in candidates:
        try:
            resp = client.get(c.url, classify_body=False, headers={"Referer": url},
                              action=f"Checking image {c.order + 1}/{len(candidates)}")
            c.content = resp.content
            _measure(c)
        except SourceError as e:
            c.reject_reason = f"couldn't download ({e.reason.value})"
    domain = _domain(url)
    hashes = [c.sha256 for c in candidates if c.sha256]
    seen = store.hashes_seen_elsewhere(domain, url, hashes) if remember else set()
    kept, rejected = filter_page_images(candidates, url, seen)
    if remember:
        store.remember_images(domain, url, hashes)
    out.images, out.rejected = kept, rejected
    if not kept:
        raise NoContentFound("Couldn't find page images here -- every image on the page was "
                             "filtered out as site furniture (icons, banners, logos). "
                             f"{len(rejected)} image(s) checked.")
    return out


# ---------------------------------------------------------------------------
# Novel text
# ---------------------------------------------------------------------------

_DROP_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form",
              "iframe", "button", "select"]
_DROP_HINT = re.compile(r"comment|sidebar|footer|header|nav|menu|breadcrumb|share|related|"
                        r"advert|\bads?\b|recommend|login|copyright", re.I)


def extract_main_text_heuristic(html: str) -> str:
    """Largest contiguous text block: strip page chrome, then score each
    container by the text it holds directly in paragraphs/line breaks."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup(_DROP_TAGS):
        t.decompose()
    doomed = []
    for t in soup.find_all(True):
        ident = " ".join([t.get("id") or ""] + list(t.get("class") or []))
        if ident.strip() and _DROP_HINT.search(ident) and t.name not in ("body", "html"):
            doomed.append(t)
    for t in doomed:
        if not t.decomposed:
            t.decompose()
    best, best_len = None, 0
    for el in soup.find_all(["article", "div", "section", "main", "td"]):
        paras = [p.get_text(" ", strip=True) for p in el.find_all("p", recursive=False)]
        direct = [s.strip() for s in el.find_all(string=True, recursive=False) if s.strip()]
        size = sum(len(p) for p in paras) + sum(len(s) for s in direct)
        if size > best_len:
            best, best_len = el, size
    if best is None:
        return ""
    lines = []
    for chunk in best.get_text("\n").splitlines():
        chunk = chunk.strip()
        if chunk:
            lines.append(chunk)
    return "\n".join(lines)


def extract_main_text(html: str, url: str = "") -> tuple:
    """(text, method). Uses trafilatura when it's installed."""
    try:
        import trafilatura
    except ImportError:
        trafilatura = None
    if trafilatura is not None:
        try:
            text = trafilatura.extract(html, url=url or None, include_comments=False,
                                       include_tables=False, favor_recall=True) or ""
        except Exception:
            text = ""
        if len(text.strip()) >= MIN_NOVEL_CHARS:
            return text.strip(), "trafilatura"
    return extract_main_text_heuristic(html).strip(), "heuristic"


def import_novel_page(url: str, client=None, rendered_fetch=None,
                      user_html: str = None) -> NovelImportResult:
    from .detect import page_title
    client = _client(client)
    lr = fetch_page(url, client, rendered_fetch, user_html)
    if lr.handoff:
        return NovelImportResult(url, "", "", "", ladder=lr)
    if not lr.ok:
        raise NoContentFound("Couldn't load this page:\n" + "\n".join(lr.summary_lines()))
    text, method = extract_main_text(lr.html, url)
    if len(text) < MIN_NOVEL_CHARS:
        raise NoContentFound("Couldn't find the main text on this page -- no block of prose "
                             "long enough to be a chapter. Paste or upload the text instead.")
    return NovelImportResult(url, page_title(lr.html), text, method, ladder=lr)
