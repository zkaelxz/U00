"""Translate one novel chapter again (services/novel_chapter_retranslate_service.py and the
/api/translate-run/dramas/{id}/chapters/{n}/retranslate routes). The offline test engine, no network."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from engine_backends import local
from lib.errors import InvalidInputError, UnsupportedOperationError
from services import line_provenance_service
from services import novel_chapter_retranslate_service as svc
from sources import pipeline
from sources.models import ChapterInfo
from tests.job_helpers import wait_for_job


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(local, "check_ollama_reachable", lambda url: False)


def _import(did, n, title, body):
    ch = ChapterInfo(source="xbanxia", series_id="s1", chapter_id=f"c{n}", title=title)
    assert pipeline._append_chapter_text("xbanxia", ch, did, body) == ""
    pipeline._clear_in_flight("xbanxia", ch, did)


def _book():
    """Chapter 1: One, 甲, 乙. Chapter 2: Two, 丙 (丙 edited by hand)."""
    did = db.create_drama(title_en="D")
    _import(did, 1, "One", "甲。\n\n乙。")
    _import(did, 2, "Two", "丙。")
    texts = ["One", "甲。", "乙。", "Two", "丙。"]
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=t, en=f"old{i}")
                        for i, t in enumerate(texts)])
    rows = db.load_lines(did)
    line_provenance_service.record(
        did, {r["id"]: (r["zh"], r["en"]) for r in rows if r["zh"] != "丙。"},
        "fake", "test-offline", "v", "")
    return did


def _en(did):
    return {r["zh"]: r["en"] for r in db.load_lines(did)}


def test_only_the_chapters_lines_are_translated_again(isolated_db):
    did = _book()
    out = svc.start(did, 1, engine_name="fake")
    wait_for_job(out["job_id"], timeout=20.0)
    after = _en(did)
    assert after["One"] == "[TEST] One" and after["甲。"] == "[TEST] 甲。" and after["乙。"] == "[TEST] 乙。"
    assert after["Two"] == "old3" and after["丙。"] == "old4"
    assert out["skipped_hand_edited_count"] == 0


def test_hand_edited_lines_stay_unless_included_and_the_old_text_is_kept(isolated_db):
    did = _book()
    out = svc.start(did, 2, engine_name="fake")
    assert out["skipped_hand_edited_count"] == 1
    wait_for_job(out["job_id"], timeout=20.0)
    assert _en(did)["丙。"] == "old4" and _en(did)["Two"] == "[TEST] Two"
    out = svc.start(did, 2, include_hand_edited=True, engine_name="fake")
    wait_for_job(out["job_id"], timeout=20.0)
    assert _en(did)["丙。"] == "[TEST] 丙。"
    assert "old4" in repr([db.get_line_history_snapshot(h["id"]) for h in db.list_line_history(did)])


def test_a_chapter_of_only_hand_edited_lines_is_refused_and_a_missing_one_too(isolated_db):
    did = _book()
    lines = db.load_line_objects(did)
    for ln in lines:
        if ln.zh in ("Two", "丙。"):
            ln.en = "mine"
    db.save_lines(did, lines)
    with pytest.raises(UnsupportedOperationError):
        svc.start(did, 2, engine_name="fake")
    with pytest.raises(InvalidInputError):
        svc.start(did, 9, engine_name="fake")


def test_the_preview_counts_lines_and_hand_edits(isolated_db):
    did = _book()
    p = svc.preview(did, 2, engine_name="fake")
    assert (p["line_count"], p["hand_edited_count"]) == (2, 1)
    assert p["estimate"] is not None and p["estimate_with_hand_edited"] is not None


def test_routes(isolated_db):
    did = _book()
    client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                        headers={"X-Baihe-Local": "1"})
    r = client.get(f"/api/translate-run/dramas/{did}/chapters/1/retranslate", params={"engine": "fake"})
    assert r.status_code == 200 and r.json()["line_count"] == 3
    r = client.post(f"/api/translate-run/dramas/{did}/chapters/1/retranslate/run", json={"engine": "fake"})
    assert r.status_code == 200, r.text
    wait_for_job(r.json()["job_id"], timeout=20.0)
    assert _en(did)["甲。"] == "[TEST] 甲。"
    assert client.get(f"/api/translate-run/dramas/{did}/chapters/9/retranslate").status_code == 422
