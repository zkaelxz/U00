"""Scan every saved chapter (services/glossary_chapters_service.py, /api/glossary/dramas/{id}/
from-chapters): terms are added chapter by chapter, never overwrite the person's, and respect the
ignore list. The engine and the LLM call are faked -- no network."""

import pytest

pytest.importorskip("fastapi")

import core
import translation_guide as tguide
from jobs import job_table
from tests.api_helpers import error_of
from tests.job_helpers import wait_for_job
from engine_backends import engine_registry
from services import translate_service

SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRET"


class _Engine:
    supports_reference = True
    model = "fake-model"


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, *a: SECRET)
    monkeypatch.setattr(engine_registry, "get_engine", lambda name, key, **kw: _Engine())


@pytest.fixture(autouse=True)
def _no_leftover_jobs():
    def clear():
        with job_table._lock:
            for jid in [j for j in job_table._jobs if j.startswith("chapters_glossary_")]:
                job_table._jobs.pop(jid, None)
    clear()
    yield
    clear()


def _novel(db, zh):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="N", series_id=sid, translation_engine="claude")
    db.save_lines(did, [core.Line(idx=i, start=float(i), end=i + 1.0, zh=z) for i, z in enumerate(zh)])
    return did, sid


CHAPTERS = ("第一章", "青云宗来了", "青云宗的人", "沈清笑了", "第二章", "青云宗走了", "青云宗再来", "师尊来了")


def _prop(term, en):
    return {"term": term, "suggested_translation": en, "category": "sect", "policy": "hybrid", "reason": "r"}


def test_each_chapter_is_scanned_and_new_terms_are_added_without_overwriting(local_client, isolated_db,
                                                                              monkeypatch, fake_engine):
    did, sid = _novel(isolated_db, CHAPTERS)
    isolated_db.upsert_glossary_term(sid, "沈清", "Shen Qing (mine)")
    seen = []

    def fake_extract(lines, engine, source_language="zh", known_terms=None, **kw):
        seen.append((list(lines), sorted(t["term_original"] for t in known_terms)))
        return [_prop("青云宗", "Qingyun Sect"), _prop("沈清", "Shenqing"), _prop("师尊", "Shizun")]
    monkeypatch.setattr(tguide, "extract_terms_llm", fake_extract)

    r = local_client.post(f"/api/glossary/dramas/{did}/from-chapters")
    assert r.status_code == 200 and r.json()["chapter_count"] == 2, r.text
    wait_for_job(f"chapters_glossary_{did}", timeout=4.0)
    assert [lines[0] for lines, _ in seen] == ["第一章", "第二章"]
    assert "青云宗" in seen[1][1]          # the second chapter already knows what the first one found
    body = local_client.get(f"/api/glossary/dramas/{did}/from-chapters").json()
    assert body["status"] == "done" and "added 1 term" in body["message"] and SECRET not in str(body)
    by = {t["term_original"]: t["term_translation"] for t in isolated_db.list_glossary_terms(sid)}
    assert by == {"沈清": "Shen Qing (mine)", "青云宗": "Qingyun Sect"}   # 师尊 appears once: left out


def test_an_ignored_term_is_not_added(local_client, isolated_db, monkeypatch, fake_engine):
    did, sid = _novel(isolated_db, CHAPTERS)
    isolated_db.add_glossary_dismissals(sid, ["青云宗"])
    monkeypatch.setattr(tguide, "extract_terms_llm", lambda *a, **k: [_prop("青云宗", "Qingyun Sect")])
    assert local_client.post(f"/api/glossary/dramas/{did}/from-chapters").status_code == 200
    wait_for_job(f"chapters_glossary_{did}", timeout=4.0)
    assert isolated_db.list_glossary_terms(sid) == []


def test_a_title_without_chapters_is_refused(local_client, isolated_db, fake_engine):
    flat, _ = _novel(isolated_db, ("她来了", "他走了"))
    r = local_client.post(f"/api/glossary/dramas/{flat}/from-chapters")
    assert r.status_code == 400 and error_of(r)["code"] == "unsupported_operation"
