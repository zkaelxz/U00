"""
Tests for services/library_service.py -- the Library list/detail queries
shared by the Streamlit Library tab and the FastAPI `/api/library`
routes. No Streamlit or FastAPI needed: this is the UI-independent layer.
"""

import pytest

from services import library_service
from services.service_errors import InvalidInputError, NotFoundError, ServiceError


def _make(db, **fields):
    return db.create_drama(**fields)


class TestListLibraryDramas:
    def test_no_filters_returns_everything_newest_first(self, isolated_db):
        a = _make(isolated_db, title_en="A")
        b = _make(isolated_db, title_en="B")
        ids = [d["id"] for d in library_service.list_library_dramas()]
        assert set(ids) == {a, b}
        assert ids == [d["id"] for d in isolated_db.list_dramas()]

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
