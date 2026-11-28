"""services/library_home_service.py: the Library's Running line."""
import db
from core import Line
from services import library_home_service as home, queue_service


def _title(name, media_type="streamer_vod", **kw):
    did = db.create_drama(title_en=name, title_zh="题", media_type=media_type, **kw)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello")])
    return did


def _queue(monkeypatch, items):
    full = [{"id": n, "drama_id": d, "step": step, "status": status, "job_id": None, "reason": reason,
             "started_at": None} for n, (d, step, status, reason) in enumerate(items, start=1)]
    monkeypatch.setattr(queue_service, "get_queue", lambda principal: {"items": full, "can_control": True})


def test_running_lists_the_running_step_before_waiting_ones(isolated_db, monkeypatch):
    a, b = _title("A"), _title("B")
    _queue(monkeypatch, [(a, "export", "waiting", None), (b, "translate", "running", None)])
    out = home.get_home()
    assert [(e["drama_id"], e["status"]) for e in out["running"]] == [(b, "running"), (a, "waiting")]


def test_a_failed_step_is_not_on_the_running_line(isolated_db, monkeypatch):
    did = _title("Broken")
    _queue(monkeypatch, [(did, "translate", "failed", "key refused")])
    assert home.get_home()["running"] == []


def test_the_running_line_is_cut_but_its_count_is_whole(isolated_db, monkeypatch):
    ids = [_title(f"T{n}") for n in range(home.SHELF_LIMIT + 2)]
    _queue(monkeypatch, [(d, "translate", "waiting", None) for d in ids])
    out = home.get_home()
    assert len(out["running"]) == home.SHELF_LIMIT and out["running_count"] == home.SHELF_LIMIT + 2


def test_the_running_line_does_not_count_lines(isolated_db, monkeypatch):
    did = _title("A")
    _queue(monkeypatch, [(did, "translate", "running", None)])

    def scanned():
        raise AssertionError("the whole-table line count ran")
    monkeypatch.setattr(db, "line_progress_by_drama", scanned)
    assert [e["drama_id"] for e in home.get_home()["running"]] == [did]
