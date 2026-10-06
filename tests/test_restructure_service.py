"""Tests for services/restructure_service.py + /api/restructure (Migration
Slice 45). Fully mocked: isolated_db, no real LLM, no GPU."""
import contextlib
import json
import time

import pytest

import background_jobs
import db
import resegment
from core import Line
from services import drama_service, restructure_service as svc
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

LONG = "我今天早上很早就起床了然后去公园跑步。" * 3  # well over the zh cap, splits at 。


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    # plain rules, no optional segmenter needed
    monkeypatch.setattr(resegment, "word_boundaries", lambda *a, **k: None)
    yield
    background_jobs.clear_all_jobs()


def _seed(rows=None):
    did = db.create_drama(title_zh="D")
    rows = rows or [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")]
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=z, en=e, speaker="S")
                        for i, (z, e) in enumerate(rows)])
    return did, [r["id"] for r in db.load_lines(did)]


def _rows(did):
    return db.load_lines(did)


def _notes(did):
    return {n["term"]: n["line_id"] for n in db.list_translation_notes(did)}


def _emo_line_ids(did):
    with contextlib.closing(db.get_conn()) as conn:
        return {r["emotion"]: r["line_id"] for r in conn.execute(
            "SELECT emotion, line_id FROM line_emotions WHERE drama_id = ?", (did,))}


def _decorate(did, ids):
    """note + emotion + flag on c and d; note on b."""
    db.save_translation_notes(did, [
        {"line_id": ids[1], "line_idx": 1, "term": "tb", "note": "n"},
        {"line_id": ids[2], "line_idx": 2, "term": "tc", "note": "n"},
        {"line_id": ids[3], "line_idx": 3, "term": "td", "note": "n"}])
    db.save_emotions(did, {2: {"emotion": "angry"}, 3: {"emotion": "sad"}})
    lines = db.load_line_objects(did)
    lines[3].flag, lines[3].flag_note = "idiom", "x"
    db.save_lines(did, lines, fields=("flag", "flag_note"))


def _all_refs_point_to_lines(did):
    ids = {r["id"] for r in _rows(did)}
    assert set(_notes(did).values()) <= ids
    assert set(_emo_line_ids(did).values()) <= ids


class TestMergeAndUndo:
    def test_merge_carries_refs_and_snapshots_first(self):
        did, ids = _seed()
        _decorate(did, ids)
        out = svc.merge_lines(did, [ids[0], ids[1]], ids)
        assert out["line_ids"] == [ids[0], ids[2], ids[3]]
        rows = _rows(did)
        assert rows[0]["zh"] == "ab" and rows[0]["en"] == "A B" and rows[0]["end"] == 2.0
        assert [r["idx"] for r in rows] == [0, 1, 2]
        assert _notes(did) == {"tb": ids[0], "tc": ids[2], "td": ids[3]}
        assert db.list_line_history(did)[0]["label"] == "before merge"
        _all_refs_point_to_lines(did)

    def test_merge_then_restore_keeps_refs_on_right_lines(self):
        did, ids = _seed()
        _decorate(did, ids)
        after = svc.merge_lines(did, [ids[0], ids[1]], ids)["line_ids"]
        hist = db.list_line_history(did)[0]["id"]
        out = svc.restore_version(did, hist, after)
        rows = _rows(did)
        assert [r["zh"] for r in rows] == ["a", "b", "c", "d"]
        by_zh = {r["zh"]: r for r in rows}
        # c/d kept their ids, notes, emotions and flag -- nothing shifted by position
        assert by_zh["c"]["id"] == ids[2] and by_zh["d"]["id"] == ids[3]
        assert by_zh["d"]["flag"] == "idiom" and by_zh["c"]["flag"] is None
        assert _notes(did)["tc"] == ids[2] and _notes(did)["td"] == ids[3]
        assert _emo_line_ids(did) == {"angry": ids[2], "sad": ids[3]}
        # the merged-away line came back with a fresh id and no stray refs
        assert by_zh["b"]["id"] not in ids
        assert out["line_ids"] == [r["id"] for r in rows]
        assert db.list_line_history(did)[0]["label"] == "before restore"
        _all_refs_point_to_lines(did)

    def test_merge_then_restore_puts_flags_and_sfx_back_on_their_lines(self):
        """Flag, flag note and SFX mark come back from the snapshot itself --
        not adopted from whichever current line now holds the same id (the
        merge moved b's flag onto a, and b's SFX mark had nowhere to go)."""
        did, ids = _seed()
        lines = db.load_line_objects(did)
        lines[1].flag, lines[1].flag_note, lines[1].sfx = "idiom", "b's note", True
        lines[3].flag, lines[3].flag_note = "name", "d's note"
        db.save_lines(did, lines, fields=("flag", "flag_note", "sfx"))
        after = svc.merge_lines(did, [ids[0], ids[1]], ids)["line_ids"]
        assert _rows(did)[0]["flag"] == "idiom"  # the merge moved b's flag onto a
        svc.restore_version(did, db.list_line_history(did)[0]["id"], after)
        by_zh = {r["zh"]: r for r in _rows(did)}
        assert (by_zh["a"]["flag"], by_zh["a"]["flag_note"], by_zh["a"]["sfx"]) == (None, "", 0)
        assert (by_zh["b"]["flag"], by_zh["b"]["flag_note"], by_zh["b"]["sfx"]) == (
            "idiom", "b's note", 1)
        assert (by_zh["d"]["flag"], by_zh["d"]["flag_note"]) == ("name", "d's note")
        assert by_zh["c"]["flag"] is None and not by_zh["c"]["sfx"]

    def test_delete_then_restore_brings_back_flag_and_sfx(self):
        did, ids = _seed()
        lines = db.load_line_objects(did)
        lines[2].flag, lines[2].flag_note, lines[2].sfx = "idiom", "c's note", True
        db.save_lines(did, lines, fields=("flag", "flag_note", "sfx"))
        after = svc.delete_line(did, ids[2], ids, confirm=True)["line_ids"]
        svc.restore_version(did, db.list_line_history(did)[0]["id"], after)
        by_zh = {r["zh"]: r for r in _rows(did)}
        assert (by_zh["c"]["flag"], by_zh["c"]["flag_note"], by_zh["c"]["sfx"]) == (
            "idiom", "c's note", 1)

    def test_restore_of_old_snapshot_without_flag_keys_keeps_current_flags(self):
        """Snapshots saved before flag/flag_note/sfx were recorded keep
        today's behaviour: a line that still exists keeps its current marks."""
        did, ids = _seed()
        snap = [{k: r[k] for k in ("id", "idx", "start", "end", "zh", "en", "speaker")}
                for r in _rows(did)]
        lines = db.load_line_objects(did)
        lines[0].flag, lines[0].flag_note, lines[0].sfx = "idiom", "n", True
        db.save_lines(did, lines, fields=("flag", "flag_note", "sfx"))
        with contextlib.closing(db.get_conn()) as conn:
            hist = conn.execute(
                "INSERT INTO line_history (drama_id, label, snapshot_json, created_at) "
                "VALUES (?, 'old', ?, '2000-01-01')", (did, json.dumps(snap))).lastrowid
            conn.commit()
        svc.restore_version(did, hist, ids)
        row = _rows(did)[0]
        assert (row["flag"], row["flag_note"], row["sfx"]) == ("idiom", "n", 1)

    def test_non_adjacent_merge_rejected(self):
        did, ids = _seed()
        with pytest.raises(InvalidInputError):
            svc.merge_lines(did, [ids[0], ids[2]], ids)
        assert len(_rows(did)) == 4 and not db.list_line_history(did)


class TestAddDeleteSplit:
    def test_add_line_inserts_after(self):
        did, ids = _seed()
        out = svc.add_line(did, ids, after_line_id=ids[1], start=1.5, end=1.8, zh="new")
        rows = _rows(did)
        assert [r["zh"] for r in rows] == ["a", "b", "new", "c", "d"]
        assert out["lines"][0]["id"] == rows[2]["id"] and out["lines"][0]["idx"] == 2
        assert [r["id"] for r in rows if r["zh"] != "new"] == ids

    def test_delete_needs_confirm_and_removes_refs(self):
        did, ids = _seed()
        _decorate(did, ids)
        with pytest.raises(InvalidInputError):
            svc.delete_line(did, ids[2], ids)
        svc.delete_line(did, ids[2], ids, confirm=True)
        assert [r["id"] for r in _rows(did)] == [ids[0], ids[1], ids[3]]
        assert "tc" not in _notes(did) and "angry" not in _emo_line_ids(did)
        assert _notes(did)["td"] == ids[3]
        assert db.list_line_history(did)[0]["label"] == "before delete line"

    def test_split_keeps_refs_on_first_piece(self):
        did, ids = _seed([("a", "A"), ("你好世界", "Hello world"), ("c", "C"), ("d", "D")])
        _decorate(did, ids)
        out = svc.split_line(did, ids[1], ids, at_char=2, expected_zh="你好世界",
                             at_time=1.25, en_at_char=5)
        rows = _rows(did)
        assert [r["zh"] for r in rows] == ["a", "你好", "世界", "c", "d"]
        assert rows[1]["id"] == ids[1] and rows[1]["end"] == 1.25 and rows[1]["en"] == "Hello"
        assert rows[2]["start"] == 1.25 and rows[2]["en"] == "world" and rows[2]["speaker"] == "S"
        assert _notes(did)["tb"] == ids[1]
        assert len(out["lines"]) == 2

    def test_split_stale_text_409(self):
        did, ids = _seed()
        with pytest.raises(ConflictError):
            svc.split_line(did, ids[0], ids, at_char=1, expected_zh="zz")

    def test_split_proportional_time(self):
        did, ids = _seed([("abcd", "")])
        svc.split_line(did, ids[0], ids, at_char=1, expected_zh="abcd")
        rows = _rows(did)
        assert rows[0]["end"] == pytest.approx(0.25) and rows[1]["start"] == pytest.approx(0.25)


class TestConcurrency:
    def test_stale_ids_409_and_nothing_written(self):
        did, ids = _seed()
        for call in (lambda: svc.merge_lines(did, ids[:2], ids[:3]),
                     lambda: svc.delete_line(did, ids[0], list(reversed(ids)), confirm=True),
                     lambda: svc.add_line(did, ids + [999], start=0, end=1)):
            with pytest.raises(ConflictError):
                call()
        assert [r["id"] for r in _rows(did)] == ids and not db.list_line_history(did)

    def test_running_job_refused(self, monkeypatch):
        did, ids = _seed()
        monkeypatch.setattr(drama_service, "job_running_for_drama", lambda d: True)
        with pytest.raises(ConflictError):
            svc.delete_line(did, ids[0], ids, confirm=True)
        db.save_line_history_snapshot(did, db.load_line_objects(did), "x")
        with pytest.raises(ConflictError):
            svc.restore_version(did, db.list_line_history(did)[0]["id"], ids)
        assert len(_rows(did)) == 4

    def test_other_dramas_snapshot_is_404(self):
        did, ids = _seed()
        other, oids = _seed()
        db.save_line_history_snapshot(other, db.load_line_objects(other), "x")
        with pytest.raises(NotFoundError):
            svc.restore_version(did, db.list_line_history(other)[0]["id"], ids)

    def test_unknown_line_404(self):
        did, ids = _seed()
        with pytest.raises(NotFoundError):
            svc.delete_line(did, 99999, ids, confirm=True)


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


class TestResegment:
    def test_preview_is_read_only(self):
        did, ids = _seed([("a", ""), (LONG, "translated")])
        p = svc.preview_resegmentation(did)
        assert p["source_line_ids"] == ids and p["line_count_after"] == 4
        assert p["changed"][0]["line_id"] == ids[1] and p["needs_confirm"] and p["translated"] == 1
        assert [r["id"] for r in _rows(did)] == ids and not db.list_line_history(did)

    def test_apply_job_no_orphans(self):
        did, ids = _seed([("a", "A"), (LONG, "translated"), ("c", "C"), ("d", "D")])
        _decorate(did, ids)
        with pytest.raises(InvalidInputError):
            svc.start_resegmentation(did, ids)
        out = svc.start_resegmentation(did, ids, confirm=True)
        job = _wait(out["job_id"])
        assert job["status"] == "done", job
        rows = _rows(did)
        assert len(rows) == 6 and ids[1] not in {r["id"] for r in rows}
        assert [r["idx"] for r in rows] == list(range(6))
        assert all(r["en"] == "" for r in rows[1:4])
        assert "tb" not in _notes(did) and _notes(did)["tc"] == ids[2]
        assert {r["id"] for r in rows} >= {ids[0], ids[2], ids[3]}
        assert db.list_line_history(did)[0]["label"] == "before re-segment"
        _all_refs_point_to_lines(did)

    def test_apply_refuses_if_lines_changed_during_job(self, monkeypatch):
        did, ids = _seed([("a", ""), (LONG, "")])
        real = resegment.resegment_lines

        def slow(*a, **k):
            db.save_lines(did, db.load_line_objects(did) + [Line(idx=9, start=9, end=10, zh="x")])
            return real(*a, **k)
        monkeypatch.setattr(resegment, "resegment_lines", slow)
        job = _wait(svc.start_resegmentation(did, ids)["job_id"])
        assert job["status"] == "error"
        assert len(_rows(did)) == 3  # the other writer's line kept, nothing duplicated

    def test_duplicate_start_409(self, monkeypatch):
        did, ids = _seed([("a", ""), (LONG, "")])
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            svc.start_resegmentation(did, ids)


class TestApi:
    @pytest.fixture
    def client(self):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)

    def test_routes(self, client):
        did, ids = _seed()
        base = f"/api/restructure/dramas/{did}"
        r = client.post(f"{base}/merge", json={"line_ids": ids[:2], "expected_line_ids": ids})
        assert r.status_code == 200, r.text
        after = r.json()["line_ids"]
        assert client.post(f"{base}/merge", json={"line_ids": after[:2],
                                                  "expected_line_ids": ids}).status_code == 409
        assert client.post(f"{base}/lines/{after[0]}/delete",
                           json={"expected_line_ids": after}).status_code == 422
        hist = client.get(f"/api/review/dramas/{did}/history").json()
        assert hist[0]["label"] == "before merge"
        r = client.post(f"{base}/history/{hist[0]['id']}/restore", json={"expected_line_ids": after})
        assert r.status_code == 200 and len(r.json()["line_ids"]) == 4
        assert client.get(f"{base}/resegment/preview").status_code == 200
        assert client.post(f"{base}/merge", json={"line_ids": ids[:2], "expected_line_ids": ids,
                                                  "bogus": 1}).status_code == 422


def test_merge_refuses_a_line_past_the_text_cap():
    half = "x" * (svc.MAX_LINE_TEXT_CHARS // 2 + 1)
    did = db.create_drama(title_zh="D", source_language="zh")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh=half),
                        Line(idx=1, start=1.0, end=2.0, zh=half)])
    ids = [r["id"] for r in db.load_lines(did)]
    with pytest.raises(InvalidInputError):
        svc.merge_lines(did, ids, ids)
