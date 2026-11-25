"""services/quality_check_service.py: Diagnostics > Quality reads the newest release check result, judges
its age against the 7-day release rule, and returns no path or error text."""
import datetime
import json

import pytest

from services import quality_check_service as qc

NOW = datetime.datetime(2026, 11, 24, 12, 0)


def _write(folder, stamp, run):
    (folder / f"release-check-{stamp}.json").write_text(json.dumps(run), encoding="utf-8")


def _run(stamp, **extra):
    return {"stamp": stamp, "when": "ignored", "machine": "DESKTOP-V100", "gpu": "Tesla V100", "repeats": 5,
            "margin": 0.02, "passed": True,
            "results": [{"id": "title59-transcription", "kind": "transcription", "metric": "cer", "score": 0.642,
                         "baseline": 0.642, "status": "pass", "fixed_decoding": True, "scores": [0.642] * 5},
                        {"id": "title59-speakers", "kind": "diarization", "metric": "der", "score": None,
                         "baseline": 0.421, "status": "fail",
                         "error": "RuntimeError: C:\\Users\\Example\\audio.wav missing"}],
            **extra}


def test_no_result_yet(tmp_path):
    out = qc.latest(str(tmp_path), NOW)
    assert out["found"] is False and out["passed"] is False and out["clips"] == []


def test_reads_the_newest_result_and_its_age(tmp_path):
    _write(tmp_path, "20261101-090000", _run("20261101-090000", machine="OLD"))
    _write(tmp_path, "20261120-101500", _run("20261120-101500"))
    out = qc.latest(str(tmp_path), NOW)
    assert out["when"] == "2026-11-20 10:15" and out["age_days"] == 4 and out["fresh"] is True
    assert out["machine"] == "DESKTOP-V100" and out["passed"] is True and out["max_age_days"] == 7
    first, second = out["clips"]
    assert first == {"id": "title59-transcription", "kind": "transcription", "metric": "cer", "score": 0.642,
                     "baseline": 0.642, "status": "pass", "failed": False, "runs": 5, "fixed_decoding": True}
    assert second["status"] == "fail" and second["failed"] is True and second["score"] is None
    assert "Example" not in json.dumps(out)


def test_older_than_seven_days_is_not_fresh(tmp_path):
    _write(tmp_path, "20261116-110000", _run("20261116-110000"))
    out = qc.latest(str(tmp_path), NOW)
    assert out["age_days"] == 8 and out["fresh"] is False


def test_age_counts_calendar_days_as_promote_does(tmp_path):
    _write(tmp_path, "20261116-230000", _run("20261116-230000"))
    out = qc.latest(str(tmp_path), datetime.datetime(2026, 11, 24, 10, 0))
    assert out["age_days"] == 8 and out["fresh"] is False


def test_a_result_from_before_baselines_shows_no_baseline_and_no_pass(tmp_path):
    _write(tmp_path, "20261119-225928", {"when": "2026-11-19 22:59", "machine": "PC", "repeats": 5,
                                         "results": [{"id": "t60", "kind": "transcription", "metric": "cer",
                                                      "score": 0.652, "scores": [0.652] * 5}]})
    out = qc.latest(str(tmp_path), NOW)
    assert out["when"] == "2026-11-19 22:59" and out["passed"] is False
    assert out["clips"][0]["status"] == "none" and out["clips"][0]["baseline"] is None


def test_a_broken_file_is_unreadable_not_an_error(tmp_path):
    (tmp_path / "release-check-20261124-010000.json").write_text("{not json", encoding="utf-8")
    out = qc.latest(str(tmp_path), NOW)
    assert out["found"] is True and out["unreadable"] is True and out["passed"] is False


def test_the_route_returns_the_card(tmp_path, monkeypatch, isolated_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from api.api_config import ApiSettings
    from api.server import create_app
    _write(tmp_path, "20261123-101500", _run("20261123-101500"))
    monkeypatch.setattr(qc.portable, "data_dir", lambda: str(tmp_path))
    client = TestClient(create_app(ApiSettings(serve_frontend=False)), base_url="http://127.0.0.1:8600",
                        client=("127.0.0.1", 5000))
    r = client.get("/api/diagnostics/quality")
    assert r.status_code == 200
    assert r.json()["found"] is True and r.json()["clips"][0]["status"] == "pass"


def _hist_run(stamp, score, language=None, clip_id="title59-transcription"):
    row = {"id": clip_id, "kind": "transcription", "metric": "cer", "score": score, "baseline": 0.6}
    if language:
        row["language"] = language
    return {"stamp": stamp, "results": [row]}


def test_history_lists_each_clip_oldest_first_with_its_language(tmp_path):
    _write(tmp_path, "20261110-090000", _hist_run("20261110-090000", 0.60))
    _write(tmp_path, "20261120-101500", _hist_run("20261120-101500", 0.65, language="zh"))
    out = qc.history(str(tmp_path))
    assert out["runs"] == 2
    (clip,) = out["series"]
    # The older run did not record a language; a later one does, and the clip keeps it.
    assert clip["id"] == "title59-transcription" and clip["language"] == "zh"
    assert [(p["when"], p["score"]) for p in clip["points"]] == [("2026-11-10 09:00", 0.60), ("2026-11-20 10:15", 0.65)]


def test_history_merges_the_per_seed_rows_of_the_first_runs_into_their_median(tmp_path):
    run = {"stamp": "20261116-123137", "results": [
        {"id": f"title59-transcription@seed{n}", "kind": "transcription", "metric": "cer", "score": s}
        for n, s in enumerate((0.5, 0.9, 0.6), 1)]}
    _write(tmp_path, "20261116-123137", run)
    (clip,) = qc.history(str(tmp_path))["series"]
    assert clip["id"] == "title59-transcription" and clip["points"][0]["score"] == 0.6


def test_history_skips_a_file_it_cannot_read_and_an_empty_folder_is_empty(tmp_path):
    assert qc.history(str(tmp_path)) == {"runs": 0, "series": []}
    (tmp_path / "release-check-20261101-090000.json").write_text("{not json", encoding="utf-8")
    _write(tmp_path, "20261120-101500", _hist_run("20261120-101500", 0.65))
    out = qc.history(str(tmp_path))
    assert out["runs"] == 1 and out["series"][0]["language"] is None
