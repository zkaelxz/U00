"""The closing line of a chapter capture is written to the app log, cleaned, and only for a title
the caller may send into."""
import pytest

import applog
import db
import page_server
from tests.test_page_server import _post


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, fmt, *args):
        self.lines.append(fmt % args)


@pytest.fixture
def log(monkeypatch):
    fake = _Log()
    monkeypatch.setattr(applog, "get_logger", lambda: fake)
    return fake


@pytest.fixture
def token(isolated_db):
    return page_server.load_or_create_token()


def test_the_summary_line_reaches_the_app_log_with_the_title_id(token, log):
    did = db.create_drama(title_zh="남편", title_en="Husband", media_type="manhwa")
    h = _post(token, {"drama_id": did, "summary": "Saved 69 of 69 pages; typeset 56 of 69. Took 5:19."},
              path="/capture-summary")
    assert h.status == 200 and h.payload == {"logged": True}
    assert log.lines == [f"Extension capture (title {did}): Saved 69 of 69 pages; typeset 56 of 69. Took 5:19."]
    assert "Husband" not in log.lines[0]


def test_paths_and_link_queries_are_removed_from_the_logged_line(token, log):
    _post(token, {"drama_id": 1, "summary": "failed: C:\\Users\\Alex\\x.png at https://a.example/p?token=abc"},
          path="/capture-summary")
    assert "Alex" not in log.lines[0] and "token=abc" not in log.lines[0]


@pytest.mark.parametrize("body", [{}, {"summary": ""}, {"summary": 5}, {"summary": "  "}])
def test_a_missing_or_empty_summary_is_refused(token, log, body):
    assert _post(token, body, path="/capture-summary").status == 400
    assert log.lines == []


def test_a_long_line_is_cut(token, log):
    _post(token, {"summary": "x" * 5000}, path="/capture-summary")
    assert len(log.lines[0]) < 2100
