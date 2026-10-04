"""
Tests for the Review read-only line-view endpoints (Migration Slice 47,
api/routers/review_lines_routes.py). TestClient against an `isolated_db`
library -- no server process, no network. Service logic itself is covered by
tests/test_review_lines_service.py; these check the HTTP contract.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])
    return body["error"]


def _seed_big():
    did = db.create_drama(title_en="D")
    lines = []
    for i in range(95):
        ln = Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f"中{i}", en=f"Hello {i}")
        if i % 10 == 0:
            ln.flag = "uncertain"
        if i % 19 == 0:
            ln.en = ""
        lines.append(ln)
    db.save_lines(did, lines)
    return did


def _line_ids(did):
    return [r["id"] for r in db.load_lines(did)]


BASE = "/api/review/dramas"


class TestLines:
    def test_pagination_and_totals(self, client):
        did = _seed_big()
        body = client.get(f"{BASE}/{did}/lines?page=1&page_size=40").json()
        assert (len(body["lines"]), body["total"], body["page"], body["page_size"]) == (40, 95, 1, 40)
        assert body["flagged_count"] == 10  # idx 0,10,...,90
        assert body["untranslated_count"] == 5  # idx 0,19,38,57,76,95->no: 0..76 = 5
        assert len(client.get(f"{BASE}/{did}/lines?page=3&page_size=40").json()["lines"]) == 15
        assert client.get(f"{BASE}/{did}/lines?page=4&page_size=40").json()["lines"] == []
        assert set(body["lines"][0]) == {"id", "idx", "start", "end", "zh", "en", "speaker",
                                         "speaker_manual", "sfx", "flag", "flag_note",
                                         "dub_filename", "lang"}

    def test_only_filters(self, client):
        did = _seed_big()
        r = client.get(f"{BASE}/{did}/lines?only=flagged").json()
        assert r["total"] == 10 and all(l["flag"] for l in r["lines"])
        r = client.get(f"{BASE}/{did}/lines?only=untranslated").json()
        assert r["total"] == 5 and all(l["en"] == "" for l in r["lines"])

    def test_bad_only_is_422(self, client):
        did = _seed_big()
        r = client.get(f"{BASE}/{did}/lines?only=bogus")
        assert r.status_code == 422
        assert _error(r)["code"] == "validation_error"

    def test_page_size_and_page_bounds_422(self, client):
        did = _seed_big()
        assert client.get(f"{BASE}/{did}/lines?page_size=201").status_code == 422
        assert client.get(f"{BASE}/{did}/lines?page_size=0").status_code == 422
        r = client.get(f"{BASE}/{did}/lines?page=0")
        assert r.status_code == 422
        _error(r)

    def test_unknown_drama_404(self, client):
        r = client.get(f"{BASE}/9999/lines")
        assert r.status_code == 404
        assert _error(r)["code"] == "not_found"
        assert client.get(f"{BASE}/0/lines").status_code == 422


class TestSearch:
    def test_hit_and_limit(self, client):
        did = _seed_big()
        hits = client.get(f"{BASE}/{did}/search", params={"term": "hello 5"}).json()
        assert {h["idx"] for h in hits} == {5, 50, 51, 52, 53, 54, 55, 56, 58, 59}  # 57 is untranslated
        assert len(client.get(f"{BASE}/{did}/search", params={"term": "hello", "limit": 3}).json()) == 3
        assert client.get(f"{BASE}/{did}/search", params={"term": "nomatch"}).json() == []

    def test_bounds_422(self, client):
        did = _seed_big()
        assert client.get(f"{BASE}/{did}/search", params={"term": ""}).status_code == 422
        assert client.get(f"{BASE}/{did}/search").status_code == 422
        assert client.get(f"{BASE}/{did}/search", params={"term": "x" * 501}).status_code == 422
        assert client.get(f"{BASE}/{did}/search", params={"term": "a", "limit": 201}).status_code == 422

    def test_unknown_drama_404(self, client):
        assert client.get(f"{BASE}/9999/search", params={"term": "a"}).status_code == 404


class TestFindReplacePreview:
    def _post(self, client, did, **body):
        return client.post(f"{BASE}/{did}/find-replace/preview", json=body)

    def test_matches_keyed_by_id(self, client):
        did = _seed_big()
        ids = _line_ids(did)
        r = self._post(client, did, find="Hello 7", replace="Hi 7")
        assert r.status_code == 200
        matches = r.json()
        assert {x["idx"] for x in matches} == {7, 70, 71, 72, 73, 74, 75, 77, 78, 79}  # 76 untranslated
        assert all(x["id"] == ids[x["idx"]] for x in matches)
        m = next(x for x in matches if x["idx"] == 7)
        assert m["idx"] == 7 and m["id"] == ids[7]
        assert (m["old_text"], m["new_text"]) == ("Hello 7", "Hi 7")

    def test_regex_and_case(self, client):
        did = _seed_big()
        r = self._post(client, did, find=r"hello (\d)$", replace=r"X\1", use_regex=True)
        got = r.json()
        assert r.status_code == 200 and len(got) == 9  # single-digit idx 1-9 minus none untranslated
        assert all(m["new_text"] == "X" + m["old_text"][-1] for m in got)
        assert self._post(client, did, find="hello 7", case_sensitive=True).json() == []

    def test_empty_find_422(self, client):
        did = _seed_big()
        r = self._post(client, did, find="", replace="x")
        assert r.status_code == 422
        _error(r)

    def test_invalid_regex_422(self, client):
        did = _seed_big()
        r = self._post(client, did, find="(", replace="x", use_regex=True)
        assert r.status_code == 422
        assert _error(r)["code"] == "validation_error"

    def test_bad_group_reference_is_422_not_500(self, client):
        did = _seed_big()
        r = self._post(client, did, find="Hello", replace=r"\9", use_regex=True)
        assert r.status_code == 422
        _error(r)

    def test_length_caps_and_extra_forbidden(self, client):
        did = _seed_big()
        assert self._post(client, did, find="x" * 501).status_code == 422
        assert self._post(client, did, find="a", replace="y" * 501).status_code == 422
        assert self._post(client, did, find="a", bogus=1).status_code == 422

    def test_unknown_drama_404(self, client):
        assert self._post(client, 9999, find="a").status_code == 404


class TestCoverageAndPacing:
    def test_coverage_keys(self, client):
        did = _seed_big()
        r = client.get(f"{BASE}/{did}/coverage")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"long_lines", "large_gaps", "blank_zh", "blank_en"}
        assert len(body["blank_en"]) == 5
        assert all(e["id"] is not None for e in body["blank_en"])

    def test_coverage_gap_ids(self, client):
        did = db.create_drama(title_en="G")
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="a"),
                            Line(idx=1, start=100, end=101, zh="b", en="b")])
        gaps = client.get(f"{BASE}/{did}/coverage").json()["large_gaps"]
        assert len(gaps) == 1
        ids = _line_ids(did)
        assert (gaps[0]["after_id"], gaps[0]["before_id"]) == (ids[0], ids[1])

    def test_pacing_flags_keys(self, client):
        did = db.create_drama(title_en="P")
        db.save_lines(did, [Line(idx=0, start=0, end=0.3, zh="a",
                                 en="a very very very long translation indeed for a tiny slot")])
        r = client.get(f"{BASE}/{did}/pacing-flags")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"flags", "count"}
        assert body["count"] == len(body["flags"]) >= 1
        assert {"id", "idx", "issue", "detail"} <= set(body["flags"][0])
        assert body["flags"][0]["id"] == _line_ids(did)[0]

    def test_unknown_drama_404(self, client):
        assert client.get(f"{BASE}/9999/coverage").status_code == 404
        assert client.get(f"{BASE}/9999/pacing-flags").status_code == 404


class TestPerLine:
    def test_provenance(self, client):
        did = _seed_big()
        lid = _line_ids(did)[3]
        r = client.get(f"{BASE}/{did}/lines/{lid}/provenance")
        assert r.status_code == 200
        body = r.json()
        assert body["line_id"] == lid and body["line_idx"] == 3
        assert body["zh"] == "中3" and body["en"] == "Hello 3"
        assert "engine" in body and "translation_notes" in body

    def test_original_text(self, client):
        did = _seed_big()
        lid = _line_ids(did)[3]
        r = client.get(f"{BASE}/{did}/lines/{lid}/original-text")
        assert r.status_code == 200
        body = r.json()
        assert body["line_id"] == lid and body["idx"] == 3 and body["current_zh"] == "中3"
        assert body["has_raw_transcript"] is False
        assert body["original_text"] is None and body["differs"] is False

    def test_unknown_and_foreign_line_404(self, client):
        did = _seed_big()
        other = db.create_drama(title_en="Other")
        db.save_lines(other, [Line(idx=0, start=0, end=1, zh="x", en="y")])
        foreign = _line_ids(other)[0]
        for path in ("provenance", "original-text"):
            for lid in (987654, foreign):
                r = client.get(f"{BASE}/{did}/lines/{lid}/{path}")
                assert r.status_code == 404, (path, lid)
                assert _error(r)["code"] == "not_found"
            assert client.get(f"{BASE}/9999/lines/1/{path}").status_code == 404
            assert client.get(f"{BASE}/{did}/lines/0/{path}").status_code == 422


class TestReadOnlyAndHygiene:
    def test_no_endpoint_writes(self, client, monkeypatch):
        did = _seed_big()
        lid = _line_ids(did)[0]

        def boom(*a, **k):
            raise AssertionError("db.save_lines called by a read-only endpoint")

        monkeypatch.setattr(db, "save_lines", boom)
        gets = [f"{BASE}/{did}/lines", f"{BASE}/{did}/search?term=hello",
                f"{BASE}/{did}/coverage", f"{BASE}/{did}/pacing-flags",
                f"{BASE}/{did}/lines/{lid}/provenance",
                f"{BASE}/{did}/lines/{lid}/original-text"]
        for url in gets:
            assert client.get(url).status_code == 200, url
        r = client.post(f"{BASE}/{did}/find-replace/preview", json={"find": "Hello", "replace": "Hi"})
        assert r.status_code == 200
        # And nothing changed in storage.
        assert client.get(f"{BASE}/{did}/lines/{lid}/original-text").json()["current_zh"] == "中0"
        assert db.load_lines(did)[0]["en"] == ""

    def test_no_filesystem_path_in_responses(self, client, isolated_db):
        did = _seed_big()
        lid = _line_ids(did)[0]
        lib = str(db.LIBRARY_DIR)
        for url in (f"{BASE}/{did}/lines", f"{BASE}/{did}/coverage",
                    f"{BASE}/{did}/pacing-flags", f"{BASE}/{did}/lines/{lid}/provenance",
                    f"{BASE}/{did}/lines/{lid}/original-text", f"{BASE}/9999/lines"):
            text = client.get(url).text
            assert lib not in text, url
            assert "Traceback" not in text
