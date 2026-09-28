"""
Tests for api/routers/review_records_routes.py (Migration Slice 48): the
Review read-only records endpoints. FastAPI TestClient against an
`isolated_db` library -- no server process, no network.
"""

import json
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from core import Line, lines_from_rows

WRITE_RE = re.compile(r"^(save|update|delete|set|record|bump|upsert)_")


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _drama(db, **f):
    f.setdefault("title_en", "D")
    return db.create_drama(**f)


def _lines(db, did, ens=("Hello", "Bye")):
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"中{i}", en=e)
                        for i, e in enumerate(ens)])
    return lines_from_rows(db.load_lines(did))


def _error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])
    assert body["error"]["code"] == code


def _seed(db):
    """A drama with a series and one of everything; returns ids."""
    sid = db.get_or_create_series("S")
    did = _drama(db, series_id=sid, title_en="Show")
    lines = _lines(db, did, ens=("Old", "Same"))
    lines[0].dub_filename = "/secret/dir/a.wav"
    db.save_line_history_snapshot(did, lines, "first")
    db.save_line_history_snapshot(did, lines, "second")
    v1 = db.save_translation_version(did, lines, "v1", engine="e")
    lines[1].en = "Changed"
    v2 = db.save_translation_version(did, lines, "v2", model="m", make_active=True)
    db.save_translation_notes(did, [
        {"line_idx": 1, "term": "T2", "note_type": "cultural", "note": "second"},
        {"line_idx": 0, "term": "T1", "note_type": "cultural", "note": "first"}])
    db.save_consistency_issues(did, [{"term": "X", "variants": ["a", "b"], "note": "n"}])
    db.save_emotions(did, {0: {"emotion": "sarcastic", "intensity": 0.8, "note": "x"},
                           1: {"emotion": "neutral", "intensity": 0.1}})
    db.record_translation_memory(sid, "中0", "Remembered")
    db.record_translation_memory(sid, "中1", "Same")
    return did, lines, v1, v2


def _paths(did, hid=1, v1=1, v2=2):
    b = f"/api/review/dramas/{did}"
    return [f"{b}/history", f"{b}/history/{hid}", f"{b}/versions",
            f"{b}/versions/compare?left_id={v1}&right_id={v2}", f"{b}/notes",
            f"{b}/notes/markdown", f"{b}/consistency", f"{b}/emotions",
            f"{b}/tendencies", f"{b}/tm-suggestions"]


def test_history_newest_first_and_snapshot(client, isolated_db):
    did, lines, _, _ = _seed(isolated_db)
    r = client.get(f"/api/review/dramas/{did}/history")
    assert r.status_code == 200
    hist = r.json()
    assert [h["label"] for h in hist] == ["second", "first"]
    assert "lines" not in hist[0]
    r = client.get(f"/api/review/dramas/{did}/history/{hist[0]['id']}")
    assert r.status_code == 200
    snap = r.json()
    assert snap["label"] == "second"
    assert [l["en"] for l in snap["lines"]] == ["Old", "Same"]
    assert snap["lines"][0]["dub_filename"] == "a.wav"


def test_history_of_other_drama_or_missing_is_404(client, isolated_db):
    did, _, _, _ = _seed(isolated_db)
    other = _drama(isolated_db)
    hid = isolated_db.list_line_history(did)[0]["id"]
    _error(client.get(f"/api/review/dramas/{other}/history/{hid}"), 404, "not_found")
    _error(client.get(f"/api/review/dramas/{did}/history/{hid + 100}"), 404, "not_found")
    _error(client.get(f"/api/review/dramas/{did}/history/0"), 422, "validation_error")


def test_versions_newest_first_and_compare(client, isolated_db):
    did, _, v1, v2 = _seed(isolated_db)
    vs = client.get(f"/api/review/dramas/{did}/versions").json()
    assert [v["label"] for v in vs] == ["v2", "v1"]
    assert vs[0]["is_active"] is True and vs[1]["is_active"] is False
    r = client.get(f"/api/review/dramas/{did}/versions/compare",
                   params={"left_id": v1, "right_id": v2})
    assert r.status_code == 200
    res = r.json()
    assert res["diff_count"] == 1 and res["left_line_count"] == 2
    assert res["diffs"] == [{"idx": 1, "zh": "中1", "left_en": "Same", "right_en": "Changed"}]


def test_compare_other_drama_version_and_bad_params(client, isolated_db):
    did, _, v1, v2 = _seed(isolated_db)
    other = _drama(isolated_db)
    vb = isolated_db.save_translation_version(other, _lines(isolated_db, other), "b")
    base = f"/api/review/dramas/{did}/versions/compare"
    _error(client.get(base, params={"left_id": v1, "right_id": vb}), 404, "not_found")
    _error(client.get(base, params={"left_id": vb, "right_id": v1}), 404, "not_found")
    _error(client.get(base, params={"left_id": v1, "right_id": v2 + 999}), 404, "not_found")
    _error(client.get(base, params={"left_id": v1}), 422, "validation_error")
    _error(client.get(base, params={"left_id": 0, "right_id": v1}), 422, "validation_error")


def test_notes_list_and_markdown(client, isolated_db):
    did, lines, _, _ = _seed(isolated_db)
    notes = client.get(f"/api/review/dramas/{did}/notes").json()
    assert [n["term"] for n in notes] == ["T1", "T2"]
    assert notes[0]["line_id"] == lines[0].id and notes[0]["line_idx"] == 0
    r = client.get(f"/api/review/dramas/{did}/notes/markdown")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "utf-8" in r.headers["content-type"].lower()
    assert "**T1**: first" in r.text and "**T2**: second" in r.text and "Show" in r.text
    other = _drama(isolated_db)
    assert client.get(f"/api/review/dramas/{other}/notes").json() == []
    assert "No notes recorded" in client.get(f"/api/review/dramas/{other}/notes/markdown").text


def test_consistency_and_emotions(client, isolated_db):
    did, _, _, _ = _seed(isolated_db)
    iss = client.get(f"/api/review/dramas/{did}/consistency").json()
    assert iss[0]["term"] == "X" and iss[0]["variants"] == ["a", "b"]
    em = client.get(f"/api/review/dramas/{did}/emotions").json()
    assert em["total"] == 2 and em["high_risk"] == 1
    assert em["by_emotion"] == {"sarcastic": 1, "neutral": 1}
    assert [t["line_idx"] for t in em["lines"]] == [0, 1]
    empty = _drama(isolated_db)
    assert client.get(f"/api/review/dramas/{empty}/consistency").json() == []
    assert client.get(f"/api/review/dramas/{empty}/emotions").json()["total"] == 0


def test_tendencies_without_and_with_profile(client, isolated_db):
    did = _drama(isolated_db)
    res = client.get(f"/api/review/dramas/{did}/tendencies").json()
    assert res["profile"] is None and res["tendencies"]["total"] == 0
    assert res["scope"] == "global"
    isolated_db.record_edit_sample(did, "中", "one two three four five", "one")
    isolated_db.save_style_profile("global", {"preferences": ["short"], "summary": "s",
                                              "confidence": "low"}, 3)
    res = client.get(f"/api/review/dramas/{did}/tendencies").json()
    assert res["tendencies"]["shortened"] == 1
    assert res["profile"]["preferences"] == ["short"] and res["profile"]["sample_count"] == 3


def test_tm_suggestions_and_line_id_filter(client, isolated_db):
    did, lines, _, _ = _seed(isolated_db)
    base = f"/api/review/dramas/{did}/tm-suggestions"
    res = client.get(base).json()
    assert len(res) == 1
    assert res[0]["line_id"] == lines[0].id and res[0]["suggestion"] == "Remembered"
    assert res[0]["exact"] is True and {"line_idx", "zh", "en", "similarity", "entry_id"} <= set(res[0])
    assert client.get(base, params={"line_id": lines[1].id}).json() == []
    assert len(client.get(base, params=[("line_id", lines[0].id), ("line_id", lines[1].id)]).json()) == 1
    _error(client.get(base, params={"line_id": "x"}), 422, "validation_error")
    assert client.get(f"/api/review/dramas/{_drama(isolated_db)}/tm-suggestions").json() == []


def test_unknown_drama_404_everywhere(client, isolated_db):
    for path in _paths(9999):
        _error(client.get(path), 404, "not_found")
    _error(client.get("/api/review/dramas/0/history"), 422, "validation_error")


def test_zero_writes_and_no_path_leak(client, isolated_db, monkeypatch):
    did, lines, v1, v2 = _seed(isolated_db)
    hid = isolated_db.list_line_history(did)[0]["id"]

    def boom(*a, **k):
        raise AssertionError("db write attempted")
    for name in dir(isolated_db):
        if WRITE_RE.match(name) and callable(getattr(isolated_db, name)):
            monkeypatch.setattr(isolated_db, name, boom)

    blob = ""
    for path in _paths(did, hid, v1, v2):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
        blob += r.text
    assert "/secret" not in blob
    assert "a.wav" in blob
    json.loads(client.get(f"/api/review/dramas/{did}/emotions").text)
