"""
sources/front_door.py -- one "paste any URL" box (Step 23 items 10, 11).

Works out what a URL is before anything is imported -- a registered
source, a video, a novel chapter, or a comic chapter -- and builds a
preview (title, chapter, language, content type, chapter count where
knowable). Nothing is imported until the person presses Import.

Video URLs go straight to the existing yt-dlp download path
(video_download.download), the same call Workspace's "Video URL" uses.
"""

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import detect, generic_import, ladder, registry
from .models import ContentType, SourceError

VIDEO = "video"
NOVEL = ContentType.NOVEL.value
COMIC = "comic"
UNKNOWN = "unknown"

_VIDEO_URL = re.compile(
    r"(youtube\.com/(watch|shorts|live)|youtu\.be/|bilibili\.com/(video|bangumi)/|b23\.tv/|"
    r"vimeo\.com/\d|twitch\.tv/(videos|[^/]+/clip)|nicovideo\.jp/watch|tiktok\.com/@[^/]+/video|"
    r"dailymotion\.com/video|missevan\.com/sound)", re.I)
_CHAPTER_IN_TITLE = re.compile(
    r"(第\s*[0-9零〇一二两三四五六七八九十百千]+\s*[章话話回集]|chapter\s*[0-9.]+|ch\.?\s*[0-9.]+|"
    r"제?\s*[0-9]+\s*화|episode\s*[0-9]+)", re.I)


@dataclass
class Preview:
    url: str
    content_type: str = UNKNOWN
    platform: str = ""
    title: str = ""
    chapter: str = ""
    language: str = ""
    chapter_count: int = None
    adapter: str = None
    series_id: str = None
    image_count: int = None
    text_length: int = None
    notes: list = field(default_factory=list)
    ladder: object = None
    html: str = ""


def detect_language(text: str) -> str:
    """Script-based guess -- enough to label a preview, not a classifier."""
    t = text or ""
    kana = len(re.findall(r"[぀-ヿ]", t))
    hangul = len(re.findall(r"[가-힯]", t))
    han = len(re.findall(r"[一-鿿]", t))
    latin = len(re.findall(r"[A-Za-z]", t))
    if kana > 20 or (kana and kana >= han * 0.1):
        return "ja"
    if hangul > max(han, 20) * 0.5 and hangul > 10:
        return "ko"
    if han > 20:
        return "zh"
    if latin > 50:
        return "en"
    return ""


def _og(html: str, prop: str) -> str:
    m = re.search(r"""<meta[^>]+property=["']og:%s["'][^>]+content=["']([^"']*)""" % prop,
                  html or "", re.I) or \
        re.search(r"""<meta[^>]+content=["']([^"']*)["'][^>]+property=["']og:%s["']""" % prop,
                  html or "", re.I)
    return m.group(1).strip() if m else ""


def is_video_url(url: str) -> bool:
    return bool(_VIDEO_URL.search(url or ""))


def classify_html(url: str, html: str) -> Preview:
    """Content type from a fetched page, with no per-site knowledge."""
    p = Preview(url=url, html=html, platform=urlsplit(url).netloc)
    p.title = _og(html, "title") or detect.page_title(html)
    m = _CHAPTER_IN_TITLE.search(p.title)
    p.chapter = m.group(1) if m else ""
    og_type = _og(html, "type").lower()
    has_video_tag = bool(re.search(r"<video[^>]+src=|<video\b[^>]*>\s*<source", html or "", re.I))
    if og_type.startswith("video") or has_video_tag or _og(html, "video"):
        p.content_type = VIDEO
        p.language = detect_language(p.title)
        return p
    text, _method = generic_import.extract_main_text(html, url)
    p.text_length = len(text)
    p.image_count = len(generic_import.image_candidates(html, url))
    p.language = detect_language(text or p.title)
    if p.text_length >= 800 and p.text_length > p.image_count * 150:
        p.content_type = NOVEL
    elif p.image_count >= 3:
        p.content_type = COMIC
    elif p.text_length >= generic_import.MIN_NOVEL_CHARS:
        p.content_type = NOVEL
    return p


def preview(url: str, client=None, rendered_fetch=None) -> Preview:
    url = (url or "").strip()
    adapter = registry.find_for_url(url)
    if adapter is not None:
        ladder.check_terms(adapter.name, adapter.capabilities())
        p = Preview(url=url, adapter=adapter.name, platform=adapter.display_name,
                    content_type=(ContentType(adapter.content_types[0]).value
                                  if adapter.content_types else UNKNOWN),
                    language=(adapter.languages or [""])[0])
        if p.content_type in (ContentType.MANGA.value, ContentType.MANHUA.value,
                              ContentType.MANHWA.value):
            p.notes.append(f"{p.content_type} -- imports into Scanlate")
            p.content_type = COMIC
        parsed = adapter.parse_url(url)
        if parsed and parsed[0] == "series":
            p.series_id = parsed[1]
            try:
                info = adapter.get_series(p.series_id)
                p.title = info.title
                p.chapter_count = len(adapter.get_chapters(p.series_id))
            except SourceError as e:
                p.notes.append(f"Couldn't load the series yet: {e.reason.value}")
        elif parsed and parsed[0] == "chapter":
            p.chapter = parsed[1].title
            p.series_id = parsed[1].series_id
        elif p.content_type == VIDEO and hasattr(adapter, "get_metadata"):
            # Step 23d: a real VideoSource adapter (e.g. BilibiliSource) --
            # metadata shown before any download, same guarantee item 2
            # asks for, via extract_info(download=False) under the hood.
            try:
                meta = adapter.get_metadata(url)
                p.title = meta.get("title") or ""
                parts = adapter.get_parts(url) if hasattr(adapter, "get_parts") else []
                if len(parts) > 1:
                    p.chapter_count = len(parts)
                    p.notes.append(f"Contains {len(parts)} parts.")
            except SourceError as e:
                p.notes.append(f"Couldn't load metadata yet: {e.reason.value}")
        return p
    if is_video_url(url):
        return Preview(url=url, content_type=VIDEO, platform=urlsplit(url).netloc,
                       notes=["Downloads through yt-dlp, the same as Workspace's Video URL."])
    lr = generic_import.fetch_page(url, client, rendered_fetch)
    if not lr.ok:
        p = Preview(url=url, ladder=lr, platform=urlsplit(url).netloc)
        p.notes.extend(lr.summary_lines())
        return p
    p = classify_html(url, lr.html)
    p.ladder = lr
    return p


def import_video(url: str, drama_id: int, audio_only: bool = True, progress_cb=None,
                 cookies_browser: str = None, cookies_file: str = None) -> str:
    """Routes a detected video URL into a download path -- a registered
    VideoSource adapter (Step 23d's BilibiliSource) if one matches this
    URL, otherwise the same generic video_download.download call and
    drama updates as before, unchanged for every other video source
    (YouTube etc., which have no dedicated adapter)."""
    import db
    ddir = db.drama_dir(drama_id)
    fetched = {}

    adapter = registry.find_for_url(url)
    if adapter is not None and hasattr(adapter, "download") and ContentType.VIDEO.value in adapter.content_types:
        options = {"quality": "Audio only" if audio_only else "Best available",
                  "cookies_browser": cookies_browser, "cookies_file": cookies_file}
        result = adapter.download(url, ddir, options=options)
        path = result["path"]
        if result.get("title"):
            fetched["title"] = result["title"]
    else:
        import video_download
        path = video_download.download(url, ddir, audio_only=audio_only, progress_cb=progress_cb,
                                       title_cb=lambda t: fetched.setdefault("title", t),
                                       cookies_browser=cookies_browser, cookies_file=cookies_file)

    drama = db.get_drama(drama_id) or {}
    update = {}
    if fetched.get("title") and not (drama.get("title_en") or drama.get("title_zh")):
        update["title_zh"] = fetched["title"]
    if audio_only:
        db.update_drama(drama_id, audio_filename=os.path.basename(path), source_url=url, **update)
    else:
        import core
        core.extract_audio_from_video(path, os.path.join(ddir, "audio.wav"))
        db.update_drama(drama_id, audio_filename="audio.wav",
                        source_video_filename=os.path.basename(path), source_url=url, **update)
    return path
