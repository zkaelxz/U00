"""Library tools > "Typeset all comics again": every comic with translations is typeset again
through its own render job; nothing is read, translated or paid for. The page renderer is a fake."""
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api.api_config import ApiSettings
from api.server import create_app
from services import scanlate_render_service as render_svc
from services import scanlate_run_service as run_svc
from services import scanlate_typeset_all_service as svc
from lib.errors import UnsupportedOperationError
from tests.job_helpers import wait_for_job

LOCAL = {"X-Baihe-Local": "1"}


def _comic(media="manhwa", pages=2, translated=True, name="Comic"):
    did = db.create_drama(title_en=name, source_language="ko", media_type=media)
    ids = []
    for i in range(pages):
        pid = db.create_page(did, i, f"pages/page_{i:04d}.png", 10, 10)
        ids.append(pid)
        if translated:
            db.save_bubbles(pid, [{"x": 1, "y": 1, "w": 5, "h": 5, "source_text": "a",
                                   "translated_text": "b", "kind": "bubble"}])
    return did, ids


@pytest.fixture
def rendered(isolated_db, monkeypatch):
    calls = []

    def fake_render(drama_id, page_id, notes=None, cancel_check=None):
        calls.append(page_id)
        return {"rendered": True, "blank": 0}

    monkeypatch.setattr(render_svc, "render_page", fake_render)
    # The paid path must be unreachable from here.
    monkeypatch.setattr(run_svc, "start_run", lambda *a, **k: pytest.fail("paid run started"))
    return calls


def test_typesets_every_comic_with_translations_and_summarises(rendered):
    a, a_ids = _comic(name="A")
    b, b_ids = _comic(media="manga", pages=1, name="B")
    _comic(translated=False, name="No text")
    _comic(media="audio_drama", name="Not a comic")
    started = svc.start_typeset_all()
    job = wait_for_job(started["job_id"])
    assert job["status"] == "done"
    assert sorted(rendered) == sorted(a_ids + b_ids)
    assert job["result"]["titles_done"] == 2
    assert job["result"]["pages_redrawn"] == 3
    assert "2 title(s) typeset, 3 page(s) redrawn" in job["message"]


def test_nothing_to_typeset_is_refused(rendered):
    _comic(translated=False)
    with pytest.raises(UnsupportedOperationError):
        svc.start_typeset_all()


def test_a_busy_title_is_skipped_and_counted(rendered, monkeypatch):
    a, _ = _comic(name="A")
    b, b_ids = _comic(name="B")
    real = render_svc.start_render
    from lib.errors import ConflictError

    def start(drama_id, *args, **kw):
        if drama_id == a:
            raise ConflictError("busy")
        return real(drama_id, *args, **kw)

    monkeypatch.setattr(render_svc, "start_render", start)
    job = wait_for_job(svc.start_typeset_all()["job_id"])
    assert job["result"]["titles_busy"] == 1
    assert job["result"]["titles_done"] == 1
    assert rendered == b_ids


def test_failed_pages_are_counted(rendered, monkeypatch):
    _comic(pages=2)

    def failing(drama_id, page_id, notes=None, cancel_check=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(render_svc, "render_page", failing)
    job = wait_for_job(svc.start_typeset_all()["job_id"])
    assert job["status"] == "done"
    assert job["result"]["pages_failed"] == 2 and job["result"]["pages_redrawn"] == 0
    assert "2 page(s) failed" in job["message"]


def test_cancel_stops_before_the_next_title(isolated_db, monkeypatch):
    first, _ = _comic(name="A")
    second, second_ids = _comic(name="B")
    seen, gate = [], threading.Event()

    def slow(drama_id, page_id, notes=None, cancel_check=None):
        seen.append(drama_id)
        background_jobs.request_cancel(svc.JOB_ID)
        gate.wait(0.2)
        return {"rendered": True, "blank": 0}

    monkeypatch.setattr(render_svc, "render_page", slow)
    job = wait_for_job(svc.start_typeset_all()["job_id"])
    assert job["status"] == "cancelled"
    assert len(set(seen)) == 1


def test_route_is_local_only_and_starts_the_job(rendered):
    _comic()
    client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    resp = client.post("/api/library/admin/typeset-comics", headers=LOCAL)
    assert resp.status_code == 200 and resp.json() == {"job_id": svc.JOB_ID}
    wait_for_job(svc.JOB_ID)
