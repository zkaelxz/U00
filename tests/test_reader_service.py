"""
Tests for services/reader_service.py -- Migration Slice 4's Reader page
HTML, shared by the FastAPI /api/reader route and (later) the Streamlit
Reader tab. No Streamlit or FastAPI needed: this is the UI-independent
layer.

The real scope decision this file guards: the service must NEVER make a
live/paid dictionary lookup call, and NEVER write to the database --
both confirmed-real side effects of the Streamlit tab's own page-load
path that an HTTP GET must not silently inherit.

`reader.build_reader_html`'s own rendering always calls
`segment.segment_and_annotate` for every non-empty line, which needs
jieba/sudachipy/kiwipiepy (optional, not installed in a core-only
env) -- so most tests here mock `reader.build_reader_html` itself (the
same pattern `tests/test_dark_mode_step68.py` already uses) to test
this service's own pagination/caching/error logic without needing any
of those installed. One test does a real end-to-end render and
`importorskip`s jieba, per this project's own optional-dependency rule.
"""

import pytest

from core import Line
from services import reader_service
from services.service_errors import InvalidInputError, NotFoundError


def _drama(db, **fields):
    fields.setdefault("source_language", "zh")
    return db.create_drama(**fields)


def _lines(n, zh="你好"):
    return [Line(idx=i, start=float(i), end=float(i + 1), zh=zh, en=f"Hello {i}") for i in range(n)]


def _stub_render(monkeypatch):
    """Replaces the real renderer with one that echoes back exactly what
    it was called with, so a test can assert on inputs without needing
    jieba/sudachipy/kiwipiepy installed."""
    import reader
    calls = []

    def fake(lines, source_language, definitions, **kw):
        calls.append({"lines": lines, "source_language": source_language,
                      "definitions": definitions, **kw})
        return f"<!DOCTYPE html><body>{len(lines)} lines, defs={sorted(definitions)}</body>"
    monkeypatch.setattr(reader, "build_reader_html", fake)
    return calls


class TestGetReaderPage:
    def test_renders_the_requested_page(self, isolated_db, monkeypatch):
        calls = _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(5))
        result = reader_service.get_reader_page(did, page=1, chapter_size=40)
        assert result["page"] == 1
        assert result["page_count"] == 1
        assert result["total_lines"] == 5
        assert "<!DOCTYPE html>" in result["html"]
        assert len(calls) == 1 and len(calls[0]["lines"]) == 5

    def test_pagination_slices_lines_correctly(self, isolated_db, monkeypatch):
        calls = _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(25))
        page1 = reader_service.get_reader_page(did, page=1, chapter_size=10)
        page2 = reader_service.get_reader_page(did, page=2, chapter_size=10)
        page3 = reader_service.get_reader_page(did, page=3, chapter_size=10)
        assert page1["page_count"] == 3
        assert [ln.idx for ln in calls[0]["lines"]] == list(range(0, 10))
        assert [ln.idx for ln in calls[1]["lines"]] == list(range(10, 20))
        assert [ln.idx for ln in calls[2]["lines"]] == list(range(20, 25))

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            reader_service.get_reader_page(999999, page=1)

    def test_drama_with_no_lines_raises_not_found(self, isolated_db):
        did = _drama(isolated_db, title_en="Empty")
        with pytest.raises(NotFoundError):
            reader_service.get_reader_page(did, page=1)

    def test_page_past_the_end_is_rejected(self, isolated_db, monkeypatch):
        _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(5))
        with pytest.raises(InvalidInputError) as exc:
            reader_service.get_reader_page(did, page=99, chapter_size=40)
        assert exc.value.details["page_count"] == 1

    def test_invalid_drama_id_is_rejected(self, isolated_db):
        with pytest.raises(InvalidInputError):
            reader_service.get_reader_page(0, page=1)
        with pytest.raises(InvalidInputError):
            reader_service.get_reader_page(-1, page=1)

    def test_invalid_chapter_size_is_rejected(self, isolated_db, monkeypatch):
        _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(5))
        with pytest.raises(InvalidInputError):
            reader_service.get_reader_page(did, chapter_size=1)
        with pytest.raises(InvalidInputError):
            reader_service.get_reader_page(did, chapter_size=99999)

    def test_never_makes_a_live_dictionary_lookup(self, isolated_db, monkeypatch):
        """The core scope guarantee: no matter what, this service must
        never call out to a translation/dictionary engine."""
        _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(3))

        def boom(*a, **k):
            raise AssertionError("get_reader_page made a live dictionary lookup call")
        import dictionary
        monkeypatch.setattr(dictionary, "build_word_definitions", boom)
        reader_service.get_reader_page(did, page=1)  # must not raise

    def test_never_writes_to_the_database(self, isolated_db, monkeypatch):
        _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(3))

        def boom(*a, **k):
            raise AssertionError("get_reader_page wrote to the database")
        monkeypatch.setattr(isolated_db, "save_vocab_lookup", boom)
        reader_service.get_reader_page(did, page=1)  # must not raise

    def test_uses_only_already_cached_definitions_matching_the_drama_language(
            self, isolated_db, monkeypatch):
        calls = _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D", source_language="ja")
        isolated_db.save_lines(did, _lines(2, zh="こんにちは"))
        isolated_db.save_vocab_lookup(did, "こんにちは", "konnichiwa", ["hello"], "ja")
        # A cached lookup from a different language must not leak in.
        isolated_db.save_vocab_lookup(did, "你好", "nihao", ["hello (zh)"], "zh")
        reader_service.get_reader_page(did, page=1)
        defs = calls[0]["definitions"]
        assert "こんにちは" in defs and defs["こんにちは"]["reading"] == "konnichiwa"
        assert "你好" not in defs

    def test_no_cached_definitions_passes_an_empty_dict(self, isolated_db, monkeypatch):
        calls = _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(3))
        reader_service.get_reader_page(did, page=1)
        assert calls[0]["definitions"] == {}

    def test_appearance_options_pass_through(self, isolated_db, monkeypatch):
        calls = _stub_render(monkeypatch)
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(2))
        reader_service.get_reader_page(did, page=1, theme="dark", font_size=30,
                                       line_height=3.0, max_width=900, font="serif")
        assert calls[0]["theme"] == "dark"
        assert calls[0]["font_size"] == 30
        assert calls[0]["line_height"] == 3.0
        assert calls[0]["max_width"] == 900
        assert calls[0]["font"] == "serif"
        assert calls[0]["audio_data_uri"] is None


class TestGetReaderPageRealRender:
    """One true end-to-end check against the real renderer -- skips
    cleanly if jieba isn't installed, per this project's own
    optional-dependency test rule."""

    def test_renders_real_html_with_translation_text(self, isolated_db):
        pytest.importorskip("jieba")
        did = _drama(isolated_db, title_en="D")
        isolated_db.save_lines(did, _lines(3))
        result = reader_service.get_reader_page(did, page=1)
        assert "<!DOCTYPE html>" in result["html"]
        assert "Hello 0" in result["html"]
