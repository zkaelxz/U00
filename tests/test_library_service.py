"""
Tests for services/library_service.py -- the Library list/detail queries
shared by the Streamlit Library tab and the FastAPI `/api/library`
routes. No Streamlit or FastAPI needed: this is the UI-independent layer.
"""

import pytest

from services import library_service
from lib.errors import InvalidInputError, NotFoundError, ServiceError


def _make(db, **fields):
    return db.create_drama(**fields)


class TestListLibraryDramas:
    def test_no_filters_returns_everything_newest_first(self, isolated_db):
        a = _make(isolated_db, title_en="A")
        b = _make(isolated_db, title_en="B")
        ids = [d["id"] for d in library_service.list_library_dramas()]
        assert set(ids) == {a, b}
        assert ids == [d["id"] for d in isolated_db.list_dramas()]

    def test_each_row_carries_its_line_progress(self, isolated_db):
        from core import Line
        a = _make(isolated_db, title_en="A")
        b = _make(isolated_db, title_en="B")
        isolated_db.save_lines(a, [Line(idx=0, start=0, end=1, zh="一", en="one"),
                                   Line(idx=1, start=1, end=2, zh="二", en="")])
        rows = {d["id"]: d for d in library_service.list_library_dramas()}
        assert (rows[a]["line_count"], rows[a]["translated_count"]) == (2, 1)
        assert (rows[b]["line_count"], rows[b]["translated_count"]) == (0, 0)

    def test_rows_carry_undismissed_new_chapters_of_the_linked_series(self, isolated_db):
        from sources import store
        from sources.models import ChapterInfo
        a = _make(isolated_db, title_en="A")
        b = _make(isolated_db, title_en="B")
        store.track_series("s", "1", "Linked", drama_id=a)
        store.track_series("s", "2", "Unlinked")
        store.record_new_chapters("s", "1", [ChapterInfo("s", "1", "c1", "1"),
                                             ChapterInfo("s", "1", "c2", "2")])
        store.record_new_chapters("s", "2", [ChapterInfo("s", "2", "c9", "9")])
        store.dismiss_notification(store.list_notifications()[-1]["id"])
        rows = {d["id"]: d["new_chapters"] for d in library_service.list_library_dramas()}
        assert rows == {a: 1, b: 0}

    def test_name_only_listing_skips_the_line_scan(self, isolated_db, monkeypatch):
        from core import Line
        a = _make(isolated_db, title_en="A")
        isolated_db.save_lines(a, [Line(idx=0, start=0, end=1, zh="一", en="one")])

        def scanned():
            raise AssertionError("the whole-table line count ran")
        monkeypatch.setattr(isolated_db, "line_progress_by_drama", scanned)
        row = library_service.list_library_dramas(with_progress=False)[0]
        assert row["title_en"] == "A" and "line_count" not in row

    def test_blank_english_is_not_counted_as_translated(self, isolated_db):
        # Review lists a whitespace-only translation as untranslated; the card must agree.
        from core import Line
        a = _make(isolated_db, title_en="A")
        isolated_db.save_lines(a, [Line(idx=0, start=0, end=1, zh="一", en="  "),
                                   Line(idx=1, start=1, end=2, zh="二", en="two")])
        row = library_service.list_library_dramas()[0]
        assert (row["line_count"], row["translated_count"]) == (2, 1)

    def test_sql_filters_pass_through(self, isolated_db):
        _make(isolated_db, title_en="Moon", status="translated", source_language="ja")
        _make(isolated_db, title_en="Sun", status="aligned", source_language="zh")
        assert [d["title_en"] for d in
                library_service.list_library_dramas(status="translated")] == ["Moon"]
        assert [d["title_en"] for d in
                library_service.list_library_dramas(source_language="zh")] == ["Sun"]
        assert [d["title_en"] for d in
                library_service.list_library_dramas(search="oo")] == ["Moon"]

    def test_quick_filter_is_case_insensitive_like_the_tab(self, isolated_db):
        fav = _make(isolated_db, title_en="Fav", custom_tags="favorite")
        _make(isolated_db, title_en="Other", custom_tags="On Hold")
        ids = [d["id"] for d in library_service.list_library_dramas(quick_filter="Favorite")]
        assert ids == [fav]

    def test_unknown_quick_filter_is_rejected_not_silently_empty(self, isolated_db):
        with pytest.raises(InvalidInputError) as exc:
            library_service.list_library_dramas(quick_filter="Favourites")
        assert exc.value.details["allowed"] == list(isolated_db.ORGANIZATIONAL_TAGS)
        assert isinstance(exc.value, ServiceError)

    def test_custom_tags_require_every_tag_exact_match(self, isolated_db):
        both = _make(isolated_db, title_en="Both", custom_tags="bl, wuxia")
        _make(isolated_db, title_en="One", custom_tags="bl")
        _make(isolated_db, title_en="Case", custom_tags="BL, wuxia")
        ids = [d["id"] for d in
               library_service.list_library_dramas(custom_tags=["bl", "wuxia"])]
        assert ids == [both]

    def test_quick_filter_and_tags_combine(self, isolated_db):
        hit = _make(isolated_db, title_en="Hit", custom_tags="Favorite, bl")
        _make(isolated_db, title_en="Miss", custom_tags="bl")
        ids = [d["id"] for d in library_service.list_library_dramas(
            quick_filter="Favorite", custom_tags=["bl"])]
        assert ids == [hit]


class TestGetLibraryDrama:
    def test_returns_the_row(self, isolated_db):
        did = _make(isolated_db, title_en="X")
        assert library_service.get_library_drama(did)["title_en"] == "X"

    def test_missing_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            library_service.get_library_drama(12345)

    @pytest.mark.parametrize("bad", [0, -3, True, "1", None])
    def test_bad_id_raises_invalid_input(self, isolated_db, bad):
        with pytest.raises(InvalidInputError):
            library_service.get_library_drama(bad)


def test_split_custom_tags_trims_and_drops_empties():
    assert library_service.split_custom_tags({"custom_tags": " a, ,b ,"}) == ["a", "b"]
    assert library_service.split_custom_tags({"custom_tags": None}) == []
    assert library_service.split_custom_tags({}) == []


def test_service_layer_never_imports_a_ui_framework():
    """The whole point of services/: callable from Streamlit, FastAPI and
    cli.py alike. A streamlit or fastapi import here would couple it back
    to one of them."""
    import os
    import re
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services")
    offenders = []
    for fname in os.listdir(root):
        if fname.endswith(".py"):
            text = open(os.path.join(root, fname), encoding="utf-8").read()
            if re.search(r"^\s*(import|from)\s+(streamlit|fastapi|starlette|common)\b",
                         text, re.M):
                offenders.append(fname)
    assert offenders == []


class TestCacheHitShare:
    """Step 9: the Library dashboard shows what share of input tokens were
    prompt-cache reads, next to the cost."""

    def test_share_of_input_tokens(self):
        assert library_service.cache_hit_share({"input_tokens": 1000, "cache_read_tokens": 250}) == 0.25

    def test_no_usage_is_zero_not_a_division_error(self):
        assert library_service.cache_hit_share({"input_tokens": 0, "cache_read_tokens": 0}) == 0.0


class TestListSeriesWithDramas:
    def _build(self, db, n_series):
        for i in range(n_series):
            sid = db.create_series(f"S{i}")
            for j in range(2):
                db.create_drama(title_en=f"S{i}-{j}", series_id=sid, episode_number=2 - j)
            db.insert_series_character(sid, f"C{i}")
            db.upsert_glossary_term(sid, f"term{i}", "T")
            db.upsert_glossary_term(sid, f"other{i}", "T")

    def _count_queries(self, db, monkeypatch):
        seen = []
        real = db.get_conn

        def traced():
            conn = real()
            conn.set_trace_callback(seen.append)
            return conn
        monkeypatch.setattr(db, "get_conn", traced)
        library_service.list_series_with_dramas()
        return len([q for q in seen if q.lstrip().upper().startswith("SELECT")])

    def test_output_has_counts_and_episode_order(self, isolated_db):
        self._build(isolated_db, 2)
        out = library_service.list_series_with_dramas()
        assert [s["name"] for s in out] == [s["name"] for s in isolated_db.list_series()]
        for s in out:
            assert s["character_count"] == 1 and s["glossary_term_count"] == 2
            assert [d["title_en"] for d in s["dramas"]] == [
                f"{s['name']}-1", f"{s['name']}-0"]

    def test_query_count_does_not_grow_with_series(self, isolated_db, monkeypatch):
        self._build(isolated_db, 2)
        few = self._count_queries(isolated_db, monkeypatch)
        for i in range(2, 8):
            sid = isolated_db.create_series(f"S{i}")
            isolated_db.create_drama(title_en=f"S{i}-0", series_id=sid)
            isolated_db.create_drama(title_en=f"S{i}-1", series_id=sid)
        many = self._count_queries(isolated_db, monkeypatch)
        assert few > 0 and many == few
