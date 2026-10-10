"""The NLLB engine was removed. Saved data may still name it: every surface
refuses a run with the removed-engine message, and old rows keep loading."""
import argparse
import contextlib
import io

import pytest

import bulk_translate
import cli
import cli_translate
import db
import translate_engines
from core import Line
from services import (benchmark_lab_service, engine_routing_service,
                      translate_run_service as svc, translate_service)
from services.service_errors import InvalidInputError

MESSAGE = "The nllb engine was removed. Pick another engine."


def _drama(**fields):
    did = db.create_drama(title_zh="D", status="aligned", **fields)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    return did


def test_message_text_is_the_documented_one():
    assert translate_engines.unknown_engine_message("nllb") == MESSAGE


class TestServices:
    def test_run_estimate_and_bulk_queue_refuse_it(self, isolated_db):
        did = _drama()
        with pytest.raises(InvalidInputError) as exc:
            svc.start_translate_run(did, engine_name="nllb")
        assert str(exc.value) == MESSAGE
        with pytest.raises(InvalidInputError, match=MESSAGE):
            svc.start_translate_run(did, engine_name="nllb", bulk=True)
        with pytest.raises(InvalidInputError, match=MESSAGE):
            svc.estimate_translate_cost(did, "nllb")

    def test_a_drama_saved_with_it_is_refused_not_crashed(self, isolated_db):
        did = _drama(translation_engine="nllb")
        with pytest.raises(InvalidInputError, match=MESSAGE):
            svc.start_translate_run(did)

    def test_a_fallback_chain_naming_it_is_refused(self, isolated_db):
        did = _drama()
        with pytest.raises(InvalidInputError, match="nllb"):
            svc.start_translate_run(did, engine_name="claude", fallback_chain=[{"engine": "nllb"}])
        assert "nllb" in (translate_engines.fallback_chain_error(["claude", "nllb"]) or "")
        assert MESSAGE in translate_engines.fallback_chain_error(["nllb", "claude"])

    def test_standalone_translate_refuses_it(self, isolated_db):
        with pytest.raises(InvalidInputError, match=MESSAGE):
            translate_service.translate("hello", "nllb", "zh", "en")

    def test_it_is_not_offered_anywhere(self, isolated_db):
        assert "nllb" not in {e["name"] for e in translate_service.list_engines()}
        assert "nllb" not in translate_engines.KEYLESS_ENGINES
        assert "nllb" not in translate_engines.FREE_ENGINES


class TestBulkResume:
    """Bulk batches only exist for Claude, Gemini and DeepSeek, so a job naming
    nllb can only come from a hand-edited library; it must still not crash."""

    def _job(self, status):
        did = _drama()
        return did, db.create_bulk_job(did, "nllb", "facebook/nllb-200-distilled-600M", status,
                                       [(0, "k", "h", "")])

    @pytest.mark.parametrize("status", ["submitted", "scheduled", "running"])
    def test_a_pending_job_for_it_starts_nothing(self, isolated_db, monkeypatch, status):
        monkeypatch.setattr(bulk_translate, "start_poller",
                            lambda *a, **k: pytest.fail("a removed engine must not poll"))
        did, jid = self._job(status)
        out = svc.resume_bulk_translations(did)
        assert out["jobs"] == [{"bulk_job_id": jid, "state": "needs_key"}]

    def test_startup_resume_does_not_crash_on_it(self, isolated_db, monkeypatch):
        did, jid = self._job("submitted")
        monkeypatch.setattr(svc.settings_service, "get_bulk_auto_resume", lambda: True)
        monkeypatch.setattr(bulk_translate, "start_poller",
                            lambda *a, **k: pytest.fail("a removed engine must not poll"))
        out = svc.resume_interrupted_at_startup()
        assert out["enabled"] is True and out["resumed"] == 0

    def test_cancelling_it_does_not_crash(self, isolated_db):
        did, jid = self._job("submitted")
        assert svc.cancel_bulk_translation(did, jid)["bulk_job"]["status"] == "cancelled"


class TestLibraryBulkQueue:
    def test_a_title_saved_with_it_is_reported_not_crashed(self, isolated_db, monkeypatch):
        from services import workspace_job_service as wjs
        did = _drama(translation_engine="nllb")
        captured = {}
        monkeypatch.setattr(wjs.background_jobs, "set_result",
                            lambda job_id, results: captured.update(results))
        wjs.run_bulk_series_translate_job("bulk_series_translate", [did], {})
        results = captured
        assert results["skipped_engine_changed"] == [did]
        assert results["translated"] == [] and results["skipped_no_key"] == [] and results["errors"] == {}


class TestOldSavedData:
    def test_a_preset_loads_and_applies_without_rewriting_the_drama(self, isolated_db):
        did = _drama(translation_engine="claude")
        pid = db.save_preset("Old", translation_engine="nllb",
                             engine_model="facebook/nllb-200-distilled-600M")
        applied = svc.apply_translate_preset(did, pid)
        assert applied["translation_engine"] is None and applied["engine_model"] is None
        assert db.get_drama(did)["translation_engine"] == "claude"
        assert [p["translation_engine"] for p in db.list_presets()] == ["nllb"]

    def test_history_and_benchmark_rows_still_load(self, isolated_db):
        db.save_translate_history("zh", "en", "nllb", "你好", "hello")
        assert [h["engine"] for h in translate_service.list_history()] == ["nllb"]
        sid = db.create_benchmark_session({
            "label": "old", "stage": "translation", "engine": "nllb",
            "model": "facebook/nllb-200-distilled-600M", "status": "complete", "case_count": 0})
        assert benchmark_lab_service.get_run(sid)["run"]["engine"] == "nllb"
        assert [r["engine"] for r in benchmark_lab_service.list_runs()["runs"]] == ["nllb"]

    def test_a_saved_routing_choice_falls_back_to_the_default(self, isolated_db):
        db.set_app_setting("capability.llm.instructions", "nllb")
        entry = engine_routing_service.get_routing()
        assert "nllb" not in {e["engine"] for e in entry["engines"]}
        assert engine_routing_service.resolve_capability("llm.instructions") != "nllb"

    def test_a_saved_model_override_for_it_is_ignored(self, isolated_db):
        db.set_app_setting("model_overrides.defaults", {"nllb": "some/model"})
        assert translate_engines.model_override_for_default("nllb") is None


class TestApi:
    @pytest.fixture
    def client(self, isolated_db):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                          headers={"X-Baihe-Local": "1"})

    def test_run_estimate_and_bulk_are_refused_with_the_message(self, client):
        did = _drama()
        base = f"/api/translate-run/dramas/{did}"
        for r in (client.post(f"{base}/run", json={"engine": "nllb"}),
                  client.post(f"{base}/run", json={"engine": "nllb", "bulk": True}),
                  client.get(f"{base}/estimate", params={"engine": "nllb"})):
            assert r.status_code in (400, 422), r.text
            assert r.json()["error"]["message"] == MESSAGE

    def test_engine_list_does_not_offer_it(self, client):
        r = client.get("/api/translate/engines")
        assert r.status_code == 200, r.text
        assert "nllb" not in r.text


class TestCli:
    def _run(self, monkeypatch, args):
        built = []
        monkeypatch.setattr(translate_engines, "get_engine",
                            lambda name, *a, **k: built.append(name) or object())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_translate.cmd_translate(args)
        assert "nllb" not in built, "a removed engine must not be built"
        return out.getvalue()

    def _args(self, **overrides):
        from tests.test_cli import _translate_args
        return _translate_args(**overrides)

    def test_a_drama_saved_with_it_is_skipped_with_the_message(self, isolated_db, monkeypatch):
        did = _drama(translation_engine="nllb")
        out = self._run(monkeypatch, self._args(id=did, engine=None, api_key=None))
        assert f"#{did} skipped" in out and MESSAGE in out

    def test_a_fallback_naming_it_is_refused_up_front(self):
        with pytest.raises(SystemExit) as exc:
            cli_translate._parse_fallback_arg("deepseek,nllb", reflect=False)
        assert MESSAGE in str(exc.value)

    def test_the_engine_flag_refuses_it_with_the_api_message(self, isolated_db, monkeypatch):
        built = []
        monkeypatch.setattr(translate_engines, "get_engine",
                            lambda name, *a, **k: built.append(name) or object())
        monkeypatch.setattr("sys.argv", ["cli.py", "translate", "--id", "1", "--engine", "nllb"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert str(exc.value) == f"translate: {MESSAGE}"
        assert not built

    def test_an_unknown_engine_flag_is_still_refused(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cli.py", "translate", "--engine", "nope"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert str(exc.value) == "translate: Unknown engine."

    def test_the_default_model_is_the_same_in_the_cli_and_the_app(self, isolated_db):
        assert translate_engines.builtin_default_model("ollama") == "gemma4:12b"
        assert svc._default_model("ollama") == "gemma4:12b"


class TestOllamaDefaultAgrees:
    """One built-in Ollama default (gemma4:12b) across every surface that shows it."""

    def test_every_surface_names_the_same_default(self, isolated_db):
        from services import model_registry_service, model_reeval_service, settings_service
        assert translate_engines.OLLAMA_DEFAULT_MODEL == "gemma4:12b"
        assert benchmark_lab_service.default_model("ollama") == "gemma4:12b"
        assert svc._default_model("ollama") == "gemma4:12b"
        settings_service.set_settings({"default_engine": "ollama"})
        assert model_reeval_service.get_production()["model"] == "gemma4:12b"
        row = next(r for r in model_registry_service.configured_models()
                   if r["engine"] == "ollama" and r["kind"] == "default")
        assert row["model"] == row["builtin_model"] == "gemma4:12b"
        ollama = next(e for e in translate_service.list_engines() if e["name"] == "ollama")
        assert ollama["models"] == ["gemma4:12b", "gemma4:26b", "gemma4:31b",
                                    "gemma4:31b-cloud", "gemma4:cloud"]

    def test_no_diagnostics_entry_for_the_removed_engine(self):
        import diagnostics
        assert not any(p["id"] == "nllb" for p in diagnostics.INSTALL_TASKS)
        assert "NLLB" not in repr(diagnostics.OPTIONAL_DEPENDENCIES) + repr(diagnostics.INSTALL_TASKS)
