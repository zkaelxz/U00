"""An engine whose Python package is missing is a readable 503 naming the
package, from every caller that builds an engine; a missing key stays its own
error. The package is hidden for real (`sys.modules[...] = None`), so the
ImportError comes from the engine's own import, not from a stub."""
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from lib.errors import MissingKeyError, MissingPackageError
from services import (discover_lookup_service, line_ai_service, reader_service,
                      restructure_service, review_jobs_service, settings_service,
                      translate_run_service, translate_service)

FIXED = "A tool or package this needs is not installed or not reachable. See Diagnostics."


@pytest.fixture(autouse=True)
def _no_anthropic(isolated_db, monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: "k")


@pytest.fixture
def drama(isolated_db):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en=""),
                        Line(idx=1, start=1, end=2, zh="再见", en="bye", flag="uncertain_translation")])
    return did, [r["id"] for r in db.load_lines(did)]


def _assert_package_error(exc):
    assert exc.message == FIXED
    assert exc.details == {"reason": "missing_package", "package": "anthropic"}


def test_get_engine_raises_the_dependency_error_not_import_error():
    with pytest.raises(MissingPackageError) as exc:
        translate_engines.get_engine("claude", "k")
    _assert_package_error(exc.value)


def test_a_nested_module_reports_only_its_top_level_package(monkeypatch):
    def boom(api_key):
        raise ModuleNotFoundError("No module named 'a.b'", name="a.b", path="/home/me/x.py")
    monkeypatch.setattr("engine_backends.claude.make_anthropic_client", boom)
    with pytest.raises(MissingPackageError) as exc:
        translate_engines.get_engine("claude", "k")
    assert exc.value.details["package"] == "a"
    assert "/home" not in repr(exc.value.details) + exc.value.message


def test_translate_run(drama):
    with pytest.raises(MissingPackageError) as exc:
        translate_run_service.start_translate_run(drama[0], engine_name="claude")
    _assert_package_error(exc.value)


@pytest.mark.parametrize("starter", ["start_consistency_check", "start_emotion_tagging",
                                      "start_translation_notes", "start_flag_review",
                                      "start_fix_flagged"])
def test_review_jobs(drama, starter):
    with pytest.raises(MissingPackageError) as exc:
        getattr(review_jobs_service, starter)(drama[0], engine_name="claude")
    _assert_package_error(exc.value)


def test_restructure(drama):
    with pytest.raises(MissingPackageError) as exc:
        restructure_service._build_engine(db.get_drama(drama[0]), "claude", None)
    _assert_package_error(exc.value)


def test_line_ai(drama):
    with pytest.raises(MissingPackageError) as exc:
        line_ai_service.engine_for(db.get_drama(drama[0]), "claude", None, False, False)
    _assert_package_error(exc.value)


def test_discover_lookup():
    with pytest.raises(MissingPackageError) as exc:
        discover_lookup_service._build_engine("claude")
    _assert_package_error(exc.value)


def test_reader():
    with pytest.raises(MissingPackageError) as exc:
        reader_service.llm_engine("claude")
    _assert_package_error(exc.value)


def test_api_answer_is_a_503_with_the_package_name_and_no_traceback_or_path(drama):
    did, ids = drama
    client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = client.post(f"/api/line-ai/dramas/{did}/lines/{ids[1]}/explain", json={"engine": "claude"})
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "dependency_unavailable" and err["message"] == FIXED
    assert err["details"] == {"reason": "missing_package", "package": "anthropic"}
    assert "Traceback" not in r.text and "site-packages" not in r.text and ".py" not in r.text


def test_a_missing_key_is_its_own_error(drama, monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
    with pytest.raises(MissingKeyError) as exc:
        translate_run_service.start_translate_run(drama[0], engine_name="claude")
    assert exc.value.message == "No claude key is configured. Add it in Settings."
    assert exc.value.details["reason"] == "no_key"
    assert not isinstance(exc.value, MissingPackageError)


def test_a_missing_hugging_face_token_is_its_own_error(drama, monkeypatch):
    from services import diarization_service
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: None)
    with pytest.raises(MissingKeyError) as exc:
        diarization_service.start_diarization_run(drama[0])
    assert exc.value.message == "No Hugging Face token is configured. Add it in Settings."
    assert exc.value.details["reason"] == "no_token"
