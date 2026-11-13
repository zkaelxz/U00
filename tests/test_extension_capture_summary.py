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


def test_capture_lines_are_kept_per_title_newest_five_and_listed_for_viewers(isolated_db):
    from services import extension_bridge_service as svc, library_service
    did = isolated_db.create_drama(title_en="T")
    other = isolated_db.create_drama(title_en="U")
    for n in range(7):
        svc.record_capture_summary({"drama_id": did, "summary": f"Saved {n} pages."})
    svc.record_capture_summary({"drama_id": other, "summary": "Other title."})
    items = library_service.list_capture_notes(did)
    assert [i["summary"] for i in items] == [f"Saved {n} pages." for n in (6, 5, 4, 3, 2)]
    assert items[0]["created_at"].endswith("Z")
    assert [i["summary"] for i in library_service.list_capture_notes(other)] == ["Other title."]


def test_capture_line_for_a_missing_title_is_logged_but_not_stored(isolated_db):
    from services import extension_bridge_service as svc
    assert svc.record_capture_summary({"drama_id": 4242, "summary": "Saved."}) == {"logged": True}
    assert isolated_db.list_capture_notes(4242) == []


def test_capture_lines_go_with_a_deleted_title(isolated_db):
    from services import extension_bridge_service as svc
    did = isolated_db.create_drama(title_en="T")
    svc.record_capture_summary({"drama_id": did, "summary": "Saved."})
    with isolated_db.get_conn() as conn:
        conn.execute("DELETE FROM dramas WHERE id = ?", (did,))
    assert isolated_db.list_capture_notes(did) == []


def test_stored_note_keeps_no_token_like_path_segment(isolated_db):
    from services import extension_bridge_service as svc
    did = isolated_db.create_drama(title_en="T")
    svc.record_capture_summary({"drama_id": did, "summary":
        "Stopped: https://site.example/read/AbCdEf0123456789XyZq9876/page?x=1 refused."})
    [note] = isolated_db.list_capture_notes(did)
    assert "AbCdEf0123456789XyZq9876" not in note["summary"]
    assert "site.example" in note["summary"]


def test_a_failing_note_write_does_not_fail_the_capture_summary(isolated_db, monkeypatch):
    from services import extension_bridge_service as svc
    did = isolated_db.create_drama(title_en="T")

    def broken(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(isolated_db, "add_capture_note", broken)
    assert svc.record_capture_summary({"drama_id": did, "summary": "Saved."}) == {"logged": True}
