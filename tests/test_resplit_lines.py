"""Re-split over-long lines in place (services/restructure_service.resplit_long_lines and
POST /api/restructure/dramas/{id}/resplit). Mocked: no GPU, no aligner model, no audio."""
import os
import time

import pytest

import background_jobs
import db
import diarize
from core import Line
from services import restructure_service as svc
from services.service_errors import ConflictError, InvalidInputError

SENT = "我今天早上很早就起床了然后去公园跑步。"  # 19 CJK chars
LONG = SENT * 3  # 57 chars, 30 s: over both limits
SHORT = "你好。"


@pytest.fixture(autouse=True)
def _env(isolated_db):
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _seed(en="", manual=False, audio=True):
    did = db.create_drama(title_zh="D", source_language="zh",
                          audio_filename="audio.wav" if audio else None)
    if audio:
        with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as f:
            f.write(b"x")
    db.save_lines(did, [
        Line(idx=0, start=0.0, end=2.0, zh=SHORT, speaker="A"),
        Line(idx=1, start=2.0, end=32.0, zh=LONG, en=en, speaker="B", speaker_manual=manual,
             flag="unsure", flag_note="n"),
        Line(idx=2, start=32.0, end=34.0, zh=SHORT, speaker="A")])
    return did, [r["id"] for r in db.load_lines(did)]


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_proportional_split_in_place_keeps_parent_id_flag_speaker():
    did, ids = _seed()
    r = svc.resplit_long_lines(did, ids)
    assert (r["split_lines"], r["lines_before"], r["line_count"]) == (1, 3, 5)
    assert r["timing"] == "proportional" and r["speakers_reassigned"] is False
    rows = db.load_lines(did)
    assert len(rows) == 5 and rows[1]["id"] == ids[1] and rows[1]["flag"] == "unsure"
    assert [x["zh"] for x in rows[1:4]] == [SENT] * 3
    assert [x["speaker"] for x in rows[1:4]] == ["B"] * 3
    assert rows[1]["start"] == 2.0 and rows[3]["end"] == 32.0
    assert all(a["end"] == pytest.approx(b["start"]) for a, b in zip(rows[1:3], rows[2:4]))
    assert any(h["label"] == "before re-split" for h in db.list_line_history(did))


def test_translated_long_line_needs_confirm_then_clears_en():
    did, ids = _seed(en="old translation")
    with pytest.raises(InvalidInputError):
        svc.resplit_long_lines(did, ids)
    assert len(db.load_lines(did)) == 3
    r = svc.resplit_long_lines(did, ids, confirm=True)
    assert r["cleared_translations"] == 1
    assert [x["en"] for x in db.load_lines(did)[1:4]] == ["", "", ""]


def test_saved_turns_relabel_pieces_and_keep_manual():
    did, ids = _seed()
    diarize.save_turns(db.drama_dir(did), [{"start": 0.0, "end": 12.0, "speaker": "S1"},
                                           {"start": 12.0, "end": 40.0, "speaker": "S2"}])
    r = svc.resplit_long_lines(did, ids)
    assert r["speakers_reassigned"] is True
    assert [x["speaker"] for x in db.load_lines(did)[1:4]] == ["S1", "S2", "S2"]
    did, ids = _seed(manual=True)
    diarize.save_turns(db.drama_dir(did), [{"start": 0.0, "end": 40.0, "speaker": "S1"}])
    svc.resplit_long_lines(did, ids)
    assert [x["speaker"] for x in db.load_lines(did)[1:4]] == ["B"] * 3


def test_nothing_to_split_and_stale_ids():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=2, zh=SHORT)])
    ids = [r["id"] for r in db.load_lines(did)]
    assert svc.resplit_long_lines(did, ids)["split_lines"] == 0
    with pytest.raises(ConflictError):
        svc.resplit_long_lines(did, [999])


def _fake_aligner(monkeypatch, fn):
    import forced_align
    monkeypatch.setattr(forced_align, "align_with_qwen3", fn)


def test_align_job_uses_aligner_boundaries(monkeypatch):
    did, ids = _seed()
    seen = {}

    def align(audio, texts, segs, language, use_gpu=False):
        seen.update(texts=texts, segs=segs, audio=os.path.basename(audio))
        return [Line(idx=i, start=2.0 + 9 * i + 1, end=2.0 + 9 * (i + 1), zh=t)
                for i, t in enumerate(texts)]
    _fake_aligner(monkeypatch, align)
    r = svc.resplit_long_lines(did, ids, align_to_audio=True)
    assert r["job_id"] == f"resplit_{did}"
    job = _wait(r["job_id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["timing"] == "aligned" and res["aligned_lines"] == 1 and res["split_lines"] == 1
    assert seen["texts"] == [SENT] * 3 and seen["audio"] == "audio.wav"
    rows = db.load_lines(did)
    assert [x["start"] for x in rows[1:4]] == [3.0, 12.0, 21.0]


def test_align_falls_back_to_proportional_when_aligner_missing(monkeypatch):
    did, ids = _seed()

    def align(*a, **k):
        raise ImportError("No module named 'qwen_asr'")
    _fake_aligner(monkeypatch, align)
    r = svc.resplit_long_lines(did, ids, align_to_audio=True)
    res = _wait(r["job_id"])["result"]
    assert res["timing"] == "proportional" and "estimated timing" in res["note"]
    assert res["split_lines"] == 1 and len(db.load_lines(did)) == 5


def test_align_without_audio_splits_immediately_with_note():
    did, ids = _seed(audio=False)
    r = svc.resplit_long_lines(did, ids, align_to_audio=True)
    assert r["timing"] == "proportional" and "no stored audio" in r["note"]
    assert len(db.load_lines(did)) == 5


def test_bad_aligner_timing_keeps_that_line_proportional(monkeypatch):
    did, ids = _seed()
    _fake_aligner(monkeypatch, lambda audio, texts, segs, language, use_gpu=False: [
        Line(idx=i, start=5.0, end=4.0, zh=t) for i, t in enumerate(texts)])
    res = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])["result"]
    assert res["timing"] == "proportional" and "unusable" in res["note"]


def test_cancelled_job_writes_nothing(monkeypatch):
    did, ids = _seed()

    def align(audio, texts, segs, language, use_gpu=False):
        background_jobs.request_cancel(f"resplit_{did}")
        return [Line(idx=i, start=3.0 + i, end=4.0 + i, zh=t) for i, t in enumerate(texts)]
    _fake_aligner(monkeypatch, align)
    res = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])["result"]
    assert res["failed_reason"] == "cancelled" and len(db.load_lines(did)) == 3


def test_refused_while_a_job_runs():
    did, ids = _seed()
    background_jobs.start_job(f"translate_{did}", lambda: time.sleep(0.5))
    with pytest.raises(ConflictError):
        svc.resplit_long_lines(did, ids)


def test_route(isolated_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    did, ids = _seed()
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                   base_url="http://127.0.0.1:8600")
    bad = c.post(f"/api/restructure/dramas/{did}/resplit", json={"expected_line_ids": ids, "x": 1})
    assert bad.status_code == 422
    r = c.post(f"/api/restructure/dramas/{did}/resplit", json={"expected_line_ids": ids})
    assert r.status_code == 200, r.text
    assert r.json()["split_lines"] == 1 and r.json()["line_count"] == 5


# ---- per-speaker time summary from the saved turns ----

from services import diarization_service  # noqa: E402
from services.service_errors import NotFoundError  # noqa: E402


def test_speaker_time_summary_overlap_and_order(monkeypatch):
    did, _ = _seed()
    diarize.save_turns(db.drama_dir(did), [
        {"start": 0, "end": 10, "speaker": "A"}, {"start": 8, "end": 12, "speaker": "B"},
        {"start": 20, "end": 30, "speaker": "A"}, {"start": 5, "end": 5, "speaker": "C"}])
    from services import transcribe_service
    monkeypatch.setattr(transcribe_service, "_audio_duration_seconds", lambda p: 50.0)
    s = diarization_service.speaker_time_summary(did)
    assert [x["label"] for x in s["speakers"]] == ["A", "B"]
    assert s["speakers"][0] == {"label": "A", "seconds": 20.0, "percent": 83.3, "turns": 2}
    assert s["total_speech_seconds"] == 24.0
    assert s["uncovered_seconds"] == 28.0  # union is 0-12 and 20-30 = 22 of 50


def test_speaker_time_summary_none_without_turns_and_404():
    did, _ = _seed()
    assert diarization_service.speaker_time_summary(did) is None
    with pytest.raises(NotFoundError):
        diarization_service.speaker_time_summary(99999)


def test_config_route_carries_summary(isolated_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    did, _ = _seed(audio=False)
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                   base_url="http://127.0.0.1:8600")
    assert c.get(f"/api/diarization/dramas/{did}/config").json()["speaker_summary"] is None
    diarize.save_turns(db.drama_dir(did), [{"start": 0, "end": 4, "speaker": "A"}])
    body = c.get(f"/api/diarization/dramas/{did}/config").json()["speaker_summary"]
    assert body == {"speakers": [{"label": "A", "seconds": 4.0, "percent": 100.0, "turns": 1}],
                    "total_speech_seconds": 4.0, "uncovered_seconds": None}
