"""
sources/adapters/zerosumonline.py -- Zero-Sum Online (zerosumonline.com,
Ichijinsha, ja manga).

**A genuine protocol-level obstacle, not another HTML site.** Confirmed
against the real, actively-maintained Keiyoushi extension
(keiyoushi/extensions-source, src/ja/zerosumonline, class `ZerosumOnline`,
Apache-2.0): this site's content API returns **Protocol Buffers, not
JSON** (`parseAsProto<TitleListView>()` / `parseAsProto<ViewerView>()`).
No generic HTML scraper or LLM-on-rendered-DOM step (the adaptive
pipeline) can read this -- something has to own the wire-format decode,
which is exactly the "genuine protocol-level obstacle" the roadmap
carved this step out for.

**The decoder below is a small, from-scratch, generic protobuf
wire-format reader** (`_read_varint`/`_parse_protobuf`) -- tag/varint/
length-delimited parsing only, not a protoc-generated or schema-compiled
decoder, and not a general-purpose protobuf library. It's exactly general
enough for this site's own fixed, small schema (read directly from the
extension's own `Dto.kt`, field numbers below), the same "port the real
shape exactly, don't approximate" discipline already applied to this
project's other custom-format adapters (baozimh's obfuscation, Kuaikan's
Nuxt state, miaoqumh's XOR).

**Verified against real, live captured data while building this
adapter** -- not assumed from reading the Kotlin source alone:
  * `GET https://api.<domain>/api/v1/list?category=series&sort=date` --
    real protobuf bytes (mislabeled `content-type: application/json` by
    the server -- confirmed genuine protobuf wire format by decoding it
    byte-for-byte against the schema below, not trusting the header).
    `TitleListView` field 3 = repeated `ApiTitle`.
  * `GET .../api/v1/title?tag=<slug>` -- `TitleDetailView`: field 2 =
    `ApiTitle`, field 3 = repeated `ApiChapter`. **Chapters come back
    newest-first** (confirmed against a real two-chapter series: field-1
    id 3491 "二話" before id 3492 "一話") -- `get_chapters()` reverses
    this to ascending order, this project's usual convention.
  * `POST .../api/v1/viewer?chapter_id=<id>` (empty body) -- `ViewerView`
    field 5 = repeated `ViewerImage`, field 1 = the page's real image URL.
  * `ApiTitle` fields used: 2 slug, 3 name, 4 altTitle, 5 authors,
    7 description, 8 thumbnail (fields 1/6 exist on the wire but aren't
    part of the extension's own modeled schema -- skipped, not guessed at).
  * `ApiChapter` fields used: 1 id, 2 name, 4 publishedAt (unix seconds;
    the extension multiplies by 1000 for a Java epoch-millis field this
    app has no equivalent use for, so it's read but not stored).

`robots.txt`: a real HTTP 404 (this is a Next.js app; the 404 page is the
app's own catch-all, not a proxy artifact -- confirmed by content) on
direct fetch while building this adapter -- no crawl guidance exists to
follow or violate. ToS still genuinely unlocatable after real effort
(site + Ichijinsha's corporate umbrella), matching the roadmap's own
finding -- stays a candidate on that basis, not a clearance.

**Not found and not guessed at**: no separate public per-chapter reader
URL. The reference extension's own `getChapterUrl()` returns the *series*
page (stripping the chapter id back off), because the real site's chapter
reader is client-side-only inside the series page -- `parse_url()` below
only recognizes the series URL for the same reason, and `ChapterInfo.url`
points at the series page too, matching the extension's own behavior
rather than inventing a URL pattern that doesn't exist.
"""

import re
from urllib.parse import quote, urlsplit

from ..base import SourceAdapter
from ..models import (AccessTier, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      SearchResult, SeriesInfo, SourceError)
from ..registry import register

BASE_URL = "https://zerosumonline.com"


class LayoutChanged(SourceError):
    """The API response didn't have what this adapter expects -- most
    likely the site changed its schema. Reported plainly, never guessed
    around."""

    def __init__(self, what: str):
        super().__init__(f"zerosumonline's API response has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _read_varint(data: bytes, pos: int):
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _parse_protobuf(data: bytes) -> dict:
    """A minimal, from-scratch protobuf wire-format reader (module
    docstring) -- {field_number: [value, ...]}, each value an int (varint
    fields) or raw bytes (length-delimited fields; the caller decodes
    them as a string or a nested message per the known schema). Fixed32/
    64 fields are skipped by their fixed length since this site's real
    schema never uses them; an unrecognized wire type means the schema
    has genuinely changed, so it's reported, never silently dropped."""
    fields: dict = {}
    pos, end = 0, len(data)
    while pos < end:
        tag, pos = _read_varint(data, pos)
        field_no, wire_type = tag >> 3, tag & 7
        if wire_type == 0:
            val, pos = _read_varint(data, pos)
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            val, pos = data[pos:pos + length], pos + length
        elif wire_type == 1:
            val, pos = data[pos:pos + 8], pos + 8
        elif wire_type == 5:
            val, pos = data[pos:pos + 4], pos + 4
        else:
            raise LayoutChanged(f"a recognized protobuf wire type ({wire_type})")
        fields.setdefault(field_no, []).append(val)
    return fields


def _field(fields: dict, num: int, default=None):
    vals = fields.get(num)
    return vals[0] if vals else default


def _text(fields: dict, num: int) -> str:
    val = _field(fields, num, b"")
    return val.decode("utf-8", errors="replace") if isinstance(val, bytes) else ""


@register
class ZerosumOnlineSource(SourceAdapter):
    name = "zerosumonline"
    display_name = "ゼロサムオンライン Zero-Sum Online"
    content_types = [ContentType.MANGA.value]
    languages = ["ja"]
    url_patterns = [r"zerosumonline\.com/detail/[^/?#]+"]

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self._details = {}

    @property
    def _api_base(self) -> str:
        host = urlsplit(self.base_url).netloc
        return f"https://api.{host}/api/v1"

    def _title_result(self, tf: dict) -> SearchResult:
        slug = _text(tf, 2)
        return SearchResult(self.name, slug, _text(tf, 3) or slug,
                            f"{self.base_url}/detail/{slug}", _text(tf, 8))

    def search(self, query: str, page: int = 1):
        query = (query or "").strip()
        if query:
            url = f"{self._api_base}/search?keyword={quote(query)}"
            action = f"Searching zerosumonline for {query!r}"
        else:
            url = f"{self._api_base}/list?category=series&sort=date"
            action = "Loading the zerosumonline series list"
        resp = self.client.get(url, classify_body=False, action=action)
        fields = _parse_protobuf(resp.content)
        results = []
        for tb in fields.get(3, []):
            tf = _parse_protobuf(tb)
            if _text(tf, 2):
                results.append(self._title_result(tf))
        return results

    def _detail(self, series_id: str) -> dict:
        if series_id not in self._details:
            resp = self.client.get(f"{self._api_base}/title?tag={quote(series_id)}",
                                   classify_body=False, action=f"Loading series {series_id}")
            self._details[series_id] = _parse_protobuf(resp.content)
        return self._details[series_id]

    def get_series(self, series_id: str):
        fields = self._detail(series_id)
        title_bytes = _field(fields, 2)
        if title_bytes is None:
            raise LayoutChanged("this series's title record")
        tf = _parse_protobuf(title_bytes)
        name = _text(tf, 3)
        if not name:
            raise LayoutChanged("the series title")
        alt = _text(tf, 4)
        description = _text(tf, 7)
        if alt:
            description = f"{description}\n\nAlternative Title: {alt}" if description \
                else f"Alternative Title: {alt}"
        authors = [a for a in [_text(tf, 5)] if a]
        return SeriesInfo(
            self.name, series_id, name, f"{self.base_url}/detail/{series_id}",
            _text(tf, 8), authors=authors, description=description,
            content_type=ContentType.MANGA.value, language="ja")

    def get_chapters(self, series_id: str):
        fields = self._detail(series_id)
        raw_chapters = fields.get(3, [])
        if not raw_chapters:
            raise LayoutChanged("any chapters for this series")
        chapters = []
        for cb in reversed(raw_chapters):   # API returns newest-first (module docstring)
            cf = _parse_protobuf(cb)
            cid = _field(cf, 1)
            if cid is None:
                continue
            name = _text(cf, 2) or str(cid)
            chapters.append(ChapterInfo(self.name, series_id, str(cid), name,
                                        f"{self.base_url}/detail/{series_id}"))
        return chapters

    def get_pages(self, chapter):
        from ..models import PageRef
        url = f"{self._api_base}/viewer?chapter_id={quote(chapter.chapter_id)}"
        resp = self.client.post(url, data=b"", classify_body=False,
                                action=f"Loading chapter {chapter.title}")
        fields = _parse_protobuf(resp.content)
        urls = []
        for pb in fields.get(5, []):
            pf = _parse_protobuf(pb)
            u = _text(pf, 1)
            if u:
                urls.append(u)
        if not urls:
            raise LayoutChanged("any page images in the viewer response")
        return [PageRef(self.name, chapter.chapter_id, i, u) for i, u in enumerate(urls)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        name = page.url.split("?", 1)[0]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        m = re.search(r"zerosumonline\.com/detail/([^/?#]+)/?$", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.access_method = AccessTier.STATIC_HTTP.value
        caps.technical = {
            "extraction_method": "a real, non-GigaViewer JSON-looking-but-actually-Protocol-"
                                 "Buffers API at a runtime-derived api.<domain> subdomain -- "
                                 "decoded with a small, generic, from-scratch protobuf "
                                 "wire-format reader (module docstring), not a JSON parser and "
                                 "not a full protobuf library dependency.",
            "browser_required": False,
            "protocol_note": "the API's HTTP responses are labeled content-type: "
                             "application/json but the body is genuine protobuf wire format -- "
                             "confirmed by decoding real captured responses byte-for-byte "
                             "against the schema while building this adapter, not trusted from "
                             "the header.",
            "reference": "keiyoushi/extensions-source src/ja/zerosumonline (Apache-2.0)",
        }
        caps.terms = {
            "robots_txt": "A real HTTP 404 (this is a Next.js app; confirmed by content that "
                          "it's the app's own catch-all page, not a proxy artifact) -- no crawl "
                          "guidance exists to follow or violate.",
            "tos": "Genuinely unlocatable after real effort (site + Ichijinsha's corporate "
                  "umbrella) -- stays a candidate on that basis, not a clearance.",
            "tos_prohibited": False,
        }
        return caps
