"""
tests/test_wiki_adaptive.py -- tests for the universe wiki (including
its spoiler boundary), adaptive style learning, line tools, and reader
theming.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import universe_wiki as uw
import adaptive_style as ast_
import line_tools as lt
import reader as reader_module
from core import Line


class PureMT:
    supports_reference = False


class TestWikiSpoilerBoundary:
    def test_entries_after_current_position_are_hidden(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.upsert_wiki_entry(did, "character", "Early", first_seen_line_idx=5)
        isolated_db.upsert_wiki_entry(did, "character", "Late", first_seen_line_idx=500)
        names = [e["name"] for e in isolated_db.list_wiki_entries(did, spoiler_limit_line_idx=100)]
        assert "Early" in names
        assert "Late" not in names

    def test_no_limit_returns_everything(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.upsert_wiki_entry(did, "character", "Early", first_seen_line_idx=5)
        isolated_db.upsert_wiki_entry(did, "character", "Late", first_seen_line_idx=500)
        assert len(isolated_db.list_wiki_entries(did)) == 2

    def test_entry_without_first_seen_always_shown(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.upsert_wiki_entry(did, "concept", "Unknown origin")
        assert len(isolated_db.list_wiki_entries(did, spoiler_limit_line_idx=0)) == 1

    def test_filter_by_entry_type(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.upsert_wiki_entry(did, "character", "A")
        isolated_db.upsert_wiki_entry(did, "sect", "B")
        assert len(isolated_db.list_wiki_entries(did, entry_type="sect")) == 1


class TestWikiAccumulation:
    def test_update_preserves_first_seen(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.upsert_wiki_entry(did, "character", "X", first_seen_line_idx=10,
                                       known_through_line_idx=50)
        isolated_db.upsert_wiki_entry(did, "character", "X", description="More detail",
                                       first_seen_line_idx=99, known_through_line_idx=200)
        e = isolated_db.list_wiki_entries(did)[0]
        assert e["first_seen_line_idx"] == 10
        assert e["known_through_line_idx"] == 200

    def test_update_preserves_aliases_when_omitted(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.upsert_wiki_entry(did, "character", "X", aliases="Nickname")
        isolated_db.upsert_wiki_entry(did, "character", "X", description="desc")
        e = isolated_db.list_wiki_entries(did)[0]
        assert e["aliases"] == "Nickname"

    def test_attributes_roundtrip(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.upsert_wiki_entry(did, "character", "X",
                                       attributes={"cultivation": "Golden Core"})
        assert isolated_db.list_wiki_entries(did)[0]["attributes"]["cultivation"] == "Golden Core"

    def test_clear_wiki(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.upsert_wiki_entry(did, "character", "X")
        isolated_db.clear_wiki(did)
        assert isolated_db.list_wiki_entries(did) == []


class TestWikiFormatting:
    def test_markdown_groups_by_type(self):
        md = uw.format_wiki_as_markdown([
            {"entry_type": "character", "name": "A", "description": "d", "attributes": {}},
            {"entry_type": "sect", "name": "B", "description": "d", "attributes": {}},
        ])
        assert "## Characters" in md and "## Sects" in md

    def test_markdown_includes_attributes_and_aliases(self):
        md = uw.format_wiki_as_markdown([
            {"entry_type": "character", "name": "A", "aliases": "Nick",
             "description": "d", "attributes": {"rank": "elder"}},
        ])
        assert "Nick" in md and "rank" in md and "elder" in md

    def test_spoiler_note_rendered(self):
        md = uw.format_wiki_as_markdown(
            [{"entry_type": "character", "name": "A", "description": "d", "attributes": {}}],
            spoiler_note="Built from lines 1-50.")
        assert "Built from lines 1-50." in md

    def test_empty_wiki_placeholder(self):
        assert "No entries yet" in uw.format_wiki_as_markdown([])

    def test_pure_mt_extraction_returns_empty(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        assert uw.extract_wiki_entries(lines, PureMT(), 0) == []


class TestAdaptiveStyle:
    def test_diff_classifies_shortening(self):
        d = ast_.diff_summary("one two three four five six seven", "one two")
        assert d["kind"] == "shortened"

    def test_diff_classifies_expansion(self):
        d = ast_.diff_summary("short", "a much much much longer version here")
        assert d["kind"] == "expanded"

    def test_diff_classifies_rephrasing(self):
        d = ast_.diff_summary("one two three", "three two one")
        assert d["kind"] == "rephrased"

    def test_tendencies_counts_each_kind(self):
        s = ast_.summarize_edit_tendencies([
            {"ai_version": "a b c d e f", "user_version": "a b"},
            {"ai_version": "x", "user_version": "x y z q r"},
            {"ai_version": "p q r", "user_version": "r q p"},
        ])
        assert s["total"] == 3 and s["shortened"] == 1 and s["expanded"] == 1

    def test_tendencies_empty(self):
        assert ast_.summarize_edit_tendencies([])["total"] == 0

    def test_insufficient_samples_refuses_to_guess(self):
        class MockLlm:
            supports_reference = True
        r = ast_.analyze_edit_patterns([{"zh": "a", "ai_version": "b", "user_version": "c"}],
                                        MockLlm())
        assert r["confidence"] == "insufficient"
        assert r["preferences"] == []

    def test_pure_mt_engine_returns_none_confidence(self):
        r = ast_.analyze_edit_patterns([{"zh": "a", "ai_version": "b", "user_version": "c"}] * 20,
                                        PureMT())
        assert r["confidence"] == "none"

    def test_profile_block_empty_when_no_preferences(self):
        assert ast_.profile_to_prompt_block({}) == ""
        assert ast_.profile_to_prompt_block({"preferences": []}) == ""

    def test_profile_block_includes_rules_and_glossary_precedence(self):
        b = ast_.profile_to_prompt_block({"preferences": ["Keep it short"]})
        assert "Keep it short" in b
        assert "glossary" in b.lower()


class TestEditSampleRecording:
    def test_records_genuine_edit(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.record_edit_sample(did, "原", "ai text", "my text")
        assert len(isolated_db.list_edit_samples(did)) == 1

    def test_ignores_unchanged_line(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.record_edit_sample(did, "原", "same text", "same text")
        assert isolated_db.list_edit_samples(did) == []

    def test_ignores_empty_user_version(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.record_edit_sample(did, "原", "ai text", "")
        assert isolated_db.list_edit_samples(did) == []

    def test_style_profile_roundtrip(self, isolated_db):
        isolated_db.save_style_profile("global", {"preferences": ["A", "B"]}, sample_count=9)
        p = isolated_db.get_style_profile("global")
        assert p["profile"]["preferences"] == ["A", "B"]
        assert p["sample_count"] == 9

    def test_missing_profile_returns_none(self, isolated_db):
        assert isolated_db.get_style_profile("nonexistent") is None


class TestLineTools:
    def test_pure_mt_fallbacks(self):
        assert lt.alternative_translations("a", "b", PureMT()) == []
        assert lt.grammar_breakdown("a", PureMT()) == []
        assert "needs an LLM" in lt.explain_translation("a", "b", PureMT())

    def test_improve_line_returns_original_on_unsupported_engine(self):
        assert lt.improve_line("原", "original text", PureMT()) == "original text"


class TestReaderTheming:
    def test_all_themes_render(self):
        for theme in reader_module.THEMES:
            html = reader_module.build_reader_html([], "zh", {}, theme=theme)
            assert reader_module.THEMES[theme]["bg"] in html

    def test_all_fonts_render(self):
        for font in reader_module.FONT_STACKS:
            html = reader_module.build_reader_html([], "zh", {}, font=font)
            assert len(html) > 0

    def test_font_size_and_width_applied(self):
        html = reader_module.build_reader_html([], "zh", {}, font_size=30, max_width=800)
        assert "30px" in html and "max-width: 800px" in html

    def test_unknown_theme_falls_back_to_light(self):
        html = reader_module.build_reader_html([], "zh", {}, theme="nope")
        assert reader_module.THEMES["light"]["bg"] in html
