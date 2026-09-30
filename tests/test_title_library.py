"""
tests/test_title_library.py -- tests for title_library.py's seed data
and search/dedup logic.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import title_library


class TestSeedKnownTitles:
    def test_seeds_all_entries_on_first_run(self, isolated_db):
        n = title_library.seed_known_titles(isolated_db)
        assert n == len(title_library.SEED_TITLES)
        assert len(isolated_db.list_known_titles()) == len(title_library.SEED_TITLES)

    def test_seeding_twice_does_not_duplicate(self, isolated_db):
        title_library.seed_known_titles(isolated_db)
        n_second_run = title_library.seed_known_titles(isolated_db)
        assert n_second_run == 0
        assert len(isolated_db.list_known_titles()) == len(title_library.SEED_TITLES)

    def test_seed_entries_have_required_fields(self):
        for entry in title_library.SEED_TITLES:
            assert entry.get("title_original")
            assert entry.get("language") in ("zh", "ja", "ko")
            assert entry.get("media_type")


class TestTranslateQueryToZh:
    def test_already_chinese_query_unchanged(self):
        class ShouldNotBeCalled:
            supports_reference = True
            def translate_batch(self, *a, **k):
                raise AssertionError("should not translate an already-Chinese query")

        result = title_library.translate_query_to_zh("你好世界", ShouldNotBeCalled())
        assert result == "你好世界"

    def test_no_engine_returns_query_unchanged(self):
        result = title_library.translate_query_to_zh("hello world", None)
        assert result == "hello world"

    def test_english_query_gets_translated(self):
        class MockEngine:
            supports_reference = True
            def translate_batch(self, texts, context):
                return ["你好世界"]

        result = title_library.translate_query_to_zh("hello world", MockEngine())
        assert result == "你好世界"


class TestSearchUrlFallback:
    def test_produces_valid_url_encoded_query(self):
        url = title_library.search_url_fallback("test query")
        assert url.startswith("https://baihehub.com/search")
        assert "test" in url

    def test_handles_chinese_query(self):
        url = title_library.search_url_fallback("百合")
        assert "baihehub.com" in url


class TestSearchLinkGeneration:
    """Search links are generated deterministically from a fixed site list --
    they can't invent a title that doesn't exist, which is the whole point
    of doing it this way rather than asking a model to 'find' things."""

    def test_generates_links_for_a_query(self):
        import known_sites
        links = known_sites.build_search_links("女将军和长公主")
        assert len(links) > 5

    def test_first_link_is_a_general_search(self):
        import known_sites
        assert known_sites.build_search_links("x")[0]["site"] == "General web search"

    def test_all_links_are_https_urls(self):
        import known_sites
        for l in known_sites.build_search_links("test"):
            assert l["url"].startswith("https://")

    def test_query_is_url_encoded(self):
        import known_sites
        links = known_sites.build_search_links("女将军")
        assert all(" " not in l["url"] for l in links)

    def test_filtering_by_content_type_narrows_results(self):
        import known_sites
        all_links = known_sites.build_search_links("x")
        audio = known_sites.build_search_links("x", content_type="audio_drama")
        assert len(audio) < len(all_links)

    def test_empty_query_returns_nothing(self):
        import known_sites
        assert known_sites.build_search_links("") == []
        assert known_sites.build_search_links("   ") == []

    def test_every_link_has_required_fields(self):
        import known_sites
        for l in known_sites.build_search_links("test"):
            for key in ("site", "url", "kind", "note"):
                assert key in l

    def test_jjwxc_tag_url_encodes_the_tag(self):
        import known_sites
        url = known_sites.jjwxc_tag_url("百合")
        assert url.startswith("https://www.jjwxc.net/") and "百合" not in url

    def test_jjwxc_tag_link_baihe_only_by_default(self):
        import known_sites
        links = known_sites.build_search_links("x")
        tag = [l for l in links if l["site"].startswith("JJWXC (百合")]
        assert len(tag) == 1 and tag[0]["kind"] == "direct"
        assert tag[0]["url"] == known_sites.jjwxc_tag_url("百合")
        assert "百合" in links[0]["url"] or "%E7%99%BE%E5%90%88" in links[0]["url"]

    def test_all_genres_drops_hint_and_tag_restriction(self):
        import known_sites
        links = known_sites.build_search_links("x", baihe_only=False)
        assert all("%E7%99%BE%E5%90%88" not in l["url"] for l in links)
        listing = [l for l in links if l["site"] == "JJWXC (all novels)"]
        assert listing and "bq=" not in listing[0]["url"]

    def test_all_genres_tag_is_url_quoted(self):
        import known_sites
        links = known_sites.build_search_links("x", baihe_only=False, tag="a&b 言情")
        url = [l for l in links if "tag)" in l["site"]][0]["url"]
        assert url == "https://www.jjwxc.net/bookbase.php?bq=a%26b+%E8%A8%80%E6%83%85&page=1"


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class TestSearchBaihehub:
    """Step 25f item 2: `data.get("data") or ... or data if isinstance(data,
    list) else []` parsed as `(... or data) if isinstance(data, list) else
    []`, so a dict response always gave [] and a list response raised on
    .get -- search could never return a result."""

    ITEM = {"documentId": "abc123", "name": "流浪的公主", "intro": "简介"}

    def _search(self, monkeypatch, payload_for):
        import requests
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append((url, params, timeout))
            return _FakeResponse(payload_for(url))
        monkeypatch.setattr(requests, "get", fake_get)
        return title_library.search_baihehub("公主"), calls

    def test_dict_response_returns_results(self, monkeypatch):
        results, _ = self._search(
            monkeypatch,
            lambda url: {"data": [self.ITEM], "meta": {}} if url.endswith("/audio-dramas") else {"data": []})
        assert results == [{"title": "流浪的公主",
                            "url": "https://baihehub.com/audio-dramas/abc123",
                            "snippet": "简介"}]

    def test_list_response_returns_results(self, monkeypatch):
        results, _ = self._search(
            monkeypatch, lambda url: [self.ITEM] if url.endswith("/manhuas") else [])
        assert [r["url"] for r in results] == ["https://baihehub.com/manhuas/abc123"]

    def test_books_use_their_title_field(self, monkeypatch):
        book = {"documentId": "b1", "title": "谁见云墨染清舒", "description": "d"}
        results, _ = self._search(
            monkeypatch, lambda url: {"data": [book]} if url.endswith("/books") else {"data": []})
        assert results[0]["title"] == "谁见云墨染清舒"
        assert results[0]["url"] == "https://baihehub.com/books/b1"

    def test_no_results_returns_none(self, monkeypatch):
        results, _ = self._search(monkeypatch, lambda url: {"data": []})
        assert results is None

    def test_query_goes_through_params_so_it_gets_url_encoded(self, monkeypatch):
        _, calls = self._search(monkeypatch, lambda url: {"data": []})
        assert calls
        for url, params, timeout in calls:
            assert "公主" not in url
            assert "公主" in params.values()
            assert timeout

    def test_http_error_falls_back_to_none(self, monkeypatch):
        import requests

        def fake_get(*a, **k):
            raise requests.ConnectionError("offline")
        monkeypatch.setattr(requests, "get", fake_get)
        assert title_library.search_baihehub("公主") is None

    def test_non_200_is_skipped(self, monkeypatch):
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"data": [self.ITEM]}, 404))
        assert title_library.search_baihehub("公主") is None
