"""services/library_home_service.py: the Library's Needs you / Running / Ready shelves."""
import json

import db
from core import Line
from services import library_home_service as home, queue_service, series_auto_service


def _title(name, lines=(("你好", "Hello", None),), media_type="streamer_vod", **kw):
    did = db.create_drama(title_en=name, title_zh="题", media_type=media_type, **kw)
    if lines:
        db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=zh, en=en, flag=flag)
                            for i, (zh, en, flag) in enumerate(lines)])
    return did


def _queue(monkeypatch, items):
    full = [{"id": n, "drama_id": d, "step": step, "status": status, "job_id": None, "reason": reason,
             "started_at": None} for n, (d, step, status, reason) in enumerate(items, start=1)]
    monkeypatch.setattr(queue_service, "get_queue", lambda principal: {"items": full, "can_control": True})


def _ids(shelf):
    return [e["drama_id"] for e in shelf]


def test_a_title_with_only_unsure_lines_stays_ready_with_a_count_not_in_needs_you(isolated_db, monkeypatch):
    _queue(monkeypatch, [])
    risky = _title("Risky", lines=(("你好", "Hello", "pronoun_check"), ("再见", "Bye", None)))
    clean = _title("Clean")
    out = home.get_home()
    assert out["needs_you"] == [] and out["needs_you_count"] == 0
    assert {e["drama_id"]: e["unsure_count"] for e in out["ready"]} == {risky: 1, clean: 0}


def test_untranslated_titles_are_on_no_shelf(isolated_db, monkeypatch):
    _queue(monkeypatch, [])
    _title("Half", lines=(("你好", "", None),))
    _title("Empty", lines=())
    out = home.get_home()
    assert out["needs_you"] == [] and out["ready"] == [] and out["running"] == []


def test_a_failed_step_needs_you_until_a_later_step_of_that_title_is_queued(isolated_db, monkeypatch):
    did = _title("Broken", lines=(("你好", "", None),))
    _queue(monkeypatch, [(did, "translate", "failed", "key refused")])
    failed = home.get_home()["needs_you"]
    assert [(e["drama_id"], e["kind"], e["step"], e["reason"]) for e in failed] == [
        (did, "failed", "translate", "key refused")]

    _queue(monkeypatch, [(did, "translate", "failed", "key refused"), (did, "translate", "waiting", None)])
    out = home.get_home()
    assert out["needs_you"] == []
    assert [(e["drama_id"], e["status"]) for e in out["running"]] == [(did, "waiting")]


def test_running_lists_the_running_step_before_waiting_ones_and_keeps_it_off_ready(isolated_db, monkeypatch):
    a, b = _title("A"), _title("B")
    _queue(monkeypatch, [(a, "export", "waiting", None), (b, "translate", "running", None)])
    out = home.get_home()
    assert [(e["drama_id"], e["status"]) for e in out["running"]] == [(b, "running"), (a, "waiting")]
    assert out["ready"] == []


def test_a_finished_or_exported_title_read_to_the_end_leaves_ready(isolated_db, monkeypatch):
    _queue(monkeypatch, [])
    exported = _title("Exported", lines=(("你好", "Hello", "pronoun_check"),))
    db.update_drama(exported, status="exported")
    out = home.get_home()
    assert _ids(out["ready"]) == [exported]        # exported: not asked to check again

    rows = [{**d, "read_percent": 100.0, "line_count": 1, "translated_count": 1} for d in db.list_dramas()]
    monkeypatch.setattr(home.library_service, "list_library_dramas", lambda principal=None: rows)
    assert home.get_home()["ready"] == []


def test_a_comic_is_ready_once_its_pages_are_translated(isolated_db, monkeypatch):
    _queue(monkeypatch, [])
    comic = _title("Comic", lines=(), media_type="manhua")
    rows = [{**d, "page_count": 3, "pages_translated": 3, "line_count": 0, "translated_count": 0,
             "read_percent": 0.0} for d in db.list_dramas()]
    monkeypatch.setattr(home.library_service, "list_library_dramas", lambda principal=None: rows)
    assert _ids(home.get_home()["ready"]) == [comic]


def test_a_series_whose_auto_run_is_on_hold_needs_you(isolated_db, monkeypatch):
    _queue(monkeypatch, [])
    sid = db.get_or_create_series("Show")
    db.set_series_auto_pipeline(sid, json.dumps({"on": True, "hold": "Ep 1 is ready for a look.",
                                                 "hold_kind": "review"}))
    off = db.get_or_create_series("Off")
    db.set_series_auto_pipeline(off, json.dumps({"on": False, "hold": "old", "hold_kind": "review"}))
    held = [e for e in home.get_home()["needs_you"] if e["kind"] == "series_hold"]
    assert [(e["series_id"], e["title"], e["reason"]) for e in held] == [(sid, "Show", "Ep 1 is ready for a look.")]
    assert series_auto_service.held_series(None)[0]["hold_kind"] == "review"


def test_each_shelf_is_cut_but_its_count_is_whole(isolated_db, monkeypatch):
    _queue(monkeypatch, [])
    for n in range(home.SHELF_LIMIT + 2):
        _title(f"T{n}")
    out = home.get_home()
    assert len(out["ready"]) == home.SHELF_LIMIT and out["ready_count"] == home.SHELF_LIMIT + 2


def test_a_started_title_comes_first_on_ready_with_its_length(isolated_db, monkeypatch):
    _queue(monkeypatch, [])
    older, newer = _title("Older"), _title("Newer")
    rows = [{**d, "line_count": 1, "translated_count": 1, "read_percent": 40.0 if d["id"] == older else 0.0}
            for d in db.list_dramas()]
    monkeypatch.setattr(home.library_service, "list_library_dramas", lambda principal=None: rows)
    ready = home.get_home()["ready"]
    assert _ids(ready)[0] == older and set(_ids(ready)) == {older, newer}
    assert ready[0]["length_seconds"] == 1.0 and ready[0]["read_percent"] == 40.0


def test_a_ready_title_carries_where_this_account_stopped_watching(isolated_db, monkeypatch):
    _queue(monkeypatch, [])
    did = _title("Stream")
    db.save_progress(did, audio_position_seconds=42.5, record_history=False, user_id=7)
    mine = home.get_home({"user_id": 7})["ready"][0]
    assert mine["watched_seconds"] == 42.5 and mine["watched_at"]
    assert home.get_home(None)["ready"][0]["watched_seconds"] == 0.0
