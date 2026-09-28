"""
sources/adapters/missevan.py -- 猫耳FM / MissEvan (zh, audio dramas),
roadmap Step 94: Baihe's first audio-drama adapter (`known_sites.py`
already listed MissEvan as a recognized-but-unbuilt "audio_drama"
candidate before this).

Confirmed directly (real fetches, 2026-09-28, not assumed):
  - `robots.txt` is real and permissive: `Disallow: /files/` and
    `/backend/` only -- no blanket `User-agent: *` disallow, and none of
    the three API paths below are blocked.
  - Three plain JSON HTTP endpoints, all confirmed live:
      `GET /dramaapi/search?s=<keyword>&p=<page>` -- the real query
        param is `s`, not `keyword` (a `keyword=` request 200s but
        always reports "no results", which looks like a working-but-
        empty search unless you separately confirm `s=` returns real
        hits for the same keyword, which it does). Pagination is
        `info.pagination.{p,pagesize,count,maxpage}`.
      `GET /dramaapi/getdrama?drama_id=<id>` -- drama metadata plus
        `info.episodes.{ft,episode,music}`, each a list of
        `{id, name, sound_id, pay_type, vip, need_pay, ...}`.
      `GET /sound/getsound?soundid=<sound_id>` -- per-episode detail.
        `soundurl`/`soundurl_128` are **signed, expiring HLS (.m3u8)
        manifest URLs** (`?...&expire_time=...&token=...`), not flat
        downloadable files -- despite looking like plain query strings.
        A `dash.audio[].base_url` structure also exists (signed,
        fragmented `.m4s` segments) but isn't used here. `videourl` is
        the same shape when an episode has video, empty otherwise.
  - **The paywall is a silent null field, not an error status.** Fetched
    a real paid drama (魔道祖师 第三季, drama_id 22602, confirmed
    `need_pay:1`/`price:399` at the drama level) and its first paid
    episode with no session at all: `getsound` still returns HTTP 200 /
    `success: true`, but `soundurl`/`soundurl_128` are both `null`, and
    the `sound` object additionally carries `need_pay: 1`, `price: 399`,
    `pay_type: 2`, `limit_type`, `paid_time` -- fields the free episode's
    own response doesn't include at all. get_audio_url() below checks
    these explicitly rather than crashing on a `None` URL or silently
    returning a broken one.
  - **A real anti-bot wall exists, but is Referer-gated, not blanket:**
    one `getsound` request sent with no `Referer` header came back
    HTTP 200 with an Aliyun WAF slide-verification challenge page
    instead of JSON; adding a `Referer: https://www.missevan.com/...`
    header (this adapter's own `default_headers`) made the same request
    succeed reliably across repeated tries. This project's existing
    `sources/detect.py` bot-challenge detection already recognizes this
    page (it contains "滑动验证", one of `_BOT_CHALLENGE_MARKERS`) and
    raises `ChallengeDetected` through `self.client` automatically if it
    recurs -- no special-casing needed here.
  - **The real ToS (猫耳FM用户使用协议, read in full at
    `link.missevan.com/rule/duty`, genuinely server-rendered, not an SPA
    shell) explicitly restricts automated access**: §4.2.11 bans using
    any automated program/script/bot/spider/crawler to obtain the
    platform's services, content or data, for any reason, without prior
    written permission. This is the same class of specific, written
    anti-scraping clause `sources/site_terms.py` already uses to mark
    Naver/Novelpia/JJWXC `EXPLICITLY_RESTRICTED` -- recorded honestly in
    `capabilities()` below (`terms.tos_prohibited = True`). Per Step 90
    (`sources/ladder.py`'s `check_terms()`), ToS/robots.txt enforcement
    is currently a deliberate no-op app-wide, per the user's own explicit
    decision -- this finding is recorded for the record, not enforced by
    this adapter, matching every other adapter's "state facts, the
    person decides" convention.

Could NOT be verified this pass, recorded honestly rather than guessed:
  - **Authenticated/paid-episode behavior.** No real MissEvan account
    (paid or VIP) was available this pass. get_audio_url() falls back,
    when the person has signed in through `sources/auth_browser.py`'s
    persistent browser profile, to reading the same `getsound` URL
    through that profile (`page_fetch.fetch_with_profile`) on the
    assumption its rendered `page.content()` still contains the raw JSON
    body (Chromium typically wraps a JSON response in a bare `<pre>`).
    Whether a real purchased/VIP session actually changes `getsound`'s
    response at all -- and whether this extraction shape is right --
    is genuinely untested, the same open-question class bilibili_manga.py
    already flags for its own authenticated tier.
  - Whether `music`-catalog entries (soundtrack-only tracks, distinct
    from `ft`/`episode`) belong in get_chapters()'s output. Left out --
    they're not spoken-dialogue episodes, which is what this app's
    transcription/translation pipeline is for.
"""

import json
import re
from urllib.parse import quote, urljoin

from .. import auth_browser
from ..base import SourceAdapter
from ..models import (AccessTier, AudioRef, AutomationPermission, CapabilityStatus, ChapterInfo,
                      ContentAccess, ContentHidden, ContentType, FailureReason, Requirement,
                      SearchResult, SeriesInfo, SourceError, TechnicalStatus, TierResult)
from ..registry import register

BASE_URL = "https://www.missevan.com"


class LayoutChanged(SourceError):
    """The API's response didn't have the shape this adapter expects --
    most likely MissEvan changed its API. Reported plainly, never
    guessed around."""

    def __init__(self, what: str):
        super().__init__(f"MissEvan's API response has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.UNKNOWN)


def _json(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError as e:
        raise LayoutChanged("valid JSON in the API response") from e


def _from_rendered(html: str) -> dict:
    """Best-effort extraction of a JSON body a browser rendered as a
    plain page (module docstring's authenticated-path hedge) -- a bare
    `<pre>` if present, otherwise every tag stripped."""
    m = re.search(r"<pre[^>]*>(.*?)</pre>", html or "", re.S)
    body = m.group(1) if m else re.sub(r"<[^>]+>", "", html or "")
    import html as html_mod
    return _json(html_mod.unescape(body).strip())


@register
class MissEvanSource(SourceAdapter):
    name = "missevan"
    display_name = "猫耳FM MissEvan"
    content_types = [ContentType.AUDIO_DRAMA.value]
    languages = ["zh"]
    url_patterns = [r"missevan\.com/mdrama/(?:drama/)?(\d+)", r"missevan\.com/sound/(\d+)"]
    default_headers = {"Referer": BASE_URL + "/", "Accept-Language": "zh-CN,zh;q=0.9"}
    auth_supported = True
    login_url = BASE_URL + "/"

    def __init__(self, client=None, rendered_fetch=None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        # Injectable for tests; production default (module docstring's
        # authenticated-path hedge) is built lazily in _sound() so tests
        # never need a real page_fetch/auth_browser profile on disk.
        self._rendered_fetch = rendered_fetch

    # -- search / browse -----------------------------------------------------

    def search(self, query, page=1):
        resp = self.client.get(f"{BASE_URL}/dramaapi/search?s={quote(query)}&p={page}",
                               use_cache=False, action=f"Searching for {query}")
        data = _json(resp.text)
        if not data.get("success"):
            return []
        results = []
        for d in ((data.get("info") or {}).get("Datas") or []):
            results.append(SearchResult(
                self.name, str(d.get("id")), d.get("name") or str(d.get("id")),
                urljoin(BASE_URL, f"/mdrama/{d.get('id')}"), d.get("cover") or "",
                extra={"author": d.get("author") or "", "pay_type": d.get("pay_type", 0)}))
        return results

    def _drama_response(self, series_id: str) -> dict:
        resp = self.client.get(f"{BASE_URL}/dramaapi/getdrama?drama_id={series_id}",
                               use_cache=False, action=f"Loading drama {series_id}")
        data = _json(resp.text)
        if not data.get("success"):
            raise LayoutChanged("a successful getdrama response")
        return data

    def get_series(self, series_id):
        drama = (self._drama_response(series_id).get("info") or {}).get("drama") or {}
        if not drama:
            raise LayoutChanged("a drama object in the getdrama response")
        return SeriesInfo(
            self.name, series_id, drama.get("name") or series_id,
            urljoin(BASE_URL, f"/mdrama/{series_id}"), drama.get("cover") or "",
            authors=[drama["author"]] if drama.get("author") else [],
            description=drama.get("abstract") or "",
            content_type=ContentType.AUDIO_DRAMA.value, language="zh")

    def get_chapters(self, series_id):
        episodes = (self._drama_response(series_id).get("info") or {}).get("episodes") or {}
        # `music` (soundtrack-only tracks) deliberately excluded -- module
        # docstring. `ft` (extras/番外/花絮) kept: real spoken dialogue.
        raw = list(episodes.get("episode") or []) + list(episodes.get("ft") or [])
        if not raw:
            raise LayoutChanged("any episodes in the drama's episode list")
        chapters = []
        for ep in raw:
            sound_id = str(ep.get("sound_id"))
            chapters.append(ChapterInfo(
                self.name, series_id, sound_id, ep.get("name") or sound_id,
                urljoin(BASE_URL, f"/sound/{sound_id}")))
        return chapters

    # -- audio ----------------------------------------------------------------

    def _sound(self, chapter, authenticated: bool) -> dict:
        url = f"{BASE_URL}/sound/getsound?soundid={chapter.chapter_id}"
        if not authenticated:
            resp = self.client.get(url, use_cache=False,
                                   action=f"Loading audio for {chapter.title}")
            data = _json(resp.text)
        else:
            fn = self._rendered_fetch
            if fn is None:
                from page_fetch import fetch_with_profile
                profile_dir = auth_browser.profile_dir(self.login_url, self.name)
                fn = lambda u: fetch_with_profile(u, profile_dir)

            def _do(u):
                html, _text = fn(u)
                return _from_rendered(html)
            data = self.client.paced(_do, url, "Authenticated browser",
                                     action=f"Loading audio for {chapter.title} (signed in)")
        if not data.get("success"):
            raise LayoutChanged("a successful getsound response")
        sound = (data.get("info") or {}).get("sound") or {}
        if not sound:
            raise LayoutChanged("a sound object in the getsound response")
        return sound

    @staticmethod
    def _audio_ref(sound: dict, chapter):
        for key in ("videourl", "soundurl_128", "soundurl"):
            u = sound.get(key)
            if u:
                fmt = "hls" if ".m3u8" in u else "direct"
                return AudioRef("missevan", chapter.chapter_id, u, format=fmt)
        return None

    def _locked_message(self, sound: dict, chapter) -> str:
        if sound.get("pay_type") or sound.get("need_pay"):
            return (f"\"{chapter.title}\" is paid/VIP content on {self.display_name} -- the API "
                    "returned no playable audio URL for it. Sign in with a purchased/VIP account "
                    "(this source's Sign in action) and try again.")
        return (f"\"{chapter.title}\" returned no playable audio URL from {self.display_name} -- "
                "it may be geo-restricted, taken down, or the API has changed.")

    def get_audio_url(self, chapter):
        sound = self._sound(chapter, authenticated=False)
        ref = self._audio_ref(sound, chapter)
        if ref is not None:
            return ref
        if self._rendered_fetch is not None or auth_browser.has_profile(self.login_url, self.name):
            sound = self._sound(chapter, authenticated=True)
            ref = self._audio_ref(sound, chapter)
            if ref is not None:
                return ref
        raise ContentHidden(self._locked_message(sound, chapter), FailureReason.PURCHASE_REQUIRED)

    def parse_url(self, url: str):
        m = re.search(r"missevan\.com/sound/(\d+)", url or "")
        if m:
            sound_id = m.group(1)
            return ("chapter", ChapterInfo(self.name, "", sound_id, sound_id,
                                           urljoin(BASE_URL, f"/sound/{sound_id}")))
        m = re.search(r"missevan\.com/mdrama/(?:drama/)?(\d+)", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.status = CapabilityStatus.PARTIALLY_SUPPORTED.value
        caps.technical_status = TechnicalStatus.PARTIALLY_SUPPORTED.value
        caps.access_method = AccessTier.STATIC_HTTP.value
        caps.content_access_status = ContentAccess.AUDIO.value
        caps.authentication_required = Requirement.UNKNOWN.value  # per-episode, not source-wide
        caps.purchase_required = Requirement.UNKNOWN.value         # some dramas free, some paid
        caps.tiers[AccessTier.STATIC_HTTP.value] = TierResult(
            tested=True, ok=True,
            detail="search/getdrama/getsound confirmed live (2026-09-28) for free content; a "
                   "real paid episode confirmed to return success:true with soundurl/"
                   "soundurl_128 both null plus need_pay/price/pay_type set -- the paywall is a "
                   "silent null field, not an HTTP error (module docstring).")
        caps.technical = {
            "extraction_method": "plain JSON HTTP API (dramaapi/search, dramaapi/getdrama, "
                                 "sound/getsound) -- no browser rendering needed for free content",
            "browser_required_for_free_content": False,
            "hls_manifest": "soundurl/soundurl_128 are signed, expiring HLS (.m3u8) manifest "
                            "URLs, not flat downloadable files -- playable, but turning one into "
                            "a local file needs an HLS-aware fetch (e.g. ffmpeg), not implemented "
                            "here.",
            "waf_challenge_seen": "an unauthenticated getsound request sent with no Referer "
                                  "header once came back as an Aliyun WAF slide-verification "
                                  "page instead of JSON (HTTP 200); this adapter always sends a "
                                  "Referer, and sources/detect.py's existing bot-challenge "
                                  "detection (matches its \"滑动验证\" text) already raises "
                                  "ChallengeDetected through self.client if it recurs.",
            "authenticated_path_unverified": "get_audio_url() falls back to reading getsound "
                                             "through the signed-in persistent browser profile "
                                             "when one exists, assuming its rendered page still "
                                             "carries the raw JSON body -- NOT independently "
                                             "confirmed against a real logged-in/VIP account (none "
                                             "was available this pass), matching bilibili_manga."
                                             "py's own hedge on authenticated behavior.",
        }
        caps.terms = {
            "robots_txt": "Disallow: /files/ and /backend/ only, no blanket User-agent: * "
                          "disallow (confirmed live, 2026-09-28).",
            "read": "猫耳FM用户使用协议 (link.missevan.com/rule/duty), read in full, 2026-09-28.",
            "clause": "§4.2.11 bans using any automated program/script/bot/spider/crawler "
                     "to obtain the platform's services, content, or data, for any reason, "
                     "without prior written permission.",
            "tos_prohibited": True,
            "enforcement_note": "ToS/robots.txt enforcement was deactivated app-wide in Step 90, "
                                "per the user's own explicit decision -- this finding is recorded "
                                "for the record here, not enforced by this adapter.",
        }
        caps.automation_permission = AutomationPermission.EXPLICITLY_RESTRICTED.value
        return caps
