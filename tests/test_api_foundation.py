"""
Tests for the FastAPI foundation (api/): startup, health/meta, the
Library endpoints' contract, the shared error shape, and the CORS /
configuration rules. Uses FastAPI's TestClient against an
`isolated_db` library -- no server process, no network.

FastAPI is a core dependency (requirements-core.txt); `httpx` (TestClient's
transport) is dev-only. The importorskips below keep a partial install
from erroring instead of skipping.
"""

import os
import sys
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient's transport

from fastapi.testclient import TestClient

import background_jobs
from jobs import job_launch
from api.api_config import ApiSettings, load_settings
from api.server import create_app
from services import diarization_service
import whisper_models
from core import Line


@pytest.fixture(autouse=True)
def _no_ollama_probe(monkeypatch):
    """GET /api/translate/engines checks whether Ollama answers; no test may make that network call."""
    monkeypatch.setattr("services.translate_run_service.ollama_reachable", lambda: False)


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])
    return body["error"]


class TestStartupAndSystem:
    def test_create_app_touches_no_database(self, isolated_db, monkeypatch):
        def boom():
            raise AssertionError("create_app opened the database")
        monkeypatch.setattr(isolated_db, "get_conn", boom)
        create_app(ApiSettings())

    def test_health(self, local_client):
        resp = local_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_meta(self, local_client):
        body = local_client.get("/api/meta").json()
        assert body["app"] == "Baihe Studio"
        assert body["api_version"] == "0.1"
        assert body["environment"] == "production"

    def test_openapi_documents_the_library_contract(self, local_client):
        spec = local_client.get("/api/openapi.json").json()
        assert "/api/library/dramas" in spec["paths"]
        assert "/api/library/dramas/{drama_id}" in spec["paths"]
        assert "DramaSummary" in spec["components"]["schemas"]
        assert local_client.get("/api/docs").status_code == 200


class TestLibraryEndpoints:
    def test_list_returns_contract_shape(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_zh="白河", title_en="Baihe",
                                       custom_tags="Favorite, bl", audio_filename="source.mp3")
        body = local_client.get("/api/library/dramas").json()
        assert body["count"] == 1
        item = body["items"][0]
        assert item["id"] == did
        assert item["title_zh"] == "白河"
        assert item["custom_tags"] == ["Favorite", "bl"]
        # Internals stay out of the contract.
        assert "audio_filename" not in item
        assert "last_translate_errors" not in item

    def test_list_filters_match_the_service(self, local_client, isolated_db):
        isolated_db.create_drama(title_en="Keep", custom_tags="favorite, bl", status="aligned")
        isolated_db.create_drama(title_en="Drop", custom_tags="bl", status="aligned")
        resp = local_client.get("/api/library/dramas",
                          params={"quick_filter": "Favorite", "tag": ["bl"], "status": "aligned"})
        assert [d["title_en"] for d in resp.json()["items"]] == ["Keep"]

    def test_list_empty_library(self, local_client):
        assert local_client.get("/api/library/dramas").json() == {"items": [], "count": 0}

    def test_detail_reduces_filenames_to_flags(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="X", audio_filename="source.mp3",
                                       summary="s")
        body = local_client.get(f"/api/library/dramas/{did}").json()
        assert body["summary"] == "s"
        assert body["has_audio"] is True
        assert body["has_cover_art"] is False
        assert not any(k.endswith("_filename") for k in body)

    def test_unknown_drama_is_404(self, local_client):
        resp = local_client.get("/api/library/dramas/999")
        assert resp.status_code == 404
        assert _error(resp)["code"] == "not_found"

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5"])
    def test_bad_drama_id_is_422(self, local_client, bad):
        resp = local_client.get(f"/api/library/dramas/{bad}")
        assert resp.status_code == 422
        assert _error(resp)["code"] == "validation_error"

    def test_bad_quick_filter_is_422_with_allowed_values(self, local_client):
        resp = local_client.get("/api/library/dramas", params={"quick_filter": "nope"})
        assert resp.status_code == 422
        err = _error(resp)
        assert err["code"] == "validation_error"
        assert "Favorite" in err["details"]["allowed"]

    def test_overlong_search_is_422(self, local_client):
        resp = local_client.get("/api/library/dramas", params={"search": "x" * 500})
        assert resp.status_code == 422


class TestErrorShape:
    def test_unknown_route_uses_error_shape(self, local_client):
        resp = local_client.get("/api/does-not-exist")
        assert resp.status_code == 404
        assert _error(resp)["code"] == "not_found"

    def test_wrong_method_is_unsupported_operation(self, local_client):
        resp = local_client.delete("/api/library/dramas")
        assert resp.status_code == 405
        assert _error(resp)["code"] == "unsupported_operation"

    def test_validation_error_never_echoes_the_input(self, local_client):
        secret = "sk-ant-api03-" + "A" * 40
        resp = local_client.get(f"/api/library/dramas/{secret}")
        assert resp.status_code == 422
        assert secret not in resp.text

    def test_unexpected_error_is_generic_500_without_internals(self, local_client, monkeypatch):
        from services import library_service

        def explode(**_):
            raise RuntimeError("/home/someone/library/library.db is locked; key=sk-ant-XYZ")
        monkeypatch.setattr(library_service, "list_library_dramas", explode)
        resp = local_client.get("/api/library/dramas")
        assert resp.status_code == 500
        err = _error(resp)
        assert err["code"] == "internal_error"
        assert "library.db" not in resp.text and "sk-ant" not in resp.text
        assert "Traceback" not in resp.text

    @pytest.mark.parametrize("method, path", [
        ("GET", "/api/benchmark/runs/{big}"),
        ("GET", "/api/benchmark/runs/{big}/export"),
        ("GET", "/api/benchmark/cases/{big}/edits"),
        ("POST", "/api/models/reeval/candidates/{big}/reject"),
        ("POST", "/api/models/reeval/candidates/{big}/reopen"),
        ("POST", "/api/translate-run/dramas/{did}/bulk/{big}/cancel"),
    ])
    def test_an_id_past_sqlites_range_is_invalid_not_a_500(self, local_client, isolated_db, method, path):
        # These ids have no upper bound on their route; the lookup overflowed.
        did = isolated_db.create_drama(title_en="D") if "{did}" in path else 0
        resp = local_client.request(method, path.format(big=2**70, did=did), json={})
        assert resp.status_code == 422, resp.text
        assert _error(resp)["code"] == "validation_error"

    def test_an_unrelated_overflow_is_still_a_500(self, local_client, monkeypatch):
        from services import library_service

        def explode(**_):
            raise OverflowError("math range error")
        monkeypatch.setattr(library_service, "list_library_dramas", explode)
        assert local_client.get("/api/library/dramas").status_code == 500

    @pytest.mark.parametrize("exc_name, status, code", [
        ("UnsupportedOperationError", 400, "unsupported_operation"),
        ("DependencyUnavailableError", 503, "dependency_unavailable"),
        ("ForbiddenError", 403, "forbidden"),
        ("RateLimitedError", 429, "rate_limited"),
        ("ServiceError", 500, "application_error"),
    ])
    def test_service_errors_map_to_status_and_code(self, local_client, monkeypatch,
                                                   exc_name, status, code):
        from services import library_service, service_errors
        exc_cls = getattr(service_errors, exc_name)

        def fail(**_):
            raise exc_cls("Needs something.")
        monkeypatch.setattr(library_service, "list_library_dramas", fail)
        resp = local_client.get("/api/library/dramas")
        assert resp.status_code == status
        assert _error(resp) == {"code": code, "message": "Needs something."}

    def test_service_error_message_is_redacted(self, local_client, monkeypatch):
        from services import library_service
        from services.service_errors import ServiceError
        key = "sk-ant-api03-" + "B" * 40

        def fail(**_):
            raise ServiceError(f"provider said no for {key}")
        monkeypatch.setattr(library_service, "list_library_dramas", fail)
        resp = local_client.get("/api/library/dramas")
        assert key not in resp.text


class TestConfigAndCors:
    def test_defaults_are_loopback_production_no_cors(self):
        s = load_settings({})
        assert (s.host, s.port, s.environment, s.cors_origins) == (
            "127.0.0.1", 8600, "production", ())

    def test_development_gets_the_vite_origins(self):
        s = load_settings({"BAIHE_API_ENV": "development"})
        assert "http://localhost:5173" in s.cors_origins

    def test_wildcard_origin_is_refused(self):
        with pytest.raises(ValueError):
            load_settings({"BAIHE_API_ENV": "development", "BAIHE_API_CORS_ORIGINS": "*"})

    @pytest.mark.parametrize("env", [{"BAIHE_API_PORT": "abc"}, {"BAIHE_API_PORT": "70000"},
                                     {"BAIHE_API_ENV": "staging"}])
    def test_bad_settings_fail_loudly(self, env):
        with pytest.raises(ValueError):
            load_settings(env)

    def test_production_sends_no_cors_headers(self, local_client):
        resp = local_client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in resp.headers

    def test_development_allows_only_listed_origins(self, isolated_db):
        dev = TestClient(create_app(load_settings({"BAIHE_API_ENV": "development"})))
        ok = dev.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
        evil = dev.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in evil.headers


class TestReaderEndpoint:
    """Migration Slice 4: served definitions must come only from cache,
    never a live/paid lookup -- mocks reader.build_reader_html the same
    way tests/test_reader_service.py does, so this stays independent of
    jieba/sudachipy/kiwipiepy (optional, not installed in a core-only env)."""

    def _stub_render(self, monkeypatch):
        import reader
        def fake(lines, source_language, definitions, **kw):
            return f"<!DOCTYPE html><body>{len(lines)} lines</body>"
        monkeypatch.setattr(reader, "build_reader_html", fake)

    def test_page_contract_shape(self, local_client, isolated_db, monkeypatch):
        self._stub_render(monkeypatch)
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=i, start=float(i), end=float(i + 1),
                                          zh="x", en="y") for i in range(5)])
        body = local_client.get(f"/api/reader/dramas/{did}/page").json()
        assert body["page"] == 1
        assert body["page_count"] == 1
        assert body["total_lines"] == 5
        assert "<!DOCTYPE html>" in body["html"]

    def test_pagination_query_params(self, local_client, isolated_db, monkeypatch):
        self._stub_render(monkeypatch)
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=i, start=float(i), end=float(i + 1),
                                          zh="x", en="y") for i in range(25)])
        body = local_client.get(f"/api/reader/dramas/{did}/page",
                          params={"page": 2, "chapter_size": 10}).json()
        assert body["page"] == 2
        assert body["page_count"] == 3
        assert "10 lines" in body["html"]

    def test_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.get("/api/reader/dramas/999999/page")
        assert resp.status_code == 404
        assert _error(resp)["code"] == "not_found"

    def test_drama_with_no_lines_is_404(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="Empty")
        resp = local_client.get(f"/api/reader/dramas/{did}/page")
        assert resp.status_code == 404

    def test_page_past_the_end_is_422(self, local_client, isolated_db, monkeypatch):
        self._stub_render(monkeypatch)
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="x", en="y")])
        resp = local_client.get(f"/api/reader/dramas/{did}/page", params={"page": 99})
        assert resp.status_code == 422
        assert _error(resp)["code"] == "validation_error"

    def test_bad_drama_id_is_422(self, local_client, isolated_db):
        resp = local_client.get("/api/reader/dramas/0/page")
        assert resp.status_code == 422

    def test_chapter_size_out_of_range_is_422(self, local_client, isolated_db, monkeypatch):
        self._stub_render(monkeypatch)
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="x", en="y")])
        resp = local_client.get(f"/api/reader/dramas/{did}/page", params={"chapter_size": 999})
        assert resp.status_code == 422

    def test_never_makes_a_live_dictionary_lookup_over_http(self, local_client, isolated_db, monkeypatch):
        self._stub_render(monkeypatch)
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="x", en="y")])

        def boom(*a, **k):
            raise AssertionError("the reader endpoint made a live dictionary lookup call")
        from services import reader_service
        monkeypatch.setattr(reader_service, "_define_words_llm", boom)
        resp = local_client.get(f"/api/reader/dramas/{did}/page")
        assert resp.status_code == 200


class TestDiagnosticsEndpoint:
    """Migration Slice 5: a read-only Diagnostics overview over HTTP --
    no admin action, contract-shaped the same way diagnostics_service's
    own unit tests check the underlying dict."""

    def test_overview_contract_shape(self, local_client, isolated_db):
        body = local_client.get("/api/diagnostics").json()
        assert set(body) == {
            "dependencies", "file_completeness", "library_writable", "gpu",
            "model_engine_versions", "running_jobs", "recent_log_lines",
        }
        assert isinstance(body["dependencies"], dict)
        assert "fastapi" in body["dependencies"]
        assert isinstance(body["file_completeness"]["all_present"], bool)

    def test_no_admin_action_is_exposed(self, local_client, isolated_db):
        # Only a GET is offered; no install/upgrade/delete verb exists here.
        assert local_client.post("/api/diagnostics").status_code == 405
        assert local_client.delete("/api/diagnostics").status_code == 405


class TestJobsEndpoint:
    """Migration Slice 8: a read-only, cross-process job list over HTTP,
    reading Migration Slice 7's job_records mirror. No cancel endpoint --
    see api/routers/jobs_routes.py's own docstring for why."""

    def test_empty_list_when_no_jobs_recorded(self, local_client, isolated_db):
        body = local_client.get("/api/jobs").json()
        assert body == {"items": [], "count": 0}

    def test_lists_a_recorded_job(self, local_client, isolated_db):
        import db
        db.save_job_record("j1", status="running", progress=0.5, description="Translating")
        body = local_client.get("/api/jobs").json()
        assert body["count"] == 1
        assert body["items"][0]["job_id"] == "j1"
        assert body["items"][0]["status"] == "running"

    def test_get_one_job(self, local_client, isolated_db):
        import db
        db.save_job_record("j1", status="done", progress=1.0)
        body = local_client.get("/api/jobs/j1").json()
        assert body["status"] == "done"

    def test_running_job_without_recent_progress_is_flagged_stalled(self, local_client, isolated_db):
        import time
        import background_jobs
        from jobs import job_table
        isolated_db.save_job_record("stall1", status="running", progress=0.2, message="Transcribing")
        old = time.time() - background_jobs.JOB_STALL_SECONDS - 60
        job_table._jobs["stall1"] = {
            "status": "running", "progress": 0.2, "message": "Transcribing", "error": None,
            "started_at": old, "progress_at": old, "cancel_requested": False, "result": None}
        try:
            body = local_client.get("/api/jobs/stall1").json()
            assert body["status"] == "running" and body["stalled"] is True
            assert "may be stalled" in body["message"]
            job_table._jobs["stall1"]["progress_at"] = time.time()
            body = local_client.get("/api/jobs/stall1").json()
            assert body["stalled"] is False and "stalled" not in body["message"]
        finally:
            background_jobs.clear_job("stall1")

    def test_unknown_job_is_404(self, local_client, isolated_db):
        resp = local_client.get("/api/jobs/nope")
        assert resp.status_code == 404
        assert _error(resp)["code"] == "not_found"

    def test_no_delete_endpoint_is_exposed(self, local_client, isolated_db):
        # Cancel exists since Migration Slice 22 (tests/test_api_job_cancel.py).
        import db
        db.save_job_record("j1", status="running")
        assert local_client.delete("/api/jobs/j1").status_code == 405


class TestSettingsEndpoint:
    """Migration Slice 10: a read-only settings overview over HTTP --
    engine key presence only, never a value (D2). No write route exists
    here; see api/routers/settings_routes.py's own docstring for why."""

    def test_overview_contract_shape(self, local_client, isolated_db):
        body = local_client.get("/api/settings").json()
        assert set(body) == {"engine_keys", "gpu_limit_enabled", "gpu_max_parallel", "unload_ollama_before_transcribe", "notify_on_completion",
                             "use_gpu", "gemini_free_tier", "bulk_auto_resume", "offer_provider_models", "remote_uploads", "preferences", "endpoints",
                             "upload_max_mb_from_env", "effective_upload_max_mb",
                             "monthly_cap_env_usd", "effective_monthly_cap_usd", "month_spend_usd",
                             "month_spend_counted_usd", "month_spend_reset_at", "choices"}
        assert isinstance(body["engine_keys"], dict)
        assert "claude" in body["engine_keys"]
        assert "monthly_cap_usd" not in body["engine_keys"]
        for value in body["engine_keys"].values():
            assert isinstance(value, bool)

    def test_overview_never_leaks_a_key_value(self, local_client, isolated_db, monkeypatch):
        monkeypatch.setenv("BAIHE_CLAUDE_KEY", "sk-should-not-leak")
        resp = local_client.get("/api/settings")
        assert "sk-should-not-leak" not in resp.text
        assert resp.json()["engine_keys"]["claude"] is True

    def test_only_non_secret_bool_writes_are_exposed(self, local_client, isolated_db):
        # Slice 23: POST takes booleans only; a key-shaped field is refused.
        assert local_client.post("/api/settings", json={"claude": "sk-x"}).status_code == 422
        assert local_client.delete("/api/settings").status_code == 405


class TestTranslateEndpoints:
    """Migration Slice 11: read-only Translate-standalone endpoints --
    engine metadata and history. Migration Slice 13 adds the translate
    action itself (POST /api/translate). Clearing history
    (DELETE /api/translate/history) is its own endpoint -- see
    TestTranslateHistoryClearEndpoint below."""

    def test_engines_contract_shape(self, local_client, isolated_db):
        body = local_client.get("/api/translate/engines").json()
        names = {e["name"] for e in body["items"]}
        assert "claude" in names
        for e in body["items"]:
            assert isinstance(e["free"], bool)
            assert isinstance(e["key_configured"], bool)

    def test_engines_name_settings_default_engine(self, local_client, isolated_db):
        from services import settings_service
        body = local_client.get("/api/translate/engines").json()
        assert body["default_engine"] == settings_service.get("default_engine")
        assert body["default_engine"] in {e["name"] for e in body["items"]}

    def test_engines_never_leak_a_key_value(self, local_client, isolated_db, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
        resp = local_client.get("/api/translate/engines")
        assert "sk-should-not-leak" not in resp.text

    def test_empty_history(self, local_client, isolated_db):
        body = local_client.get("/api/translate/history").json()
        assert body == {"items": []}

    def test_history_reflects_saved_translations(self, local_client, isolated_db):
        isolated_db.save_translate_history("zh", "en", "fake", "你好", "[TEST] Hello")
        body = local_client.get("/api/translate/history").json()
        assert len(body["items"]) == 1
        assert body["items"][0]["source_text"] == "你好"

    def test_translate_with_test_offline_engine(self, local_client, isolated_db):
        resp = local_client.post("/api/translate", json={
            "text": "你好", "engine": "fake",
            "source_language": "zh", "target_language": "en",
        })
        assert resp.status_code == 200
        assert resp.json() == {"translated_text": "[TEST] 你好"}
        history = local_client.get("/api/translate/history").json()["items"]
        assert len(history) == 1

    def test_translate_never_accepts_or_leaks_a_key_field(self, local_client, isolated_db):
        resp = local_client.post("/api/translate", json={
            "text": "你好", "engine": "fake",
            "source_language": "zh", "target_language": "en",
            "api_key": "sk-should-be-ignored",
        })
        assert resp.status_code == 200
        assert "sk-should-be-ignored" not in resp.text

    def test_translate_unknown_engine_is_422(self, local_client, isolated_db):
        resp = local_client.post("/api/translate", json={
            "text": "hi", "engine": "not_a_real_engine",
            "source_language": "zh", "target_language": "en",
        })
        assert resp.status_code == 422
        assert _error(resp)["code"] == "validation_error"

    def test_translate_missing_key_is_503(self, local_client, isolated_db, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("BAIHE_CLAUDE_KEY", raising=False)
        resp = local_client.post("/api/translate", json={
            "text": "hi", "engine": "claude",
            "source_language": "zh", "target_language": "en",
        })
        assert resp.status_code == 503
        assert _error(resp)["code"] == "dependency_unavailable"
        # A missing key is told apart from a missing package by its details.
        assert resp.json()["error"]["details"] == {"reason": "no_key", "engine": "claude"}
        assert "No claude key is configured" in _error(resp)["message"]

    def test_translate_unsupported_direction_is_400(self, local_client, isolated_db, monkeypatch):
        import translate_engines
        monkeypatch.setattr(translate_engines, "standalone_direction_support",
                            lambda *a: (False, "Not supported."))
        resp = local_client.post("/api/translate", json={
            "text": "hello", "engine": "fake_mt",
            "source_language": "en", "target_language": "zh",
        })
        assert resp.status_code == 400
        assert _error(resp)["code"] == "unsupported_operation"


class TestExportReadinessEndpoint:
    """Migration Slice 12: a drama's read-only export-readiness summary.
    Never flags a line, never generates a file; see
    api/routers/export_routes.py's own docstring for why."""

    def test_readiness_contract_shape(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        body = local_client.get(f"/api/export/dramas/{did}/readiness").json()
        assert body == {
            "drama_id": did, "total_lines": 1, "zh_filled": 1, "en_filled": 1,
            "fully_translated": True,
            "overlap_count": 0, "auto_qc_issue_count": 0, "dense_line_count": 0,
        }

    def test_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.get("/api/export/dramas/999999/readiness")
        assert resp.status_code == 404
        assert _error(resp)["code"] == "not_found"

    def test_no_write_endpoint_is_exposed(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        assert local_client.post(f"/api/export/dramas/{did}/readiness").status_code == 405


class TestExportSubtitleEndpoint:
    """Migration Slice 14: SRT/VTT subtitle text generation as a
    plain-text download. Never flags a line, never writes to disk; ASS/
    EPUB/audiobook/video export stay out of scope -- see
    api/routers/export_routes.py's own docstring for why."""

    def _drama_with_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        return did

    def test_srt_download_shape(self, local_client, isolated_db):
        did = self._drama_with_lines(isolated_db)
        resp = local_client.get(f"/api/export/dramas/{did}/subtitle?fmt=srt&field=en")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-subrip")
        assert "attachment" in resp.headers["content-disposition"]
        assert "Hello" in resp.text

    def test_vtt_download_shape(self, local_client, isolated_db):
        did = self._drama_with_lines(isolated_db)
        resp = local_client.get(f"/api/export/dramas/{did}/subtitle?fmt=vtt&field=en")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/vtt")
        assert resp.text.startswith("WEBVTT")

    def test_default_format_and_field(self, local_client, isolated_db):
        did = self._drama_with_lines(isolated_db)
        resp = local_client.get(f"/api/export/dramas/{did}/subtitle")
        assert resp.status_code == 200
        assert "Hello" in resp.text

    def test_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.get("/api/export/dramas/999999/subtitle")
        assert resp.status_code == 404
        assert _error(resp)["code"] == "not_found"

    def test_unknown_format_is_422(self, local_client, isolated_db):
        did = self._drama_with_lines(isolated_db)
        resp = local_client.get(f"/api/export/dramas/{did}/subtitle?fmt=ass")
        assert resp.status_code == 422

    def test_no_write_endpoint_is_exposed(self, local_client, isolated_db):
        did = self._drama_with_lines(isolated_db)
        assert local_client.post(f"/api/export/dramas/{did}/subtitle").status_code == 405


class TestExportFlaggingEndpoints:
    """Migration Slice 15: the three flagging actions. Each writes only
    flag/flag_note fields (a field-scoped db.save_lines write); see
    services/export_service.py's own docstring for why that matters."""

    def test_flag_overlaps(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        resp = local_client.post(f"/api/export/dramas/{did}/flag-overlaps")
        assert resp.status_code == 200
        assert resp.json() == {"flagged_count": 1}

    def test_flag_overlaps_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.post("/api/export/dramas/999999/flag-overlaps")
        assert resp.status_code == 404

    def test_flag_dense_lines(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="", en="word " * 60)])
        resp = local_client.post(f"/api/export/dramas/{did}/flag-dense-lines")
        assert resp.status_code == 200
        assert resp.json() == {"flagged_count": 1}

    def test_reading_speed_mode_round_trip(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        assert local_client.get(f"/api/export/dramas/{did}/reading-speed").json() == {"mode": "normal"}
        resp = local_client.post(f"/api/export/dramas/{did}/reading-speed", json={"mode": "off"})
        assert resp.status_code == 200 and resp.json() == {"mode": "off"}
        assert local_client.get(f"/api/export/dramas/{did}/reading-speed").json() == {"mode": "off"}
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="", en="word " * 60)])
        assert local_client.post(f"/api/export/dramas/{did}/flag-dense-lines").json() == {"flagged_count": 0}

    def test_reading_speed_mode_rejects_unknown_values_and_drama(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        assert local_client.post(f"/api/export/dramas/{did}/reading-speed",
                           json={"mode": "strict"}).status_code == 422
        assert local_client.post("/api/export/dramas/999999/reading-speed",
                           json={"mode": "off"}).status_code == 404
        assert local_client.get("/api/export/dramas/999999/reading-speed").status_code == 404

    def test_clear_reading_speed_flags(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=5.0, zh="", en="word " * 20, flag="reading_speed", flag_note="x"),
            Line(idx=1, start=5.0, end=9.0, zh="", en="Hi", flag="timing_overlap", flag_note="y")])
        resp = local_client.post(f"/api/export/dramas/{did}/clear-reading-speed-flags?recheck=true")
        assert resp.status_code == 200
        body = resp.json()
        assert (body["cleared_count"], body["flagged_count"]) == (1, 1)
        assert isolated_db.load_lines(did)[1]["flag"] == "timing_overlap"
        assert local_client.post("/api/export/dramas/999999/clear-reading-speed-flags").status_code == 404

    def test_flag_dense_lines_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.post("/api/export/dramas/999999/flag-dense-lines")
        assert resp.status_code == 404

    def test_flag_auto_qc(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="他有三个孩子。", en="He has kids.")])
        resp = local_client.post(f"/api/export/dramas/{did}/flag-auto-qc")
        assert resp.status_code == 200
        body = resp.json()
        assert body["flagged"] == 1
        assert body["checked"] == 1

    def test_flag_auto_qc_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.post("/api/export/dramas/999999/flag-auto-qc")
        assert resp.status_code == 404

    def test_no_get_endpoint_is_exposed_for_flag_actions(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        assert local_client.get(f"/api/export/dramas/{did}/flag-overlaps").status_code == 405


class TestDiarizationEndpoints:
    """Migration Slice 16: Diarize-stage config + starting a real
    speaker-detection job. hf_token_configured is a boolean only, never
    the token value (D2); job status is polled via the existing
    GET /api/jobs/{job_id} (Migration Slice 8), not duplicated here."""

    def test_config_contract_shape(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        body = local_client.get(f"/api/diarization/dramas/{did}/config").json()
        assert body == {
            "drama_id": did, "hf_token_configured": False,
            "speaker_notice": diarization_service.NO_TOKEN_NOTICE,
            "expected_speakers": None, "min_speakers": None, "max_speakers": None,
            "last_device": None, "audio_available": False,
            "manual_speaker_count": 0, "speaker_summary": None,
        }

    def test_config_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.get("/api/diarization/dramas/999999/config")
        assert resp.status_code == 404

    def test_config_never_leaks_a_token_value(self, local_client, isolated_db, monkeypatch):
        monkeypatch.setenv("BAIHE_HF_TOKEN", "sk-should-not-leak")
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.get(f"/api/diarization/dramas/{did}/config")
        assert "sk-should-not-leak" not in resp.text
        assert resp.json()["hf_token_configured"] is True

    def test_run_with_no_audio_is_400(self, local_client, isolated_db, monkeypatch):
        monkeypatch.setenv("BAIHE_HF_TOKEN", "sk-test")
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/diarization/dramas/{did}/run")
        assert resp.status_code == 400

    def test_run_with_no_token_is_503(self, local_client, isolated_db, monkeypatch):
        monkeypatch.delenv("BAIHE_HF_TOKEN", raising=False)
        monkeypatch.delenv("HF_TOKEN", raising=False)
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/diarization/dramas/{did}/run")
        assert resp.status_code == 503

    def test_run_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.post("/api/diarization/dramas/999999/run")
        assert resp.status_code == 404

    def test_run_starts_a_real_job_visible_in_jobs_api(self, local_client, isolated_db, monkeypatch):
        monkeypatch.setenv("BAIHE_HF_TOKEN", "sk-test")
        did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav")
        ddir = isolated_db.drama_dir(did)
        import os
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "audio.wav"), "wb").close()

        def fake_start_process_job(job_id, target, args=(), gpu_touching=False, description=None,
                                   on_done=None, run_settings=None, **launch):
            isolated_db.save_job_record(job_id, status="running", description=description)
            return True

        from jobs import job_launch
        monkeypatch.setattr(job_launch, "start_process_job", fake_start_process_job)

        resp = local_client.post(f"/api/diarization/dramas/{did}/run")
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        job_body = local_client.get(f"/api/jobs/{job_id}").json()
        assert job_body["status"] == "running"
        assert local_client.get(f"/api/export/dramas/{did}/flag-dense-lines").status_code == 405
        assert local_client.get(f"/api/export/dramas/{did}/flag-auto-qc").status_code == 405


class TestTranslateHistoryClearEndpoint:
    """Migration Slice 17: clearing translate history requires an
    explicit confirm=true -- mirrors tabs/translate_tab.py's own
    checkbox-then-button gate; see services/translate_service.py's own
    docstring for the reasoning."""

    def test_clear_without_confirm_is_422(self, local_client, isolated_db):
        isolated_db.save_translate_history("zh", "en", "fake", "你好", "[TEST] Hello")
        resp = local_client.delete("/api/translate/history")
        assert resp.status_code == 422
        history = local_client.get("/api/translate/history").json()["items"]
        assert len(history) == 1

    def test_clear_with_confirm_actually_clears(self, local_client, isolated_db):
        isolated_db.save_translate_history("zh", "en", "fake", "你好", "[TEST] Hello")
        resp = local_client.delete("/api/translate/history?confirm=true")
        assert resp.status_code == 200
        assert resp.json() == {"cleared": True}
        history = local_client.get("/api/translate/history").json()["items"]
        assert history == []


class TestExportEpubEndpoint:
    """Migration Slice 18: EPUB export for novel-narration dramas only.
    Binary download (application/epub+zip); see
    services/export_service.py's own docstring for the scope decision."""

    def _novel_drama_with_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", content_mode="novel_narration")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        return did

    def test_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.get("/api/export/dramas/999999/epub")
        assert resp.status_code == 404

    def test_non_novel_drama_is_400(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D", content_mode="audio_drama")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        resp = local_client.get(f"/api/export/dramas/{did}/epub")
        assert resp.status_code == 400

    def test_unknown_field_is_422(self, local_client, isolated_db):
        did = self._novel_drama_with_lines(isolated_db)
        resp = local_client.get(f"/api/export/dramas/{did}/epub?field=bilingual")
        assert resp.status_code == 422

    def test_missing_ebooklib_is_503(self, local_client, isolated_db, monkeypatch):
        # ebooklib is imported inside epub_io.export_epub, so block that package itself.
        monkeypatch.setitem(sys.modules, "ebooklib", None)
        did = self._novel_drama_with_lines(isolated_db)
        resp = local_client.get(f"/api/export/dramas/{did}/epub")
        assert resp.status_code == 503
        assert "ebooklib" in resp.json()["error"]["message"]

    def test_epub_download_shape(self, local_client, isolated_db):
        pytest.importorskip("ebooklib")
        did = self._novel_drama_with_lines(isolated_db)
        resp = local_client.get(f"/api/export/dramas/{did}/epub")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/epub+zip")
        assert "attachment" in resp.headers["content-disposition"]


class TestSourceConfigEndpoints:
    """Migration Slice 19: Source-stage config only -- audio/video upload
    and transcript/novel text are out of scope, see
    services/source_service.py's own docstring for why."""

    def test_get_config_contract_shape(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        body = local_client.get(f"/api/source/dramas/{did}/config").json()
        assert body == {
            "drama_id": did, "source_language": "zh", "chinese_script": "simplified",
            "content_mode": "audio_drama", "has_audio_pipeline": True,
            "audio_available": False, "has_video_source": False,
            "transcript_mode": "have_transcript",
            "transcript_mode_options": ["have_transcript", "whisper"],
            "has_raw_novel_context": False,
        }

    def test_get_config_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.get("/api/source/dramas/999999/config")
        assert resp.status_code == 404

    def test_post_config_updates_source_language(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/source/dramas/{did}/config", json={"source_language": "ja"})
        assert resp.status_code == 200
        assert resp.json()["source_language"] == "ja"

    def test_post_config_unknown_value_is_422(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/source/dramas/{did}/config", json={"content_mode": "not_a_mode"})
        assert resp.status_code == 422

    def test_post_config_hardsub_ocr_without_video_is_400(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/source/dramas/{did}/config",
                          json={"transcript_mode": "hardsub_ocr"})
        assert resp.status_code == 400

    def test_post_config_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.post("/api/source/dramas/999999/config", json={"source_language": "ja"})
        assert resp.status_code == 404

    def test_post_config_streamer_vod_syncs_media_type(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/source/dramas/{did}/config",
                          json={"content_mode": "streamer_vod"})
        assert resp.status_code == 200
        drama = isolated_db.get_drama(did)
        assert drama["media_type"] == "streamer_vod"


class TestTranscribeConfigEndpoints:
    """Migration Slice 20: Transcript-stage config + the job-does-
    everything start action -- see services/transcribe_service.py's own
    docstring for the scope decision and what's deliberately out."""

    def test_a_title_saved_with_the_removed_moss_backend_loads_with_a_notice(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D", asr_backend_choice="moss_td", source_language="ko")
        resp = local_client.get(f"/api/transcribe/dramas/{did}/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["asr_backend_choice"] == "whisper" and "MOSS" in body["asr_backend_notice"]
        assert isolated_db.get_drama(did)["asr_backend_choice"] == "moss_td"

    def test_get_config_contract_shape(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        body = local_client.get(f"/api/transcribe/dramas/{did}/config").json()
        assert body == {
            "drama_id": did, "transcript_mode": "have_transcript", "has_audio_pipeline": True,
            "audio_available": False, "alignment_method": "whisper_diff",
            "asr_backend_choice": "whisper", "asr_backend_notice": None,
            "whisper_size": whisper_models.DEFAULT_WHISPER_SIZE,
            "whisper_size_notice": None, "whisper_exclusions": whisper_models.whisper_language_exclusions(),
            "selectable_whisper_sizes": ["large-v3", "large-v3-turbo", "ja-1.5b", "kotoba-v2"],
            "whisper_model_cached": body["whisper_model_cached"], "measured_speed": None, "measured_speed_runs": 0,
            "measured_stage_seconds": {}, "measured_diarize_speed": None, "measured_diarize_runs": 0,
            "whisper_installed": body["whisper_installed"], "gpu_runtime": body["gpu_runtime"],
            "beam_size": 5, "min_silence_ms": 300, "vad_threshold": 0.5, "sensitivity_preset": "normal", "effective_vad_threshold": 0.5,
            "hallucination_silence_sec": 0.0, "min_pause_sec": 0.35,
            "separate_vocals_first": False, "separation_backend": "auto",
            "realign_long_segments": False, "whisper_fast_mode": False,
            "whisper_repeat_guard": False, "split_by_sentences": False, "difficult_audio": False, "vocabulary_hint": False, "use_groq": False,
            "has_video_source": False, "hardsub_ocr_backend": "paddle",
            "hardsub_interval_sec": 1.0, "auto_initial_prompt": "",
            "source_language": "zh", "chinese_script": "simplified",
            "run_start": {"run_diarize": False, "expected_speakers": None, "min_speakers": None,
                          "max_speakers": None, "extra_names": "", "initial_prompt": ""},
        }

    def test_get_config_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.get("/api/transcribe/dramas/999999/config")
        assert resp.status_code == 404

    def test_post_config_updates_a_tuning_knob(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/transcribe/dramas/{did}/config", json={"min_silence_ms": 1000})
        assert resp.status_code == 200
        assert resp.json()["min_silence_ms"] == 1000

    def test_post_config_out_of_range_is_422(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/transcribe/dramas/{did}/config", json={"beam_size": 20})
        assert resp.status_code == 422

    def test_post_config_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.post("/api/transcribe/dramas/999999/config", json={"beam_size": 8})
        assert resp.status_code == 404

    def test_run_without_audio_is_400(self, local_client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        resp = local_client.post(f"/api/transcribe/dramas/{did}/run", json={})
        assert resp.status_code == 400

    def test_run_unknown_drama_is_404(self, local_client, isolated_db):
        resp = local_client.post("/api/transcribe/dramas/999999/run", json={})
        assert resp.status_code == 404

    def test_run_starts_a_real_job_visible_in_jobs_api(self, local_client, isolated_db, monkeypatch):
        from services import settings_service
        settings_service.set_settings({"use_gpu": False})   # the mocked in-process CPU path
        did = isolated_db.create_drama(title_en="D", transcript_mode="whisper")
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "audio.wav"), "wb").close()
        isolated_db.update_drama(did, audio_filename="audio.wav")
        # Mocked so the real background thread never touches a real model
        # or the network -- this test only checks the job is registered
        # and pollable through the existing jobs API.
        import services.transcribe_pipeline as transcribe_pipeline_module
        monkeypatch.setattr(transcribe_pipeline_module, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        resp = local_client.post(f"/api/transcribe/dramas/{did}/run", json={})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        assert job_id == f"transcribe_{did}"

        job_resp = local_client.get(f"/api/jobs/{job_id}")
        assert job_resp.status_code == 200
        # Wait for the real thread to finish before the test (and its
        # isolated_db) ends: a thread still saving its job record while the
        # next test's init_db runs left "database is locked" errors in the
        # next test's fixture setup (seen twice in full-suite runs).
        deadline = time.time() + 10
        while time.time() < deadline:
            status = (background_jobs.get_status(job_id) or {}).get("status")
            if status not in ("queued", "running"):
                break
            time.sleep(0.05)
        background_jobs.clear_job(job_id)

    def test_run_already_running_is_409(self, local_client, isolated_db, monkeypatch):
        # start_process_job spawning a real process makes "still running" a race to
        # assert on directly (the real transcribe_for_timing would run and
        # the job could finish before the second request lands) -- mocked
        # here the same way TestStartTranscribeRun mocks it at the service
        # layer, just via the API this time.
        did = isolated_db.create_drama(title_en="D", transcript_mode="whisper")
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "audio.wav"), "wb").close()
        isolated_db.update_drama(did, audio_filename="audio.wav")
        monkeypatch.setattr(job_launch, "start_process_job", lambda *a, **k: False)

        resp = local_client.post(f"/api/transcribe/dramas/{did}/run", json={})
        assert resp.status_code == 409


def test_auth_module_imports_no_router():
    """api.auth holds the loopback helpers itself; importing a router from it
    (even lazily, inside a function) brings back the settings_routes <->
    api.auth import cycle."""
    import ast
    import api.auth
    import api.auth_gates
    import api.routers.settings_routes as settings_routes
    for module in (api.auth, api.auth_gates):
        with open(module.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in imported if m and m.startswith("api.routers")], module.__name__
    assert settings_routes.is_loopback_peer is api.auth.is_loopback_peer


def test_only_the_facades_import_their_split_modules():
    """api.auth_gates, sources.store_tracking and sources.store_domains need their
    facade (api.auth, sources.store) fully defined, and the facade re-exports
    them at its end, so importing one of them first raises a circular
    ImportError. Everything else imports the names from the facade. Retired if
    those modules stop reading names back from their facade."""
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    allowed = {"api.auth_gates": "api/auth.py", "sources.store_tracking": "sources/store.py",
               "sources.store_domains": "sources/store.py",
               "services.sources_extraction_views": "services/sources_extraction_service.py",
               "services.sources_extraction_actions": "services/sources_extraction_service.py",
               "services.translate_bulk_run_service": "services/translate_run_service.py",
               "services.transcribe_pipeline_helpers": "services/transcribe_pipeline.py"}
    split_files = {m.replace(".", "/") + ".py" for m in allowed}
    offenders = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".git", "frontend", "venv", ".venv", "__pycache__"}]
        for name in files:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if not name.endswith(".py") or rel in split_files or path == os.path.abspath(__file__):
                continue
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                imported = []
                if isinstance(node, ast.ImportFrom):
                    imported = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
                elif isinstance(node, ast.Import):
                    imported = [a.name for a in node.names]
                for module, facade in allowed.items():
                    if rel != facade and any(n == module or n.startswith(module + ".") for n in imported):
                        offenders.append(f"{rel} imports {module}")
    assert offenders == []
