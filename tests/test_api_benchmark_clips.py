"""
Routes for the owner's clip set (api/routers/benchmark_routes.py): the
multipart upload, case exclude/edit, run delete and export. Who may call
what, the caps, and that no path or file name leaves the server.
"""
import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from tests.test_api_benchmark import BASE, _local, _remote

pytestmark = pytest.mark.usefixtures("whisper_present")

ROW = {"filename": "scene one.wav", "language": "ja", "transcript": "ありがとう",
       "translation": "Thank you", "title": "Show", "tags": ["music"]}


def _upload(client, rows=None, files=None, **fields):
    rows = [ROW] if rows is None else rows
    files = files if files is not None else [("files", ("scene one.wav", b"RIFFdata", "audio/wav"))]
    data = {"set_name": "mine", "clips": json.dumps(rows), **fields}
    # Multipart is not JSON, so a local_only route also needs the local header.
    return client.post(f"{BASE}/clips/import", data=data, files=files, headers={"X-Baihe-Local": "1"})


class TestImportRoute:
    def test_upload_makes_cases_and_returns_no_path_or_file_name(self, isolated_db):
        r = _upload(_local())
        assert r.status_code == 200, r.text
        out = r.json()
        assert (out["clips"], out["transcription_cases"], out["translation_cases"]) == (1, 1, 1)
        listed = _local().get(f"{BASE}/cases").json()["cases"]
        assert {c["stage"] for c in listed} == {"transcription", "translation"}
        assert all(c["has_input_file"] for c in listed if c["stage"] == "transcription")
        text = r.text + json.dumps(listed)
        stored = os.listdir(db.BENCHMARK_DIR)
        assert len(stored) == 1 and stored[0] not in text and "scene one" not in text
        assert db.BENCHMARK_DIR not in text

    def test_a_manifest_in_place_of_typed_rows(self, isolated_db):
        manifest = json.dumps({"audio": "scene one.wav", "language": "ko", "transcript": "안녕하세요"})
        r = _local().post(f"{BASE}/clips/import", data={"set_name": "m", "manifest": manifest},
                          files=[("files", ("scene one.wav", b"x", "audio/wav"))],
                          headers={"X-Baihe-Local": "1"})
        assert r.status_code == 200 and r.json()["translation_cases"] == 0

    @pytest.mark.parametrize("fields,files", [
        ({"clips": "nope"}, None),                                  # not JSON
        ({"clips": "{}"}, None),                                    # not a list
        ({"manifest": "{}"}, None),                                 # both given
        ({"set_name": ""}, None),
        ({}, [("files", ("scene one.wav", b"x", "audio/wav")), ("files", ("scene one.wav", b"y", "audio/wav"))]),
        ({}, [("files", ("scene one.txt", b"x", "text/plain"))]),
    ])
    def test_bad_forms_are_422_and_write_nothing(self, isolated_db, fields, files):
        r = _upload(_local(), files=files, **fields)
        assert r.status_code == 422, r.text
        assert db.list_benchmark_cases() == []
        assert not os.path.isdir(db.BENCHMARK_DIR) or os.listdir(db.BENCHMARK_DIR) == []

    def test_a_body_over_the_cap_is_413(self, isolated_db, monkeypatch):
        from services import benchmark_clip_service as clips
        from api.routers import benchmark_routes
        monkeypatch.setattr(clips, "MAX_IMPORT_BYTES", 10)
        monkeypatch.setattr(benchmark_routes, "_CLIP_OVERHEAD", 1_000)
        r = _upload(_local(), files=[("files", ("scene one.wav", b"x" * 5_000_000, "audio/wav"))])
        assert r.status_code == 413 and db.list_benchmark_cases() == []

    def test_pc_only(self, isolated_db):
        assert _upload(_remote()).status_code in (401, 403, 404)
        assert db.list_benchmark_cases() == []

    def test_check_saves_nothing_and_reports_the_clip(self, isolated_db):
        r = _local().post(f"{BASE}/clips/check", json={"clips": [{**ROW, "language": "zh"}]})
        assert r.status_code == 200
        (w,) = r.json()["warnings"]
        assert w["clip"] == "scene one.wav" and "Japanese" in w["message"]
        assert db.list_benchmark_cases() == []

    def test_check_rejects_unknown_fields(self, isolated_db):
        assert _local().post(f"{BASE}/clips/check", json={"clips": [{**ROW, "path": "/x"}]}).status_code == 422


class TestCaseControls:
    def _case(self):
        _upload(_local())
        return next(c for c in _local().get(f"{BASE}/cases").json()["cases"] if c["stage"] == "transcription")

    def test_exclude_edit_and_history(self, isolated_db):
        c = _local()
        case = self._case()
        r = c.post(f"{BASE}/cases/{case['id']}/excluded", json={"excluded": True})
        assert r.status_code == 200 and r.json()["excluded"] is True
        r = c.post(f"{BASE}/cases/{case['id']}/reference", json={"reference_text": "ありがとうございます"})
        assert r.status_code == 200 and r.json()["ref_version"] == 2
        (edit,) = c.get(f"{BASE}/cases/{case['id']}/edits").json()["edits"]
        assert edit["old_text"] == "ありがとう" and edit["new_text"] == "ありがとうございます"

    def test_writes_are_pc_only_and_unknown_case_is_404(self, isolated_db):
        case = self._case()
        assert _remote().post(f"{BASE}/cases/{case['id']}/excluded", json={"excluded": True}).status_code in (401, 403, 404)
        assert _local().post(f"{BASE}/cases/999/excluded", json={"excluded": True}).status_code == 404
        assert _local().post(f"{BASE}/cases/{case['id']}/reference", json={"reference_text": ""}).status_code == 422


class TestRunRoutes:
    def _run(self, monkeypatch):
        from tests.test_benchmark_clips import _scored_run
        _upload(_local())
        return _scored_run(monkeypatch, lambda c: "ありがとう")

    def test_delete_needs_confirm(self, isolated_db, monkeypatch):
        sid = self._run(monkeypatch)
        c = _local()
        assert c.post(f"{BASE}/runs/{sid}/delete", json={}).status_code == 422
        assert c.get(f"{BASE}/runs/{sid}").status_code == 200
        r = c.post(f"{BASE}/runs/{sid}/delete", json={"confirm": True})
        assert r.status_code == 200 and r.json() == {"deleted": True, "id": sid}
        assert c.get(f"{BASE}/runs/{sid}").status_code == 404
        assert c.post(f"{BASE}/runs/{sid}/delete", json={"confirm": True}).status_code == 404

    def test_export_headers_and_formats(self, isolated_db, monkeypatch):
        sid = self._run(monkeypatch)
        r = _local().get(f"{BASE}/runs/{sid}/export?format=csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert f"benchmark-run-{sid}.csv" in r.headers["content-disposition"]
        assert "ありがとう" in r.content.decode("utf-8-sig")
        j = _local().get(f"{BASE}/runs/{sid}/export?format=json").json()
        assert j["run"]["id"] == sid and db.BENCHMARK_DIR not in json.dumps(j)
        assert _local().get(f"{BASE}/runs/{sid}/export?format=xml").status_code == 422
        assert _local().get(f"{BASE}/runs/999/export").status_code == 404

    def test_comparison_export_and_arena_left_out(self, isolated_db, monkeypatch):
        from tests.test_benchmark_clips import _scored_run
        a = self._run(monkeypatch)
        b = _scored_run(monkeypatch, lambda c: "ありがと")
        c = _local()
        view = c.get(f"{BASE}/arena", params={"run_ids": [a, b]}).json()
        assert view["left_out"]["total"] == 0 and len(view["rows"]) == 1
        r = c.get(f"{BASE}/arena/export", params={"run_ids": [a, b], "format": "json"})
        assert r.status_code == 200 and r.json()["compared_cases"] == 1
        assert c.get(f"{BASE}/arena/export", params={"run_ids": [a]}).status_code == 422


class TestMaxSizeImport:
    def test_250_clips_of_4000_cjk_characters_fit(self, isolated_db, monkeypatch):
        from services import benchmark_clip_service as clips
        monkeypatch.setattr(clips, "check_room_for", lambda *_a, **_k: None)
        text = "你" * 4000
        rows = [{"filename": f"c{i}.wav", "language": "zh", "transcript": text} for i in range(250)]
        files = [("files", (f"c{i}.wav", b"RIFF", "audio/wav")) for i in range(250)]
        # ensure_ascii makes each character six bytes, the largest the field gets.
        assert len(json.dumps(rows)) > 6_000_000
        r = _upload(_local(), rows=rows, files=files)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["clips"] == 250
