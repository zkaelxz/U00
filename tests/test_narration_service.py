"""
Tests for services/narration_service.py + /api/narration (Migration Slice
33). The LLM is a fake engine; the job body is called directly. No network.
"""
import os

import pytest
from fastapi.testclient import TestClient

import background_jobs
import dub
from api.server import ApiSettings, create_app
from services import narration_service, settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

NOVEL = "他说：你好。\n\n夜很深了。风很大。"


def _drama(db, novel=NOVEL, **fields):
    fields.setdefault("title_en", "N")
    fields.setdefault("content_mode", "novel_narration")
    did = db.create_drama(**fields)
    if novel is not None:
        with open(os.path.join(db.drama_dir(did), dub.NOVEL_SOURCE_FILENAME), "w",
                  encoding="utf-8") as f:
            f.write(novel)
    return did


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: "sk-secret" if k == "claude" else None)


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


class TestStart:
    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            narration_service.start_narration_run(999)

    def test_no_novel_source(self, isolated_db, key):
        did = _drama(isolated_db, novel=None)
        with pytest.raises(InvalidInputError):
            narration_service.start_narration_run(did)

    def test_bad_engine(self, isolated_db, key):
        did = _drama(isolated_db)
        with pytest.raises(InvalidInputError):
            narration_service.start_narration_run(did, "deepl")

    def test_no_key_is_503_type(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: None)
        did = _drama(isolated_db)
        with pytest.raises(DependencyUnavailableError):
            narration_service.start_narration_run(did)

    def test_starts_job(self, isolated_db, key, monkeypatch):
        did = _drama(isolated_db)
        calls = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **k: calls.append((job_id, a)) or True)
        assert narration_service.start_narration_run(did) == {"job_id": f"narration_{did}"}
        assert calls[0][1][3:] == ("claude", "sk-secret", None)

    def test_duplicate_conflict(self, isolated_db, key, monkeypatch):
        did = _drama(isolated_db)
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            narration_service.start_narration_run(did)


class _Engine:
    supports_reference = True
    model = "fake"


class TestJob:
    def _run(self, db, did, monkeypatch, speakers):
        import translate_engines
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: _Engine())
        monkeypatch.setattr(translate_engines, "tag_speakers_by_id",
                            lambda chunks, engine, known, **k: speakers(chunks))
        job_id = f"narration_{did}"
        narration_service._run_narration_job(job_id, did, NOVEL, "claude", "k", None)

    def test_replaces_lines_tags_speakers_and_snapshots(self, isolated_db, monkeypatch):
        from core import Line
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="旧", en="old")])
        self._run(isolated_db, did, monkeypatch,
                  lambda ch: {i: ("Ann" if "说" in c else "Narrator") for i, c in ch.items()})
        lines = isolated_db.load_line_objects(did)
        assert [ln.zh for ln in lines] and all(ln.id for ln in lines)
        assert {ln.speaker for ln in lines} == {"Ann", "Narrator"}
        assert all("旧" not in ln.zh for ln in lines)
        assert isolated_db.get_drama(did)["status"] == "aligned"
        labels = {c["speaker_label"] for c in isolated_db.list_characters(did)}
        assert {"Ann", "Narrator"} <= labels

    def test_labels_attach_by_idx_missing_and_extra_ids_are_safe(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {1: "Ann", 999: "Ghost"})
        lines = isolated_db.load_line_objects(did)
        assert len(lines) > 1
        assert [ln.speaker for ln in lines] == ["Ann" if i == 1 else "Narrator" for i in range(len(lines))]
        assert "Ghost" not in {c["speaker_label"] for c in isolated_db.list_characters(did)}

    def test_failure_redacts_secret_in_job_error(self, isolated_db, monkeypatch, key):
        did = _drama(isolated_db)
        import translate_engines
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: _Engine())

        def boom(*a, **k):
            raise RuntimeError("bad key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
        monkeypatch.setattr(translate_engines, "tag_speakers_by_id", boom)
        job_id = f"narration_{did}"
        assert background_jobs.start_job(
            job_id, narration_service._run_narration_job, job_id, did, NOVEL, "claude", "k", None)
        import time
        for _ in range(100):
            st = background_jobs.get_status(job_id)
            if st["status"] != "running":
                break
            time.sleep(0.05)
        assert st["status"] == "error"
        assert "abcdefghijklmnop" not in (st["error"] or "")
        assert isolated_db.load_line_objects(did) == []


class TestApi:
    def test_config(self, client, isolated_db, key):
        did = _drama(isolated_db)
        body = client.get(f"/api/narration/dramas/{did}/config").json()
        assert body["has_novel_source"] and body["is_narration"]
        assert {e["key"]: e["key_configured"] for e in body["engines"]}["claude"] is True
        assert "sk-secret" not in str(body) and "novel_narration_source" not in str(body)
        assert client.get("/api/narration/dramas/999/config").status_code == 404

    def test_run_status_codes(self, client, isolated_db, key, monkeypatch):
        assert client.post("/api/narration/dramas/999/run", json={}).status_code == 404
        no_src = _drama(isolated_db, novel=None)
        assert client.post(f"/api/narration/dramas/{no_src}/run", json={}).status_code == 422
        did = _drama(isolated_db)
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
        r = client.post(f"/api/narration/dramas/{did}/run", json={})
        assert r.status_code == 200 and r.json() == {"job_id": f"narration_{did}"}
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
        assert client.post(f"/api/narration/dramas/{did}/run", json={}).status_code == 409
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: None)
        assert client.post(f"/api/narration/dramas/{did}/run", json={}).status_code == 503
