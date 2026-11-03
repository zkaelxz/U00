"""
sources/adapters/mh03.py -- mh03 (mh03.com, zh manhua on an Mccms "zizhi001" template).

Server-rendered, so plain GETs are enough. Selectors checked against the
live site on 2026-11-03:

  series      GET /comic_<slug>.html    h1 title, div.comic-cover-large img,
                                         div.comic-stats (作者：...), div.comic-tags
                                         (连载中/完结), div.comic-description p
  chapters    same page                 div.chapter-item a, oldest first
  pages       GET /chapter_<n>_<m>.html img.comic-image inside div.comic-content;
                                         the real URL is in data-src (src is a
                                         lazy-load placeholder)

The site has no search endpoint a plain GET can reach (its search box is
script-driven), so the adapter doesn't offer search rather than guess one.

Chapter ids are "<comic number>_<chapter number>" because the site's chapter
URLs carry no series slug; both halves come from the page and end up in
request paths, so anything that isn't digits is refused.

The template ships hidden buy-box and vip-box blocks on every chapter page,
paid or not, so their presence says nothing. A chapter whose reader holds no
images is reported as withheld; it is never worked around.
"""

import re
from urllib.parse import quote, urljoin, urlsplit

from ..base import PlainImageDownload, SourceAdapter, layout_changed_class, soup as _soup
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentHidden, ContentType,
                      FailureReason, PageRef, SeriesInfo)
from ..registry import register

BASE_URL = "https://www.mh03.com"
HOST = "www.mh03.com"

_SLUG = re.compile(r"[A-Za-z0-9_-]{1,80}")
_CHAPTER_ID = re.compile(r"\d{1,10}_\d{1,10}")
_CHAPTER_HREF = re.compile(r"/chapter_(\d{1,10}_\d{1,10})\.html")
# Notice rows the site slots into the chapter list; they are not readable chapters.
_NOTICE_TITLE = re.compile(r"^(?:预告|更新提示)\b")

LayoutChanged = layout_changed_class("mh03")


def _safe(pattern, value, what: str) -> str:
    value = str(value or "")
    if not pattern.fullmatch(value):
        raise LayoutChanged(f"a usable {what}")
    return value


def _https(url) -> str:
    url = str(url or "")
    return url if urlsplit(url).scheme == "https" else ""


_JPEG_MAGIC = bytes.fromhex("ffd8ff")
_PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")


def _extension_from_bytes(content: bytes) -> str:
    if content.startswith(_JPEG_MAGIC):
        return ".jpg"
    if content.startswith(_PNG_MAGIC):
        return ".png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return ""


@register
class Mh03Source(PlainImageDownload, SourceAdapter):
    name = "mh03"
    display_name = "mh03"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    url_patterns = [r"mh03\.com/comic_[A-Za-z0-9_-]+\.html"]
    # robots.txt has no Crawl-delay, but the site is ad-funded and Fast stays off
    # until its terms are read.
    host_min_interval = {HOST: 2.5}
    default_headers = {"Referer": BASE_URL + "/"}

    def _get(self, path: str, action: str) -> str:
        return self.client.get(urljoin(BASE_URL, path), action=action, use_cache=False).text

    def _series_html(self, series_id: str) -> str:
        slug = _safe(_SLUG, series_id, "series id")
        return self._get(f"/comic_{quote(slug)}.html", f"Loading series {slug}")

    def get_series(self, series_id: str):
        soup = _soup(self._series_html(series_id))
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True).strip("《》") if h1 is not None else ""
        if not title:
            raise LayoutChanged("this series's title")
        cover = soup.select_one("div.comic-cover-large img")
        desc = soup.select_one("div.comic-description p")
        authors = []
        for stat in soup.select("div.comic-stats .stat-item"):
            text = stat.get_text(strip=True)
            if text.startswith("作者"):
                names = text.split("：", 1)[-1]
                authors = [n.strip() for n in names.split(",") if n.strip()]
        tags = " ".join(t.get_text(strip=True) for t in soup.select("div.comic-tags .tag"))
        status = "completed" if "完结" in tags else "ongoing" if "连载" in tags else "unknown"
        return SeriesInfo(
            self.name, series_id, title, f"{BASE_URL}/comic_{series_id}.html",
            _https(cover.get("src")) if cover is not None else "",
            authors=authors, description=desc.get_text(" ", strip=True) if desc is not None else "",
            status=status, content_type=ContentType.MANHUA.value, language="zh")

    def get_chapters(self, series_id: str):
        soup = _soup(self._series_html(series_id))
        chapters, seen = [], set()
        for a in soup.select("div.chapter-item a"):
            m = _CHAPTER_HREF.fullmatch(str(a.get("href") or ""))
            title = a.get_text(" ", strip=True)
            if not m or m.group(1) in seen or _NOTICE_TITLE.match(title):
                continue
            seen.add(m.group(1))
            chapters.append(ChapterInfo(self.name, series_id, m.group(1), title or m.group(1),
                                        f"{BASE_URL}/chapter_{m.group(1)}.html"))
        if not chapters:
            raise LayoutChanged("any chapters in this series")
        return chapters

    def get_pages(self, chapter):
        cid = _safe(_CHAPTER_ID, chapter.chapter_id, "chapter id")
        soup = _soup(self._get(f"/chapter_{cid}.html", f"Loading chapter {chapter.title}"))
        reader = soup.select_one("div.comic-content")
        if reader is None:
            raise LayoutChanged("the chapter's reader")
        urls = []
        for img in reader.select("img.comic-image"):
            # src is a lazy-load placeholder; data-src is the real file.
            url = _https(img.get("data-src"))
            if url and url not in urls:
                urls.append(url)
        if not urls:
            raise ContentHidden(f"mh03 shows no page images for \"{chapter.title}\"; it may be paid "
                                "or VIP-only. Baihe does not get around that.",
                                FailureReason.PURCHASE_REQUIRED)
        return [PageRef(self.name, chapter.chapter_id, i, u, headers={"Referer": BASE_URL + "/"})
                for i, u in enumerate(urls)]

    def download_page(self, page):
        content, ext = super().download_page(page)
        # The site serves JPEG bytes under .webp names; the importer stores a
        # non-native extension by re-encoding to PNG, so name the file by its bytes.
        return content, _extension_from_bytes(content) or ext

    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        m = re.search(r"/comic_([A-Za-z0-9_-]+)\.html", urlsplit(url).path)
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML: the series page's title, info and chapter links, "
                                 "and the chapter page's img.comic-image tags, whose data-src is "
                                 "the image URL; no JavaScript runs",
            "browser_required": False,
            "images": "manhua.5um.net WebP files, served with a Referer",
        }
        caps.terms = {
            "robots_txt": "www.mh03.com/robots.txt (fetched 2026-11-03): `User-agent: *` with "
                          "`Allow: /` and no Crawl-delay; named crawlers (Amazonbot and others) "
                          "are disallowed. Content signals: `search=yes, ai-train=no, "
                          "use=reference`; the site reserves AI training.",
            "read": "No terms page was read.",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
