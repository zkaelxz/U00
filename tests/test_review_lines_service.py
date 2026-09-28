"""Tests for services/review_lines_service.py (Review stage read-only views)."""
import json
import os

import pytest

import core
import translate_engines
from core import Line
from services import review_lines_service as svc
from services.service_errors import InvalidInputError, NotFoundError


def _seed(db, lines):
    did = db.create_drama(title_en="D")
    db.save_lines(did, lines)
    return did


def _big(db):
    lines = []
    for i in range(95):
        ln = Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f"中{i}", en=f"Hello {i}")
        if i % 10 == 0:
            ln.flag = "uncertain"
        if i % 19 == 0:
            ln.en = ""
        lines.append(ln)
    return _seed(db, lines), lines


class TestNotFound:
    def test_unknown_drama(self, isolated_db):
        for call in (lambda: svc.list_review_lines(999), lambda: svc.search_lines(999, "a"),
                     lambda: svc.preview_find_replace(999, "a", "b"),
                     lambda: svc.get_coverage_report(999), lambda: svc.get_pacing_flags(999),
                     lambda: svc.get_line_provenance(999, 1),
                     lambda: svc.get_original_text(999, 1)):
            with pytest.raises(NotFoundError):
                call()

    def test_unknown_line(self, isolated_db):
        did = _seed(isolated_db, [Line(idx=0, start=0, end=1, zh="a")])
        with pytest.raises(NotFoundError):
            svc.get_line_provenance(did, 987654)
        with pytest.raises(NotFoundError):
            svc.get_original_text(did, 987654)


class TestList:
    def test_pagination(self, isolated_db):
        did, _ = _big(isolated_db)
        p1 = svc.list_review_lines(did, 1, 40)
        assert (len(p1["lines"]), p1["total"], p1["page"], p1["page_size"]) == (40, 95, 1, 40)
        assert all(l["id"] is not None for l in p1["lines"])
        assert len(svc.list_review_lines(did, 3, 40)["lines"]) == 15
        assert svc.list_review_lines(did, 4, 40)["lines"] == []
        ids = [l["id"] for p in (1, 2, 3) for l in svc.list_review_lines(did, p, 40)["lines"]]
        assert len(set(ids)) == 95

    def test_filters_and_counts(self, isolated_db):
        did, lines = _big(isolated_db)
        n_flag = sum(1 for l in lines if l.flag)
        n_untr = sum(1 for l in lines if l.zh.strip() and not l.en.strip())
        r = svc.list_review_lines(did, only="flagged")
        assert r["total"] == n_flag and all(l["flag"] for l in r["lines"])
        assert r["flagged_count"] == n_flag and r["untranslated_count"] == n_untr
        r = svc.list_review_lines(did, only="untranslated")
        assert r["total"] == n_untr and all(not l["en"] for l in r["lines"])

    @pytest.mark.parametrize("kw", [{"only": "x"}, {"page": 0}, {"page_size": 0},
                                    {"page_size": 201}, {"page": "1"}])
    def test_bad_args(self, isolated_db, kw):
        did, _ = _big(isolated_db)
        with pytest.raises(InvalidInputError):
            svc.list_review_lines(did, **kw)


class TestSearch:
    def test_hits_and_limit(self, isolated_db):
        did = _seed(isolated_db, [
            Line(idx=0, start=0, end=1, zh="你好", en="Hello World"),
            Line(idx=1, start=1, end=2, zh="再见", en="bye"),
            Line(idx=2, start=2, end=3, zh="世界", en="hello again")])
        assert [h["idx"] for h in svc.search_lines(did, "HELLO")] == [0, 2]
        assert [h["idx"] for h in svc.search_lines(did, "再见")] == [1]
        assert len(svc.search_lines(did, "hello", limit=1)) == 1
        assert svc.search_lines(did, "  ") == []

    def test_bad(self, isolated_db):
        did = _seed(isolated_db, [Line(idx=0, start=0, end=1, zh="a")])
        with pytest.raises(InvalidInputError):
            svc.search_lines(did, "x" * 501)
        with pytest.raises(InvalidInputError):
            svc.search_lines(did, "x", limit=0)


class TestPreview:
    def _did(self, db):
        return _seed(db, [Line(idx=0, start=0, end=1, zh="a", en="Cat and cat"),
                          Line(idx=1, start=1, end=2, zh="b", en="dog"),
                          Line(idx=2, start=2, end=3, zh="c", en="c4t")])

    def test_plain_and_case(self, isolated_db):
        did = self._did(isolated_db)
        r = svc.preview_find_replace(did, "cat", "fox")
        assert len(r) == 1 and r[0]["new_text"] == "fox and fox" and r[0]["old_text"] == "Cat and cat"
        assert r[0]["id"] is not None and set(r[0]) == {"id", "idx", "old_text", "new_text"}
        r = svc.preview_find_replace(did, "cat", "fox", case_sensitive=True)
        assert r[0]["new_text"] == "Cat and fox"

    def test_regex_toggle(self, isolated_db):
        did = self._did(isolated_db)
        assert [m["idx"] for m in svc.preview_find_replace(did, "c.t", "X", use_regex=True)] == [0, 2]
        assert [m["idx"] for m in svc.preview_find_replace(did, "c.t", "X")] == []

    def test_errors(self, isolated_db):
        did = self._did(isolated_db)
        with pytest.raises(InvalidInputError):
            svc.preview_find_replace(did, "", "x")
        with pytest.raises(InvalidInputError):
            svc.preview_find_replace(did, "(", "x", use_regex=True)
        with pytest.raises(InvalidInputError):
            svc.preview_find_replace(did, "(cat)", r"\2", use_regex=True)
        with pytest.raises(InvalidInputError):
            svc.preview_find_replace(did, "a" * 501, "x")
        with pytest.raises(InvalidInputError):
            svc.preview_find_replace(did, "a", "x" * 501)


class TestChecks:
    def _lines(self):
        return [Line(idx=0, start=0, end=20, zh="短", en="ok"),
                Line(idx=1, start=30, end=31, zh="你好", en=""),
                Line(idx=2, start=31, end=32, zh="", en="x"),
                Line(idx=3, start=32, end=33, zh="再见", en=" ".join(["w"] * 20))]

    def test_coverage_matches_underlying(self, isolated_db):
        did = _seed(isolated_db, self._lines())
        loaded = core.lines_from_rows(isolated_db.load_lines(did))
        expected = core.diagnose_line_coverage(loaded)
        got = svc.get_coverage_report(did)
        assert set(got) == set(expected)
        for key in expected:
            assert len(got[key]) == len(expected[key])
            for g, e in zip(got[key], expected[key]):
                assert {k: g[k] for k in e} == e
        assert got["long_lines"] and got["large_gaps"] and got["blank_zh"] and got["blank_en"]
        assert got["long_lines"][0]["id"] == loaded[0].id
        assert got["large_gaps"][0]["after_id"] == loaded[0].id
        json.dumps(got)

    def test_pacing_matches_underlying(self, isolated_db):
        did = _seed(isolated_db, self._lines())
        loaded = core.lines_from_rows(isolated_db.load_lines(did))
        expected = translate_engines.smart_segment_lines(loaded)
        got = svc.get_pacing_flags(did)
        assert got["count"] == len(expected) and expected
        assert [{k: f[k] for k in ("idx", "issue", "detail")} for f in got["flags"]] == expected
        assert all(f["id"] is not None for f in got["flags"])


def _write_raw(db, did, lines):
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    payload = {"created_at": "2026-01-01T00:00:00", "backend": "t", "model": "", "language": "",
               "mode": "", "text": "", "segments": [], "lines": lines}
    with open(os.path.join(ddir, "raw_transcript.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


class TestOriginalAndProvenance:
    def test_original_text(self, isolated_db):
        did = _seed(isolated_db, [Line(idx=0, start=0, end=2, zh="改过", en="x")])
        lid = isolated_db.load_lines(did)[0]["id"]
        assert svc.get_original_text(did, lid)["has_raw_transcript"] is False
        assert svc.get_original_text(did, lid)["original_text"] is None
        _write_raw(isolated_db, did, [{"id": lid, "idx": 0, "start": 0, "end": 2, "text": "原文"}])
        r = svc.get_original_text(did, lid)
        assert r["original_text"] == "原文" and r["differs"] is True and r["line_id"] == lid
        assert isolated_db.LIBRARY_DIR not in json.dumps(r, ensure_ascii=False)

    def test_provenance(self, isolated_db):
        did = _seed(isolated_db, [Line(idx=0, start=0, end=2, zh="你好", en="Hi"),
                                  Line(idx=1, start=2, end=3, zh="再见", en="Bye")])
        lid = isolated_db.load_lines(did)[0]["id"]
        r = svc.get_line_provenance(did, lid)
        assert r["line_id"] == lid and r["zh"] == "你好"
        assert r["current_neighbors_after"][0][0] == 1
        assert isolated_db.LIBRARY_DIR not in json.dumps(r, ensure_ascii=False, default=str)


def test_zero_writes_and_no_path_leak(isolated_db, monkeypatch):
    did, _ = _big(isolated_db)
    lid = isolated_db.load_lines(did)[0]["id"]
    _write_raw(isolated_db, did, [{"id": lid, "idx": 0, "start": 0, "end": 1, "text": "o"}])

    def boom(*a, **k):
        raise AssertionError("db write attempted")
    for name in dir(isolated_db):
        if name.startswith(("save_", "update_", "delete_", "add_", "create_", "set_", "replace_")):
            monkeypatch.setattr(isolated_db, name, boom)

    results = [svc.list_review_lines(did), svc.search_lines(did, "hello"),
               svc.preview_find_replace(did, "hello", "bye"), svc.get_coverage_report(did),
               svc.get_pacing_flags(did), svc.get_line_provenance(did, lid),
               svc.get_original_text(did, lid)]
    blob = json.dumps(results, ensure_ascii=False, default=str)
    assert isolated_db.LIBRARY_DIR not in blob
