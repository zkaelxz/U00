"""
The hard-sub case route (api/routers/benchmark_routes.py): PC only, strict body, fractions for the band,
and no picture name or path in the answer. The case logic itself is in tests/test_benchmark_hardsub.py.
"""
import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from tests.test_api_benchmark import BASE, LOCAL_HDR, _admin, _local, _remote
from tests.test_benchmark_hardsub import _title, ffmpeg  # noqa: F401


def _url(did):
    return f"{BASE}/dramas/{did}/hard-sub-cases"


class TestHardSubRoute:
    def test_the_owner_at_the_pc_adds_a_case_and_nothing_leaks(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        r = _local().post(_url(did), json={"line_ids": [ids[0]]}, headers=LOCAL_HDR)
        assert r.status_code == 200, r.text
        assert r.json() == {"set_name": "hard-sub", "added": 1, "skipped": 0, "unclear": 0}
        listed = _local().get(f"{BASE}/cases?stage=ocr").json()["cases"]
        assert listed[0]["hard_sub"] is True
        text = r.text + json.dumps(listed)
        assert all(name not in text for name in os.listdir(db.BENCHMARK_DIR)) and db.BENCHMARK_DIR not in text

    def test_a_remote_admin_is_refused_and_nothing_is_written(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        r = _remote().post(_url(did), json={"line_ids": [ids[0]]}, headers={**_admin(), **LOCAL_HDR})
        assert r.status_code == 403
        assert db.list_benchmark_cases("ocr") == [] and ffmpeg["calls"] == []

    @pytest.mark.parametrize("body", [
        {}, {"line_ids": []}, {"line_ids": list(range(1, 12))}, {"line_ids": [1], "path": "/tmp/x.mp4"},
        {"line_ids": [1], "band_top": "70%"}, {"line_ids": [1], "band_top": 1.5},
        {"line_ids": [1], "set_name": ""}])
    def test_the_body_is_strict_and_the_client_never_sends_a_path(self, isolated_db, ffmpeg, body):
        did, _ = _title(isolated_db)
        r = _local().post(_url(did), json=body, headers=LOCAL_HDR)
        assert r.status_code == 422, r.text
        assert ffmpeg["calls"] == []

    def test_a_band_that_is_too_thin_is_refused_by_the_service(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        r = _local().post(_url(did), json={"line_ids": [ids[0]], "band_top": 0.9, "band_bottom": 0.95},
                          headers=LOCAL_HDR)
        assert r.status_code == 422, r.text


class TestHardSubReadingResultRoute:
    def test_a_title_never_read_answers_not_found_at_the_pc(self, isolated_db):
        did = db.create_drama(title_en="Moon Drama", source_language="zh")
        r = _local().get(f"{BASE}/dramas/{did}/hard-sub-reading", headers=LOCAL_HDR)
        assert r.status_code == 404, r.text

    def test_a_remote_admin_is_refused(self, isolated_db):
        did = db.create_drama(title_en="Moon Drama", source_language="zh")
        r = _remote().get(f"{BASE}/dramas/{did}/hard-sub-reading", headers={**_admin(), **LOCAL_HDR})
        assert r.status_code == 403

    def test_a_finished_reading_is_returned_as_the_declared_shape(self, isolated_db):
        import background_jobs
        from jobs import job_launch
        from services import hardsub_speaker_read_service as svc
        did = db.create_drama(title_en="Moon Drama", source_language="zh")
        outcome = {"groups": [{"key": "white-left", "lines": [{"start": 0.0, "end": 3.0, "text": "你好"}]}],
                   "windows_read": 1, "windows_total": 1, "unreadable": 0, "unclear_pictures": 0,
                   "failed_reads": 0, "cost_usd": 0.01, "stopped": None, "note": None,
                   "comparison": {"groups": [{"key": "white-left", "characters": 2, "recall": 1.0}],
                                  "recall": 1.0, "precision": 1.0, "label_overlap": {}, "label_match": None}}
        jid = svc.job_id(did)
        job_launch.start_job(jid, lambda j: background_jobs.set_result(j, outcome), jid,
                             description="x", line_fields=())
        import time
        for _ in range(100):
            if (background_jobs.get_status(jid) or {}).get("status") == "done":
                break
            time.sleep(0.05)
        r = _local().get(f"{BASE}/dramas/{did}/hard-sub-reading", headers=LOCAL_HDR)
        assert r.status_code == 200, r.text
        assert r.json()["result"] == outcome
        background_jobs.clear_all_jobs()


    def test_a_corrupt_saved_file_is_not_found_not_a_server_error(self, isolated_db):
        from services import hardsub_speaker_read_service as svc
        did = db.create_drama(title_en="Moon Drama", source_language="zh")
        os.makedirs(db.drama_dir(did), exist_ok=True)
        with open(svc._result_path(did), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        r = _local().get(f"{BASE}/dramas/{did}/hard-sub-reading", headers=LOCAL_HDR)
        assert r.status_code == 404, r.text

    def test_a_saved_stopped_reading_carries_its_reason_and_no_path(self, isolated_db):
        from services import hardsub_speaker_read_service as svc
        did = db.create_drama(title_en="Moon Drama", source_language="zh")
        os.makedirs(db.drama_dir(did), exist_ok=True)
        outcome = {"groups": [], "windows_read": 3, "windows_total": 9, "unreadable": 0, "unclear_pictures": 0,
                   "failed_reads": 0, "cost_usd": 0.5, "stopped": "cap", "note": None,
                   "comparison": {"groups": [], "recall": None, "precision": None, "label_overlap": {},
                                  "label_match": None}}
        svc._save_result(did, outcome)
        r = _local().get(f"{BASE}/dramas/{did}/hard-sub-reading", headers=LOCAL_HDR)
        assert r.status_code == 200, r.text
        assert r.json()["result"]["stopped"] == "cap"
        assert db.drama_dir(did) not in r.text and svc.RESULT_FILE not in r.text
