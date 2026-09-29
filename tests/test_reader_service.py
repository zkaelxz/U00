"""
Tests for services/reader_service.py -- Migration Slice 4's Reader page
HTML, shared by the FastAPI /api/reader route and (later) the Streamlit
Reader tab. No Streamlit or FastAPI needed: this is the UI-independent
layer.

The real scope decision this file guards: the service must NEVER make a
live/paid dictionary lookup call, and NEVER write to the database --
both confirmed-real side effects of the Streamlit tab's own page-load
path that an HTTP GET must not silently inherit.

`reader.build_reader_html` splits each line into words with
`segment.segment_and_annotate`, which needs jieba/sudachipy/kiwipiepy
(optional, not installed in a core-only env); without them it shows each
line unsplit with a note. Most tests here mock `reader.build_reader_html`
itself (the same pattern `tests/test_dark_mode_step68.py` already uses)
to test this service's own pagination/caching/error logic. One test does
a real end-to-end render and `importorskip`s jieba, per this project's
own optional-dependency rule; another renders with the splitter missing.
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

    def test_renders_without_the_word_splitter(self, isolated_db, monkeypatch):
        # CI installs requirements-core only (no jieba/pypinyin): the page
        # must still show every line, unsplit, with a note saying why.
        import segment

        def missing(*a, **k):
            raise ImportError("No module named 'jieba'", name="jieba")
        monkeypatch.setattr(segment, "segment_and_annotate", missing)
        did = _drama(isolated_db, title_en="D")
        lines = _lines(3)
        lines[0].zh = "<b>x</b>"
        isolated_db.save_lines(did, lines)
        html_str = reader_service.get_reader_page(did, page=1)["html"]
        assert html_str.count('class="line-row"') == 3
        assert "Hello 0" in html_str
        assert "&lt;b&gt;x&lt;/b&gt;" in html_str and "<b>x</b>" not in html_str
        assert 'class="segmenter-note"' in html_str and "jieba" in html_str
        assert 'class="word"' not in html_str


# ---------------------------------------------------------------------------
# M4: the rest of the Reader tab's logic, moved here
# ---------------------------------------------------------------------------

import os  # noqa: E402

from services.service_errors import (DependencyUnavailableError,  # noqa: E402
                                      ServiceError)


class TestCaptionTracks:
    """Moved from tests/test_reader_tab.py with the function (assertions
    unchanged): CC tracks only for languages that actually have text."""

    def test_both_sides_filled_gives_three_tracks(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")]
        tracks = reader_service.caption_tracks(lines)
        assert list(tracks) == ["Source", "English", "Bilingual"]
        assert "你好" in tracks["Source"] and "Hello" not in tracks["Source"]
        assert "Hello" in tracks["English"]
        assert "Hello\n你好" in tracks["Bilingual"]

    def test_untranslated_drama_gets_source_only(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en=""),
                 Line(idx=1, start=1.0, end=2.0, zh="再见", en="   ")]
        assert list(reader_service.caption_tracks(lines)) == ["Source"]

    def test_no_text_at_all_gives_no_tracks(self):
        assert reader_service.caption_tracks([Line(idx=0, start=0.0, end=1.0, zh=" ", en="")]) == {}

    def test_tab_uses_the_service_function(self):
        import tabs.reader_tab as rt
        assert rt.caption_tracks is reader_service.caption_tracks

    def test_get_caption_tracks_by_drama(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
        assert list(reader_service.get_caption_tracks(did)["tracks"]) == ["Source"]

    def test_caption_readout_audio_fallback(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=65.0, end=66.0, zh="第一行", en="Line one"),
                                     Line(idx=1, start=70.0, end=71.0, zh="", en="")])
        src = reader_service.get_caption_readout(did, "Source")["lines"]
        assert [r["text"] for r in src] == ["第一行"]
        assert src[0]["timestamp"] == "01:05"
        assert src[0]["line_id"] is not None
        bi = reader_service.get_caption_readout(did, "Bilingual")["lines"]
        assert bi[0]["text"] == "Line one  \n第一行"
        with pytest.raises(InvalidInputError):
            reader_service.get_caption_readout(did, "French")


class TestOwnership:
    """Every public function 404s on an unknown drama."""

    @pytest.mark.parametrize("call", [
        lambda: reader_service.get_caption_tracks(999),
        lambda: reader_service.get_caption_readout(999),
        lambda: reader_service.get_media_availability(999),
        lambda: reader_service.media_file_path(999, "original"),
        lambda: reader_service.get_series_glossary(999),
        lambda: reader_service.get_reading_overview(999),
        lambda: reader_service.save_reading_position(999, 1),
        lambda: reader_service.get_notes(999),
        lambda: reader_service.save_notes(999, "x"),
        lambda: reader_service.lookup_page_definitions(999, 1),
        lambda: reader_service.list_vocab(999),
        lambda: reader_service.set_rich_export(999, ["x"]),
        lambda: reader_service.export_vocab_csv(999),
        lambda: reader_service.export_vocab_apkg(999),
        lambda: reader_service.who_is_character(999, "A"),
        lambda: reader_service.explain_reference(999, "A"),
        lambda: reader_service.recap(999, 1),
        lambda: reader_service.relationship_map(999),
        lambda: reader_service.list_wiki(999),
        lambda: reader_service.update_wiki(999),
        lambda: reader_service.clear_wiki(999),
        lambda: reader_service.export_wiki_markdown(999),
        lambda: reader_service.ask_about_drama(999, "Q?"),
    ])
    def test_unknown_drama_is_not_found(self, isolated_db, call):
        with pytest.raises(NotFoundError):
            call()

    def test_bad_id_is_invalid(self, isolated_db):
        with pytest.raises(InvalidInputError):
            reader_service.get_notes(0)

    def test_cannot_queue_another_dramas_word(self, isolated_db):
        a, b = _drama(isolated_db), _drama(isolated_db)
        isolated_db.save_vocab_lookup(b, "猫", "māo", ["cat"], "zh", 0)
        with pytest.raises(NotFoundError):
            reader_service.set_rich_export(a, ["猫"])
        assert isolated_db.list_vocab_lookups(b, rich_only=True) == []

    def test_vocab_and_wiki_are_per_drama(self, isolated_db):
        a, b = _drama(isolated_db), _drama(isolated_db)
        isolated_db.save_vocab_lookup(b, "猫", "māo", ["cat"], "zh", 0)
        isolated_db.upsert_wiki_entry(b, "character", "B-only")
        assert reader_service.list_vocab(a)["words"] == []
        assert reader_service.list_wiki(a)["entries"] == []
        reader_service.clear_wiki(a, confirm=True)
        assert len(isolated_db.list_wiki_entries(b)) == 1


class TestMedia:
    def _media(self, db, did, name, data=b"\x00" * 16):
        d = db.drama_dir(did)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)

    def test_video_preferred_and_no_paths_returned(self, isolated_db):
        did = _drama(isolated_db, source_video_filename="v.mp4", audio_filename="a.wav")
        isolated_db.save_lines(did, _lines(1))
        self._media(isolated_db, did, "v.mp4")
        self._media(isolated_db, did, "a.wav")
        self._media(isolated_db, did, "dub_track.wav")
        info = reader_service.get_media_availability(did)
        assert info["original"] == "video" and info["dub"] is True and info["narration"] is False
        assert info["captions_overlay"] is True
        assert os.sep not in repr(info)
        kind, path = reader_service.media_file_path(did, "original")
        assert kind == "video" and path.endswith("v.mp4")

    def test_audio_only_has_no_overlay_and_missing_file_is_404(self, isolated_db):
        did = _drama(isolated_db, audio_filename="a.wav", source_video_filename="gone.mp4")
        isolated_db.save_lines(did, _lines(1))
        self._media(isolated_db, did, "a.wav")
        info = reader_service.get_media_availability(did)
        assert info["original"] == "audio" and info["captions_overlay"] is False
        with pytest.raises(NotFoundError):
            reader_service.media_file_path(did, "narration")
        with pytest.raises(InvalidInputError):
            reader_service.media_file_path(did, "../etc")


class TestGlossaryProgressNotes:
    def test_series_glossary(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        did = _drama(isolated_db, series_id=sid)
        isolated_db.upsert_glossary_term(sid, "师尊", "Master")
        g = reader_service.get_series_glossary(did)
        assert g["series_id"] == sid
        assert g["terms"][0]["term_original"] == "师尊"
        assert g["terms"][0]["term_translation"] == "Master"

    def test_no_series_gives_empty_glossary(self, isolated_db):
        did = _drama(isolated_db)
        assert reader_service.get_series_glossary(did) == {"drama_id": did, "series_id": None,
                                                           "terms": []}

    def test_progress_round_trip(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, _lines(50))
        saved = reader_service.save_reading_position(did, page=2, chapter_size=20)
        assert saved["last_line_idx"] == 39 and saved["percent_complete"] == 80.0
        ov = reader_service.get_reading_overview(did)
        assert ov["last_page"] == 2 and ov["percent_complete"] == 80.0 and ov["line_count"] == 50
        with pytest.raises(InvalidInputError):
            reader_service.save_reading_position(did, page=4, chapter_size=20)

    def test_notes_round_trip_and_validation(self, isolated_db):
        did = _drama(isolated_db)
        assert reader_service.get_notes(did)["notes"] == ""
        reader_service.save_notes(did, "remember X")
        assert reader_service.get_notes(did)["notes"] == "remember X"
        with pytest.raises(InvalidInputError):
            reader_service.save_notes(did, "x" * (reader_service.MAX_NOTES_CHARS + 1))


class _FakeEngine:
    supports_reference = True
    model = "fake"


def _fake_engine(monkeypatch):
    from services import translate_service
    import translate_engines
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: _FakeEngine())


_EVIL = '</script><img src=x onerror=alert(1)>"'


def _evil_page(monkeypatch):
    import reader
    import segment
    monkeypatch.setattr(segment, "segment_and_annotate",
                        lambda text, lang, *a, **k: [(_EVIL, _EVIL)])
    ln = Line(idx=0, start=0.0, end=1.0, zh=_EVIL, en=_EVIL)
    defs = {_EVIL: {"reading": _EVIL, "definitions": [_EVIL, "a b c  &"]}}
    return reader.build_reader_html([ln], "zh", defs)


class TestReaderHtmlEscaping:
    """B-30: LLM-sourced words/readings/definitions must not inject HTML."""

    def test_no_raw_script_close_or_tag(self, monkeypatch):
        page = _evil_page(monkeypatch)
        assert page.count("</script>") == 1  # only the page's own closing tag
        assert "<img" not in page
        assert " " not in page and " " not in page

    def test_defs_json_round_trips(self, monkeypatch):
        import json
        import re
        page = _evil_page(monkeypatch)
        m = re.search(r"const DEFS = (.*?);\n", page)
        raw = m.group(1)
        assert "&" not in raw and "\\u0026" in raw and "\\u2028" in raw
        entry = json.loads(raw)[_EVIL]
        assert entry["reading"] == _EVIL
        assert entry["definitions"][1] == "a b c  &"

    def test_data_word_attribute_quoted(self, monkeypatch):
        import html
        page = _evil_page(monkeypatch)
        assert f'data-word="{html.escape(_EVIL, quote=True)}"' in page

    def test_popup_uses_text_nodes_and_csp(self, monkeypatch):
        page = _evil_page(monkeypatch)
        assert "innerHTML" not in page
        assert 'http-equiv="Content-Security-Policy"' in page

    def test_browser_shows_literal_text(self, monkeypatch, tmp_path):
        sync_api = pytest.importorskip("playwright.sync_api")
        f = tmp_path / "r.html"
        f.write_text(_evil_page(monkeypatch), encoding="utf-8")
        try:
            with sync_api.sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    pg = browser.new_page()
                    pg.goto(f.as_uri())
                    pg.click("ruby.word")
                    imgs = pg.locator("img").count()
                    text = pg.inner_text("#popup")
                finally:
                    browser.close()
        except Exception as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium not installed")
            raise
        assert imgs == 0
        assert _EVIL in text


class TestLookupDefinitions:
    def _setup(self, isolated_db, monkeypatch, lang="zh"):
        import dictionary
        import segment
        monkeypatch.setattr(segment, "segment_and_annotate",
                            lambda text, language, chinese_script="simplified":
                            [(w, "") for w in text.split(" ")])
        monkeypatch.setattr(dictionary, "lookup_cedict",
                            lambda w: {"word": w, "pinyin": "māo", "definitions": ["cat"]}
                            if w == "猫" else None)
        did = _drama(isolated_db, source_language=lang)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="猫 狗 鸟", en="")])
        return did

    def test_local_only_by_default_and_saves_vocab(self, isolated_db, monkeypatch):
        did = self._setup(isolated_db, monkeypatch)
        import translate_engines
        monkeypatch.setattr(translate_engines, "call_llm_json",
                            lambda *a, **k: pytest.fail("no paid call without use_llm"))
        out = reader_service.lookup_page_definitions(did, 1)
        assert out["definitions"] == {"猫": {"reading": "māo", "definitions": ["cat"]}}
        assert [r["word"] for r in isolated_db.list_vocab_lookups(did)] == ["猫"]

    def test_llm_fallback_matches_by_id_not_position(self, isolated_db, monkeypatch):
        did = self._setup(isolated_db, monkeypatch)
        _fake_engine(monkeypatch)
        import translate_engines
        # Reordered, and word 1 missing: 鸟 (id 2) must get "bird", 狗 nothing.
        monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **k:
                            '{"2": {"reading": "niǎo", "definitions": ["bird"]}}')
        out = reader_service.lookup_page_definitions(did, 1, use_llm=True)
        assert out["definitions"]["鸟"] == {"reading": "niǎo", "definitions": ["bird"]}
        assert "狗" not in out["definitions"]

    def test_llm_requested_without_key_is_dependency_error(self, isolated_db, monkeypatch):
        did = self._setup(isolated_db, monkeypatch)
        from services import translate_service
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
        with pytest.raises(DependencyUnavailableError):
            reader_service.lookup_page_definitions(did, 1, use_llm=True)

    def test_engine_error_is_redacted(self, isolated_db, monkeypatch):
        did = self._setup(isolated_db, monkeypatch)
        _fake_engine(monkeypatch)
        import translate_engines
        secret = "sk-ant-api03-" + "A" * 40

        def boom(*a, **k):
            raise RuntimeError(f"bad key {secret}")
        monkeypatch.setattr(translate_engines, "call_llm_json", boom)
        with pytest.raises(ServiceError) as ei:
            reader_service.lookup_page_definitions(did, 1, use_llm=True)
        assert secret not in ei.value.message


class TestVocabExport:
    def _vocab(self, db):
        did = _drama(db, title_en="My/Drama")
        db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="我有一只猫", en="I have a cat")])
        db.save_vocab_lookup(did, "猫", "māo", ["cat", "feline"], "zh", 0)
        db.save_vocab_lookup(did, "有", "yǒu", ["to have"], "zh", 0)
        return did

    def test_csv(self, isolated_db):
        did = self._vocab(isolated_db)
        out = reader_service.export_vocab_csv(did)
        assert out["filename"] == "My_Drama.csv"
        assert "猫 (māo),cat; feline" in out["content"]

    def test_empty_vocab_is_not_found(self, isolated_db):
        did = _drama(isolated_db)
        with pytest.raises(NotFoundError):
            reader_service.export_vocab_csv(did)
        with pytest.raises(NotFoundError):
            reader_service.export_vocab_apkg(did, rich=True)

    def test_rich_queue(self, isolated_db):
        did = self._vocab(isolated_db)
        r = reader_service.set_rich_export(did, ["猫", "猫"])
        assert r["updated"] == 1 and r["rich_count"] == 1
        words = reader_service.list_vocab(did, rich_only=True)["words"]
        assert [w["word"] for w in words] == ["猫"] and words[0]["export_rich"] is True
        with pytest.raises(InvalidInputError):
            reader_service.set_rich_export(did, [])

    def _notes(self, content: bytes, tmp_path):
        import sqlite3
        import zipfile
        p = tmp_path / "deck.apkg"
        p.write_bytes(content)
        with zipfile.ZipFile(p) as z:
            z.extract("collection.anki2", tmp_path)
        conn = sqlite3.connect(tmp_path / "collection.anki2")
        try:
            return [r[0].split("\x1f") for r in conn.execute("SELECT flds FROM notes")]
        finally:
            conn.close()

    def test_apkg_content(self, isolated_db, tmp_path):
        pytest.importorskip("genanki")
        did = self._vocab(isolated_db)
        out = reader_service.export_vocab_apkg(did)
        assert out["filename"] == "vocab.apkg" and isinstance(out["content"], bytes)
        notes = self._notes(out["content"], tmp_path)
        assert sorted(notes) == sorted([["猫 (māo)", "cat; feline"], ["有 (yǒu)", "to have"]])
        assert not os.path.exists(os.path.join(isolated_db.drama_dir(did), "vocab.apkg"))

    def test_rich_apkg_content_text_only_without_audio(self, isolated_db, tmp_path):
        pytest.importorskip("genanki")
        did = self._vocab(isolated_db)
        reader_service.set_rich_export(did, ["猫"])
        out = reader_service.export_vocab_apkg(did, rich=True)
        assert out["filename"] == "vocab_sentence.apkg"
        [[sentence, back]] = self._notes(out["content"], tmp_path)
        assert sentence == "我有一只猫"
        assert "I have a cat" in back and "猫 (māo)" in back and "[sound:" not in sentence

    def test_apkg_without_genanki_is_dependency_error(self, isolated_db, monkeypatch):
        did = self._vocab(isolated_db)
        import vocab_export

        def missing(*a, **k):
            raise ImportError("genanki")
        monkeypatch.setattr(vocab_export, "export_vocab_apkg", missing)
        with pytest.raises(DependencyUnavailableError):
            reader_service.export_vocab_apkg(did)


class TestStoryWikiQa:
    def _drama_lines(self, db, n=10):
        did = _drama(db, title_en="Story")
        db.save_lines(did, _lines(n))
        return did

    def test_story_tools_are_spoiler_scoped(self, isolated_db, monkeypatch):
        did = self._drama_lines(isolated_db)
        _fake_engine(monkeypatch)
        import story_context
        seen = {}
        monkeypatch.setattr(story_context, "who_is_character",
                            lambda name, lines, meta, eng: seen.setdefault("n", len(lines)) and "A hero")
        out = reader_service.who_is_character(did, "沈清疑", up_to_line_idx=3)
        assert out["answer"] == "A hero" and seen["n"] == 4
        monkeypatch.setattr(story_context, "explain_reference",
                            lambda p, lines, eng, source_language="zh": f"{p}/{len(lines)}/{source_language}")
        assert reader_service.explain_reference(did, "一石二鸟")["answer"] == "一石二鸟/10/zh"
        with pytest.raises(InvalidInputError):
            reader_service.who_is_character(did, "  ")

    def test_recap_uses_lines_before_the_page(self, isolated_db, monkeypatch):
        did = self._drama_lines(isolated_db, 30)
        _fake_engine(monkeypatch)
        import story_context
        monkeypatch.setattr(story_context, "summarize_section",
                            lambda lines, eng, section_label="": f"{len(lines)} {section_label}")
        assert reader_service.recap(did, 2, chapter_size=10)["summary"] == "10 up to page 2"
        assert reader_service.recap(did, 1, chapter_size=10)["summary"] == "10 up to page 1"

    def test_relationship_map(self, isolated_db, monkeypatch):
        did = self._drama_lines(isolated_db)
        _fake_engine(monkeypatch)
        import story_context
        monkeypatch.setattr(story_context, "build_relationship_map", lambda lines, eng: {
            "characters": [{"name": "A", "role": "lead", "description": "d"}],
            "relationships": []})
        out = reader_service.relationship_map(did)
        assert out["characters"][0]["name"] == "A" and "A" in out["mermaid"]

    def test_update_wiki_upserts_by_name_and_hides_spoilers(self, isolated_db, monkeypatch):
        did = self._drama_lines(isolated_db)
        _fake_engine(monkeypatch)
        import universe_wiki
        calls = {}

        def fake_extract(lines, eng, up_to, meta, existing_entries=None):
            calls["up_to"] = up_to
            return [{"entry_type": "character", "name": "Early", "description": "e",
                     "first_seen_line_idx": 1, "known_through_line_idx": up_to},
                    {"entry_type": "place", "name": "Late", "first_seen_line_idx": 8,
                     "known_through_line_idx": up_to}]
        monkeypatch.setattr(universe_wiki, "extract_wiki_entries", fake_extract)
        assert reader_service.update_wiki(did, up_to_line_idx=500)["updated"] == 2
        assert calls["up_to"] == 9  # clamped to the drama's last line
        shown = reader_service.list_wiki(did, up_to_line_idx=3)["entries"]
        assert [e["name"] for e in shown] == ["Early"]
        md = reader_service.export_wiki_markdown(did, up_to_line_idx=3)
        assert "Early" in md["content"] and "Late" not in md["content"]
        assert "Built from lines 1–4." in md["content"]
        with pytest.raises(InvalidInputError):
            reader_service.list_wiki(did, entry_type="spaceship")
        reader_service.clear_wiki(did, confirm=True)
        assert reader_service.list_wiki(did)["entries"] == []

    def test_qa_passes_history_and_validates(self, isolated_db, monkeypatch):
        did = self._drama_lines(isolated_db)
        _fake_engine(monkeypatch)
        import qa
        got = {}

        def fake(q, lines, meta, eng, chat_history=None):
            got.update(q=q, n=len(lines), h=chat_history)
            return "answer"
        monkeypatch.setattr(qa, "ask_about_drama", fake)
        hist = [{"role": "user", "content": "hi", "extra": 1}, {"role": "assistant", "content": "yo"}]
        assert reader_service.ask_about_drama(did, "Who?", hist)["answer"] == "answer"
        assert got["h"] == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        assert got["n"] == 10
        with pytest.raises(InvalidInputError):
            reader_service.ask_about_drama(did, "Who?", [{"role": "system", "content": "x"}])

    def test_translation_only_engine_rejected(self, isolated_db, monkeypatch):
        did = self._drama_lines(isolated_db)
        import translate_engines
        name = sorted(translate_engines.TRANSLATION_ONLY_ENGINES)[0]
        from services.service_errors import UnsupportedOperationError
        with pytest.raises(UnsupportedOperationError):
            reader_service.ask_about_drama(did, "Q?", engine_name=name)


class TestReviewFixes:
    """One test per review finding on the M4 reader service."""

    def _file(self, db, did, name, data=b"\x00" * 16):
        d = db.drama_dir(did)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)
        return os.path.join(d, name)

    def test_media_confined_to_drama_folder(self, isolated_db, tmp_path):
        did = _drama(isolated_db, audio_filename="..", source_video_filename="../x.mp4")
        os.makedirs(isolated_db.drama_dir(did), exist_ok=True)
        outside = tmp_path / "secret.wav"
        outside.write_bytes(b"\x00" * 16)
        os.symlink(str(outside), os.path.join(isolated_db.drama_dir(did), "dub_track.wav"))
        os.makedirs(os.path.join(isolated_db.drama_dir(did), "narration_track.wav"))
        for kind in ("original", "dub", "narration"):
            with pytest.raises(NotFoundError):
                reader_service.media_file_path(did, kind)
        info = reader_service.get_media_availability(did)
        assert info["original"] is None and not info["dub"] and not info["narration"]

    def test_media_extension_whitelist_and_no_symlinked_original(self, isolated_db):
        did = _drama(isolated_db, audio_filename="a.txt")
        self._file(isolated_db, did, "a.txt")
        with pytest.raises(NotFoundError):
            reader_service.media_file_path(did, "original")
        did2 = _drama(isolated_db, audio_filename="a.wav")
        real = self._file(isolated_db, did2, "real.wav")
        os.symlink(real, os.path.join(isolated_db.drama_dir(did2), "a.wav"))
        with pytest.raises(NotFoundError):
            reader_service.media_file_path(did2, "original")

    def test_clear_wiki_requires_confirm(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.upsert_wiki_entry(did, "character", "A")
        with pytest.raises(InvalidInputError):
            reader_service.clear_wiki(did)
        with pytest.raises(InvalidInputError):
            reader_service.clear_wiki(did, confirm="yes")
        assert len(isolated_db.list_wiki_entries(did)) == 1
        reader_service.clear_wiki(did, confirm=True)
        assert isolated_db.list_wiki_entries(did) == []

    def test_oversized_id_is_invalid(self, isolated_db):
        with pytest.raises(InvalidInputError):
            reader_service.get_notes(2**63)
        with pytest.raises(InvalidInputError):
            reader_service.get_reader_page(2**63)

    def test_local_oserror_is_fixed_message(self):
        from services.service_errors import ServiceError

        def boom():
            raise OSError("/home/someone/.cache/cedict.txt: permission denied")
        with pytest.raises(ServiceError) as ei:
            reader_service._run_engine(boom)
        assert "/home" not in str(ei.value) and "cedict.txt" not in str(ei.value)

        def boom2():
            raise RuntimeError("failed at /home/someone/secret/dir/file.py")
        with pytest.raises(ServiceError) as ei:
            reader_service._run_engine(boom2)
        assert "/home/someone" not in str(ei.value)

    def test_chat_history_and_model_caps(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, _lines(3))
        _fake_engine(monkeypatch)
        too_long_turn = [{"role": "user", "content": "x" * 20_001}]
        too_much_total = [{"role": "user", "content": "x" * 19_000}] * 6
        for hist in (too_long_turn, too_much_total, [{"role": "user", "content": "x"}] * 41):
            with pytest.raises(InvalidInputError):
                reader_service.ask_about_drama(did, "Q?", hist)
        for model in ("m" * 101, "bad model", "bad\x00", ""):
            with pytest.raises(InvalidInputError):
                reader_service.ask_about_drama(did, "Q?", model=model)

    def test_spoiler_none_means_whole_drama(self):
        scoped, limit = reader_service._scope(_lines(5), None)
        assert len(scoped) == 5 and limit == 4
        assert "None means NO" in reader_service._scope.__doc__
        assert "must pass that boundary" in reader_service.__doc__

    def test_filename_is_ascii(self, isolated_db):
        for title, want in (("我的剧", "vocab.csv"), ("Café 猫/Drama", "Caf_ _Drama.csv")):
            did = _drama(isolated_db, title_en=title)
            isolated_db.save_vocab_lookup(did, "猫", "māo", ["cat"], "zh", 0)
            name = reader_service.export_vocab_csv(did)["filename"]
            name.encode("latin-1")
            assert name.isascii() and name == want
