"""The Echo (testing) engine: offered and accepted only in Developer Mode."""

import pytest

import background_jobs
import db
from engine_backends import engine_registry
import core
from api.api_config import ApiSettings
from api.server import create_app
from fastapi.testclient import TestClient
from core import Line, lines_to_srt
from engine_backends import echo
from services import assistant_settings_service as settings_svc
from services import translate_run_service as run_svc
from services import translate_service
from lib.errors import InvalidInputError
from tests import fake_engine
from tests.job_helpers import wait_for_job


@pytest.fixture(autouse=True)
def _echo_off_after():
    yield
    echo.set_enabled(False)
    background_jobs.clear_all_jobs()



def _seed(texts):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en="")
                        for i, z in enumerate(texts)])
    return did


def _listed():
    return {e["name"]: e for e in translate_service.list_engines()}


def test_translate_run_through_a_title_then_export(isolated_db):
    settings_svc.set_settings({"developer_mode": True})
    entry = _listed()["echo"]
    assert entry["free"] and entry["key_configured"]
    assert entry["label"] == echo.NOTE
    assert "echo" in engine_registry.KEYLESS_ENGINES and "echo" in engine_registry.FREE_ENGINES
    did = _seed(["你好", "再见", "x" * 50])

    out = run_svc.start_translate_run(did, engine_name="echo")

    assert wait_for_job(out["job_id"])["status"] == "done"
    ens = [r["en"] for r in db.load_lines(did)]
    assert ens == ["[EN] 你好", "[EN] 再见", "[EN] " + "x" * 40 + "…"]
    assert "[EN] 你好" in lines_to_srt([core.line_from_row(r) for r in db.load_lines(did)])


def test_absent_and_refused_with_developer_mode_off(isolated_db):
    settings_svc.set_settings({"developer_mode": True})
    assert "echo" in _listed()
    settings_svc.set_settings({"developer_mode": False})
    assert "echo" not in _listed()
    assert "echo" not in engine_registry.KEYLESS_ENGINES and "echo" not in engine_registry.FREE_ENGINES
    did = _seed(["你好"])
    with pytest.raises(InvalidInputError, match="Developer Mode"):
        run_svc.start_translate_run(did, engine_name="echo")
    assert db.load_lines(did)[0]["en"] == ""


def test_server_start_syncs_the_engine_with_the_saved_setting(isolated_db):
    db.set_app_setting("assistant.developer_mode", True)
    with TestClient(create_app(ApiSettings())):
        assert "echo" in engine_registry.ENGINES
    db.set_app_setting("assistant.developer_mode", False)
    with TestClient(create_app(ApiSettings())):
        assert "echo" not in engine_registry.ENGINES


def test_fake_engine_keeps_its_test_output():
    engine = fake_engine.FakeEngine()
    assert engine.translate_batch(["你好"], {}) == ["[TEST] 你好"]
    assert engine.last_usage["input_tokens"] == 2
    assert engine.client is None and engine.supports_reference
    assert engine_registry.ENGINES["fake"] is fake_engine.FakeEngine
