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
