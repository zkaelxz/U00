"""
tests/test_sources_fanjiao.py -- Step 113: the 饭角 Fanjiao metadata
adapter. No network and no browser: the share-page render is replaced by
a fake capture that returns rendered HTML plus the page's own API
responses.

The fixtures follow the shapes the page's own script reads and writes
(`www.fanjiao.co/files/js/share03.js`: getpagecv, getcvuser, getaudioId)
and the reference's field list. They are not a trimmed live capture: a
live render couldn't be run from the build container (module docstring
on sources/adapters/fanjiao.py).
"""
import json

import pytest

fanjiao = __import__("sources.adapters.fanjiao", fromlist=["_"])

from sources import registry, site_terms
from sources.models import (AccessTier, AutomationPermission, ContentHidden, FailureReason,
                            SourceError, TechnicalProtection)

from .sources_helpers import FakeClock, make_client

ALBUM = "111601"
SHARE = f"https://www.fanjiao.co/pages/share.html?album_id={ALBUM}"
API = "https://api.fanjiao.co/walkman/api"

ALBUM_INFO = {"code": 0, "data": {
    "album_id": 111601, "name": "一目余生", "description": "简介第一行&amp;更多",
    "cover": "https://fanjiao-media.fanjiao.co/Fcover", "author_name": "原著作者",
    "update_frequency": "已完结", "ori_price": 1200, "play": 123456, "liked": 789}}
ACTOR_CVS = {"code": 0, "data": {"cv_list": [
    {"name": "CV甲", "role_name": "角色A", "cv_type": 1, "avatar": "https://x/a"},
    {"name": "CV乙", "role_name": "", "cv_type": 2, "avatar": "https://x/b"}]}}
AUDIO = {"code": 0, "data": {"audios_list": [
    {"audio_id": 9001, "name": "第一集"},
    {"audio_id": 9002, "name": "第二集", "need_pay": 1},
    {"audio_id": 9003, "name": "番外", "price": 30},
]}}

# share.html after the page's own script ran (getpagecv + getcvuser).
RENDERED = """<html><body>
<div class="ban"><span class="titleimg"><img src="https://fanjiao-media.fanjiao.co/Fdom"></span>
<div class="title">一目余生（DOM）</div></div>
<div class="brief"><span class="brieftext">DOM 简介</span></div>
<div class="cv"><span class="yel"></span>参演CV(2)</div>
<div class="cvdiv"><span class="cvuser"><a class="cvimg"><img src="https://x/a"></a>
<a class="cvname">CV甲</a></span><span class="cvuser"><a class="cvname">CV乙</a></span></div>
<div class="lastmore"><span class="lasttext">打开APP查看全部内容&gt;</span></div>
</body></html>"""

EMPTY_TEMPLATE = """<html><body><div class="title"></div><span class="brieftext"></span>
<div class="cvdiv"><!-- <span class="cvuser"><a class="cvname">x</a></span> --></div>
</body></html>"""


def _entry(path, body, album=ALBUM, status=200, extra=""):
    url = f"{API}/{path}?album_id={album}{extra}"
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return {"url": url, "status": status, "content_type": "application/json", "body": raw}


def _captured(info=ALBUM_INFO, cvs=ACTOR_CVS, audio=AUDIO):
    out = []
    if info is not None:
        out.append(_entry("album/album_info", info, extra="&from=H5"))
    if cvs is not None:
        out.append(_entry("album/actor_cvs", cvs))
    if audio is not None:
        out.append(_entry("/album/audio", audio, extra="&from=H5"))  # the page's own `api//`
    return out


class FakeCapture:
    def __init__(self, html=RENDERED, captured=None):
        self.html = html
        self.captured = _captured() if captured is None else captured
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self.html, self.captured


def _adapter(capture=None, clock=None):
    client = make_client("fanjiao", clock=clock)
    return fanjiao.FanjiaoSource(client=client, capture=capture or FakeCapture())


def test_registered_and_metadata_only():
    assert "fanjiao" in registry.adapter_classes()
    a = _adapter()
    assert a.supports("get_series") and a.supports("get_chapters")
    # No public search, and the public page exposes no audio.
    assert not a.supports("search")
    assert not a.supports("get_audio_url")
    assert not a.supports("get_pages")


def test_get_series_reads_the_pages_own_responses():
    cap = FakeCapture()
    s = _adapter(cap).get_series(ALBUM)
    assert cap.urls == [SHARE]
    assert s.title == "一目余生"
    assert s.cover_url == "https://fanjiao-media.fanjiao.co/Fcover"
    assert s.authors == ["原著作者"]
    assert s.description.startswith("简介第一行&更多")
    assert "参演CV: CV甲（角色A）、CV乙" in s.description
    assert s.status == "completed"
    assert s.url == SHARE and s.content_type == "audio_drama" and s.language == "zh"


def test_get_series_falls_back_to_rendered_dom():
    cap = FakeCapture(captured=[])
    s = _adapter(cap).get_series(ALBUM)
    assert s.title == "一目余生（DOM）"
    assert s.cover_url == "https://fanjiao-media.fanjiao.co/Fdom"
    assert s.description == "DOM 简介\n\n参演CV: CV甲、CV乙"
    assert s.status == "unknown"


def test_get_chapters_in_page_order_with_paid_label():
    chapters = _adapter().get_chapters(ALBUM)
    assert [(c.chapter_id, c.title) for c in chapters] == [
        ("9001", "第一集"), ("9002", "第二集"), ("9003", "番外")]
    assert [c.group for c in chapters] == ["", fanjiao.PAID_GROUP, fanjiao.PAID_GROUP]
    assert all(c.series_id == ALBUM and c.url == SHARE for c in chapters)


def test_one_render_serves_series_and_chapters():
    cap = FakeCapture()
    a = _adapter(cap)
    a.get_series(ALBUM)
    a.get_chapters(ALBUM)
    assert len(cap.urls) == 1


def test_withheld_episode_list_is_content_hidden():
    withheld = {"code": 403, "msg": "需要登录", "data": None}
    a = _adapter(FakeCapture(captured=_captured(audio=withheld)))
    with pytest.raises(ContentHidden) as e:
        a.get_chapters(ALBUM)
    assert e.value.reason == FailureReason.PURCHASE_REQUIRED
    assert "app" in str(e.value)


def test_empty_template_is_a_plain_layout_error():
    """The page showed nothing without the app: say so, don't return an
    untitled series or an empty episode list."""
    a = _adapter(FakeCapture(html=EMPTY_TEMPLATE, captured=[]))
    with pytest.raises(fanjiao.LayoutChanged):
        a.get_series(ALBUM)
    with pytest.raises(fanjiao.LayoutChanged):
        a.get_chapters(ALBUM)


def test_ignores_other_albums_bad_status_and_bad_bodies():
    captured = [
        _entry("album/album_info", {"data": {"name": "别的剧"}}, album="999"),
        _entry("album/album_info", {"data": {"name": "错误"}}, status=500),
        _entry("album/album_info", b"<html>not json</html>"),
        {"url": f"{API}/album/audio?album_id={ALBUM}", "status": 200, "body": None},
    ]
    a = _adapter(FakeCapture(captured=captured))
    assert a.get_series(ALBUM).title == "一目余生（DOM）"
    with pytest.raises(fanjiao.LayoutChanged):
        a.get_chapters(ALBUM)


@pytest.mark.parametrize("bad", ["", "abc", "1/../2", "12&x=1", "1" * 13])
def test_rejects_non_numeric_album_ids_before_rendering(bad):
    cap = FakeCapture()
    with pytest.raises(SourceError):
        _adapter(cap).get_series(bad)
    assert cap.urls == []


def test_render_is_paced_through_the_client():
    clock = FakeClock()
    cap = FakeCapture()
    client = make_client("fanjiao", clock=clock,
                         host_min_interval={"www.fanjiao.co": 10.0})
    a = fanjiao.FanjiaoSource(client=client, capture=cap)
    a.get_series("1")
    a.get_series("2")
    assert len(cap.urls) == 2
    assert sum(clock.sleeps) >= 10.0
    assert client.stats["requests"] == 2


def test_parse_url_and_matches():
    a = _adapter()
    assert a.parse_url(SHARE) == ("series", ALBUM)
    assert a.parse_url("https://www.fanjiao.co/pages/share.html?x=1&album_id=42") == ("series", "42")
    assert a.parse_url("https://www.fanjiao.co/pages/share.html") is None
    assert a.parse_url("https://evil.example/share.html?album_id=1") is None
    assert fanjiao.FanjiaoSource.matches_url(SHARE)
    assert not fanjiao.FanjiaoSource.matches_url("https://www.fanjiao.co/")


def test_api_pattern_only_matches_the_three_album_calls():
    p = fanjiao.API_PATTERN
    assert p.search(f"{API}/album/album_info?album_id=1&from=H5")
    assert p.search(f"{API}//album/audio?album_id=1&from=H5")
    assert p.search(f"{API}/album/actor_cvs?album_id=1")
    assert not p.search(f"{API}/comment/list?audio_id=1")
    assert not p.search(f"https://evil.example/?u={API}/album/audio?album_id=1")


def test_capabilities_record_the_vetting():
    caps = _adapter().capabilities()
    assert caps.access_method == AccessTier.RENDERED_BROWSER.value
    assert caps.automation_permission == AutomationPermission.UNKNOWN.value
    assert caps.technical_protection == TechnicalProtection.DETECTED.value
    assert caps.terms["tos_prohibited"] is False
    assert "signature" in caps.technical["technical_protection"]


def test_site_terms_entry():
    caps = site_terms.capabilities_for("https://www.fanjiao.co/pages/share.html?album_id=1")
    assert caps is not None
    assert caps.automation_permission == AutomationPermission.UNKNOWN.value
    assert caps.terms["tos_prohibited"] is False


def test_known_sites_points_at_the_live_domain():
    from known_sites import KNOWN_SITES
    fj = [s for s in KNOWN_SITES if "Fanjiao" in s["name"]]
    assert fj and fj[0]["url"] == "https://www.fanjiao.co/"


def test_default_capture_uses_page_fetchs_guarded_session(monkeypatch):
    """Production path: page_fetch.api_capture_session (the guarded
    browser), watching only the three album calls; the adapter never
    sends an API request of its own."""
    import contextlib
    import page_fetch
    seen = {}

    class Page:
        def content(self):
            return RENDERED

    @contextlib.contextmanager
    def fake_session(url, url_pattern, **kw):
        seen["url"], seen["pattern"] = url, url_pattern
        yield Page(), _captured()

    monkeypatch.setattr(page_fetch, "api_capture_session", fake_session)
    a = fanjiao.FanjiaoSource(client=make_client("fanjiao"))
    assert a.get_series(ALBUM).title == "一目余生"
    assert seen == {"url": SHARE, "pattern": fanjiao.API_PATTERN}
