"""Step 36: capability-based task routing ("which engine does what") and the
per-engine Test. Mocked throughout: no network, no real keys."""

import pytest

import db
import diagnostics
import translate_engines
from services import engine_routing_service as routing
from services import line_ai_service, settings_service, translate_run_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                      NotFoundError)

SECRET = "sk-ant-api03-" + "A" * 40


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: None)
    monkeypatch.setattr(settings_service, "key_status", lambda *a, **k: {})


class TestEngineTags:
    def test_every_engine_is_tagged_and_translates(self):
        assert set(translate_engines.ENGINE_CAPABILITIES) == set(translate_engines.ENGINES)
        for name in translate_engines.ENGINES:
            assert translate_engines.CAP_TRANSLATE in translate_engines.engine_capabilities(name)

    def test_translation_only_engines_do_not_follow_instructions(self):
        followers = set(translate_engines.engines_with_capability(
            translate_engines.CAP_INSTRUCTIONS))
        assert not followers & translate_engines.TRANSLATION_ONLY_ENGINES
        assert "fake" not in followers  # fake output; can't do QC/JSON tasks

    def test_free_engines_are_cheap(self):
        cheap = set(translate_engines.engines_with_capability(translate_engines.CAP_CHEAP))
        assert translate_engines.FREE_ENGINES <= cheap


class TestResolve:
    def test_unset_capabilities_use_their_defaults(self, isolated_db):
        settings_service.set_settings({"default_engine": "deepseek"})
        assert routing.resolve_capability("translation.cheap") == "deepseek"
        assert routing.resolve_capability("translation.high_quality") == "deepseek"
        assert routing.resolve_capability("llm.instructions") == "deepseek"
        assert routing.resolve_capability("summary.episode") == "ollama"
        assert routing.resolve_capability("research.grounded_search") == "gemini"

    def test_returns_the_configured_engine(self, isolated_db):
        routing.set_capability_engine("translation.high_quality", "claude")
        assert routing.resolve_capability("translation.high_quality") == "claude"
        routing.set_capability_engine("translation.high_quality", None)
        assert routing.resolve_capability("translation.high_quality") == "claude"  # the default
        settings_service.set_settings({"default_engine": "gemini"})
        assert routing.resolve_capability("translation.high_quality") == "gemini"

    def test_pref_backed_capabilities_share_the_existing_setting(self, isolated_db):
        routing.set_capability_engine("translation.cheap", "ollama")
        assert settings_service.get_default_engine() == "ollama"
        settings_service.set_settings({"episode_summary_engine": "claude"})
        assert routing.resolve_capability("summary.episode") == "claude"
        routing.set_capability_engine("translation.cheap", None)
        assert settings_service.get_default_engine() == "claude"

    def test_refuses_an_engine_without_the_capability(self, isolated_db):
        with pytest.raises(InvalidInputError):
            routing.set_capability_engine("llm.instructions", "nllb")
        with pytest.raises(InvalidInputError):
            routing.set_capability_engine("research.grounded_search", "claude")
        with pytest.raises(InvalidInputError):
            routing.set_capability_engine("summary.episode", "ollama_typo")
        with pytest.raises(NotFoundError):
            routing.resolve_capability("coding.strong")

    def test_offered_choices_match_what_can_be_saved(self, isolated_db):
        for name in routing.engine_choices("translation.cheap"):
            routing.set_capability_engine("translation.cheap", name)
            assert settings_service.get_default_engine() == name

    def test_stronger_engine_is_off_until_picked_even_when_equal_to_default(self, isolated_db):
        entry = routing._capability_entry("translation.high_quality")
        assert entry["is_default"] is True and entry["unset_label"] == "Off (no suggestions)"
        default = settings_service.get_default_engine()
        entry = routing.set_capability_engine("translation.high_quality", default)
        assert entry["is_default"] is False and entry["engine"] == default
        assert routing.is_configured("translation.high_quality")
        entry = routing.set_capability_engine("translation.high_quality", None)
        assert entry["is_default"] is True
        assert not routing.is_configured("translation.high_quality")
        assert routing._capability_entry("llm.instructions")["unset_label"] is None

    def test_a_stale_stored_value_reads_back_as_the_default(self, isolated_db):
        db.set_app_setting("capability.llm.instructions", "nllb")  # not an LLM
        assert routing.resolve_capability("llm.instructions") == settings_service.get_default_engine()

    def test_never_switches_on_a_missing_key(self, isolated_db, no_keys):
        routing.set_capability_engine("translation.high_quality", "claude")
        assert routing.resolve_capability("translation.high_quality") == "claude"


class TestMigratedCallSites:
    def test_translate_run_resolves_through_the_registry(self, isolated_db, monkeypatch):
        did = db.create_drama(title_zh="t", translation_engine=None)  # column default is claude
        asked = []

        def fake(cap):
            asked.append(cap)
            return "fake"
        monkeypatch.setattr(routing, "resolve_capability", fake)
        cfg = translate_run_service.get_translate_config(did)
        assert asked == ["translation.cheap"] and cfg["translation_engine"] == "fake"

    def test_a_drama_engine_still_wins(self, isolated_db, monkeypatch):
        did = db.create_drama(title_zh="t")
        db.update_drama(did, translation_engine="deepseek")
        monkeypatch.setattr(routing, "resolve_capability",
                            lambda cap: pytest.fail("should not be asked"))
        assert translate_run_service.get_translate_config(did)["translation_engine"] == "deepseek"

    def test_line_helpers_use_the_instructions_capability(self, isolated_db, monkeypatch):
        did = db.create_drama(title_zh="t", translation_engine=None)
        monkeypatch.setattr(routing, "resolve_capability",
                            lambda cap: {"llm.instructions": "ollama"}[cap])
        assert line_ai_service.tool_engine_name(did) == "ollama"
        assert line_ai_service.tool_engine_name(did, "claude") == "claude"

    def test_line_helpers_use_the_capability_for_a_translation_only_drama(
            self, isolated_db, monkeypatch):
        did = db.create_drama(title_zh="t", translation_engine="nllb")
        monkeypatch.setattr(routing, "resolve_capability",
                            lambda cap: {"llm.instructions": "gemini"}[cap])
        assert line_ai_service.tool_engine_name(did) == "gemini"
        did2 = db.create_drama(title_zh="u", translation_engine="deepseek")
        assert line_ai_service.tool_engine_name(did2) == "deepseek"  # the drama's own wins


class TestEngineTest:
    def test_success_is_recorded_without_a_network_call(self, isolated_db, monkeypatch):
        calls = []

        def fake(engine, api_key=None, model=None, base_url=None):
            calls.append((engine, api_key))
            return {"engine": engine, "ok": True, "error": None}
        monkeypatch.setattr(diagnostics, "check_engine_reachable", fake)
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: SECRET)
        monkeypatch.setattr(settings_service, "key_status", lambda *a, **k: {"claude": True})
        out = routing.test_engine("claude")
        assert calls == [("claude", SECRET)]
        assert out["status"] == "working" and out["last_test"]["ok"] is True
        assert SECRET not in repr(out)

    def test_bad_key_reports_a_redacted_failure(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_engine_reachable", lambda *a, **k: {
            "engine": "claude", "ok": False,
            "error": f"AuthenticationError: invalid x-api-key {SECRET}"})
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: SECRET)
        monkeypatch.setattr(settings_service, "key_status", lambda *a, **k: {"claude": True})
        out = routing.test_engine("claude")
        assert out["status"] == "failed"
        assert "AuthenticationError" in out["last_test"]["error"]
        assert SECRET not in repr(out) and SECRET not in repr(routing.get_routing())

    def test_real_check_path_redacts_an_engine_exception(self, isolated_db, monkeypatch):
        class Boom:
            name, model, supports_reference = "claude", "m", True

            def translate_batch(self, lines, context):
                raise RuntimeError(f"401 Unauthorized for key {SECRET}")
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: Boom())
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: SECRET)
        monkeypatch.setattr(settings_service, "key_status", lambda *a, **k: {"claude": True})
        out = routing.test_engine("claude")
        assert out["status"] == "failed" and SECRET not in repr(out)

    def test_no_key_is_refused_before_any_call(self, isolated_db, no_keys, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_engine_reachable",
                            lambda *a, **k: pytest.fail("no call without a key"))
        with pytest.raises(DependencyUnavailableError):
            routing.test_engine("gemini")
        assert routing.engine_status("gemini")["status"] == "not_configured"

    def test_a_slow_engine_times_out(self, isolated_db, monkeypatch):
        import threading
        release = threading.Event()
        monkeypatch.setattr(routing, "TEST_TIMEOUT_S", 0.05)
        monkeypatch.setattr(diagnostics, "check_engine_reachable",
                            lambda *a, **k: release.wait(5) and {"ok": True})
        try:
            out = routing.test_engine("fake")
        finally:
            release.set()
        assert out["status"] == "failed" and "No answer" in out["last_test"]["error"]

    def test_a_timed_out_engine_stays_busy_until_its_call_ends(self, isolated_db, monkeypatch):
        import threading
        from services.service_errors import ConflictError
        release = threading.Event()
        monkeypatch.setattr(routing, "TEST_TIMEOUT_S", 0.05)
        monkeypatch.setattr(diagnostics, "check_engine_reachable",
                            lambda *a, **k: release.wait(5) and {"ok": True})
        try:
            routing.test_engine("fake")
            with pytest.raises(ConflictError):
                routing.test_engine("fake")
        finally:
            release.set()

    def test_a_key_saved_during_a_test_discards_its_result(self, isolated_db, tmp_path,
                                                           monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: SECRET)
        env = str(tmp_path / ".env")

        def fake(*a, **k):
            settings_service.set_engine_key("claude", "sk-new-value-123", env_path=env)
            return {"ok": True, "error": None}
        monkeypatch.setattr(diagnostics, "check_engine_reachable", fake)
        routing.test_engine("claude")
        assert routing._last_test("claude") is None

    def test_nllb_is_not_tested(self, isolated_db, monkeypatch):
        from services.service_errors import UnsupportedOperationError
        monkeypatch.setattr(diagnostics, "check_engine_reachable",
                            lambda *a, **k: pytest.fail("would download a model"))
        with pytest.raises(UnsupportedOperationError):
            routing.test_engine("nllb")
        assert routing.engine_status("nllb")["test_blocked"]

    def test_saving_a_key_forgets_the_last_test(self, isolated_db, tmp_path, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_engine_reachable",
                            lambda *a, **k: {"ok": True, "error": None})
        routing.test_engine("fake")
        assert routing.engine_status("fake")["status"] == "working"
        db.set_app_setting("engine_test.claude", {"ok": True, "tested_at": "x"})
        settings_service.set_engine_key("claude", "sk-new-value-123", env_path=str(tmp_path / ".env"))
        assert routing._last_test("claude") is None
        db.set_app_setting("engine_test.ollama", {"ok": True, "tested_at": "x"})
        settings_service.set_endpoint_url("ollama_url", "http://127.0.0.1:11434",
                                          env_path=str(tmp_path / ".env"))
        assert routing._last_test("ollama") is None

    def test_unknown_engine(self, isolated_db):
        with pytest.raises(NotFoundError):
            routing.test_engine("nope")


class TestRoutes:
    @pytest.fixture
    def client(self, isolated_db):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)

    def test_get_lists_capabilities_and_engines(self, client, no_keys):
        body = client.get("/api/settings/engine-routing").json()
        ids = [c["id"] for c in body["capabilities"]]
        assert "translation.cheap" in ids and "translation.high_quality" in ids
        instr = next(c for c in body["capabilities"] if c["id"] == "llm.instructions")
        assert "nllb" not in instr["choices"] and "claude" in instr["choices"]
        claude = next(e for e in body["engines"] if e["engine"] == "claude")
        assert claude["status"] == "not_configured"

    def test_set_and_reset_a_capability(self, client):
        r = client.post("/api/settings/engine-routing/capabilities/translation.high_quality",
                        json={"engine": "gemini"})
        assert r.status_code == 200 and r.json()["engine"] == "gemini"
        assert r.json()["is_default"] is False
        r = client.post("/api/settings/engine-routing/capabilities/translation.high_quality",
                        json={"engine": None})
        assert r.json()["is_default"] is True
        assert client.post("/api/settings/engine-routing/capabilities/llm.instructions",
                           json={"engine": "libretranslate"}).status_code == 422
        assert client.post("/api/settings/engine-routing/capabilities/nope",
                           json={"engine": "claude"}).status_code == 404

    def test_engine_test_route(self, client, monkeypatch):
        monkeypatch.setattr(diagnostics, "check_engine_reachable",
                            lambda *a, **k: {"ok": True, "error": None})
        r = client.post("/api/settings/engine-routing/engines/fake/test", json={})
        assert r.status_code == 200 and r.json()["status"] == "working"

    def test_engine_test_without_key_is_503(self, client, no_keys):
        r = client.post("/api/settings/engine-routing/engines/claude/test", json={})
        assert r.status_code == 503

    def test_writes_are_pc_only(self, isolated_db):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        remote = TestClient(create_app(ApiSettings()), base_url="http://192.168.1.20:8600",
                            client=("192.168.1.20", 5000), raise_server_exceptions=False)
        for path in ("/api/settings/engine-routing/capabilities/translation.cheap",
                     "/api/settings/engine-routing/engines/fake/test"):
            assert remote.post(path, json={"engine": "claude"} if "capab" in path
                               else {}).status_code == 403


def test_a_key_write_never_fails_on_status_bookkeeping(isolated_db, tmp_path, monkeypatch):
    """CI regression: a library whose tables don't exist yet made every key
    write a 500 once the key write also forgot the engine's Test result."""
    import sqlite3

    def broken(*a, **k):
        raise sqlite3.OperationalError("no such table: app_settings")
    monkeypatch.setattr(db, "set_app_setting", broken)
    monkeypatch.setattr(db, "get_app_setting", broken)
    env = str(tmp_path / ".env")
    assert settings_service.set_engine_key("claude", "sk-abc-123", env_path=env)["configured"]
    assert settings_service.clear_engine_key("claude", env_path=env)["engine"] == "claude"
    settings_service.set_endpoint_url("ollama_url", "http://127.0.0.1:11434", env_path=env)
