"""
sources/adapters/fanjiao.py -- 饭角 Fanjiao (zh, baihe/GL audio dramas,
18+). Operator 深圳热蓝科技有限公司. (The roadmap's
"泛娱有声" label was wrong; the platform is 饭角, www.fanjiao.co.)

Metadata only, through the RENDERED_BROWSER tier. User decision
(2026-09-30): a browser-rendered adapter; the app's API signing is never
extracted or reimplemented.

What the public web actually offers (read directly, 2026-09-30):
  - One public page per title: `https://www.fanjiao.co/pages/share.html
    ?album_id=<id>`. Its static HTML is an empty template (title, brief,
    参演CV, 收听第一集, 最新评论, 打开APP查看全部内容); the page's own
    script (`/files/js/share03.js`) fills it in by calling the site's
    signed API (`api.fanjiao.co/walkman/api/...`, an md5 `signature`
    header). That signing is protection in the `docs/adding-source.md`
    sense, so this adapter never builds such a request. Instead it opens
    the share page in a real browser (`page_fetch.api_capture_session`)
    and reads the responses the page's own script already made and
    earned -- the same "let the site's own execution path produce the
    result" rule `bilibili_manga.py` and `manhuaku.py` follow. Nothing about the signature is read, stored or reproduced.
  - The page's script makes these calls, which are the only ones read:
      `album/album_info?album_id=<id>&from=H5` -> `data.{name, description,
        cover, play, liked, album_id, ...}` (the page renders name, cover,
        description, play and liked counts);
      `album/actor_cvs?album_id=<id>` -> `data.cv_list[].{name, avatar,
        role_name, cv_type}` (rendered as 参演CV);
      `album/audio?album_id=<id>&from=H5` -> `data.audios_list[]`. The page
        uses only the first `audio_id` (to load comments) and does not
        render the list; its response is still the page's own response,
        so the episode list is read from it. Per-episode `audio_id` and
        `name` are the keys the YuriAudio2Notion reference reads
        (Apache-2.0, read for field names only).
    Comments are loaded too and are not read.
  - No public search or catalogue exists, so there is no `search()`.
    Album ids come from baihehub.com: its audio-drama records carry a
    `fjId` field holding the Fanjiao album id.
  - No audio. The share page exposes no playable audio URL (收听第一集
    opens the app), so there's no `get_audio_url()`. The UI then hides the
    download action instead of offering one that can only fail.

Could NOT be verified this pass (recorded honestly rather than guessed):
  - A live render. This build's container could not open the page in
    its headless browser, so the test fixtures follow the shapes the
    page's own script reads (`share03.js`) and the reference's field list.
    They are not a trimmed capture. The first real run should confirm
    them (docs/content-sources.md, manual check list).
  - Per-episode paid/locked flags. Their key names aren't known. The
    adapter checks a small set of likely ones (`need_pay`, `is_pay`,
    `pay_type`, `vip`, `is_vip`, `price`, `is_free == 0`, `lock`/`is_lock`)
    and labels matching episodes `付费 / paid (app only)` in the
    chapter's `group`. It never tries to unlock them.
  - What the page's API returns for a paid, 18+ or removed album. If the
    page's own album or episode call is refused (non-200) or answers
    without a list, it's treated as withheld from the public page and
    ContentHidden (PURCHASE_REQUIRED) is raised with a plain message
    rather than an empty list.

No browser for a request not from this PC (docs/remote-access-decision.md):
the API sets `allow_browser` from that (preview, preflight, series listing,
"Check now"), and `_render` refuses when it's
off.
"""

import html as html_mod
import json
import re
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from ..base import SourceAdapter
from ..models import (AccessTier, AutomationPermission, CapabilityStatus, ChapterInfo,
                      ContentAccess, ContentHidden, ContentType, FailureReason, Requirement,
                      SeriesInfo, SourceError, TechnicalProtection, TechnicalStatus,
                      TierResult)
from ..registry import register

SHARE_HOST = "www.fanjiao.co"
SHARE_URL = "https://" + SHARE_HOST + "/pages/share.html?album_id={}"
# Only the page's own responses for these three endpoints are kept.
API_PATTERN = re.compile(r"^https://api\.fanjiao\.co/walkman/api/+album/"
                         r"(?:album_info|actor_cvs|audio)\?")
_ALBUM_ID = re.compile(r"^[0-9]{1,12}$")
PAID_GROUP = "付费 / paid (app only)"


class LayoutChanged(SourceError):
    """The rendered share page didn't have the shape this adapter
    expects -- reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"Fanjiao's share page has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _endpoint(url: str) -> str:
    """'album_info' / 'actor_cvs' / 'audio' for a captured response URL."""
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def _album_of(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("album_id") or [""])[0]


def _body_json(entry: dict):
    body = entry.get("body")
    if not body:
        return None
    try:
        data = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _is_locked(ep: dict) -> bool:
    for key in ("need_pay", "is_pay", "pay_type", "vip", "is_vip", "lock", "is_lock"):
        if ep.get(key) not in (None, 0, False, "", "0"):
            return True
    try:
        if float(ep.get("price") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return ep.get("is_free") in (0, False, "0")


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""


def _str(value) -> str:
    """A field from the site's JSON as text; anything that isn't a
    string or a number counts as missing."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _data(resp) -> dict:
    data = resp.get("data") if isinstance(resp, dict) else None
    return data if isinstance(data, dict) else {}


def _default_capture(url: str):
    """Opens the share page in page_fetch's guarded browser and returns
    (rendered html, the page's own matching responses). Browser failures
    come back as a plain SourceError, like the other browser-tier readers."""
    from page_fetch import api_capture_session
    try:
        with api_capture_session(url, API_PATTERN, timeout=30, wait_ms=5000) as (page,
                                                                                 captured):
            html = page.content()
            return html, list(captured)
    except ImportError as e:
        raise SourceError(str(e), FailureReason.NOT_INSTALLED) from None
    except SourceError:
        raise
    except Exception as e:
        raise SourceError(f"Fanjiao's share page couldn't be opened in the browser "
                          f"({type(e).__name__}).", FailureReason.UNKNOWN) from None


@register
class FanjiaoSource(SourceAdapter):
    name = "fanjiao"
    display_name = "饭角 Fanjiao"
    content_types = [ContentType.AUDIO_DRAMA.value]
    languages = ["zh"]
    url_patterns = [r"fanjiao\.co/pages/share\.html\?(?:[^#]*&)?album_id=\d+"]
    # One render makes four or five API calls on the site's side, so
    # renders are spaced well apart.
    host_min_interval = {SHARE_HOST: 10.0}
    default_headers = {"Accept-Language": "zh-CN,zh;q=0.9"}

    def __init__(self, client=None, capture=None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        # Injectable for tests: capture(url) -> (html, [captured entries]).
        self._capture = capture or _default_capture
        self._renders = {}

    # -- one render, shared by get_series and get_chapters -------------------

    def _render(self, series_id: str) -> dict:
        series_id = str(series_id or "").strip()
        if not _ALBUM_ID.match(series_id):
            raise SourceError(f"\"{series_id}\" isn't a Fanjiao album id (digits only).",
                              FailureReason.UNKNOWN)
        if series_id in self._renders:
            return self._renders[series_id]
        if not self.allow_browser:
            # docs/remote-access-decision.md: no browser for a request not
            # from this PC, and this source has nothing without one.
            raise SourceError("Fanjiao needs the browser, which only runs for requests from "
                              "this PC. Open it there.", FailureReason.JAVASCRIPT_REQUIRED)
        url = SHARE_URL.format(series_id)
        html, captured = self.client.paced(self._capture, url, "Rendered browser",
                                           action=f"Loading Fanjiao album {series_id}")
        responses, refused = {}, {}
        for entry in captured or []:
            if _album_of(entry.get("url", "")) != series_id:
                continue
            if entry.get("status") != 200:
                # The site's own call was refused (paid, 18+, removed...).
                refused.setdefault(_endpoint(entry["url"]), entry.get("status"))
                continue
            data = _body_json(entry)
            if data is not None:
                responses.setdefault(_endpoint(entry["url"]), data)
        result = {"url": url, "html": html or "", "responses": responses, "refused": refused}
        self._renders[series_id] = result
        return result

    # -- metadata -------------------------------------------------------------

    def get_series(self, series_id):
        r = self._render(series_id)
        info = _data(r["responses"].get("album_info"))
        soup = BeautifulSoup(r["html"], "html.parser")

        title = (_str(info.get("name")) or _text(soup.select_one(".title"))).strip()
        if not title:
            if "album_info" in r["refused"] and "album_info" not in r["responses"]:
                raise self._withheld("album")
            raise LayoutChanged("the album title (the page showed nothing without the app)")
        description = _str(info.get("description")) or _text(soup.select_one(".brieftext"))
        cover = _str(info.get("cover"))
        if not cover:
            img = soup.select_one(".titleimg img")
            cover = (img.get("src") or "") if img is not None else ""

        cvs = self._cv_names(r, soup)
        if cvs:
            description = (description + "\n\n" if description else "") + "参演CV: " + "、".join(cvs)
        authors = [a for a in (_str(info.get("author_name")),) if a]
        freq = _str(info.get("update_frequency"))
        status = "completed" if "完结" in freq and "未完结" not in freq else "unknown"
        return SeriesInfo(self.name, str(series_id), html_mod.unescape(title), r["url"], cover,
                          authors=authors, description=html_mod.unescape(description).strip(),
                          status=status, content_type=ContentType.AUDIO_DRAMA.value,
                          language="zh")

    @staticmethod
    def _cv_names(r: dict, soup) -> list:
        cv_list = _data(r["responses"].get("actor_cvs")).get("cv_list")
        if isinstance(cv_list, list):
            names = []
            for cv in cv_list:
                name = _str(cv.get("name")) if isinstance(cv, dict) else ""
                if name:
                    role = _str(cv.get("role_name"))
                    names.append(f"{name}（{role}）" if role else name)
            return names
        return [_text(n) for n in soup.select(".cvdiv .cvname") if _text(n)]

    def get_chapters(self, series_id):
        r = self._render(series_id)
        resp = r["responses"].get("audio")
        if resp is None:
            if "audio" in r["refused"]:
                raise self._withheld("album's episode list")
            raise LayoutChanged("the episode list (the page's own episode request never "
                                "came back)")
        episodes = _data(resp).get("audios_list")
        if not isinstance(episodes, list) or not episodes:
            raise self._withheld("album's episode list")
        chapters = []
        for ep in episodes:
            audio_id = _str(ep.get("audio_id")) if isinstance(ep, dict) else ""
            if not audio_id:
                continue
            title = html_mod.unescape(_str(ep.get("name")) or _str(ep.get("title")) or audio_id)
            chapters.append(ChapterInfo(self.name, str(series_id), audio_id, title, r["url"],
                                        group=PAID_GROUP if _is_locked(ep) else ""))
        if not chapters:
            raise LayoutChanged("an audio_id on any episode")
        return chapters

    def _withheld(self, what: str) -> ContentHidden:
        return ContentHidden(
            f"{self.display_name}'s public share page doesn't show this {what} -- it may be "
            "paid, 18+, removed or app-only. Open it in the Fanjiao app.",
            FailureReason.PURCHASE_REQUIRED)

    def parse_url(self, url: str):
        parts = urlsplit(url or "")
        if not (parts.hostname or "").lower().endswith("fanjiao.co"):
            return None
        album = (parse_qs(parts.query).get("album_id") or [""])[0]
        return ("series", album) if _ALBUM_ID.match(album) else None

    def capabilities(self):
        caps = super().capabilities()
        caps.status = CapabilityStatus.PARTIALLY_SUPPORTED.value
        caps.technical_status = TechnicalStatus.PARTIALLY_SUPPORTED.value
        caps.access_method = AccessTier.RENDERED_BROWSER.value
        caps.content_access_status = ContentAccess.METADATA.value
        caps.authentication_required = Requirement.UNKNOWN.value
        caps.purchase_required = Requirement.UNKNOWN.value
        caps.tiers[AccessTier.STATIC_HTTP.value] = TierResult(
            tested=True, ok=False,
            detail="share.html is an empty template until its own script runs (2026-09-30).")
        caps.tiers[AccessTier.RENDERED_BROWSER.value] = TierResult(
            tested=False, ok=False,
            detail="Built against the page's own script (share03.js); a live render could not "
                   "be run from the build container. Unconfirmed until the first real run.")
        caps.technical = {
            "extraction_method": "Renders the public share page and reads the responses its "
                                 "own script made (album_info, actor_cvs, album/audio), with "
                                 "the rendered DOM as a fallback for title/brief/CVs.",
            "browser_required_for_free_content": True,
            "technical_protection": "DETECTED -- the site's API needs an md5 `signature` "
                                    "header; the app adds Shumei risk control and 360 "
                                    "hardening. Recorded, never worked around: this adapter "
                                    "never builds a signed request.",
            "audio": "None exposed on the public page (收听第一集 opens the app); metadata only.",
            "search": "No public search or catalogue; album ids come from baihehub.com (fjId).",
        }
        caps.terms = {
            "robots_txt": "None: every unknown path, /robots.txt included, returns the "
                          "homepage (2026-09-30).",
            "read": "The user agreement is only viewable inside the app. The public "
                    "/pages/useragree.html is the privacy policy and has no automation clause.",
            "tos_prohibited": False,
            "unverified": "Not cleared: UNKNOWN is not PERMITTED.",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        caps.technical_protection = TechnicalProtection.DETECTED.value
        return caps
