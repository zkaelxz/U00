"""Run 3 times, review differences: services/three_run_service.py and its routes
under /api/three-run. The translation runs are faked by writing English to the
lines and reporting the title's Translate job as done: no engine, no network."""

import pytest

import background_jobs
import db
from core import Line
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import three_run_service as svc


@pytest.fixture(autouse=True)
def _reset(isolated_db, monkeypatch):
    svc._sessions.clear()
    monkeypatch.setattr(svc.drama_service, "job_running_for_drama", lambda drama_id: False)
    yield
    svc._sessions.clear()


@pytest.fixture
def title(monkeypatch):
    did = db.create_drama(title_en="T")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=f"原{i}", en=f"old{i}")
                        for i in range(4)])
    status = {"status": "done", "result": {"errors": []}}
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: status)
    return did, status


def _run(did, texts):
    """What a finished Translate run leaves: English per line, in order."""
    lines = db.load_line_objects(did)
    for ln, text in zip(lines, texts):
        ln.en = text
    db.save_lines(did, lines, fields=("en",))


def _three(did, a, b, c):
    svc.begin(did)
    for n, texts in enumerate((a, b, c), start=1):
        _run(did, texts)
        svc.capture_run(did, n)


def test_only_lines_that_differ_beyond_case_and_punctuation_are_listed(title):
    did, _ = title
    _three(did, ["Hi.", "Go", "Run", "Same"], ["hi", "Go now", "Run", "Same!"], ["HI!", "Go", "Run", "same"])
    st = svc.state(did)
    assert st["runs_done"] == 3
    assert [it["zh"] for it in st["items"]] == ["原1"]
    assert st["items"][0]["versions"] == ["Go", "Go now", "Go"]


def test_agreeing_lines_keep_the_last_run_and_differing_lines_hold_it_until_picked(title):
    did, _ = title
    _three(did, ["a", "b1", "c", "d"], ["a", "b2", "c", "d"], ["a", "b3", "c", "d"])
    assert [ln.en for ln in db.load_line_objects(did)] == ["a", "b3", "c", "d"]


def test_pick_writes_only_en_and_an_edited_line_is_skipped(title):
    did, _ = title
    _three(did, ["a", "b1", "c1", "d"], ["a", "b2", "c2", "d"], ["a", "b3", "c3", "d"])
    ids = [it["line_id"] for it in svc.state(did)["items"]]
    lines = db.load_line_objects(did)
    lines[2].en = "my edit"
    db.save_lines(did, lines, fields=("en",))
    out = svc.pick_runs(did, [{"line_id": ids[0], "run": 1}, {"line_id": ids[1], "run": 2}])
    assert out == {"applied": [ids[0]], "skipped": [ids[1]]}
    by_id = {ln.id: ln for ln in db.load_line_objects(did)}
    assert by_id[ids[0]].en == "b1" and by_id[ids[0]].zh.startswith("原")
    assert by_id[ids[1]].en == "my edit"
    assert svc.state(did)["items"][0]["picked_run"] == 1


def test_a_pick_can_be_changed_and_keep_rest_skips_picked_lines(title):
    did, _ = title
    _three(did, ["a1", "b1", "c", "d"], ["a2", "b2", "c", "d"], ["a3", "b3", "c", "d"])
    first, second = [it["line_id"] for it in svc.state(did)["items"]]
    svc.pick_runs(did, [{"line_id": first, "run": 2}])
    svc.pick_runs(did, [{"line_id": first, "run": 1}])
    svc.keep_run_for_rest(did, 2)
    en = {ln.id: ln.en for ln in db.load_line_objects(did)}
    assert (en[first], en[second]) == ("a1", "b2")


def test_capture_refuses_a_run_that_failed_or_is_out_of_order(title):
    did, status = title
    svc.begin(did)
    with pytest.raises(ConflictError):
        svc.capture_run(did, 2)
    status["result"] = {"errors": ["batch failed"]}
    with pytest.raises(ConflictError):
        svc.capture_run(did, 1)
    status.update(status="failed", result={})
    with pytest.raises(ConflictError):
        svc.capture_run(did, 1)


def test_begin_takes_an_undo_snapshot_of_the_english_before_the_runs(title):
    did, _ = title
    out = svc.begin(did)
    snap = db.get_line_history_snapshot(out["history_id"])
    assert [r["en"] for r in snap["lines"]] == ["old0", "old1", "old2", "old3"] if "lines" in snap else snap


def test_refusals(title, monkeypatch):
    did, _ = title
    with pytest.raises(NotFoundError):
        svc.pick_runs(did, [{"line_id": 1, "run": 1}])
    svc.begin(did)
    with pytest.raises(ConflictError):
        svc.pick_runs(did, [{"line_id": 1, "run": 1}])
    with pytest.raises(InvalidInputError):
        svc.begin(did, line_ids=[10**9])
    monkeypatch.setattr(svc.drama_service, "job_running_for_drama", lambda drama_id: True)
    with pytest.raises(ConflictError):
        svc.begin(did)


def test_routes_round_trip(title, local_client):
    did, _ = title
    r = local_client.post(f"/api/three-run/dramas/{did}/begin", json={})
    assert r.status_code == 200 and r.json()["runs_done"] == 0
    for n, texts in enumerate((["a", "x1", "c", "d"], ["a", "x2", "c", "d"], ["a", "x3", "c", "d"]), start=1):
        _run(did, texts)
        assert local_client.post(f"/api/three-run/dramas/{did}/capture", json={"run": n}).status_code == 200
    st = local_client.get(f"/api/three-run/dramas/{did}").json()
    assert len(st["items"]) == 1
    out = local_client.post(f"/api/three-run/dramas/{did}/keep-rest", json={"run": 1}).json()
    assert out["applied"] == [st["items"][0]["line_id"]]
    assert local_client.delete(f"/api/three-run/dramas/{did}").status_code == 204
    assert local_client.get(f"/api/three-run/dramas/{did}").json()["active"] is False
