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

Step 23g (sources/adaptive.py, sources/ai_extract.py) runs these same
deterministic passes first and only asks an LLM when they come back
empty or ambiguous; this module stays the deterministic tier and the
resource downloader.
"""

import hashlib
import io
import re
from dataclasses import dataclass, field
from statistics import median
from urllib.parse import urljoin, urlsplit

from . import auth_browser, ladder, registry, store
from .http import SourceClient
from .models import AccessTier, ContentAccess, SourceError

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
    # Step 23g: where the URL came from and what surrounds it -- read by
    # the AI-assisted classifier (sources/ai_extract.py), never used to
    # download anything. `attr` is "srcset"/"data-src"/"src"/.../"manifest".
    attr: str = ""
    hint: str = ""
    width_attr: int = 0
    height_attr: int = 0
    tag: object = field(default=None, repr=False, compare=False)


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
        url, used = "", ""
        srcset = tag.get("srcset") or tag.get("data-srcset")
        if srcset:
            url, used = _best_from_srcset(srcset), "srcset"
        if not url:
            for attr in _LAZY_ATTRS:
                v = (tag.get(attr) or "").strip()
                if v and not v.startswith("data:"):
                    url, used = v, attr
                    break
        if not url or url.startswith("data:") or url.lower().endswith(".svg"):
            continue
        absolute = urljoin(page_url, url)
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(ImageCandidate(url=absolute, order=len(out), attr=used, hint=_tag_hint(tag),
                                  width_attr=_int_attr(tag, "width"),
                                  height_attr=_int_attr(tag, "height"), tag=tag))
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def _int_attr(tag, name: str) -> int:
    m = re.match(r"\s*(\d+)", str(tag.get(name) or ""))
    return int(m.group(1)) if m else 0


def _tag_hint(tag) -> str:
    """alt text plus the id/class chain of the tag and its nearest
    ancestors, and where it links -- the context a person would use to
    tell a page from a cover, an ad or a thumbnail."""
    bits = []
    if tag.get("alt"):
        bits.append(f"alt={tag.get('alt')!r}")
    node, chain = tag, []
    for _ in range(4):
        if node is None or node.name in (None, "[document]", "html", "body"):
            break
        ident = node.name + (f"#{node.get('id')}" if node.get("id") else "") + \
            "".join(f".{c}" for c in (node.get("class") or []))
        chain.append(ident)
        node = node.parent
    bits.append(" < ".join(chain))
    link = tag.find_parent("a")
    if link is not None and link.get("href"):
        bits.append(f"links to {link.get('href')}")
    return " | ".join(bits)[:240]


# Image URLs sitting as text inside inline scripts (a reader's page
# manifest, a JSON-LD block, a framework data blob) -- common on lazy
# readers whose <img> tags are only filled in by script.
_MANIFEST_URL = re.compile(
    r"""(?:https?:)?(?:\\?/){2}[^\s"'<>()]+?\.(?:jpe?g|png|webp|gif|avif)(?:\?[^\s"'<>()]*)?""",
    re.I)


def manifest_candidates(html: str, page_url: str, skip=()) -> list:
    """Image URLs found in <script> bodies, in document order, as
    candidates with attr="manifest" (order numbers left for the caller to
    assign). Nothing is decoded or executed -- only URLs already in the
    page as plain text."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    seen, out = set(skip), []
    for script in soup.find_all("script"):
        body = script.string or script.get_text() or ""
        for m in _MANIFEST_URL.finditer(body):
            raw = m.group(0).replace("\\/", "/")
            if raw.startswith("//"):
                raw = (urlsplit(page_url).scheme or "https") + ":" + raw
            absolute = urljoin(page_url, raw)
            if absolute in seen:
                continue
            seen.add(absolute)
            kind = f" type={script.get('type')}" if script.get("type") else ""
            out.append(ImageCandidate(url=absolute, order=len(out), attr="manifest",
                                      hint=f"listed inside <script{kind}>"))
            if len(out) >= MAX_CANDIDATES:
                return out
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


def _client(client=None, url: str = "") -> SourceClient:
    """A URL a registered adapter recognizes is fetched (and recorded)
    under that adapter's source name, so its capability record -- and
    its terms -- apply here too; anything else is GENERIC_SOURCE."""
    if client is not None:
        return client
    cls = registry.adapter_class_for_url(url)
    return SourceClient(cls.name if cls else GENERIC_SOURCE)


def _default_capabilities(client: SourceClient):
    cls = registry.adapter_classes().get(client.source)
    return cls(client=client).capabilities() if cls else None


def fetch_page(url: str, client=None, rendered_fetch=None, user_html: str = None,
               authenticated_fetch=None):
    """Runs the ladder for one URL: static HTTP, then a real browser if
    that failed (unless a challenge stopped everything). With `user_html`
    -- the page source the person saved after completing a verification
    themselves -- the USER_ASSISTED tier is used instead of any request.
    Once the person has signed in to this source through its browser
    window (Step 23k), the page is read through that saved, signed-in
    profile instead, so they see what their account sees. Raises
    TermsProhibited, before anything is sent, when the source's record (or
    the site's own terms entry) restricts automated access -- signed in or
    not; otherwise the run is folded into that record."""
    client = _client(client, url)
    default = _default_capabilities(client)
    ladder.check_terms(client.source, default, url=url)
    if user_html is not None:
        tiers = {AccessTier.USER_ASSISTED_BROWSER: ladder.user_assisted_tier(user_html)}
    elif auth_browser.has_profile(url, client.source):
        tiers = {AccessTier.AUTHENTICATED_BROWSER: ladder.authenticated_tier(
            auth_browser.profile_dir(url, client.source), client, authenticated_fetch)}
    else:
        tiers = {AccessTier.STATIC_HTTP: ladder.static_tier(client),
                 AccessTier.RENDERED_BROWSER: ladder.rendered_tier(client, rendered_fetch)}
    result = ladder.run_ladder(url, tiers, source=client.source)
    if result.ok:
        from .ai_extract import content_access_for, resource_types
        result.resource_types = resource_types(result.html, url)
        if result.content_access == ContentAccess.UNKNOWN.value:
            result.content_access = content_access_for(result.resource_types)
    ladder.record_ladder_result(client.source, result, default)
    return result


def download_candidates(candidates, page_url: str, client) -> None:
    """The resource downloader: fetches and measures each candidate not
    fetched yet, through the paced client."""
    for c in candidates:
        if c.content or c.reject_reason:
            continue
        try:
            resp = client.get(c.url, classify_body=False, headers={"Referer": page_url},
                              action=f"Checking image {c.order + 1}/{len(candidates)}")
            c.content = resp.content
            _measure(c)
        except SourceError as e:
            c.reject_reason = f"couldn't download ({e.reason.value})"


def filter_candidates(candidates, page_url: str, remember: bool = True) -> tuple:
    """filter_page_images plus this site's cross-chapter image memory."""
    domain = _domain(page_url)
    hashes = [c.sha256 for c in candidates if c.sha256]
    seen = store.hashes_seen_elsewhere(domain, page_url, hashes) if remember else set()
    kept, rejected = filter_page_images(candidates, page_url, seen)
    if remember:
        store.remember_images(domain, page_url, hashes)
    return kept, rejected


def import_comic_page(url: str, client=None, rendered_fetch=None, user_html: str = None,
                      remember: bool = True) -> ComicImportResult:
    """Fetches a chapter URL and returns its page images, filtered. Raises
    NoContentFound (with the ladder's per-tier lines in the message) when
    nothing usable is there; ChallengeDetected-shaped hand-offs come back
    via `result.ladder.handoff` with no images."""
    client = _client(client, url)
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
    download_candidates(candidates, url, client)
    kept, rejected = filter_candidates(candidates, url, remember)
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


def _link_text_len(el) -> int:
    return sum(len(a.get_text(" ", strip=True)) for a in el.find_all("a"))


def _best_by_descendant_text(containers):
    """The container holding the most prose *anywhere* beneath it, not
    just in its direct children.

    Link text is discounted, so a dense block of chapter links can't
    out-score the chapter itself, and ties go to the shallower element so
    a whole chapter beats one of its own paragraphs. Returns
    `(element, plain_text_size)` -- the size is undiscounted, so the
    caller compares like with like against the direct-child pass.
    """
    best, best_score, best_size, best_depth = None, 0, 0, None
    for el in containers:
        paras = [p.get_text(" ", strip=True) for p in el.find_all("p")]
        direct = [s.strip() for s in el.find_all(string=True, recursive=False) if s.strip()]
        size = sum(len(p) for p in paras) + sum(len(s) for s in direct)
        score = size - _link_text_len(el)
        if score <= 0:
            continue
        depth = len(list(el.parents))
        shallower_tie = score == best_score and best_depth is not None and depth < best_depth
        if score > best_score or shallower_tie:
            best, best_score, best_size, best_depth = el, score, size, depth
    return best, best_size


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
    containers = soup.find_all(["article", "div", "section", "main", "td"])
    best, best_len = None, 0
    for el in containers:
        paras = [p.get_text(" ", strip=True) for p in el.find_all("p", recursive=False)]
        direct = [s.strip() for s in el.find_all(string=True, recursive=False) if s.strip()]
        size = sum(len(p) for p in paras) + sum(len(s) for s in direct)
        if size > best_len:
            best, best_len = el, size
    # The scan above counts only a container's *direct* children, which
    # misses a very ordinary shape: one wrapper element per paragraph
    # (`<div class=content><div><p>..</p></div><div><p>..</p></div>`).
    # There the outer container scores zero and each inner one scores a
    # single paragraph, so the winner is one paragraph and the rest of the
    # chapter is dropped -- silently, since what comes back still looks
    # like text.
    #
    # Note this can't be gated on "the direct pass came up short": one
    # long paragraph already clears MIN_NOVEL_CHARS, so such a gate never
    # fires on exactly the pages that need it. Instead the deeper scan
    # always runs and only wins when it finds *substantially* more prose,
    # so a marginal difference can't flip a page that already extracted
    # correctly.
    alt, alt_len = _best_by_descendant_text(containers)
    if alt is not None and alt_len > best_len * 1.5:
        best = alt
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

# The novel import itself (fetch + this extractor + an AI fallback when
# the result is ambiguous) is sources/adaptive.import_novel (Step 23g).
