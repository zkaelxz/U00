"""
tests/test_applog.py -- applog.py's log-line keyword filter (Step 18
item 4). tail()/get_logger() themselves are already exercised indirectly
via tests/test_background_jobs.py and tests/test_word_align.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import applog


class TestFilterLines:
    def test_blank_keyword_returns_every_line_unchanged(self):
        lines = ["one", "two", "three"]
        assert applog.filter_lines(lines, "") == lines
        assert applog.filter_lines(lines) == lines

    def test_keeps_only_matching_lines(self):
        lines = ["INFO starting job", "ERROR job failed", "INFO job done"]
        assert applog.filter_lines(lines, "ERROR") == ["ERROR job failed"]

    def test_matches_case_insensitively(self):
        lines = ["ERROR something broke"]
        assert applog.filter_lines(lines, "error") == lines

    def test_matches_a_substring_like_a_drama_title(self):
        lines = ["translating Some Drama Title", "translating Another One"]
        assert applog.filter_lines(lines, "Some Drama") == ["translating Some Drama Title"]

    def test_no_match_returns_empty_list(self):
        assert applog.filter_lines(["one", "two"], "nope") == []

    def test_whitespace_only_keyword_is_treated_as_blank(self):
        lines = ["one", "two"]
        assert applog.filter_lines(lines, "   ") == lines
