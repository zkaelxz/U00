"""
Tests for services/review_records_service.py (Review read-only records
slice): every function is read-only, scoped to its drama, and raises
NotFoundError for another drama's history/version ids.
"""
import json
import re

import pytest

from core import Line, lines_from_rows
from services import review_records_service as svc
from services.service_errors import NotFoundError

WRITE_RE = re.compile(r"^(save|update|delete|set|record|bump|upsert)_")


def _drama(db, **f):
    f.setdefault("title_en", "D")
    return db.create_drama(**f)


def _lines(db, did, ens=("Hello", "Bye")):
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"中{i}", en=e)
                        for i, e in enumerate(ens)])
    return lines_from_rows(db.load_lines(did))


ALL_CALLS = [
    lambda d: svc.list_line_history(d),
    lambda d: svc.list_translation_versions(d),
    lambda d: svc.list_translation_notes(d),
    lambda d: svc.get_notes_markdown(d),
    lambda d: svc.get_consistency_issues(d),
    lambda d: svc.get_emotion_summary(d),
    lambda d: svc.get_edit_tendencies(d),
    lambda d: svc.list_tm_suggestions(d),
    lambda d: svc.get_line_history_snapshot(d, 1),
    lambda d: svc.compare_versions(d, 1, 2),
]


@pytest.mark.parametrize("call", ALL_CALLS)
def test_unknown_drama_not_found(isolated_db, call):
    with pytest.raises(NotFoundError):
        call(9999)


def test_history_newest_first_and_snapshot(isolated_db):
    did = _drama(isolated_db)
    lines = _lines(isolated_db, did)
    isolated_db.save_line_history_snapshot(did, lines, "first")
    isolated_db.save_line_history_snapshot(did, lines, "second")
    hist = svc.list_line_history(did)
    assert [h["label"] for h in hist] == ["second", "first"]
    assert "lines" not in hist[0]
    snap = svc.get_line_history_snapshot(did, hist[0]["id"])
    assert snap["label"] == "second"
    assert [l["en"] for l in snap["lines"]] == ["Hello", "Bye"]
    assert "flag" not in snap["lines"][0]
    json.dumps(snap)


def test_history_of_other_drama_not_found(isolated_db):
    a, b = _drama(isolated_db), _drama(isolated_db)
    isolated_db.save_line_history_snapshot(a, _lines(isolated_db, a), "a")
    hid = isolated_db.list_line_history(a)[0]["id"]
    with pytest.raises(NotFoundError):
        svc.get_line_history_snapshot(b, hid)
    with pytest.raises(NotFoundError):
        svc.get_line_history_snapshot(a, hid + 100)


def test_versions_newest_first_and_compare(isolated_db):
    did = _drama(isolated_db)
    lines = _lines(isolated_db, did)
    v1 = isolated_db.save_translation_version(did, lines, "v1", engine="e")
    lines[1].en = "Goodbye"
    v2 = isolated_db.save_translation_version(did, lines, "v2", model="m", make_active=True)
    vs = svc.list_translation_versions(did)
    assert [v["label"] for v in vs] == ["v2", "v1"]
    assert vs[0]["is_active"] is True and vs[1]["is_active"] is False
    res = svc.compare_versions(did, v1, v2)
    assert res["diff_count"] == 1 and res["left_line_count"] == 2
    assert res["diffs"][0] == {"idx": 1, "zh": "中1", "left_en": "Bye", "right_en": "Goodbye"}
    json.dumps(res)


def test_compare_with_other_drama_version_not_found(isolated_db):
    a, b = _drama(isolated_db), _drama(isolated_db)
    va = isolated_db.save_translation_version(a, _lines(isolated_db, a), "a")
    vb = isolated_db.save_translation_version(b, _lines(isolated_db, b), "b")
    with pytest.raises(NotFoundError):
        svc.compare_versions(a, va, vb)
    with pytest.raises(NotFoundError):
        svc.compare_versions(a, vb, va)
    with pytest.raises(NotFoundError):
        svc.compare_versions(a, va, va + 999)


def test_notes_list_and_markdown(isolated_db):
    did = _drama(isolated_db, title_en="Show")
    lines = _lines(isolated_db, did)
    isolated_db.save_translation_notes(did, [
        {"line_idx": 1, "term": "T2", "note_type": "cultural", "note": "second"},
        {"line_idx": 0, "term": "T1", "note_type": "cultural", "note": "first"}])
    notes = svc.list_translation_notes(did)
    assert [n["term"] for n in notes] == ["T1", "T2"]
    assert notes[0]["line_id"] == lines[0].id and notes[0]["line_idx"] == 0
    md = svc.get_notes_markdown(did)
    assert "Show" in md and "**T1**: first" in md and "**T2**: second" in md
    other = _drama(isolated_db)
    assert svc.list_translation_notes(other) == []
    assert "No notes recorded" in svc.get_notes_markdown(other)


def test_consistency_and_emotions(isolated_db):
    did = _drama(isolated_db)
    _lines(isolated_db, did)
    assert svc.get_consistency_issues(did) == []
    assert svc.get_emotion_summary(did)["total"] == 0
    isolated_db.save_consistency_issues(did, [{"term": "X", "variants": ["a", "b"], "note": "n"}])
    isolated_db.save_emotions(did, {0: {"emotion": "sarcastic", "intensity": 0.8, "note": "x"},
                                    1: {"emotion": "neutral", "intensity": 0.1}})
    iss = svc.get_consistency_issues(did)
    assert iss[0]["term"] == "X" and iss[0]["variants"] == ["a", "b"]
    em = svc.get_emotion_summary(did)
    assert em["total"] == 2 and em["high_risk"] == 1
    assert em["by_emotion"] == {"sarcastic": 1, "neutral": 1}
    assert [t["line_idx"] for t in em["lines"]] == [0, 1]
    json.dumps([iss, em])


def test_tendencies_without_profile_and_with(isolated_db):
    did = _drama(isolated_db)
    res = svc.get_edit_tendencies(did)
    assert res["profile"] is None and res["tendencies"]["total"] == 0
    assert res["scope"] == "global"
    isolated_db.record_edit_sample(did, "中", "one two three four five", "one")
    isolated_db.save_style_profile("global", {"preferences": ["short"], "summary": "s",
                                              "confidence": "low"}, 3)
    res = svc.get_edit_tendencies(did)
    assert res["tendencies"]["shortened"] == 1
    assert res["profile"]["preferences"] == ["short"] and res["profile"]["sample_count"] == 3
    isolated_db.save_style_profile("global", {"preferences": []}, 0)
    assert svc.get_edit_tendencies(did)["profile"] is None


def test_tm_suggestions(isolated_db):
    sid = isolated_db.get_or_create_series("S")
    did = _drama(isolated_db, series_id=sid)
    lines = _lines(isolated_db, did, ens=("Old", "Same"))
    isolated_db.record_translation_memory(sid, "中0", "Remembered")
    isolated_db.record_translation_memory(sid, "中1", "Same")  # already used
    res = svc.list_tm_suggestions(did)
    assert len(res) == 1
    assert res[0]["line_id"] == lines[0].id and res[0]["suggestion"] == "Remembered"
    assert res[0]["exact"] is True
    assert svc.list_tm_suggestions(did, line_ids=[lines[1].id]) == []
    assert len(svc.list_tm_suggestions(did, line_ids=[lines[0].id])) == 1
    assert svc.list_tm_suggestions(_drama(isolated_db)) == []


def test_no_db_writes_and_no_path_leak(isolated_db, monkeypatch):
    sid = isolated_db.get_or_create_series("S")
    did = _drama(isolated_db, series_id=sid)
    lines = _lines(isolated_db, did)
    lines[0].dub_filename = "/secret/dir/a.wav"
    isolated_db.save_line_history_snapshot(did, lines, "h")
    hid = isolated_db.list_line_history(did)[0]["id"]
    v1 = isolated_db.save_translation_version(did, lines, "v1")
    v2 = isolated_db.save_translation_version(did, lines, "v2")
    isolated_db.record_translation_memory(sid, "中0", "R")

    def boom(*a, **k):
        raise AssertionError("db write attempted")
    for name in dir(isolated_db):
        if WRITE_RE.match(name) and callable(getattr(isolated_db, name)):
            monkeypatch.setattr(isolated_db, name, boom)

    out = [svc.list_line_history(did), svc.get_line_history_snapshot(did, hid),
           svc.list_translation_versions(did), svc.compare_versions(did, v1, v2),
           svc.list_translation_notes(did), svc.get_notes_markdown(did),
           svc.get_consistency_issues(did), svc.get_emotion_summary(did),
           svc.get_edit_tendencies(did), svc.list_tm_suggestions(did)]
    blob = json.dumps(out, default=str)
    assert "/secret" not in blob
    assert svc.get_line_history_snapshot(did, hid)["lines"][0]["dub_filename"] == "a.wav"
