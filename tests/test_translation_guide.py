"""
tests/test_translation_guide.py -- tests for translation_guide.py's
craft layer: style presets, term-policy grouping, notes formatting,
and hard term enforcement.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import translation_guide as tg


SAMPLE_GLOSSARY = [
    {"term_original": "沈清疑", "term_translation": "Shen Qingyi",
     "category": "person_name", "policy": "keep_pinyin", "notes": ""},
    {"term_original": "云隐宗", "term_translation": "Yunyin Sect",
     "category": "clan_sect", "policy": "hybrid", "notes": ""},
    {"term_original": "姐姐", "term_translation": "jiejie",
     "category": "honorific", "policy": "contextual", "notes": "intimate address"},
]


class TestStylePresets:
    def test_all_presets_have_label_and_guidance(self):
        for key, preset in tg.STYLE_PRESETS.items():
            assert preset.get("label")
            assert preset.get("guidance")

    def test_audio_drama_emphasizes_spoken_delivery(self):
        g = tg.build_style_guidelines("audio_drama")
        assert "SPOKEN" in g

    def test_novel_preset_allows_literary_register(self):
        g = tg.build_style_guidelines("novel")
        assert "prose" in g.lower()

    def test_subtitle_preset_emphasizes_readability(self):
        g = tg.build_style_guidelines("subtitle")
        assert "subtitle" in g.lower()

    def test_unknown_preset_falls_back_without_crashing(self):
        g = tg.build_style_guidelines("not_a_real_preset")
        assert len(g) > 0

    def test_genre_notes_included_by_default(self):
        g = tg.build_style_guidelines("audio_drama")
        assert "baihe" in g.lower()

    def test_genre_notes_can_be_disabled(self):
        g = tg.build_style_guidelines("audio_drama", include_genre_notes=False)
        assert "Genre notes" not in g

    def test_female_pronoun_default_is_off_by_default(self):
        g = tg.build_style_guidelines("audio_drama")
        assert "Pronoun default" not in g

    def test_female_pronoun_default_can_be_enabled(self):
        g = tg.build_style_guidelines("audio_drama", default_female_pronouns=True)
        assert "default an ambiguous third-person" in g
        assert "他/她/它" in g

    def test_custom_notes_appended(self):
        g = tg.build_style_guidelines("audio_drama", custom_notes="keep the narrator distant")
        assert "keep the narrator distant" in g

    def test_empty_custom_notes_adds_no_section(self):
        g = tg.build_style_guidelines("audio_drama", custom_notes="   ")
        assert "ADDITIONAL PROJECT NOTES" not in g


class TestCharacterGenderHints:
    """build_character_gender_hints() -- a fixed pronoun assignment for a
    named character, since spoken Mandarin's 他/她/它 are homophones and
    Whisper's transcribed pronoun character isn't a reliable gender
    signal to translate literally."""

    def test_no_characters_have_a_gender_set_returns_empty(self):
        chars = [{"character_name": "Su Shan", "gender": None},
                 {"character_name": "Liang", "gender": ""}]
        assert tg.build_character_gender_hints(chars) == ""

    def test_labels_female_and_male_correctly(self):
        chars = [{"character_name": "Su Shan", "gender": "female"},
                 {"character_name": "Liang", "gender": "male"}]
        result = tg.build_character_gender_hints(chars)
        assert "Su Shan: she/her" in result
        assert "Liang: he/him" in result

    def test_characters_without_a_gender_are_omitted_not_listed_as_unknown(self):
        chars = [{"character_name": "Su Shan", "gender": "female"},
                 {"character_name": "Guest", "gender": None}]
        result = tg.build_character_gender_hints(chars)
        assert "Su Shan" in result
        assert "Guest" not in result

    def test_empty_list_returns_empty(self):
        assert tg.build_character_gender_hints([]) == ""


class TestCharacterPronouns:
    """Step 1e: pronouns as free text (she/her, he/him, they/them, or a
    custom value), settable per drama for dramas with no series, with the
    per-drama value overriding the series value."""

    def test_legacy_female_male_still_map_to_she_and_he(self):
        assert tg.normalize_pronouns("female") == "she/her"
        assert tg.normalize_pronouns("male") == "he/him"
        assert tg.normalize_pronouns(None) == ""
        assert tg.normalize_pronouns("  they/them ") == "they/them"

    def test_they_them_and_custom_values_reach_the_hints(self):
        chars = [{"character_name": "Ash", "gender": "they/them"},
                 {"character_name": "Rin", "gender": "xe/xem"}]
        result = tg.build_character_gender_hints(chars)
        assert "Ash: they/them" in result
        assert "Rin: xe/xem" in result

    def test_standalone_drama_per_drama_pronouns_produce_hints(self):
        drama_chars = [{"speaker_label": "SPEAKER_00", "character_name": "Xiaoling",
                        "pronouns": "they/them", "series_pronouns": None}]
        result = tg.build_character_gender_hints([], drama_chars)
        assert "Xiaoling: they/them" in result

    def test_per_drama_value_overrides_the_series_value(self):
        series = [{"character_name": "Su Shan", "gender": "female"}]
        drama_chars = [{"speaker_label": "SPEAKER_00", "character_name": "Su Shan",
                        "pronouns": "they/them", "series_pronouns": "female"}]
        result = tg.build_character_gender_hints(series, drama_chars)
        assert "Su Shan: they/them" in result
        assert "she/her" not in result
        assert tg.build_speaker_labels(drama_chars, series) == {"SPEAKER_00": "Su Shan (they/them)"}

    def test_linked_series_value_is_the_per_drama_default(self):
        drama_chars = [{"speaker_label": "SPEAKER_00", "character_name": "Su Shan",
                        "pronouns": None, "series_pronouns": "female"}]
        assert tg.build_speaker_labels(drama_chars, []) == {"SPEAKER_00": "Su Shan (she/her)"}

    def test_unlinked_but_same_named_series_character_is_used(self):
        series = [{"character_name": "Liang", "gender": "male"}]
        drama_chars = [{"speaker_label": "SPEAKER_01", "character_name": "Liang",
                        "pronouns": None, "series_pronouns": None}]
        assert tg.build_speaker_labels(drama_chars, series) == {"SPEAKER_01": "Liang (he/him)"}

    def test_speaker_labels_skip_unnamed_and_leave_unset_pronouns_off(self):
        drama_chars = [{"speaker_label": "SPEAKER_00", "character_name": "Xiaoling",
                        "pronouns": None, "series_pronouns": None},
                       {"speaker_label": "SPEAKER_01", "character_name": None,
                        "pronouns": "she/her", "series_pronouns": None}]
        assert tg.build_speaker_labels(drama_chars) == {"SPEAKER_00": "Xiaoling"}


class TestGlossaryInGuidelines:
    def test_terms_appear_in_output(self):
        g = tg.build_style_guidelines("audio_drama", glossary_terms=SAMPLE_GLOSSARY)
        assert "Shen Qingyi" in g
        assert "Yunyin Sect" in g

    def test_terms_grouped_by_policy(self):
        g = tg.build_style_guidelines("audio_drama", glossary_terms=SAMPLE_GLOSSARY)
        assert "Keep as pinyin" in g
        assert "Hybrid" in g
        assert "Depends on context" in g

    def test_term_notes_included(self):
        g = tg.build_style_guidelines("audio_drama", glossary_terms=SAMPLE_GLOSSARY)
        assert "intimate address" in g

    def test_no_glossary_omits_the_section(self):
        g = tg.build_style_guidelines("audio_drama", glossary_terms=None)
        assert "TERM GLOSSARY" not in g

    def test_term_missing_policy_defaults_to_pinyin(self):
        terms = [{"term_original": "X", "term_translation": "Y"}]
        g = tg.build_style_guidelines("audio_drama", glossary_terms=terms)
        assert "Keep as pinyin" in g


class TestTermCategoriesAndPolicies:
    def test_every_policy_has_label_example_guidance(self):
        for key, pol in tg.TERM_POLICIES.items():
            assert pol.get("label") and pol.get("example") and pol.get("guidance")

    def test_categories_cover_expected_cnovel_types(self):
        for expected in ("person_name", "clan_sect", "honorific", "cultivation_realm"):
            assert expected in tg.TERM_CATEGORIES


class TestGroupNotesByLine:
    """group_notes_by_line() -- reshapes db.list_translation_notes()'s
    flat rows into {line_idx: [...]}, the shape core.lines_to_srt() needs
    to inline a note onto the exported subtitle line it's about."""

    def test_groups_by_line_idx(self):
        notes = [{"line_idx": 3, "term": "a", "note": "note a"},
                 {"line_idx": 3, "term": "b", "note": "note b"},
                 {"line_idx": 7, "term": "c", "note": "note c"}]
        grouped = tg.group_notes_by_line(notes)
        assert len(grouped[3]) == 2
        assert grouped[7] == [{"term": "c", "note": "note c"}]

    def test_notes_with_no_line_idx_are_dropped(self):
        notes = [{"line_idx": None, "term": "a", "note": "note a"}]
        assert tg.group_notes_by_line(notes) == {}

    def test_empty_input_returns_empty_dict(self):
        assert tg.group_notes_by_line([]) == {}


class TestNotesFormatting:
    def test_notes_render_with_title(self):
        notes = [{"line_idx": 5, "term": "一石二鸟", "note_type": "idiom", "note": "A set phrase."}]
        md = tg.format_notes_as_markdown(notes, "My Drama")
        assert "My Drama" in md
        assert "一石二鸟" in md

    def test_line_numbers_are_one_based_in_output(self):
        notes = [{"line_idx": 0, "term": "X", "note_type": "idiom", "note": "N"}]
        md = tg.format_notes_as_markdown(notes)
        assert "Line 1" in md

    def test_notes_grouped_by_type(self):
        notes = [
            {"line_idx": 1, "term": "A", "note_type": "idiom", "note": "n1"},
            {"line_idx": 2, "term": "B", "note_type": "wordplay", "note": "n2"},
        ]
        md = tg.format_notes_as_markdown(notes)
        assert md.count("##") >= 2

    def test_empty_notes_produces_placeholder(self):
        md = tg.format_notes_as_markdown([])
        assert "No notes recorded" in md

    def test_all_note_types_have_descriptions(self):
        for key, desc in tg.NOTE_TYPES.items():
            assert isinstance(desc, str) and len(desc) > 0


class TestHardTermSubstitution:
    def test_replaces_known_variants_with_canonical(self):
        terms = [{"term_translation": "Shen Qingyi",
                  "notes": "Shen Qing Yi|Chen Qingyi", "enforce_exact": True}]
        result = tg.apply_hard_term_substitutions("Shen Qing Yi met Chen Qingyi.", terms)
        assert result == "Shen Qingyi met Shen Qingyi."

    def test_ignores_terms_not_marked_enforce(self):
        terms = [{"term_translation": "Canonical", "notes": "Variant", "enforce_exact": False}]
        result = tg.apply_hard_term_substitutions("Variant stays put.", terms)
        assert result == "Variant stays put."

    def test_case_insensitive_matching(self):
        terms = [{"term_translation": "Yunyin Sect", "notes": "yunyin sect", "enforce_exact": True}]
        result = tg.apply_hard_term_substitutions("the yunyin sect gathered", terms)
        assert "Yunyin Sect" in result

    def test_longer_terms_replaced_first(self):
        # "Shen Qingyi" should not be corrupted by a rule targeting "Shen"
        terms = [
            {"term_translation": "Shen Qingyi", "notes": "Shen Qing Yi", "enforce_exact": True},
            {"term_translation": "Shen", "notes": "Chen", "enforce_exact": True},
        ]
        result = tg.apply_hard_term_substitutions("Shen Qing Yi arrived.", terms)
        assert "Shen Qingyi" in result

    def test_empty_glossary_returns_text_unchanged(self):
        assert tg.apply_hard_term_substitutions("unchanged", []) == "unchanged"
        assert tg.apply_hard_term_substitutions("unchanged", None) == "unchanged"

    def test_no_variants_listed_leaves_text_alone(self):
        terms = [{"term_translation": "Canonical", "notes": "", "enforce_exact": True}]
        assert tg.apply_hard_term_substitutions("some text", terms) == "some text"


class TestLlmFunctionsGracefulFallback:
    def test_extract_terms_returns_empty_for_pure_mt_engine(self):
        class PureMT:
            supports_reference = False
        assert tg.extract_terms_llm(["你好"], PureMT()) == []

    def test_extract_terms_returns_empty_for_blank_input(self):
        class MockLlm:
            supports_reference = True
        assert tg.extract_terms_llm(["", "  "], MockLlm()) == []

    def test_generate_notes_returns_empty_for_pure_mt_engine(self):
        from core import Line
        class PureMT:
            supports_reference = False
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        assert tg.generate_translation_notes_llm(lines, PureMT()) == []

    def test_generate_notes_returns_empty_when_nothing_translated(self):
        from core import Line
        class MockLlm:
            supports_reference = True
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]
        assert tg.generate_translation_notes_llm(lines, MockLlm()) == []


class TestNovelSampling:
    def test_samples_spread_across_the_text(self):
        long_text = "".join(f"part{i} content. " for i in range(3000))
        samples = tg._sample_across_text(long_text, total_chars=6000, chunks=6)
        assert len(samples) == 6
        assert samples[0] != samples[-1]

    def test_short_text_returned_whole(self):
        assert tg._sample_across_text("tiny") == ["tiny"]

    def test_empty_text(self):
        assert tg._sample_across_text("") == []
        assert tg._sample_across_text(None) == []

    def test_pure_mt_engine_extracts_nothing(self):
        class PureMT:
            supports_reference = False
        assert tg.extract_glossary_from_novel("some text", PureMT()) == []


class TestGlossaryFileImport:
    def test_csv_with_header(self):
        text = ("term_original,term_translation,category,policy,enforce_exact,notes\n"
                "沈清疑,Shen Qingyi,person_name,keep_pinyin,yes,protagonist")
        entries, warnings = tg.parse_glossary_file(text, "g.csv")
        assert len(entries) == 1
        assert entries[0]["term_translation"] == "Shen Qingyi"
        assert entries[0]["enforce_exact"] is True

    def test_headerless_two_column_csv_warns(self):
        entries, warnings = tg.parse_glossary_file("沈清疑,Shen Qingyi", "g.csv")
        assert len(entries) == 1
        assert any("No header" in w for w in warnings)

    def test_tsv_and_column_aliases(self):
        entries, _ = tg.parse_glossary_file("term\ttranslation\n洛神\tLuo Shen", "g.tsv")
        assert entries[0]["term_original"] == "洛神"
        assert entries[0]["term_translation"] == "Luo Shen"

    def test_json_import(self):
        entries, _ = tg.parse_glossary_file(
            '[{"term_original":"道","term_translation":"dao","policy":"keep_with_note"}]', "g.json")
        assert entries[0]["policy"] == "keep_with_note"

    def test_unknown_category_corrected_and_warned(self):
        entries, warnings = tg.parse_glossary_file(
            "term_original,category\nX,not_a_real_category", "g.csv")
        assert entries[0]["category"] == "other"
        assert any("category" in w for w in warnings)

    def test_unknown_policy_corrected_and_warned(self):
        entries, warnings = tg.parse_glossary_file(
            "term_original,policy\nX,not_a_real_policy", "g.csv")
        assert entries[0]["policy"] == "keep_pinyin"
        assert any("policy" in w for w in warnings)

    def test_row_without_term_skipped_with_warning(self):
        entries, warnings = tg.parse_glossary_file(
            "term_original,term_translation\n,orphan translation\nX,ok", "g.csv")
        assert len(entries) == 1
        assert any("no original term" in w for w in warnings)

    def test_empty_file(self):
        entries, warnings = tg.parse_glossary_file("", "g.csv")
        assert entries == [] and warnings

    def test_malformed_json_reports_error(self):
        entries, warnings = tg.parse_glossary_file("{not valid json", "g.json")
        assert entries == [] and "parse" in warnings[0].lower()

    def test_json_not_a_list_rejected(self):
        entries, warnings = tg.parse_glossary_file('{"a":1}', "g.json")
        assert entries == [] and warnings


class TestGlossaryExport:
    def test_csv_has_header_row(self):
        out = tg.glossary_to_csv([])
        assert out.splitlines()[0].startswith("term_original")

    def test_round_trip_preserves_fields(self):
        original = [{"term_original": "沈清疑", "term_translation": "Shen Qingyi",
                     "category": "person_name", "policy": "keep_pinyin",
                     "enforce_exact": True, "notes": "lead"}]
        back, _ = tg.parse_glossary_file(tg.glossary_to_csv(original), "r.csv")
        assert back[0]["term_original"] == "沈清疑"
        assert back[0]["enforce_exact"] is True
        assert back[0]["category"] == "person_name"

    def test_export_handles_missing_optional_fields(self):
        out = tg.glossary_to_csv([{"term_original": "X"}])
        assert "X" in out
