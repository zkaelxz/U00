"""B-25: adapter/front-door URL routing must match the parsed host, not a
substring of the whole URL (no network; resolver is a recorder)."""
import pytest

from sources.adapters.bilibili import BilibiliSource
from sources.adapters.bilibili_manga import BilibiliMangaSource
from sources.adapters.toonkor import ToonkorSource
from sources.front_door import is_video_url

EVIL = [
    "http://169.254.169.254/?b23.tv/",
    "http://169.254.169.254/#b23.tv/",
    "https://evil.com/bilibili.com/video/BV1xx411c7mD",
    "https://evilbilibili.com/video/BV1xx411c7mD",
    "https://bilibili.com.evil.com/video/BV1xx411c7mD",
    "https://bilibili.com@evil.com/video/BV1xx411c7mD",
    "https://evil.com/?u=https://www.youtube.com/watch?v=abc",
    "https://evilb23.tv/x",
    "https://b23.tv.evil.com/x",
    "ftp://www.bilibili.com/video/BV1xx411c7mD",
    "javascript:b23.tv/",
]
GOOD = [
    "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
    "http://bilibili.com/video/av170001",
    "https://m.bilibili.com/video/BV1xx411c7mD",
    "https://space.bilibili.com/bangumi/play/ss1",
    "https://B23.TV/abc123",
    "https://b23.tv/abc123",
]


@pytest.mark.parametrize("url", EVIL)
def test_evil_urls_do_not_match_bilibili_or_video_routing(url):
    assert not BilibiliSource.matches_url(url)
    assert not is_video_url(url)


@pytest.mark.parametrize("url", GOOD)
def test_real_bilibili_urls_still_match(url):
    assert BilibiliSource.matches_url(url)
    assert is_video_url(url)


def test_normalize_url_never_resolves_a_non_b23_host():
    calls = []
    a = BilibiliSource(url_resolver=lambda u: calls.append(u) or u)
    a.normalize_url("http://169.254.169.254/?b23.tv/")
    assert calls == []
    a.normalize_url("https://b23.tv/abc")
    assert calls == ["https://b23.tv/abc"]


def test_other_adapters_share_the_host_check():
    assert BilibiliMangaSource.matches_url("https://manga.bilibili.com/detail/mc1")
    assert not BilibiliMangaSource.matches_url("http://10.0.0.1/?manga.bilibili.com/")
    assert ToonkorSource.matches_url("https://toonkor123.com/abc.html")
    assert not ToonkorSource.matches_url("https://toonkor1.com.evil.com/abc.html")


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc", "https://youtu.be/abc",
    "https://m.youtube.com/shorts/abc", "https://vimeo.com/123",
])
def test_other_video_hosts_still_route(url):
    assert is_video_url(url)
