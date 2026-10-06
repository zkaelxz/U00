"""One-click undo of a structural edit: every write returns the snapshot it took
(`history_id`) and a fingerprint of the lines it left, and restoring with that
fingerprint refuses when anything restorable changed since."""
import contextlib
import dataclasses

import pytest

import background_jobs
import db
from core import Line
from services import review_extras_service as extras
from services import lines_service
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


def _new_piece(did, before_ids):
    return next(r["id"] for r in db.load_lines(did) if r["id"] not in set(before_ids))


def _emotions(did):
    with contextlib.closing(db.get_conn()) as conn:
        return {r[0] for r in conn.execute("SELECT line_id FROM line_emotions WHERE drama_id = ?",
                                           (did,))}


def test_undo_refused_when_a_split_piece_got_a_note():
    did, ids = _seed(ROWS)
    out = svc.split_line(did, ids[1], ids, at_char=1, expected_zh="我很好")
    piece = _new_piece(did, ids)
    lines_service.add_note(did, piece, "好", "idiom", "A note on the new piece")
    after = _texts(did)
    with pytest.raises(ConflictError):
        _undo(did, out)
    assert _texts(did) == after
    assert [n["line_id"] for n in db.list_translation_notes(did)] == [piece]


def test_undo_refused_when_a_split_piece_got_an_emotion():
    did, ids = _seed(ROWS)
    out = svc.split_line(did, ids[1], ids, at_char=1, expected_zh="我很好")
    piece = _new_piece(did, ids)
    db.save_emotions(did, {2: {"emotion": "joy", "intensity": 0.8}},
                     id_by_idx={2: piece})
    with pytest.raises(ConflictError):
        _undo(did, out)
    assert _emotions(did) == {piece}


def test_undo_refused_when_a_resplit_piece_got_a_note():
    did, ids = _seed([("你好。", ""), (LONG, "x"), ("再见。", "")])
    out = svc.resplit_long_lines(did, ids, confirm=True)
    piece = _new_piece(did, ids)
    lines_service.add_note(did, piece, "公园", "cultural", "A note on a re-split piece")
    now = [r["id"] for r in db.load_lines(did)]
    with pytest.raises(ConflictError):
        svc.restore_version(did, out["history_id"], now, out["lines_fingerprint"])
    assert [r["id"] for r in db.load_lines(did)] == now
    assert [n["line_id"] for n in db.list_translation_notes(did)] == [piece]


def test_undo_still_works_with_notes_only_on_lines_it_keeps():
    did, ids = _seed(ROWS)
    lines_service.add_note(did, ids[1], "好", "idiom", "On the line being split")
    out = svc.split_line(did, ids[1], ids, at_char=1, expected_zh="我很好")
    lines_service.add_note(did, ids[3], "再见", "cultural", "Added after the split")
    _undo(did, out)
    assert [r["id"] for r in db.load_lines(did)] == ids
    assert sorted(n["line_id"] for n in db.list_translation_notes(did)) == [ids[1], ids[3]]


@pytest.mark.parametrize("edit", [
    lambda did, lid: lines_service.patch_line(did, lid, speaker="B"),
    lambda did, lid: lines_service.patch_line(did, lid, start=0.1),
    lambda did, lid: db.save_lines(did, [dataclasses.replace(
        next(ln for ln in db.load_line_objects(did) if ln.id == lid), flag="check", flag_note="x")],
        fields=("flag", "flag_note")),
], ids=["speaker", "timing", "flag"])
def test_undo_refused_after_a_field_edit(edit):
    did, ids = _seed(ROWS)
    out = svc.split_line(did, ids[0], ids, at_char=1, expected_zh="你好吗")
    edit(did, ids[0])
    after = [dict(r) for r in db.load_lines(did)]
    with pytest.raises(ConflictError):
        _undo(did, out)
    assert [dict(r) for r in db.load_lines(did)] == after


def test_undo_refused_when_a_split_piece_got_a_reading_position():
    did, ids = _seed(ROWS)
    out = svc.split_line(did, ids[1], ids, at_char=1, expected_zh="我很好")
    db.save_progress(did, last_line_idx=2)
    with pytest.raises(ConflictError):
        _undo(did, out)
    assert len(db.load_lines(did)) == 5


def test_what_undo_of_a_delete_or_merge_brings_back():
    """Pins the wording of the undo confirmations: a deleted line comes back
    with a fresh id and its own fields but not its notes or emotion tag; a
    merge's notes and emotion tags stay on the line they were merged into."""
    did, ids = _seed(ROWS)
    line = db.load_line_objects(did)[2]
    line.flag, line.flag_note, line.dub_filename = "check", "why", "c.wav"
    db.save_lines(did, [line], fields=("flag", "flag_note", "dub_filename"))
    lines_service.add_note(did, ids[2], "谢谢", "honorific", "n")
    db.save_emotions(did, {2: {"emotion": "joy"}}, id_by_idx={2: ids[2]})
    full = [{k: v for k, v in r.items() if k != "id"} for r in db.load_lines(did)]
    deleted = svc.delete_line(did, ids[2], ids, confirm=True)
    _undo(did, deleted)
    back = db.load_lines(did)
    assert [{k: v for k, v in r.items() if k != "id"} for r in back] == full
    assert back[2]["id"] != ids[2]
    assert db.list_translation_notes(did) == [] and _emotions(did) == set()

    ids = [r["id"] for r in back]
    lines_service.add_note(did, ids[3], "再见", "cultural", "n")
    db.save_emotions(did, {3: {"emotion": "sad"}}, id_by_idx={3: ids[3]})
    merged = svc.merge_lines(did, ids[2:], ids)
    _undo(did, merged)
    back = db.load_lines(did)
    assert [{k: v for k, v in r.items() if k != "id"} for r in back] == full
    assert back[2]["id"] == ids[2] and back[3]["id"] != ids[3]
    assert [n["line_id"] for n in db.list_translation_notes(did)] == [ids[2]]
    assert _emotions(did) == {ids[2]}
