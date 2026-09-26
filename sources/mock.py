"""
sources/mock.py -- an offline demo source (Step 23 item 7: "tested
against a fake/mock source only").

Nothing here touches the network. Requests still go through the real
paced SourceClient, served by a fake transport, so the Source Access
status view shows real pacing, request counts, cache hits and -- for the
"Challenge test" series -- the real challenge hand-off. Hidden unless
"Show the demo source" is switched on in the Sources tab.
"""

import io
import re

from .base import SourceAdapter
from .http import Response
from .models import ChapterInfo, ContentType, PageRef, SearchResult, SeriesInfo
from .registry import register

DEMO_HOST = "demo.invalid"
BASE = f"https://{DEMO_HOST}"

_SERIES = {
    "1": {"title": "Demo Comic 演示漫画", "chapters": 3, "pages": 3},
    "2": {"title": "Challenge test (always shows a verification page)", "chapters": 1, "pages": 2},
}


def _png(text: str, w: int = 600, h: int = 900) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, w - 20, h - 20], outline="black", width=4)
    d.ellipse([w // 4, h // 4, 3 * w // 4, h // 2], outline="black", width=3, fill="white")
    d.text((w // 4 + 30, h // 4 + 60), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def demo_transport(method, url, headers, data, timeout):
    """Serves the demo site. /img/2/... answers like a Cloudflare challenge
    so the hand-off path can be seen for real."""
    path = url.split(DEMO_HOST, 1)[-1]
    m = re.match(r"/img/(\d+)/(\d+)/(\d+)\.png", path)
    if m:
        sid, ch, pg = m.groups()
        if sid == "2":
            return Response(403, {"cf-mitigated": "challenge", "content-type": "text/html"},
                            b"<html><title>Just a moment...</title></html>", url)
        return Response(200, {"content-type": "image/png"},
                        _png(f"Demo page {pg}, chapter {ch}"), url)
    return Response(200, {"content-type": "text/html; charset=utf-8"},
                    b"<html><body>demo</body></html>", url)


@register
class DemoSource(SourceAdapter):
    name = "demo"
    display_name = "Demo source (offline)"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    url_patterns = [re.escape(DEMO_HOST)]
    is_demo = True

    def __init__(self, client=None, **client_kwargs):
        client_kwargs.setdefault("transport", demo_transport)
        super().__init__(client, **client_kwargs)

    def search(self, query: str, page: int = 1):
        q = (query or "").casefold()
        return [SearchResult(self.name, sid, s["title"], f"{BASE}/series/{sid}")
                for sid, s in _SERIES.items() if q in s["title"].casefold() or q in ("demo", "")]

    def get_series(self, series_id: str):
        s = _SERIES[series_id]
        return SeriesInfo(self.name, series_id, s["title"], f"{BASE}/series/{series_id}",
                          description="Generated locally; no network.",
                          content_type=ContentType.MANHUA.value, language="zh")

    def get_chapters(self, series_id: str):
        s = _SERIES[series_id]
        return [ChapterInfo(self.name, series_id, f"{series_id}-{n}", f"第{n}话",
                            f"{BASE}/series/{series_id}/{n}")
                for n in range(1, s["chapters"] + 1)]

    def get_pages(self, chapter):
        sid, n = chapter.chapter_id.split("-")
        return [PageRef(self.name, chapter.chapter_id, i,
                        f"{BASE}/img/{sid}/{n}/{i + 1}.png")
                for i in range(_SERIES[sid]["pages"])]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False,
                               action=f"Downloading page {page.index + 1}")
        return resp.content, ".png"

    def parse_url(self, url: str):
        m = re.search(r"/series/(\d+)", url or "")
        return ("series", m.group(1)) if m and m.group(1) in _SERIES else None
