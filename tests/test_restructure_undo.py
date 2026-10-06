"""One-click undo of a structural edit: every write returns the snapshot it took
(`history_id`) and a fingerprint of the lines it left, and restoring with that
fingerprint refuses when anything restorable changed since."""
import pytest

import background_jobs
import db
from core import Line
from services import review_extras_service as extras
from services import restructure_service as svc
from services.service_errors import ConflictError

SENT = "我今天早上很早就起床了然后去公园跑步。"
LONG = SENT * 3


@pytest.fixture(autouse=True)
def _env(isolated_db):
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _seed(rows):
    did = db.create_drama(title_zh="D", source_language="zh")
    db.save_lines(did, [Line(idx=i, start=float(i * 3), end=i * 3 + 1.0, zh=z, en=e, speaker="S")
                        for i, (z, e) in enumerate(rows)])
    return did, [r["id"] for r in db.load_lines(did)]


def _texts(did):
    return [(r["zh"], r["en"], r["start"], r["end"]) for r in db.load_lines(did)]


def _undo(did, out, ids_now=None, fingerprint=True):
    ids = ids_now if ids_now is not None else out["line_ids"]
    return svc.restore_version(did, out["history_id"], ids,
                               out["lines_fingerprint"] if fingerprint else None)


ROWS = [("你好吗", "Hi"), ("我很好", "Fine"), ("谢谢", "Thanks"), ("再见", "Bye")]


def test_split_returns_its_snapshot_and_undo_round_trips():
    did, ids = _seed(ROWS)
    before = _texts(did)
    out = svc.split_line(did, ids[0], ids, at_char=1, expected_zh="你好吗")
    assert db.list_line_history(did)[0]["id"] == out["history_id"]
    assert db.list_line_history(did)[0]["label"] == "before split"
    assert len(_texts(did)) == 5
    _undo(did, out)
    assert _texts(did) == before


def test_merge_and_delete_undo_round_trip():
    did, ids = _seed(ROWS)
    before = _texts(did)
    merged = svc.merge_lines(did, ids[:2], ids)
    _undo(did, merged)
    assert _texts(did) == before
    ids = [r["id"] for r in db.load_lines(did)]
    deleted = svc.delete_line(did, ids[2], ids, confirm=True)
    _undo(did, deleted)
    assert _texts(did) == before


def test_merge_short_undo_round_trips():
    did, ids = _seed([("a", "A"), ("b", "B"), ("c", "C")])
    before = _texts(did)
    preview = extras.preview_merge_short(did, min_duration=5, max_gap=5, max_chars=50)
    assert preview["groups"]
    out = extras.apply_merge_short(did, ids, preview["groups"], min_duration=5, max_gap=5,
                                   max_chars=50)
    assert len(_texts(did)) < len(before)
    _undo(did, out)
    assert _texts(did) == before


def test_resplit_undo_round_trips():
    did, ids = _seed([("你好。", ""), (LONG, "x"), ("再见。", "")])
    before = _texts(did)
    out = svc.resplit_long_lines(did, ids, confirm=True)
    assert out["split_lines"] == 1 and out["history_id"] and out["lines_fingerprint"]
    assert db.list_line_history(did)[0]["label"] == "before re-split"
    now = [r["id"] for r in db.load_lines(did)]
    svc.restore_version(did, out["history_id"], now, out["lines_fingerprint"])
    assert _texts(did) == before


def test_nothing_to_resplit_offers_no_undo():
    did, ids = _seed(ROWS)
    out = svc.resplit_long_lines(did, ids)
    assert out["split_lines"] == 0 and "history_id" not in out


def test_undo_refused_after_a_text_edit_that_kept_the_ids():
    did, ids = _seed(ROWS)
    out = svc.split_line(did, ids[0], ids, at_char=1, expected_zh="你好吗")
    line = db.load_line_objects(did)[3]
    line.en = "Edited after the split"
    db.save_lines(did, [line], fields=("en",))
    edited = _texts(did)
    with pytest.raises(ConflictError):
        _undo(did, out)
    assert _texts(did) == edited
    # the Records route (no fingerprint) still restores, as before
    _undo(did, out, fingerprint=False)
    assert len(_texts(did)) == 4


def test_undo_refused_when_the_line_set_changed():
    did, ids = _seed(ROWS)
    out = svc.split_line(did, ids[0], ids, at_char=1, expected_zh="你好吗")
    newer = svc.delete_line(did, ids[3], out["line_ids"], confirm=True)
    after = _texts(did)
    with pytest.raises(ConflictError):
        svc.restore_version(did, out["history_id"], out["line_ids"], out["lines_fingerprint"])
    assert _texts(did) == after
    assert newer["history_id"] != out["history_id"]


def test_undo_over_the_api(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    did, ids = _seed(ROWS)
    base = f"/api/restructure/dramas/{did}"
    r = client.post(f"{base}/lines/{ids[0]}/split", json={
        "expected_line_ids": ids, "at_char": 1, "expected_zh": "你好吗"})
    body = r.json()
    assert r.status_code == 200 and body["history_id"] and body["lines_fingerprint"]
    url = f"{base}/history/{body['history_id']}/restore"
    stale = client.post(url, json={"expected_line_ids": body["line_ids"],
                                   "expected_fingerprint": "0" * 64})
    assert stale.status_code == 409
    ok = client.post(url, json={"expected_line_ids": body["line_ids"],
                                "expected_fingerprint": body["lines_fingerprint"]})
    assert ok.status_code == 200 and len(ok.json()["line_ids"]) == 4
