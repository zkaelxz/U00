"""B-28 (proposed): db.save_lines opens a DEFERRED transaction, reads, then
writes. In WAL mode, if another connection commits between that read and the
first write, SQLite refuses the read->write upgrade with SQLITE_BUSY at once
(the 5 s busy timeout never applies), so the whole save fails with
"database is locked".

Found while diagnosing
tests/test_library_features.py::TestBulkSeriesTranslate::
test_translates_every_eligible_drama_with_its_own_saved_engine, where the
other writer is background_jobs' job-heartbeat thread (spinning because that
test patches time.sleep process-wide). The spin is a test bug; the fact that
one concurrent commit makes a translate job's save fail outright, instead of
waiting, is the product side, reproduced here deterministically.
"""
import sqlite3

import pytest

import core
from core import Line


@pytest.mark.xfail(strict=True, raises=sqlite3.OperationalError,
                   reason="B-28: db.save_lines' deferred BEGIN fails at once with "
                          "'database is locked' when another connection commits "
                          "between its SELECT and its first UPDATE")
def test_save_lines_survives_a_commit_between_its_read_and_its_write(isolated_db, monkeypatch):
    db = isolated_db
    did = db.create_drama(title_en="Race", media_type="audio_drama", content_mode="audio_drama")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句0")])
    lines = core.lines_from_rows(db.load_lines(did))
    lines[0].en = "hi"

    real_get_conn = db.get_conn

    class _Conn:
        """Passes everything through. Right after save_lines' own
        'SELECT id FROM lines' has been read, a second connection commits
        an unrelated write, the way the job-heartbeat thread, another job
        or a UI edit can in real use."""

        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *args):
            cur = self._c.execute(sql, *args)
            if sql.startswith("SELECT id FROM lines WHERE drama_id"):
                rows = cur.fetchall()
                # Short timeout, and a refusal is fine: once save_lines
                # holds the write lock up front (the fix), this other
                # writer is simply the one that has to wait.
                other = sqlite3.connect(db.DB_PATH, timeout=0.2)
                try:
                    other.execute("UPDATE dramas SET title_en = 'touched' WHERE id = ?", (did,))
                    other.commit()
                except sqlite3.OperationalError:
                    pass
                finally:
                    other.close()

                class _Rows:
                    def fetchall(self_inner):
                        return rows
                return _Rows()
            return cur

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(db, "get_conn", lambda: _Conn(real_get_conn()))
    db.save_lines(did, lines, fields=("en",))
    monkeypatch.setattr(db, "get_conn", real_get_conn)
    assert db.load_lines(did)[0]["en"] == "hi"
