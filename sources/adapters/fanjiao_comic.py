"""
sources/adapters/fanjiao_comic.py -- 饭角 Fanjiao comics (www.fanjiao.co, zh manhua).

Rendered-browser tier, like the audio adapter in fanjiao.py: the site's API
is signed and its page images arrive encrypted, and both are done by the
page's own script. This adapter opens the page in page_fetch's guarded
browser and reads only what that script already produced; it never builds a
signed request or decrypts an image address.

  series      /cartoonDetail?data=<base64 of url-encoded {"comic_id": N}>
              the page's own `comic/home_page` response: `comic_info` (name,
              description, author_name, cover, is_over) and `chapter_list`
              (chapter_id, name, is_buy), oldest first
  pages       /cartoonRead?data=<base64 of url-encoded
              {"chapter_id": N, "comic_name": "..."}>
              `div.read-content img` once the page's script has filled in `src`;
              `chapter/details` is read only for is_buy and the image count

A chapter with is_buy 0 is listed under the paid group and never opened. The
comic-level `is_payment` is not trusted: it was 0 for a comic priced at 126.

No browser for a request not from this PC (docs/remote-access-decision.md).
"""

import base64
import binascii
import json
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..base import PlainImageDownload, SourceAdapter, layout_changed_class, soup as _soup
from ..models import (AccessTier, AutomationPermission, CapabilityStatus, ChapterInfo,
                      ContentAccess, ContentHidden, ContentType, FailureReason, PageRef,
                      Requirement, SeriesInfo, SourceError, TechnicalProtection,
                      TechnicalStatus, TierResult)
from ..registry import register

HOST = "www.fanjiao.co"
BASE_URL = "https://" + HOST
REFERER = BASE_URL + "/"
# Only the page's own responses for these two endpoints are kept.
API_PATTERN = re.compile(r"^https://api\.fanjiao\.co/walkman/api/+(?:comic/home_page|chapter/details)\b")
PAID_GROUP = "付费 / paid (not bought)"
_ID = re.compile(r"[0-9]{1,12}")
# A teaser row the site slots in ahead of chapter one; it holds no story pages.
_NOTICE_TITLE = re.compile(r"^预告")
_JPEG_MAGIC = bytes.fromhex("ffd8ff")
_PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")

LayoutChanged = layout_changed_class("Fanjiao", "comic page layout")


def _page_url(route: str, payload: dict) -> str:
    """The address the site's own front end builds: base64 of url-encoded JSON."""
    raw = quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), safe="")
    return f"{BASE_URL}/{route}?data=" + quote(base64.b64encode(raw.encode()).decode(), safe="")


def _link_payload(url: str, route: str) -> dict | None:
    """The JSON a Fanjiao page link carries in `data`, or None when it isn't one."""
    parts = urlsplit(url or "")
    if (parts.hostname or "").lower() != HOST or parts.path != "/" + route:
        return None
    data = (parse_qs(parts.query).get("data") or [""])[0]
    try:
        payload = json.loads(unquote(base64.b64decode(data + "=" * (-len(data) % 4), validate=True).decode()))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _digits(value) -> str:
    text = str(value) if isinstance(value, (int, str)) and not isinstance(value, bool) else ""
    return text if _ID.fullmatch(text) else ""


def _data(entry: dict | None) -> dict:
    body = (entry or {}).get("body")
    try:
        parsed = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body or "")
    except (ValueError, UnicodeDecodeError):
        return {}
    data = parsed.get("data") if isinstance(parsed, dict) else None
    return data if isinstance(data, dict) else {}


def _extension_from_bytes(content: bytes) -> str:
    if content.startswith(_JPEG_MAGIC):
        return ".jpg"
    if content.startswith(_PNG_MAGIC):
        return ".png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return ""


def _default_capture(url: str):
    """(rendered html, the page's own matching responses) from page_fetch's guarded browser."""
    from page_fetch import api_capture_session
    try:
        with api_capture_session(url, API_PATTERN, timeout=40, wait_ms=8000) as (page, captured):
            return page.content(), list(captured)
    except ImportError as e:
        raise SourceError(str(e), FailureReason.NOT_INSTALLED) from None
    except SourceError:
        raise
    except Exception as e:
        raise SourceError(f"Fanjiao's page couldn't be opened in the browser ({type(e).__name__}).",
                          FailureReason.UNKNOWN) from None


def _withheld(what: str) -> ContentHidden:
    return ContentHidden(f"Fanjiao doesn't show {what} without buying it. Baihe only reads "
                         "what the site shows for free or what you already bought; open it in "
                         "the Fanjiao app.", FailureReason.PURCHASE_REQUIRED)


@register
class FanjiaoComicSource(PlainImageDownload, SourceAdapter):
    name = "fanjiao_comic"
    display_name = "饭角漫画 Fanjiao Comics"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    needs_browser = True
    url_patterns = [r"fanjiao\.co/cartoon(?:Detail|Read)\?(?:[^#]*&)?data="]
    # One render makes several API calls on the site's side, so renders are spaced well apart.
    host_min_interval = {HOST: 10.0}
    default_headers = {"Accept-Language": "zh-CN,zh;q=0.9", "Referer": REFERER}

    def __init__(self, client=None, capture=None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        # Injectable for tests: capture(url) -> (html, [captured entries]).
        self._capture = capture or _default_capture
        self._renders = {}

    def _render(self, url: str, action: str):
        if not self.allow_browser:
            # docs/remote-access-decision.md: no browser for a request not from this PC.
            raise SourceError("Fanjiao comics need the browser, which only runs for requests "
                              "from this PC. Open it there.", FailureReason.JAVASCRIPT_REQUIRED)
        if url not in self._renders:
            html, captured = self.client.paced(self._capture, url, "Rendered browser", action=action)
            ok = {}
            for entry in captured or []:
                if entry.get("status") == 200:
                    ok.setdefault(urlsplit(entry.get("url", "")).path.rstrip("/").rsplit("/", 1)[-1], entry)
            self._renders[url] = (html or "", ok)
        return self._renders[url]

    def _home(self, series_id: str) -> dict:
        comic_id = _digits(series_id)
        if not comic_id:
            raise SourceError(f"\"{series_id}\" isn't a Fanjiao comic id (digits only).",
                              FailureReason.UNKNOWN)
        _, ok = self._render(_page_url("cartoonDetail", {"comic_id": int(comic_id)}),
                             f"Loading Fanjiao comic {comic_id}")
        data = _data(ok.get("home_page"))
        info = data.get("comic_info")
        if not isinstance(info, dict) or _digits(info.get("comic_id")) != comic_id:
            raise LayoutChanged("the comic's own details")
        return data

    def get_series(self, series_id: str):
        info = self._home(series_id)["comic_info"]
        title = str(info.get("name") or "").strip()
        if not title:
            raise LayoutChanged("the comic's title")
        authors = [a for a in (str(info.get("author_name") or "").strip(),) if a]
        cover = str(info.get("cover") or "")
        return SeriesInfo(
            self.name, str(series_id), title, _page_url("cartoonDetail", {"comic_id": int(series_id)}),
            cover if cover.startswith("https://") else "", authors=authors,
            description=str(info.get("description") or "").strip(),
            status="completed" if info.get("is_over") == 1 else "ongoing",
            content_type=ContentType.MANHUA.value, language="zh")

    def get_chapters(self, series_id: str):
        data = self._home(series_id)
        comic_name = str(data["comic_info"].get("name") or "")
        chapters, seen = [], set()
        for row in data.get("chapter_list") or []:
            cid = _digits(row.get("chapter_id")) if isinstance(row, dict) else ""
            title = str(row.get("name") or "").strip() if cid else ""
            if not cid or cid in seen or _NOTICE_TITLE.match(title):
                continue
            seen.add(cid)
            bought = row.get("is_buy") in (1, True, "1")
            chapters.append(ChapterInfo(
                self.name, str(series_id), cid, title or cid,
                _page_url("cartoonRead", {"chapter_id": int(cid), "comic_name": comic_name}),
                group="" if bought else PAID_GROUP))
        if not chapters:
            raise LayoutChanged("any chapters in this comic")
        return chapters

    def get_pages(self, chapter):
        if chapter.group == PAID_GROUP:
            raise _withheld(f"\"{chapter.title}\"")
        payload = _link_payload(chapter.url, "cartoonRead")
        if payload is None or not _digits(payload.get("chapter_id")):
            raise LayoutChanged("a usable chapter address")
        html, ok = self._render(chapter.url, f"Loading chapter {chapter.title}")
        details = _data(ok.get("details"))
        if details and details.get("is_buy") not in (1, True, "1"):
            raise _withheld(f"\"{chapter.title}\"")
        reader = _soup(html).select_one("div.read-content")
        if reader is None:
            raise LayoutChanged("the chapter's reader")
        urls = []
        for img in reader.select("img[src]"):
            src = str(img.get("src") or "")
            if src.startswith("https://") and src not in urls:
                urls.append(src)
        if not urls:
            raise _withheld(f"\"{chapter.title}\"")
        expected = details.get("details")
        if isinstance(expected, list) and len(urls) < len(expected):
            raise LayoutChanged("all of the chapter's images (the page had not finished loading them)")
        return [PageRef(self.name, chapter.chapter_id, i, u, headers={"Referer": REFERER})
                for i, u in enumerate(urls)]

    def download_page(self, page):
        content, ext = super().download_page(page)
        # Name the file by its bytes: the importer re-encodes an extension it doesn't keep as is.
        return content, _extension_from_bytes(content) or ext

    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        series = _link_payload(url, "cartoonDetail")
        if series is not None and _digits(series.get("comic_id")):
            return ("series", _digits(series["comic_id"]))
        chapter = _link_payload(url, "cartoonRead")
        if chapter is None:
            return None
        cid = _digits(chapter.get("chapter_id"))
        if not cid:
            return None
        # A chapter link carries no comic id, so the series stays empty; the link itself is the page to read.
        return ("chapter", ChapterInfo(self.name, "", cid, str(chapter.get("comic_name") or cid), url))

    def capabilities(self):
        caps = super().capabilities()
        caps.status = CapabilityStatus.PARTIALLY_SUPPORTED.value
        caps.technical_status = TechnicalStatus.PARTIALLY_SUPPORTED.value
        caps.access_method = AccessTier.RENDERED_BROWSER.value
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.authentication_required = Requirement.UNKNOWN.value
        caps.purchase_required = Requirement.UNKNOWN.value
        caps.tiers[AccessTier.RENDERED_BROWSER.value] = TierResult(
            tested=True, ok=True,
            detail="A free chapter's 39 images and a comic's chapter list were read through the "
                   "guarded browser on 2026-11-03; no purchase or sign-in was involved.")
        caps.technical = {
            "extraction_method": "Renders the comic and reader pages and reads the responses and "
                                 "the DOM the page's own script produced (home_page, chapter/details, "
                                 "div.read-content img).",
            "browser_required": True,
            "technical_protection": "DETECTED -- the API is signed and the page's image addresses "
                                    "arrive encrypted. Recorded, never worked around: nothing is "
                                    "signed or decrypted here.",
            "images": "static.rela.me files, plain src once the page's script has run; named by "
                      "their bytes (the site serves JPEG data under .webp names).",
            "paid": "A chapter with is_buy 0 is listed under the paid group and never opened.",
        }
        caps.terms = {
            "robots_txt": "None: every unknown path, /robots.txt included, returns the homepage "
                          "(2026-09-30, see the Fanjiao audio adapter).",
            "read": "The user agreement is only viewable inside the app; the public privacy page "
                    "has no automation clause.",
            "tos_prohibited": False,
            "unverified": "Not cleared: UNKNOWN is not PERMITTED.",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        caps.technical_protection = TechnicalProtection.DETECTED.value
        return caps
