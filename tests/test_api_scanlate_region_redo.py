"""POST /api/scanlate/dramas/{id}/pages/{page}/regions/{region}/redo: OCR, engine and render are faked."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("PIL")

import db
import scanlate
import scanlate_translate
from engine_backends import llm_tasks
from lib.errors import ConflictError
from services import scanlate_pages_service as pages_svc
from services import scanlate_render_service as render_svc
from services import scanlate_run_service as run_svc
from tests.test_api_comic_region_edit import _page, client  # noqa: F401


class Spy:
    def __init__(self):
        self.ocr, self.translated, self.renders = [], [], []


@pytest.fixture
def spy(monkeypatch):
    s = Spy()
    monkeypatch.setattr(run_svc, "_require_opencv", lambda: None)
    monkeypatch.setattr(run_svc, "_require_ocr_backend", lambda d: None)
    monkeypatch.setattr(run_svc, "_build_engine", lambda name: object())
    monkeypatch.setattr(llm_tasks, "bound_batches", lambda e: e)
    monkeypatch.setattr(run_svc, "_predecessor_context", lambda d, p: "")

    def ocr(path, box, lang, **kw):
        s.ocr.append(box["id"])
        return s.read_text

    def translate(texts, engine, meta, **kw):
        s.translated.append(dict(texts))
        return ({k: f"EN:{v}" for k, v in texts.items()}, "ctx") if s.answer else (None, "")

    s.read_text, s.answer = "新的字", True
    monkeypatch.setattr(scanlate, "ocr_box_region", ocr)
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", translate)
    monkeypatch.setattr(render_svc, "start_render", lambda d, p=None: s.renders.append(p) or {"job_id": "x"})
    return s


def _redo(client, did, pid, rid, rev, **body):
    return client.post(f"/api/scanlate/dramas/{did}/pages/{pid}/regions/{rid}/redo",
                       json={"expected_rev": rev, **body})


def _rev(pid):
    return int(db.get_page(pid).get("rev") or 0)


def test_both_reads_translates_saves_and_renders_only_that_region(client, spy):
    did, pid, (r1, r2) = _page()
    other_before = next(b for b in db.load_bubbles(pid) if b["id"] == r2)["translated_text"]
    r = _redo(client, did, pid, r1, _rev(pid))
    assert r.status_code == 200, r.text
    regions = {x["id"]: x for x in r.json()["regions"]}
    assert regions[r1]["translated_text"] == "EN:新的字" and regions[r2]["translated_text"] == other_before
    assert spy.ocr == [r1] and spy.translated == [{str(r1): "新的字"}] and spy.renders == [pid]
    assert next(b for b in db.load_bubbles(pid) if b["id"] == r1)["source_text"] == "新的字"
    assert r.json()["rev"] == _rev(pid) and r.json()["undo_depth"] >= 1


def test_translate_mode_keeps_the_stored_source_and_skips_ocr(client, spy):
    did, pid, (r1, _r2) = _page()
    r = _redo(client, did, pid, r1, _rev(pid), mode="translate")
    assert r.status_code == 200 and spy.ocr == []
    assert spy.translated == [{str(r1): "你好"}]


def test_stale_rev_is_409_and_nothing_runs(client, spy):
    did, pid, (r1, _r2) = _page()
    r = _redo(client, did, pid, r1, _rev(pid) + 5)
    assert r.status_code == 409 and spy.ocr == [] and spy.translated == []


def test_unreadable_box_is_422_and_changes_nothing(client, spy):
    did, pid, (r1, _r2) = _page()
    spy.read_text = "  "
    before = db.load_bubbles(pid)
    assert _redo(client, did, pid, r1, _rev(pid)).status_code == 422
    assert db.load_bubbles(pid) == before and spy.translated == []


def test_unmatched_translation_is_503_and_changes_nothing(client, spy):
    did, pid, (r1, _r2) = _page()
    spy.answer = False
    before = db.load_bubbles(pid)
    assert _redo(client, did, pid, r1, _rev(pid)).status_code == 503
    assert db.load_bubbles(pid) == before and spy.renders == []


def test_running_job_is_409(client, spy, monkeypatch):
    did, pid, (r1, _r2) = _page()
    monkeypatch.setattr(pages_svc, "job_busy", lambda d: True)
    assert _redo(client, did, pid, r1, _rev(pid)).status_code == 409


def test_a_render_that_loses_the_race_still_saves_the_text(client, spy, monkeypatch):
    did, pid, (r1, _r2) = _page()

    def busy(d, p=None):
        raise ConflictError("busy")
    monkeypatch.setattr(render_svc, "start_render", busy)
    assert _redo(client, did, pid, r1, _rev(pid)).status_code == 200


def test_unknown_region_is_404(client, spy):
    did, pid, _ids = _page()
    assert _redo(client, did, pid, 99999, _rev(pid)).status_code == 404
