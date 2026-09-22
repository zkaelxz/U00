"""
tests/test_bulk_import.py -- tests for bulk_import.py's pagination
URL building and library-commit logic (not the LLM extraction itself,
which needs a real API call).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bulk_import


class TestPaginateUrls:
    def test_builds_correct_number_of_urls(self):
        urls = bulk_import.paginate_urls("https://example.com/tag?p={page}", 1, 5)
        assert len(urls) == 5

    def test_page_numbers_substituted_correctly(self):
        urls = bulk_import.paginate_urls("https://example.com/tag?p={page}", 1, 3)
        assert urls == [
            "https://example.com/tag?p=1",
            "https://example.com/tag?p=2",
            "https://example.com/tag?p=3",
        ]

    def test_single_page_range(self):
        urls = bulk_import.paginate_urls("https://x.com/{page}", 5, 5)
        assert urls == ["https://x.com/5"]


class TestCommitEntriesToLibrary:
    def test_commits_entries_with_correct_media_type(self, isolated_db):
        entries = [
            {"title": "Novel A", "author": "Author A", "has_audio_drama": False,
             "tags": "fantasy", "source_url": "http://x.com", "language": "zh"},
            {"title": "Drama B", "author": "Author B", "has_audio_drama": True,
             "tags": "romance", "source_url": "http://x.com", "language": "zh"},
        ]
        n = bulk_import.commit_entries_to_library(isolated_db, entries, source_name="test_source")
        assert n == 2
        results = isolated_db.list_known_titles(source_name="test_source")
        novel = next(r for r in results if r["title_original"] == "Novel A")
        drama = next(r for r in results if r["title_original"] == "Drama B")
        assert novel["media_type"] == "novel"
        assert drama["media_type"] == "audio_drama"

    def test_empty_entries_list_commits_nothing(self, isolated_db):
        n = bulk_import.commit_entries_to_library(isolated_db, [], source_name="test")
        assert n == 0
        assert isolated_db.list_known_titles() == []

    def test_missing_optional_fields_do_not_crash(self, isolated_db):
        entries = [{"title": "Minimal Entry"}]  # no author/tags/has_audio_drama
        n = bulk_import.commit_entries_to_library(isolated_db, entries, source_name="test")
        assert n == 1
