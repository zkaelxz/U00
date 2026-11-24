"""The built-in comic glossary: what a comic page carries, and how a person's Settings lines and a series glossary change it."""

import pytest

from services import comic_glossary_service as svc
from services import settings_service


def _page(*texts):
    return [{"source_text": t} for t in texts]


class TestCustomLines:
    def test_lines_are_parsed_with_comments_and_empty_english_kept(self):
        assert svc.custom_entries("# note\n師兄 = senior brother\n\n殿 =\nno equals sign") == {
            "師兄": "senior brother", "殿": ""}

    def test_an_overlong_list_is_cut_instead_of_filling_the_prompt(self):
        text = "\n".join(f"語{i} = w" for i in range(svc._MAX_CUSTOM_LINES + 50))
        assert len(svc.custom_entries(text)) == svc._MAX_CUSTOM_LINES


class TestReferenceBlock:
    @pytest.mark.parametrize("lang,text,expected", [
        ("ja", "勇者よ、ドン！", ["勇者", "ドン"]),
        ("ko", "회귀한 헌터", ["회귀", "헌터"]),
        ("zh", "他是金丹期的宗主", ["金丹", "宗主"]),
    ])
    def test_only_terms_on_the_page_are_sent(self, lang, text, expected, isolated_db):
        block = svc.reference_block(lang, _page(text))
        assert all(term in block for term in expected)
        assert "魔王" not in block and "던전" not in block and "渡劫" not in block

    def test_forms_of_address_from_the_subtitle_packs_apply_to_comics(self, isolated_db):
        assert "さん → default: -san" in svc.reference_block("ja", _page("田中さん"))

    def test_a_page_with_no_text_or_an_unknown_language_gets_nothing(self, isolated_db):
        assert svc.reference_block("ja", _page("")) == ""
        assert svc.reference_block("en", _page("勇者")) == ""

    def test_streamer_words_are_not_sent_to_a_comic(self, isolated_db):
        assert "配信" not in svc.reference_block("ja", _page("配信 勇者"))

    def test_a_series_term_with_the_same_source_wins(self, isolated_db):
        block = svc.reference_block("ja", _page("勇者"), [{"term_original": "勇者"}])
        assert "勇者" not in block

    def test_a_skipped_region_is_not_read_for_terms(self, isolated_db):
        assert svc.reference_block("ja", [{"source_text": "勇者", "skip": True}]) == ""


class TestPersonsLines:
    def test_a_line_replaces_the_built_in_entry(self, isolated_db):
        settings_service.set_settings({"comic_glossary_custom": "勇者 = the Chosen One"})
        block = svc.reference_block("ja", _page("勇者"))
        assert "the Chosen One" in block and "default: Hero" not in block

    def test_an_empty_line_drops_the_entry(self, isolated_db):
        settings_service.set_settings({"comic_glossary_custom": "勇者 ="})
        assert svc.reference_block("ja", _page("勇者")) == ""

    def test_a_new_line_is_sent_when_its_source_is_on_the_page(self, isolated_db):
        settings_service.set_settings({"comic_glossary_custom": "ロイド = Lloyd"})
        assert "Lloyd" in svc.reference_block("ja", _page("ロイドさん"))
        assert "Lloyd" not in svc.reference_block("ja", _page("勇者"))


def test_with_reference_joins_notes_and_block(isolated_db):
    out = svc.with_reference("CAST", "ja", _page("勇者"))
    assert out.startswith("CAST\n\n") and "勇者" in out
    assert svc.with_reference("CAST", "ja", _page("")) == "CAST"


def test_quick_read_page_extras_carry_the_glossary_for_a_page_with_no_title(isolated_db):
    from services import page_server
    extras = page_server._page_extras(None, {}, None, b"", _page("勇者が来た"), "ja")
    assert "勇者 → default: Hero" in extras["character_notes"]
    assert "勇者" not in page_server._page_extras(None, {}, None, b"", _page("犬"), "ja")["character_notes"]


class TestSoundEffectsNeedAWordOfTheirOwn:
    def test_an_effect_inside_a_misread_katakana_run_is_not_matched(self, isolated_db):
        assert svc.reference_block("ja", _page("シクシーンバッズ部の人は")) == ""

    def test_the_effect_standing_alone_or_beside_other_script_is_matched(self, isolated_db):
        assert "シーン → default: silence" in svc.reference_block("ja", _page("シーン…"))
        assert "シーン → default: silence" in svc.reference_block("ja", _page("その場はシーンとなった"))

    def test_a_korean_effect_inside_a_longer_hangul_word_is_not_matched(self, isolated_db):
        assert svc.reference_block("ko", _page("쿵쿵쿵")) == ""
        assert "쿵 → default: thud" in svc.reference_block("ko", _page("쿵! 뭐야"))

    def test_a_non_katakana_term_still_matches_inside_a_sentence(self, isolated_db):
        assert "勇者" in svc.reference_block("ja", _page("あの勇者さま"))
