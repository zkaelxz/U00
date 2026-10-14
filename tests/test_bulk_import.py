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
