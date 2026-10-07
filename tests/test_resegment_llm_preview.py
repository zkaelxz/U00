"""Parity R47: the LLM re-segmentation preview (services/restructure_service
start_llm_resegment_preview / get_llm_resegment_preview and
/api/restructure/.../resegment/preview-llm). Fully mocked: no real LLM, no
GPU, no subprocess. The preview must never write the drama's lines."""
import time

import pytest

import background_jobs
import db
import resegment
import translate_engines
from core import Line
from services import jobs_service, restructure_service as svc, settings_service, translate_service
from services.service_errors import (ConflictError, NotFoundError,
                                      UnsupportedOperationError)

LONG = "我今天早上很早就起床了然后去公园跑步。" * 3


class FakeEngine:
    model = "fake-model"


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    svc._llm_previews.clear()
    monkeypatch.setattr(resegment, "word_boundaries", lambda *a, **k: None)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    yield
    background_jobs.clear_all_jobs()
    svc._llm_previews.clear()


def _seed():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="短", en="short"),
                        Line(idx=1, start=1.0, end=9.0, zh=LONG, en="long one", flag="x")])
    return did


def _snapshot(did):
    return [(r["id"], r["idx"], r["zh"], r["en"], r["flag"]) for r in db.load_lines(did)]


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _fake_llm_split(monkeypatch, seen):
    real = resegment.resegment_lines

    def fake(lines, language, engine=None, usage_cb=None, **kw):
        seen.append(engine)
        if usage_cb:
            usage_cb(100, 20)
        return real(lines, language, engine=None, **kw)
    monkeypatch.setattr(resegment, "resegment_lines", fake)


def test_llm_preview_writes_no_lines_and_reads_back(monkeypatch):
    did = _seed()
    before = _snapshot(did)
    seen, logged = [], []
    monkeypatch.setattr(db, "log_usage", lambda d, e, m, op, i, o, *a, **k:
                        logged.append((d, op, i, o)))
    _fake_llm_split(monkeypatch, seen)
    out = svc.start_llm_resegment_preview(did, engine="claude")
    assert out == {"job_id": f"resegpreview_{did}", "drama_id": did}
    assert _wait(out["job_id"])["status"] == "done"
    assert isinstance(seen[0], FakeEngine)                # the LLM pass ran
    assert _snapshot(did) == before                       # nothing written to the lines
    p = svc.get_llm_resegment_preview(did)
    assert p["engine"] == "claude" and p["line_count_before"] == 2
    assert p["line_count_after"] > 2 and p["changed"][0]["line_id"] == before[1][0]
    assert p["translated"] == 1 and p["flagged"] == 1 and p["needs_confirm"] is True
    assert p["source_line_ids"] == [r[0] for r in before]
    # the LLM's usage is logged (the one non-line write), so spend caps see it
    assert logged == [(did, "resegment", 100, 20)]


def test_job_result_never_carries_line_text(monkeypatch):
    did = _seed()
    _fake_llm_split(monkeypatch, [])
    job = _wait(svc.start_llm_resegment_preview(did, engine="claude")["job_id"])
    assert LONG[:5] not in str(jobs_service.project_result(job.get("result")))


def test_no_preview_until_one_finishes():
    did = _seed()
    with pytest.raises(NotFoundError):
        svc.get_llm_resegment_preview(did)
    with pytest.raises(NotFoundError):
        svc.get_llm_resegment_preview(99999)


def test_ollama_runs_in_a_process_job_and_stores_on_done(monkeypatch):
    did = _seed()
    before = _snapshot(did)
    captured = {}

    def fake_process_job(job_id, target, args=(), on_done=None, **kw):
        captured.update(job_id=job_id, target=target, kw=kw)
        lines, language, eng, segments, script, min_pause = args
        new_lines, changed = resegment.resegment_lines(lines, language)
        on_done(job_id, {"lines": new_lines, "usage_calls": [(10, 2)],
                         "changed": [(ln.id, ln.idx, ln.zh, p) for ln, p in changed]})
        return True
    monkeypatch.setattr(background_jobs, "start_process_job", fake_process_job)
    svc.start_llm_resegment_preview(did, engine="ollama")
    assert captured["target"] is resegment.resegment_subprocess_worker
    assert captured["kw"]["gpu_touching"] is True
    assert svc.get_llm_resegment_preview(did)["engine"] == "ollama"
    assert _snapshot(did) == before


def test_refusals(monkeypatch):
    did = _seed()
    with pytest.raises(UnsupportedOperationError):
        svc.start_llm_resegment_preview(did, engine="fake_mt")   # translation-only
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda: 1.0)
    monkeypatch.setattr(db, "get_month_spend", lambda *a, **k: 5.0)
    with pytest.raises(UnsupportedOperationError):
        svc.start_llm_resegment_preview(did, engine="claude")
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda: 0.0)
    monkeypatch.setattr(background_jobs, "is_running", lambda job_id: True)
    with pytest.raises(ConflictError):
        svc.start_llm_resegment_preview(did, engine="claude")


def test_preview_job_is_a_drama_job():
    from services import ownership_service
    assert ownership_service.drama_id_of_job("resegpreview_12") == 12


def test_api_routes(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    did = _seed()
    before = _snapshot(did)
    _fake_llm_split(monkeypatch, [])
    base = f"/api/restructure/dramas/{did}/resegment/preview-llm"
    assert c.get(base).status_code == 404
    r = c.post(base, json={"engine": "claude"})
    assert r.status_code == 200, r.text
    _wait(r.json()["job_id"])
    r = c.get(base)
    assert r.status_code == 200 and r.json()["engine"] == "claude"
    assert r.json()["changed"] and _snapshot(did) == before
    assert c.post(base, json={"engine": "claude", "bogus": 1}).status_code == 422
    assert c.post("/api/restructure/dramas/99999/resegment/preview-llm",
                  json={"engine": "claude"}).status_code == 404


# --- Apply the stored preview (use_preview=true): no second LLM call ---

def _preview(monkeypatch, did):
    seen = []
    _fake_llm_split(monkeypatch, seen)
    _wait(svc.start_llm_resegment_preview(did, engine="claude")["job_id"])
    return seen


def test_apply_commits_the_preview_without_calling_the_llm(monkeypatch):
    did = _seed()
    seen = _preview(monkeypatch, did)
    p = svc.get_llm_resegment_preview(did)
    calls = len(seen)
    out = svc.start_resegmentation(did, p["source_line_ids"], confirm=True, use_preview=True)
    job = _wait(out["job_id"])
    assert job["status"] == "done", job
    assert len(seen) == calls                               # no second LLM pass
    rows = db.load_lines(did)
    assert len(rows) == p["line_count_after"]
    assert [r["zh"] for r in rows[1:]] == p["changed"][0]["pieces"]
    assert rows[0]["zh"] == "短" and rows[0]["en"] == "short"
    with pytest.raises(NotFoundError):                     # consumed: can't apply twice
        svc.get_llm_resegment_preview(did)


def test_apply_refused_after_an_edit_since_the_preview(monkeypatch):
    did = _seed()
    _preview(monkeypatch, did)
    ids = svc.get_llm_resegment_preview(did)["source_line_ids"]
    # the user edits the short line's translation after previewing
    db.save_lines(did, [Line(id=ids[0], idx=0, start=0.0, end=1.0, zh="短", en="edited")],
                  fields=("en",))
    before = _snapshot(did)
    with pytest.raises(ConflictError):
        svc.start_resegmentation(did, ids, confirm=True, use_preview=True)
    assert _snapshot(did) == before


def test_apply_edit_racing_the_job_writes_nothing(monkeypatch):
    did = _seed()
    _preview(monkeypatch, did)
    preview = svc._llm_previews[did]
    ids = preview["source_line_ids"]
    db.save_lines(did, [Line(id=ids[0], idx=0, start=0.0, end=1.0, zh="改", en="short")],
                  fields=("zh",))
    before = _snapshot(did)
    background_jobs.start_job("resegment_x", svc._apply_llm_preview_job, "resegment_x", did,
                              preview)
    assert _wait("resegment_x")["status"] == "error"
    assert _snapshot(did) == before


def test_apply_needs_confirm_and_a_preview(monkeypatch):
    did = _seed()
    ids = [r["id"] for r in db.load_lines(did)]
    with pytest.raises(NotFoundError):
        svc.start_resegmentation(did, ids, use_preview=True)
    _preview(monkeypatch, did)
    from services.service_errors import InvalidInputError
    with pytest.raises(InvalidInputError):                  # the long line is translated+flagged
        svc.start_resegmentation(did, ids, use_preview=True)
    with pytest.raises(InvalidInputError):
        svc.start_resegmentation(did, ids, confirm=True, use_preview=True, use_llm=True)
    with pytest.raises(ConflictError):
        svc.start_resegmentation(did, ids[:1], confirm=True, use_preview=True)


def test_api_apply_preview(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    did = _seed()
    seen = _preview(monkeypatch, did)
    calls = len(seen)
    p = c.get(f"/api/restructure/dramas/{did}/resegment/preview-llm").json()
    r = c.post(f"/api/restructure/dramas/{did}/resegment",
               json={"expected_line_ids": p["source_line_ids"], "confirm": True,
                     "use_preview": True})
    assert r.status_code == 200, r.text
    assert _wait(r.json()["job_id"])["status"] == "done"
    assert len(db.load_lines(did)) == p["line_count_after"] and len(seen) == calls
    assert c.get(f"/api/restructure/dramas/{did}/resegment/preview-llm").status_code == 404
    r = c.post(f"/api/restructure/dramas/{did}/resegment",
               json={"expected_line_ids": [], "use_preview": "yes"})
    assert r.status_code == 422


def _seed_unconfirmed():
    """The long line has no translation, flag or note: no confirm needed yet."""
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="短", en="short"),
                        Line(idx=1, start=1.0, end=9.0, zh=LONG, en="")])
    return did


def test_note_added_after_the_preview_needs_confirm_on_apply(monkeypatch):
    """Review fix: a translation note isn't in the preview's fingerprint, so
    the preview's needs_confirm can be stale; apply recounts on the lines
    as they are now and refuses without confirm, keeping the note."""
    from services import lines_service
    from services.service_errors import InvalidInputError
    did = _seed_unconfirmed()
    _preview(monkeypatch, did)
    p = svc.get_llm_resegment_preview(did)
    assert p["needs_confirm"] is False and p["changed"]
    long_id = p["changed"][0]["line_id"]
    lines_service.add_note(did, long_id, "公园", "cultural", "a park")
    before = _snapshot(did)
    with pytest.raises(InvalidInputError):
        svc.start_resegmentation(did, p["source_line_ids"], use_preview=True)
    assert _snapshot(did) == before
    assert [n["line_id"] for n in db.list_translation_notes(did)] == [long_id]
    out = svc.start_resegmentation(did, p["source_line_ids"], confirm=True, use_preview=True)
    assert _wait(out["job_id"])["status"] == "done"
    assert len(db.load_lines(did)) == p["line_count_after"]


def test_note_racing_the_apply_job_needs_confirm(monkeypatch):
    """The same recount runs inside the job, under the drama lock: a note
    added between the start check and the write stops an unconfirmed apply."""
    from services import lines_service
    did = _seed_unconfirmed()
    _preview(monkeypatch, did)
    preview = svc._llm_previews[did]
    lines_service.add_note(did, preview["changed"][0]["line_id"], "公园", "cultural", "a park")
    before = _snapshot(did)
    background_jobs.start_job("resegment_x", svc._apply_llm_preview_job, "resegment_x", did,
                              preview, False)
    job = _wait("resegment_x")
    assert job["status"] == "error" and "confirm=true" in job["error"]
    assert _snapshot(did) == before and len(db.list_translation_notes(did)) == 1


def test_stale_preview_for_other_lines_is_not_read_back(monkeypatch):
    """Review fix: previews are kept per drama id in memory. Once the
    drama's lines are replaced (a reset, a backup restore, a deleted drama
    whose id comes back), the old preview -- which carries the old lines'
    text -- is a 404 and is dropped, not served."""
    did = _seed()
    _preview(monkeypatch, did)
    assert svc.get_llm_resegment_preview(did)["changed"]
    # a plain edit keeps the line ids: the preview is still readable
    ids = svc.get_llm_resegment_preview(did)["source_line_ids"]
    db.save_lines(did, [Line(id=ids[0], idx=0, start=0.0, end=1.0, zh="短", en="edited")],
                  fields=("en",))
    assert svc.get_llm_resegment_preview(did)["source_line_ids"] == ids
    # the lines are replaced wholesale (new ids): stale
    db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="别的", en="other")])
    with pytest.raises(NotFoundError):
        svc.get_llm_resegment_preview(did)
    assert did not in svc._llm_previews
