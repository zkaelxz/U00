"""The one-time rescoring of lines that shared a segment's Whisper score."""
import core
from segment_splitting import encode_line_words
from services import confidence_backfill_service as svc


def _line(idx, text, prob, conf=0.5):
    words = [{"word": text, "start": idx + 0.1, "end": idx + 0.9, "probability": prob}] if prob else None
    return core.Line(idx=idx, start=idx, end=idx + 1, zh=text, confidence=conf,
                     word_timings=encode_line_words(text, words) if words else None)


def _scores(isolated_db, did):
    return [r["confidence"] for r in isolated_db.load_lines(did)]


def test_lines_sharing_a_segment_score_get_their_own_once(isolated_db):
    did = isolated_db.create_drama(title_en="A")
    isolated_db.save_lines(did, [_line(0, "你好", 0.9), _line(1, "再见", 0.3), _line(2, "谢谢", 0.6, conf=0.7)])
    assert svc.run_once() == 2
    assert _scores(isolated_db, did) == [0.75, 0.25, 0.7]   # 0.5 x 0.9/0.6 and 0.5 x 0.3/0.6
    assert svc.run_once() == 0
    assert _scores(isolated_db, did) == [0.75, 0.25, 0.7]


def test_a_run_without_stored_probabilities_keeps_its_score(isolated_db):
    did = isolated_db.create_drama(title_en="A")
    isolated_db.save_lines(did, [_line(0, "你好", None), _line(1, "再见", None)])
    assert svc.run_once() == 0
    assert _scores(isolated_db, did) == [0.5, 0.5]


def test_equal_scores_in_two_titles_are_not_one_run(isolated_db):
    first = isolated_db.create_drama(title_en="A")
    second = isolated_db.create_drama(title_en="B")
    isolated_db.save_lines(first, [_line(0, "你好", 0.9)])
    isolated_db.save_lines(second, [_line(0, "再见", 0.3)])
    assert svc.run_once() == 0
    assert _scores(isolated_db, first) == [0.5] and _scores(isolated_db, second) == [0.5]


def test_a_line_without_times_does_not_stop_the_pass(isolated_db):
    did = isolated_db.create_drama(title_en="A")
    isolated_db.save_lines(did, [_line(0, "你好", 0.9), _line(1, "再见", 0.3)])
    import contextlib
    import sqlite3
    with contextlib.closing(sqlite3.connect(isolated_db.DB_PATH)) as conn:
        conn.execute("UPDATE lines SET start = NULL WHERE idx = 0")
        conn.commit()
    assert svc.run_once() == 0
    assert _scores(isolated_db, did) == [0.5, 0.5]


def test_a_line_without_a_score_ends_a_run(isolated_db):
    did = isolated_db.create_drama(title_en="A")
    isolated_db.save_lines(did, [_line(0, "你好", 0.9), _line(1, "再见", 0.3, conf=None), _line(2, "谢谢", 0.6)])
    assert svc.run_once() == 0


def test_a_title_that_cannot_be_read_is_skipped_and_the_pass_finishes_once(isolated_db):
    import contextlib
    import sqlite3
    bad = isolated_db.create_drama(title_en="A")
    good = isolated_db.create_drama(title_en="B")
    isolated_db.save_lines(bad, [_line(0, "你好", 0.9), _line(1, "再见", 0.3)])
    isolated_db.save_lines(good, [_line(0, "你好", 0.9), _line(1, "再见", 0.3)])
    with contextlib.closing(sqlite3.connect(isolated_db.DB_PATH)) as conn:
        conn.execute("UPDATE lines SET start = 'x' WHERE drama_id = ? AND idx = 0", (bad,))
        conn.commit()
    assert svc.run_once() == 2
    assert _scores(isolated_db, bad) == [0.5, 0.5] and _scores(isolated_db, good) == [0.75, 0.25]
    assert svc.run_once() == 0


def test_startup_does_not_wait_for_the_pass(isolated_db, monkeypatch):
    import threading
    import api.__main__ as entry
    release = threading.Event()
    monkeypatch.setattr(svc, "run_once", lambda: release.wait(5))
    entry._open_library(isolated_db)
    assert not svc.wait(0)
    release.set()
    assert svc.wait(5)


def test_the_background_pass_scores_old_lines_once(isolated_db):
    did = isolated_db.create_drama(title_en="A")
    isolated_db.save_lines(did, [_line(0, "你好", 0.9), _line(1, "再见", 0.3)])
    svc.start_in_background()
    assert svc.wait(5)
    assert _scores(isolated_db, did) == [0.75, 0.25]
    svc.start_in_background()
    assert svc.wait(5)
    assert _scores(isolated_db, did) == [0.75, 0.25]


def test_a_failed_background_pass_is_logged_and_left_for_the_next_start(isolated_db, monkeypatch):
    import applog

    def broken():
        raise RuntimeError("boom")
    logged = []
    monkeypatch.setattr(svc, "run_once", broken)
    monkeypatch.setattr(applog.get_logger(), "warning", lambda msg, **kw: logged.append((msg, kw)))
    svc.start_in_background()
    assert svc.wait(5)
    assert logged == [("Line scores were not rescored", {"exc_info": True})]
    assert not isolated_db.get_app_setting("backfills.per_line_confidence")


def test_a_restored_backup_from_before_the_pass_gets_its_lines_scored(isolated_db):
    from tests.test_library_admin_service import _backup_bytes, _restore
    did = isolated_db.create_drama(title_en="A")
    isolated_db.save_lines(did, [_line(0, "你好", 0.9), _line(1, "再见", 0.3)])
    data = _backup_bytes()
    assert svc.run_once() == 2
    _restore(data)
    assert svc.wait(5)
    assert _scores(isolated_db, did) == [0.75, 0.25]
