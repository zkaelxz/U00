"""Stopping a batch the app is still working on: the extension names the run, and the pages
not yet worked on are left alone."""
import pytest

import db
import page_server

from tests.test_page_server import _b64, _png_bytes, _post, fake_pipeline  # noqa: F401
from tests.test_page_server import token, _reset_module_state  # noqa: F401
from services import extension_bridge_service as bridge

RUN = "0f8fad5b-d9cb-469f-a165-70867728950e"


@pytest.fixture
def drama(isolated_db, monkeypatch):
    monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
    bridge._cancelled_runs.clear()
    return db.create_drama(title_en="Cancel me", media_type="manhua", source_language="zh")


def _images(count):
    return [{"data": _b64(_png_bytes(colour=(i * 40, 30, 90))), "content_type": "image/png", "key": f"k{i}"}
            for i in range(count)]


def test_a_cancelled_run_is_not_worked_on_further(token, fake_pipeline, drama):
    assert _post(token, {"run_id": RUN}, path="/cancel").status == 200
    answer = _post(token, {"images": _images(3), "drama_id": drama, "store": True,
                           "filter_pages": False, "run_id": RUN}, path="/pages")
    assert answer.status == 200
    assert answer.payload["pages"] == []
    assert answer.payload["stopped"] == "cancelled by the extension"


def test_another_run_is_not_affected(token, fake_pipeline, drama):
    _post(token, {"run_id": RUN}, path="/cancel")
    answer = _post(token, {"images": _images(2), "drama_id": drama, "store": True,
                           "filter_pages": False, "run_id": "11111111-2222-3333-4444-555555555555"}, path="/pages")
    assert len(answer.payload["pages"]) == 2 and "stopped" not in answer.payload


@pytest.mark.parametrize("bad", ["", "short", "x" * 65, "has spaces here!", None, 12345678])
def test_a_run_id_must_be_a_short_plain_token(token, drama, bad):
    assert _post(token, {"run_id": bad}, path="/cancel").status == 400


def test_the_remembered_ids_stay_bounded(drama):
    for i in range(bridge._MAX_CANCELLED_RUNS + 50):
        bridge.cancel_run({"run_id": f"run-{i:010d}"})
    assert len(bridge._cancelled_runs) == bridge._MAX_CANCELLED_RUNS
