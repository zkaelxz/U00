"""
tests/test_sources_bilibili.py -- the BilibiliSource adapter: URL matching,
the b23.tv short-link hop and its capabilities. No network: the resolver
is faked or runs over a scripted transport.
"""
import pytest

from sources.adapters.bilibili import BilibiliDownloadError, BilibiliSource


class TestCanHandleAndNormalizeUrl:
    def test_bv_form(self):
        a = BilibiliSource()
        assert a.can_handle("https://www.bilibili.com/video/BV1xx411c7abcd")

    def test_av_form(self):
        a = BilibiliSource()
        assert a.can_handle("https://www.bilibili.com/video/av170001")

    def test_b23_short_link(self):
        a = BilibiliSource()
        assert a.can_handle("https://b23.tv/abc123")

    def test_query_parameters_url(self):
        a = BilibiliSource()
        assert a.can_handle("https://www.bilibili.com/video/BV1xx411c7abcd?spm_id_from=333.999")

    def test_non_bilibili_url_not_handled(self):
        a = BilibiliSource()
        assert not a.can_handle("https://www.youtube.com/watch?v=abc")

    def test_normalize_strips_tracking_params(self):
        a = BilibiliSource()
        normalized = a.normalize_url(
            "https://www.bilibili.com/video/BV1xx411c7abcd?spm_id_from=333.999&vd_source=abc")
        assert "spm_id_from" not in normalized and "vd_source" not in normalized
        assert "BV1xx411c7abcd" in normalized

    def test_normalize_keeps_explicit_part_selector(self):
        a = BilibiliSource()
        normalized = a.normalize_url(
            "https://www.bilibili.com/video/BV1yy411c7wxyz?p=2&spm_id_from=333.999")
        assert "p=2" in normalized
        assert "spm_id_from" not in normalized

    def test_normalize_resolves_b23_short_link(self):
        resolved = {"https://b23.tv/abc123": "https://www.bilibili.com/video/BV1xx411c7abcd"}
        a = BilibiliSource(url_resolver=lambda u: resolved[u])
        assert a.normalize_url("https://b23.tv/abc123") == \
            "https://www.bilibili.com/video/BV1xx411c7abcd"

    def test_real_url_resolver_routes_through_the_shared_paced_transport(self):
        """Step 28 gap 2: the real resolver used to make a bare
        `requests.head()` call, bypassing sources/http.py's shared
        SourceClient transport (and therefore its pacing) entirely."""
        from sources.http_transport import Response
        from tests.sources_helpers import ScriptedTransport, make_client
        short_url = "https://b23.tv/abc123"
        resolved_url = "https://www.bilibili.com/video/BV1xx411c7abcd"
        t = ScriptedTransport({short_url: Response(200, {}, b"", resolved_url)})
        a = BilibiliSource(client=make_client("bilibili", t))
        assert a.normalize_url(short_url) == resolved_url
        assert len(t.calls) == 1
        assert t.calls[0]["method"] == "HEAD" and t.calls[0]["url"] == short_url

    def test_short_link_to_another_site_is_refused(self):
        a = BilibiliSource(url_resolver=lambda u: "https://evil.example/video/BV1xx411c7abcd")
        with pytest.raises(BilibiliDownloadError):
            a.normalize_url("https://b23.tv/abc123")


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://www.bilibili.com/video/BV1xx411c7abcd")
        assert adapter is not None
        assert adapter.name == "bilibili"

    def test_capabilities_records_no_challenge_defeat(self):
        a = BilibiliSource()
        caps = a.capabilities()
        assert caps.technical["no_challenge_defeat"] is True
