"""
tests/test_applog.py -- applog.py's log-line keyword filter (Step 18
item 4) and its secret-redaction filter. tail()/get_logger() themselves are already exercised indirectly
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


class TestSecretRedaction:
    """Every record written to app.log is redacted: the message, its
    %-args, and an exc_info traceback (which repeats the raw exception
    text a caller may already have redacted in its own message)."""

    KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"

    def _log_text(self):
        import db
        for h in applog.get_logger().handlers:
            h.flush()
        with open(os.path.join(db.LIBRARY_DIR, "logs", "app.log"), encoding="utf-8") as f:
            return f.read()

    def test_message_args_and_traceback_are_redacted(self, isolated_db):
        logger = applog.get_logger()
        logger.warning(f"direct {self.KEY}")
        logger.warning("via args %s", self.KEY)
        try:
            raise RuntimeError(f"401 Unauthorized for key {self.KEY}")
        except RuntimeError:
            logger.error("hook failed: [redacted by caller]", exc_info=True)
        text = self._log_text()
        assert "direct" in text and "via args" in text
        assert "RuntimeError: 401 Unauthorized for key" in text   # traceback still written
        assert self.KEY not in text

    def test_a_record_without_secrets_is_unchanged(self, isolated_db):
        applog.get_logger().info("job %s finished in %.1fs", "abc", 2.5)
        assert "job abc finished in 2.5s" in self._log_text()
