"""Review's untranscribed gaps: services/transcribe_gap_service.py and its
routes under /api/transcribe/dramas/{id}/gaps. No audio is decoded; the
speech coverage result and the pause detector are faked."""

import os

import pytest
from fastapi.testclient import TestClient

import background_jobs
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service, speech_coverage_service
from services import transcribe_gap_service as svc
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)


def L(i, start, end, zh="x"):
    return Line(id=i, idx=i, start=start, end=end, zh=zh)


class TestFindLineGaps:
    def test_no_lines_and_no_gaps(self):
        assert svc.find_line_gaps([]) == []
        assert svc.find_line_gaps([L(1, 0, 3), L(2, 3, 6), L(3, 6.5, 9)]) == []

    def test_tiny_gaps_are_ignored_and_two_seconds_counts(self):
        assert svc.find_line_gaps([L(1, 0, 3), L(2, 4.9, 6)]) == []
        gaps = svc.find_line_gaps([L(1, 0, 3), L(2, 5, 6)])
        assert gaps == [{"start": 3.0, "end": 5.0, "after_line_id": 1, "before_line_id": 2}]

    def test_leading_gap_has_no_line_before(self):
        assert svc.find_line_gaps([L(1, 10, 12)]) == [
            {"start": 0.0, "end": 10.0, "after_line_id": None, "before_line_id": 1}]

    def test_overlapping_and_nested_lines_leave_no_gap_under_them(self):
        lines = [L(1, 0, 10), L(2, 4, 6), L(3, 8, 13), L(4, 20, 22)]
        assert svc.find_line_gaps(lines) == [
            {"start": 13.0, "end": 20.0, "after_line_id": 3, "before_line_id": 4}]

    def test_the_gap_is_measured_from_the_furthest_end_not_the_previous_line(self):
        # line 2 sits inside line 1, so the stretch after line 2 is still covered by line 1
        assert svc.find_line_gaps([L(1, 0, 10), L(2, 1, 2), L(3, 11, 12)]) == []

    def test_unsorted_input_and_windowless_lines(self):
        lines = [L(3, 20, 22), L(1, 0, 3), L(9, 5, 5), L(2, 3, 4)]
        assert svc.find_line_gaps(lines) == [
            {"start": 4.0, "end": 20.0, "after_line_id": 2, "before_line_id": 3}]

    def test_blank_lines_cover(self):
        assert svc.find_line_gaps([L(1, 0, 3), L(2, 3, 10, zh=""), L(3, 10, 12)]) == []


class TestSplitGap:
    def test_short_stretch_is_one_piece(self):
        assert svc.split_gap(10, 22.5) == ([(10, 22.5)], False)
        assert svc.split_gap(0, 30.0)[0] == [(0, 30.0)]

    def test_just_over_the_limit_splits_in_two(self):
        pieces, snapped = svc.split_gap(0, 30.5)
        assert len(pieces) == 2 and not snapped

    @pytest.mark.parametrize("length", [31, 60, 61, 95, 187.3, 600])
    def test_pieces_tile_the_stretch_and_none_exceeds_the_limit(self, length):
        pieces, _ = svc.split_gap(100, 100 + length)
        assert pieces[0][0] == 100 and pieces[-1][1] == 100 + length
        assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:]))
        assert all(0 < e - s <= 30.0 + 1e-9 for s, e in pieces)
        assert len(pieces) == -(-length // 30)

    def test_cuts_move_to_nearby_pauses(self):
        pieces, snapped = svc.split_gap(0, 95, [22, 48.5, 70])
        assert snapped and pieces == [(0, 22), (22, 48.5), (48.5, 70), (70, 95)]

    def test_pauses_that_would_make_a_piece_too_long_are_ignored(self):
        # snapping to 28 leaves 28 + 28 + ... fine; snapping the second to 61 leaves a 33 s tail
        pieces, snapped = svc.split_gap(0, 90, [29.9, 59.9, 60.9])
        assert all(e - s <= 30.0 + 1e-9 for s, e in pieces)
        assert pieces[-1][1] == 90

    def test_a_pause_too_far_from_the_even_cut_is_ignored(self):
        pieces, snapped = svc.split_gap(0, 61, [29.9])
        assert not snapped and len(pieces) == 3

    def test_pauses_may_not_leave_a_sliver(self):
        pieces, snapped = svc.split_gap(0, 40, [20.0, 20.4])
        assert all(e - s >= 1.0 for s, e in pieces)


def _drama(isolated_db, audio=True, spans=((0.0, 3.0), (3.0, 6.0), (20.0, 23.0), (23.0, 30.0)),
           status="aligned"):
    sid = isolated_db.get_or_create_series("S")
    did = isolated_db.create_drama(title_en="D", series_id=sid, audio_filename="audio.wav",
                                   source_language="zh", status=status)
    if audio:
        with open(os.path.join(isolated_db.drama_dir(did), "audio.wav"), "wb") as f:
            f.write(b"x")
    isolated_db.save_lines(did, [Line(idx=i, start=s, end=e, zh=f"行{i}", en=f"Line {i}")
                                 for i, (s, e) in enumerate(spans)])
    return did, [ln.id for ln in isolated_db.load_line_objects(did)]


def _coverage(monkeypatch, gaps, status="done"):
    monkeypatch.setattr(
        speech_coverage_service, "get_speech_coverage",
        lambda did: {"job_id": "x", "status": status, "progress": 1, "message": "",
                     "result": {"gaps": [{"start": s, "end": e} for s, e in gaps]}
                     if status == "done" else None})


class TestListGaps:
    def test_from_lines_alone_speech_is_unknown(self, isolated_db):
        did, ids = _drama(isolated_db)
        out = svc.list_gaps(did)
        assert out["speech_checked"] is False
        assert out["gaps"] == [{"start": 6.0, "end": 20.0, "seconds": 14.0, "pieces": 1,
                                "after_line_id": ids[1], "before_line_id": ids[2],
                                "speech": None}]

    def test_long_gap_reports_its_pieces(self, isolated_db):
        did, _ = _drama(isolated_db, spans=((0, 3), (100, 103)))
        gaps = svc.list_gaps(did)["gaps"]
        assert [(g["start"], g["end"], g["pieces"]) for g in gaps] == [(3.0, 100.0, 4)]

    def test_coverage_marks_speech_and_adds_a_stretch_after_the_last_line(
            self, isolated_db, monkeypatch):
        did, ids = _drama(isolated_db)
        _coverage(monkeypatch, [(8.0, 12.0), (40.0, 47.0), (31.0, 31.5)])
        out = svc.list_gaps(did)
        assert out["speech_checked"] is True
        assert [(g["start"], g["end"], g["speech"], g["after_line_id"], g["before_line_id"])
                for g in out["gaps"]] == [
            (6.0, 20.0, True, ids[1], ids[2]), (40.0, 47.0, True, ids[3], None)]

    def test_coverage_never_hides_a_gap_and_never_says_no_speech(self, isolated_db, monkeypatch):
        did, _ = _drama(isolated_db)
        _coverage(monkeypatch, [(50.0, 60.0)])
        gaps = svc.list_gaps(did)["gaps"]
        assert [g["speech"] for g in gaps if g["start"] == 6.0] == [None]

    def test_coverage_over_a_line_added_since_is_not_a_gap(self, isolated_db, monkeypatch):
        did, _ = _drama(isolated_db)
        _coverage(monkeypatch, [(21.0, 26.0)])
        assert [g["start"] for g in svc.list_gaps(did)["gaps"]] == [6.0]

    def test_unfinished_coverage_is_ignored(self, isolated_db, monkeypatch):
        did, _ = _drama(isolated_db)
        _coverage(monkeypatch, [], status="running")
        assert svc.list_gaps(did)["speech_checked"] is False

    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.list_gaps(999999)


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


class TestAddGapLines:
    def test_adds_flagged_blank_lines_in_order_after_the_given_line(self, isolated_db):
        did, ids = _drama(isolated_db)
        out = svc.add_gap_lines(did, ids, start=7.0, end=19.0, after_line_id=ids[1])
        assert out["split"] == "single" and len(out["new_line_ids"]) == 1
        lines = isolated_db.load_line_objects(did)
        assert [ln.id for ln in lines][:2] == ids[:2]
        new = lines[2]
        assert new.id == out["new_line_ids"][0] and (new.start, new.end) == (7.0, 19.0)
        assert (new.zh, new.en, new.flag) == ("", "", svc.GAP_FLAG)
        assert [ln.idx for ln in lines] == list(range(5))
        assert out["history_id"] and len(isolated_db.list_line_history(did)) == 1
        assert [ln.zh for ln in lines if ln.id in ids] == [f"行{i}" for i in range(4)]

    def test_long_stretch_is_cut_into_30_second_lines(self, isolated_db, monkeypatch):
        did, ids = _drama(isolated_db, spans=((0, 3), (100, 103)))
        monkeypatch.setattr(svc, "_pauses", lambda *a: None)
        out = svc.add_gap_lines(did, ids, start=3.0, end=100.0, after_line_id=ids[0])
        assert out["split"] == "even" and len(out["new_line_ids"]) == 4
        added = [ln for ln in isolated_db.load_line_objects(did) if ln.id in out["new_line_ids"]]
        assert added[0].start == 3.0 and added[-1].end == 100.0
        assert all(0 < ln.end - ln.start <= 30.0 for ln in added)
        assert all(a.end == b.start for a, b in zip(added, added[1:]))

    def test_pauses_decide_the_cuts_when_the_detector_has_them(self, isolated_db, monkeypatch):
        did, ids = _drama(isolated_db, spans=((0, 3), (100, 103)))
        decoded = []
        monkeypatch.setattr(speech_coverage_service, "_decode_chunk",
                            lambda path, start, seconds: decoded.append((start, seconds)) or b"")
        # speech in [0,22] and [23,47] ... pauses at 22.5, 47.5 (relative to the stretch start)
        monkeypatch.setattr(speech_coverage_service, "_detect_speech",
                            lambda chunk: [(0, 22), (23, 47), (48, 70), (71, 97)])
        out = svc.add_gap_lines(did, ids, start=3.0, end=100.0, after_line_id=ids[0])
        assert decoded == [(3.0, 97.0)]
        assert out["split"] == "pauses"
        added = [ln for ln in isolated_db.load_line_objects(did) if ln.id in out["new_line_ids"]]
        assert [round(ln.end, 1) for ln in added[:-1]] == [25.5, 50.5, 73.5]

    def test_detector_failure_falls_back_to_even_cuts(self, isolated_db, monkeypatch):
        did, ids = _drama(isolated_db, spans=((0, 3), (100, 103)))

        def boom(*a):
            raise ImportError("no silero")
        monkeypatch.setattr(speech_coverage_service, "_decode_chunk", boom)
        assert svc.add_gap_lines(did, ids, start=3.0, end=100.0,
                                 after_line_id=ids[0])["split"] == "even"

    def test_short_stretch_never_runs_the_detector(self, isolated_db, monkeypatch):
        did, ids = _drama(isolated_db)

        def boom(*a):
            raise AssertionError("decoded audio for a short gap")
        monkeypatch.setattr(speech_coverage_service, "_decode_chunk", boom)
        svc.add_gap_lines(did, ids, start=7.0, end=19.0, after_line_id=ids[1])

    def test_leading_gap_inserts_at_the_start(self, isolated_db):
        did, ids = _drama(isolated_db, spans=((10, 12), (13, 14)))
        out = svc.add_gap_lines(did, ids, start=0.0, end=9.0, after_line_id=None)
        assert isolated_db.load_line_objects(did)[0].id == out["new_line_ids"][0]

    def test_does_not_touch_translations_or_the_title_status(self, isolated_db):
        did, ids = _drama(isolated_db, status="translated")
        svc.add_gap_lines(did, ids, start=7.0, end=19.0, after_line_id=ids[1])
        assert isolated_db.get_drama(did)["status"] == "translated"

    @pytest.mark.parametrize("start,end", [(7.0, 7.2), (19.0, 7.0), (0.0, 700.0), (-1.0, 5.0)])
    def test_bad_window(self, isolated_db, start, end):
        did, ids = _drama(isolated_db)
        with pytest.raises(InvalidInputError):
            svc.add_gap_lines(did, ids, start=start, end=end, after_line_id=ids[1])
        assert len(isolated_db.load_line_objects(did)) == 4

    @pytest.mark.parametrize("start,end", [(5.0, 19.0), (7.0, 21.0), (2.0, 4.0)])
    def test_a_stretch_a_line_now_covers_is_refused(self, isolated_db, start, end):
        did, ids = _drama(isolated_db)
        with pytest.raises(ConflictError):
            svc.add_gap_lines(did, ids, start=start, end=end, after_line_id=ids[1])
        assert len(isolated_db.load_line_objects(did)) == 4
        assert isolated_db.list_line_history(did) == []

    def test_the_wrong_neighbour_is_refused(self, isolated_db):
        did, ids = _drama(isolated_db)
        with pytest.raises(ConflictError):
            svc.add_gap_lines(did, ids, start=7.0, end=19.0, after_line_id=ids[0])
        with pytest.raises(NotFoundError):
            svc.add_gap_lines(did, ids, start=7.0, end=19.0, after_line_id=999999)

    def test_stale_line_list_is_refused(self, isolated_db):
        did, ids = _drama(isolated_db)
        with pytest.raises(ConflictError):
            svc.add_gap_lines(did, ids[:-1], start=7.0, end=19.0, after_line_id=ids[1])

    def test_refused_while_a_job_runs_on_the_title(self, isolated_db):
        did, ids = _drama(isolated_db)
        with background_jobs._lock:
            background_jobs._jobs[f"retranscribe_{did}"] = {
                "status": "running", "progress": 0.0, "message": "", "result": None,
                "error": None, "started_at": 0, "finished_at": None, "cancel_requested": False}
        with pytest.raises(ConflictError):
            svc.add_gap_lines(did, ids, start=7.0, end=19.0, after_line_id=ids[1])

    def test_needs_stored_audio(self, isolated_db):
        did, ids = _drama(isolated_db, audio=False)
        with pytest.raises(UnsupportedOperationError):
            svc.add_gap_lines(did, ids, start=7.0, end=19.0, after_line_id=ids[1])

    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.add_gap_lines(999999, [], start=1.0, end=5.0)

    def test_gap_is_gone_from_the_list_once_filled(self, isolated_db):
        did, ids = _drama(isolated_db)
        svc.add_gap_lines(did, ids, start=7.0, end=19.0, after_line_id=ids[1])
        # 6-7 and 19-20 remain, both under the 2 s threshold
        assert svc.list_gaps(did)["gaps"] == []


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


class TestRoutes:
    def test_gaps_and_add_lines(self, client, isolated_db):
        did, ids = _drama(isolated_db)
        r = client.get(f"/api/transcribe/dramas/{did}/gaps")
        assert r.status_code == 200, r.text
        assert r.json()["gaps"][0]["start"] == 6.0 and r.json()["speech_checked"] is False
        assert isolated_db.drama_dir(did) not in r.text
        body = {"expected_line_ids": ids, "start": 7.0, "end": 19.0, "after_line_id": ids[1]}
        r = client.post(f"/api/transcribe/dramas/{did}/gaps/add-lines", json=body)
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"new_line_ids", "line_ids", "split", "history_id",
                                 "lines_fingerprint"}
        assert len(r.json()["new_line_ids"]) == 1
        assert isolated_db.drama_dir(did) not in r.text and "audio.wav" not in r.text
        # the same request again is stale
        assert client.post(f"/api/transcribe/dramas/{did}/gaps/add-lines",
                           json=body).status_code == 409

    def test_errors(self, client, isolated_db):
        did, ids = _drama(isolated_db)
        assert client.get("/api/transcribe/dramas/999999/gaps").status_code == 404
        assert client.get("/api/transcribe/dramas/0/gaps").status_code == 422
        base = {"expected_line_ids": ids, "start": 7.0, "end": 19.0, "after_line_id": ids[1]}
        url = f"/api/transcribe/dramas/{did}/gaps/add-lines"
        assert client.post(url.replace(str(did), "999999"), json=base).status_code == 404
        for bad in ({**base, "start": -1}, {**base, "x": 1}, {**base, "after_line_id": 0},
                    {k: v for k, v in base.items() if k != "end"},
                    {**base, "expected_line_ids": ["a"]}):
            assert client.post(url, json=bad).status_code == 422, bad
        assert client.post(url, json={**base, "end": 7.1}).status_code == 422
        assert client.post(url, json={**base, "start": 5.0}).status_code == 409


REMOTE = "https://baihe.example.com"


def _bare(email, *grants):
    u = auth_service.add_user(email)
    for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
        auth_service.revoke_permission(u["id"], p)
    for g in grants:
        auth_service.grant_permission(u["id"], g)
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


class TestPermissions:
    def test_gaps_need_lines_read_and_adding_needs_lines_edit(self, isolated_db):
        did, ids = _drama(isolated_db)
        c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                       raise_server_exceptions=False)
        url = f"/api/transcribe/dramas/{did}/gaps"
        body = {"expected_line_ids": ids, "start": 7.0, "end": 19.0, "after_line_id": ids[1]}
        assert c.get(url).status_code == 401
        assert c.post(url + "/add-lines", json=body).status_code == 401
        h = _bare("g1@example.com", "jobs.start", "lines.edit")
        assert c.get(url, headers=h).status_code == 403
        h = _bare("g2@example.com", "lines.read")
        assert c.get(url, headers=h).status_code == 200
        assert c.post(url + "/add-lines", json=body, headers=h).status_code == 403
        assert len(isolated_db.load_line_objects(did)) == 4
        h = _bare("g3@example.com", "lines.edit")
        assert c.post(url + "/add-lines", json=body, headers=h).status_code == 200
