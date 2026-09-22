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
