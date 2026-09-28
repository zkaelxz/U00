"""
tests/test_sources_missevan.py -- Step 94: the MissEvan/MaoerFM
audio-drama adapter. Everything here is mocked/recorded fixtures, trimmed
from real responses captured live against missevan.com while building the
adapter (module docstring on sources/adapters/missevan.py) -- no live
network call, matching this repo's convention.

Covers the roadmap's explicit exit conditions:
  - search() / get_series() / get_chapters() against a real trimmed
    fixture shape.
  - get_audio_url() resolves a free episode to a real playable URL.
  - get_audio_url() reports a clear, specific "needs sign-in" status
    (ContentHidden/PURCHASE_REQUIRED) for a paywalled episode with no
    session -- not a raw failure -- and the real, confirmed shape of that
    paywall (success:true, soundurl/soundurl_128 both null, need_pay set).
"""
import json

import pytest

missevan = __import__("sources.adapters.missevan", fromlist=["_"])

from sources.models import AudioRef, ChapterInfo, ContentHidden, FailureReason

from .sources_helpers import FakeClock, ScriptedTransport, make_client

SEARCH_URL = "https://www.missevan.com/dramaapi/search?s=%E9%87%8D%E7%94%9F&p=1"
DRAMA_URL = "https://www.missevan.com/dramaapi/getdrama?drama_id=32"
PAID_DRAMA_URL = "https://www.missevan.com/dramaapi/getdrama?drama_id=22602"
FREE_SOUND_URL = "https://www.missevan.com/sound/getsound?soundid=111645"
PAID_SOUND_URL = "https://www.missevan.com/sound/getsound?soundid=1258656"

# Trimmed, real shape (module docstring) -- not the full payload.
SEARCH_JSON = {
    "success": True,
    "info": {
        "Datas": [
            {"id": 43922, "name": "重生之将门毒后 第一季", "author": "千山茶客",
             "cover": "https://static.maoercdn.com/dramacovers/x.jpg", "pay_type": 2},
        ],
        "pagination": {"p": 1, "pagesize": 30, "count": 1, "maxpage": 1},
    },
}

FREE_DRAMA_JSON = {
    "success": True,
    "info": {
        "drama": {"id": 32, "name": "重生之国民女神", "author": "清楼",
                  "cover": "https://static.maoercdn.com/dramacovers/y.jpg",
                  "abstract": "", "need_pay": 0},
        "episodes": {
            "ft": [],
            "music": [{"id": 999, "name": "OST 1", "sound_id": 500000, "pay_type": 0,
                      "vip": 0, "need_pay": 0}],
            "episode": [
                {"id": 85, "name": "预告", "sound_id": 95398, "pay_type": 0, "vip": 0, "need_pay": 0},
                {"id": 12955, "name": "第一期", "sound_id": 111645, "pay_type": 0, "vip": 0,
                 "need_pay": 0},
            ],
        },
    },
}

PAID_DRAMA_JSON = {
    "success": True,
    "info": {
        "drama": {"id": 22602, "name": "魔道祖师 第三季", "author": "墨香铜臭",
                  "cover": "", "abstract": "付费广播剧", "need_pay": 1},
        "episodes": {
            "ft": [], "music": [],
            "episode": [
                {"id": 364686, "name": "第一集", "sound_id": 1258656, "pay_type": 2, "vip": 0,
                 "need_pay": 1},
            ],
        },
    },
}

# Real, confirmed shape (module docstring): signed, expiring HLS manifests.
FREE_SOUND_JSON = {
    "success": True,
    "info": {"sound": {
        "id": 111645, "soundstr": "第一期",
        "soundurl": "https://www.missevan.com/x/sound/hls.m3u8?sound_id=111645&quality_id=64"
                    "&expire_time=1790562762&token=aaa",
        "soundurl_128": "https://www.missevan.com/x/sound/hls.m3u8?sound_id=111645"
                        "&quality_id=128&expire_time=1790562762&token=bbb",
        "videourl": "", "pay_type": 0,
    }},
}

# Real, confirmed shape (module docstring): success:true, both URLs null,
# extra pay fields present that the free response doesn't carry at all.
PAID_SOUND_JSON_LOCKED = {
    "success": True,
    "info": {"sound": {
        "id": 1258656, "soundstr": "第一集",
        "soundurl": None, "soundurl_128": None, "videourl": "",
        "pay_type": 2, "need_pay": 1, "price": 399, "limit_type": 1,
    }},
}

PAID_SOUND_JSON_UNLOCKED = {
    "success": True,
    "info": {"sound": {
        "id": 1258656, "soundstr": "第一集",
        "soundurl": "https://www.missevan.com/x/sound/hls.m3u8?sound_id=1258656&quality_id=64"
                    "&expire_time=1790562762&token=ccc",
        "soundurl_128": "https://www.missevan.com/x/sound/hls.m3u8?sound_id=1258656"
                        "&quality_id=128&expire_time=1790562762&token=ddd",
        "videourl": "", "pay_type": 2, "need_pay": 1,
    }},
}


def _json_resp(obj, url=""):
    from sources.http import Response
    return Response(200, {"content-type": "application/json; charset=utf-8"},
                    json.dumps(obj).encode("utf-8"), url)


def _adapter(routes, rendered_fetch=None, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("missevan", t, clock, max_retries=kw.pop("max_retries", 0))
    return missevan.MissEvanSource(client=client, rendered_fetch=rendered_fetch, **kw), t


def _chapter(sound_id="111645", title="第一期"):
    return ChapterInfo("missevan", "32", sound_id, title,
                       f"https://www.missevan.com/sound/{sound_id}")


class TestSearch:
    def test_parses_real_search_shape(self, isolated_db):
        a, t = _adapter({SEARCH_URL: _json_resp(SEARCH_JSON)})
        results = a.search("重生", page=1)
        assert len(results) == 1
        assert results[0].series_id == "43922"
        assert results[0].title == "重生之将门毒后 第一季"
        assert results[0].url == "https://www.missevan.com/mdrama/43922"
        assert results[0].extra["author"] == "千山茶客"

    def test_the_real_query_param_is_s_not_keyword(self, isolated_db):
        """A real live check while building this adapter: `?keyword=` 200s
        but always reports zero results, `?s=` is the real param -- assert
        the adapter calls the one that actually works."""
        a, t = _adapter({SEARCH_URL: _json_resp(SEARCH_JSON)})
        a.search("重生", page=1)
        assert t.urls() == [SEARCH_URL]

    def test_no_results_returns_an_empty_list_not_an_error(self, isolated_db):
        a, t = _adapter({SEARCH_URL: _json_resp({"success": False, "code": 1, "info": "none"})})
        assert a.search("重生", page=1) == []


class TestGetSeriesAndChapters:
    def test_get_series_parses_the_drama_object(self, isolated_db):
        a, t = _adapter({DRAMA_URL: _json_resp(FREE_DRAMA_JSON)})
        info = a.get_series("32")
        assert info.title == "重生之国民女神"
        assert info.authors == ["清楼"]
        assert info.content_type == "audio_drama"

    def test_get_chapters_includes_episode_and_ft_but_not_music(self, isolated_db):
        a, t = _adapter({DRAMA_URL: _json_resp(FREE_DRAMA_JSON)})
        chapters = a.get_chapters("32")
        sound_ids = [c.chapter_id for c in chapters]
        assert sound_ids == ["95398", "111645"]
        assert "500000" not in sound_ids  # the music-only track

    def test_layout_changed_when_no_episodes_at_all(self, isolated_db):
        empty = {"success": True, "info": {"drama": {}, "episodes": {"ft": [], "episode": [],
                                                                     "music": []}}}
        a, t = _adapter({DRAMA_URL: _json_resp(empty)})
        with pytest.raises(missevan.LayoutChanged):
            a.get_chapters("32")


class TestGetAudioUrl:
    def test_resolves_a_free_episode_to_a_real_playable_url(self, isolated_db):
        a, t = _adapter({FREE_SOUND_URL: _json_resp(FREE_SOUND_JSON)})
        ref = a.get_audio_url(_chapter())
        assert isinstance(ref, AudioRef)
        assert ref.url == FREE_SOUND_JSON["info"]["sound"]["soundurl_128"]
        assert ref.format == "hls"

    def test_prefers_soundurl_128_over_soundurl(self, isolated_db):
        a, t = _adapter({FREE_SOUND_URL: _json_resp(FREE_SOUND_JSON)})
        ref = a.get_audio_url(_chapter())
        assert "quality_id=128" in ref.url

    def test_direct_file_url_is_tagged_direct_not_hls(self, isolated_db):
        direct = json.loads(json.dumps(FREE_SOUND_JSON))
        direct["info"]["sound"]["soundurl_128"] = "https://static.maoercdn.com/sounds/x.mp3"
        a, t = _adapter({FREE_SOUND_URL: _json_resp(direct)})
        ref = a.get_audio_url(_chapter())
        assert ref.format == "direct"

    def test_paywalled_episode_with_no_session_raises_a_clear_purchase_required_error(
            self, isolated_db):
        a, t = _adapter({PAID_SOUND_URL: _json_resp(PAID_SOUND_JSON_LOCKED)})
        with pytest.raises(ContentHidden) as e:
            a.get_audio_url(_chapter(sound_id="1258656", title="第一集"))
        assert e.value.reason == FailureReason.PURCHASE_REQUIRED
        assert "paid/VIP" in str(e.value)
        assert "第一集" in str(e.value)

    def test_paywalled_episode_never_returns_a_broken_or_empty_ref(self, isolated_db):
        """The real, confirmed shape (module docstring): HTTP 200 /
        success:true with null URLs. Must not be mistaken for success."""
        a, t = _adapter({PAID_SOUND_URL: _json_resp(PAID_SOUND_JSON_LOCKED)})
        with pytest.raises(ContentHidden):
            a.get_audio_url(_chapter(sound_id="1258656"))

    def test_falls_back_to_the_authenticated_profile_when_one_exists(self, isolated_db):
        """Simulates the module docstring's unverified authenticated path:
        an injected rendered_fetch stands in for a signed-in browser
        profile reading the same getsound URL and finding it unlocked."""
        calls = []

        def rendered_fetch(url):
            calls.append(url)
            return f"<html><body><pre>{json.dumps(PAID_SOUND_JSON_UNLOCKED)}</pre></body></html>", ""

        a, t = _adapter({PAID_SOUND_URL: _json_resp(PAID_SOUND_JSON_LOCKED)},
                        rendered_fetch=rendered_fetch)
        ref = a.get_audio_url(_chapter(sound_id="1258656", title="第一集"))
        assert isinstance(ref, AudioRef)
        assert calls == [PAID_SOUND_URL]

    def test_still_raises_purchase_required_if_the_authenticated_profile_is_also_locked(
            self, isolated_db):
        def rendered_fetch(url):
            return f"<pre>{json.dumps(PAID_SOUND_JSON_LOCKED)}</pre>", ""

        a, t = _adapter({PAID_SOUND_URL: _json_resp(PAID_SOUND_JSON_LOCKED)},
                        rendered_fetch=rendered_fetch)
        with pytest.raises(ContentHidden) as e:
            a.get_audio_url(_chapter(sound_id="1258656"))
        assert e.value.reason == FailureReason.PURCHASE_REQUIRED

    def test_layout_changed_on_an_unrecognized_response_shape(self, isolated_db):
        a, t = _adapter({FREE_SOUND_URL: _json_resp({"success": False})})
        with pytest.raises(missevan.LayoutChanged):
            a.get_audio_url(_chapter())


class TestParseUrl:
    def test_sound_url_is_a_chapter(self):
        a, t = _adapter({})
        kind, chapter = a.parse_url("https://www.missevan.com/sound/111645")
        assert kind == "chapter"
        assert chapter.chapter_id == "111645"

    def test_mdrama_url_is_a_series(self):
        a, t = _adapter({})
        assert a.parse_url("https://www.missevan.com/mdrama/32") == ("series", "32")

    def test_mdrama_drama_url_is_also_a_series(self):
        a, t = _adapter({})
        assert a.parse_url("https://www.missevan.com/mdrama/drama/22602") == ("series", "22602")

    def test_an_unrelated_url_does_not_match(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestCapabilities:
    def test_records_the_confirmed_tos_restriction(self):
        a, t = _adapter({})
        caps = a.capabilities()
        assert caps.terms["tos_prohibited"] is True
        assert "4.2.11" in caps.terms["clause"]
        assert caps.automation_permission == "EXPLICITLY_RESTRICTED"

    def test_records_the_hls_manifest_and_paywall_findings(self):
        a, t = _adapter({})
        caps = a.capabilities()
        assert "hls" in caps.technical["hls_manifest"].lower()
        assert caps.content_access_status == "AUDIO"

    def test_static_http_tier_is_marked_tested_and_ok(self):
        a, t = _adapter({})
        caps = a.capabilities()
        assert caps.tiers["STATIC_HTTP"].tested is True
        assert caps.tiers["STATIC_HTTP"].ok is True


class TestRegistration:
    def test_registered_and_found_by_sound_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://www.missevan.com/sound/111645")
        assert adapter is not None
        assert adapter.name == "missevan"

    def test_registered_and_found_by_drama_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://www.missevan.com/mdrama/32")
        assert adapter is not None
        assert adapter.name == "missevan"
