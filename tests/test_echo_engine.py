"""The Echo (testing) engine: offered and accepted only in Developer Mode."""
import time

import pytest

import background_jobs
import db
import translate_engines as te
import core
from api.api_config import ApiSettings
from api.server import create_app
from fastapi.testclient import TestClient
from core import Line, lines_to_srt
from engine_backends import echo
from services import maintenance_assistant_service as assistant
from services import translate_run_service as run_svc
from services import translate_service
from services.service_errors import InvalidInputError
from tests import fake_engine


@pytest.fixture(autouse=True)
def _echo_off_after():
    yield
    echo.set_enabled(False)
    background_jobs.clear_all_jobs()


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _seed(texts):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en="")
                        for i, z in enumerate(texts)])
    return did


def _listed():
    return {e["name"]: e for e in translate_service.list_engines()}


def test_translate_run_through_a_title_then_export(isolated_db):
    assistant.set_settings({"developer_mode": True})
    entry = _listed()["echo"]
    assert entry["free"] and entry["key_configured"]
    assert entry["label"] == echo.NOTE
    assert "echo" in te.KEYLESS_ENGINES and "echo" in te.FREE_ENGINES
    did = _seed(["你好", "再见", "x" * 50])

    out = run_svc.start_translate_run(did, engine_name="echo")

    assert _wait(out["job_id"])["status"] == "done"
    ens = [r["en"] for r in db.load_lines(did)]
    assert ens == ["[EN] 你好", "[EN] 再见", "[EN] " + "x" * 40 + "…"]
    assert "[EN] 你好" in lines_to_srt([core.line_from_row(r) for r in db.load_lines(did)])


def test_absent_and_refused_with_developer_mode_off(isolated_db):
    assistant.set_settings({"developer_mode": True})
    assert "echo" in _listed()
    assistant.set_settings({"developer_mode": False})
    assert "echo" not in _listed()
    assert "echo" not in te.KEYLESS_ENGINES and "echo" not in te.FREE_ENGINES
    did = _seed(["你好"])
    with pytest.raises(InvalidInputError, match="Developer Mode"):
        run_svc.start_translate_run(did, engine_name="echo")
    assert db.load_lines(did)[0]["en"] == ""


def test_server_start_syncs_the_engine_with_the_saved_setting(isolated_db):
    db.set_app_setting("assistant.developer_mode", True)
    with TestClient(create_app(ApiSettings())):
        assert "echo" in te.ENGINES
    db.set_app_setting("assistant.developer_mode", False)
    with TestClient(create_app(ApiSettings())):
        assert "echo" not in te.ENGINES


def test_fake_engine_keeps_its_test_output():
    engine = fake_engine.FakeEngine()
    assert engine.translate_batch(["你好"], {}) == ["[TEST] 你好"]
    assert engine.last_usage["input_tokens"] == 2
    assert engine.client is None and engine.supports_reference
    assert te.ENGINES["fake"] is fake_engine.FakeEngine
