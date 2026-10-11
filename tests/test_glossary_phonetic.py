"""glossary_phonetic: sound-alike keys and the similarity rule.

Expectations are stated per case. A "must not match" case is as important as a match:
the keys are lossy on purpose, and a loose threshold would merge different people.
"""
import importlib.util

import pytest

import glossary_phonetic as ph

needs_kakasi = pytest.mark.skipif(importlib.util.find_spec("pykakasi") is None,
                                  reason="pykakasi not installed")
needs_pypinyin = pytest.mark.skipif(importlib.util.find_spec("pypinyin") is None,
                                    reason="pypinyin not installed")


def key(text, lang="ja"):
    return ph.phonetic_key(text, lang)


def alike(a, b, lang="ja"):
    return ph.similar(key(a, lang), key(b, lang), lang)


class TestJapanese:
    def test_long_vowel_and_kana_script_do_not_change_the_key(self):
        # ー, ウ after オ and hiragana vs katakana are the same sound: one name.
        assert key("ヤロ") == key("ヤロー") == key("ヤロウ") == key("やろ")

    def test_trailing_sound_is_a_variant_but_a_different_sound_is_not(self):
        assert alike("ヤロ", "ヤロック")      # ASR added a trailing ッ + ク
        assert not alike("ヤロ", "ヤヤ")      # different consonant AND vowel: the owner adds it by hand
        assert not alike("ミキ", "ミカ")      # Miki and Mika are two people (short key, no slack)
        assert not alike("アキラ", "ヤロ")

    def test_voiced_unvoiced_and_small_kana_are_tolerated(self):
        assert key("ガクト") == key("カクト")
        assert key("シャーロット") == key("シャロット")
        assert key("ファイト") == key("ハイト")

    @needs_kakasi
    def test_kanji_use_their_reading(self):
        assert key("山田") == key("ヤマダ") == key("やまだ")

    def test_longer_names_allow_one_slip(self):
        assert alike("サクラコ", "サクナコ")   # 6-letter key, one letter off
        assert not alike("サクラコ", "ヒロシ")

    def test_halfwidth_forms_match(self):
        assert key("ﾔﾛｰ") == key("ヤロー")

    def test_cross_script_with_latin(self):
        assert key("ヤロ", "ja") == key("Yalo", "ja")


@needs_pypinyin
class TestChinese:
    def test_tones_and_script_do_not_matter(self):
        # 亚洛 / 雅洛 / 亚罗 / 亞洛 all read ya-luo without tones.
        assert (key("亚洛", "zh") == key("雅洛", "zh") == key("亚罗", "zh")
                == key("亞洛", "zh") == "yaluo")

    def test_accent_confusions_are_folded_but_other_sounds_are_not(self):
        assert key("陈", "zh") == key("成", "zh")            # chen / cheng: -n vs -ng folded
        assert not alike("兰", "然", "zh")                   # lan and ran are different people
        assert not alike("亚洛", "沈清疑", "zh")

    def test_punctuation_is_skipped(self):
        assert key("亚·洛", "zh") == "yaluo"


class TestKorean:
    def test_final_consonant_spellings_sound_the_same(self):
        assert key("낫", "ko") == key("낟", "ko")             # ㅅ and ㄷ finals

    def test_tense_and_plain_initials_fold(self):
        assert key("가나", "ko") == key("카나", "ko") == key("까나", "ko")

    def test_different_names_do_not_match(self):
        assert not alike("민수", "지우", "ko")
        assert not alike("김민수", "김지우", "ko")           # same surname, different given name

    def test_one_slip_in_a_long_name_is_a_variant(self):
        assert alike("김민수", "김민슈", "ko")               # su / syu: 1 edit in 8 letters


class TestLatin:
    def test_owner_example(self):
        # Yalo / Yalou are the same sound; Yaloch only adds a trailing sound.
        assert key("Yalo", "en") == key("Yalou", "en")
        assert alike("Yalo", "Yaloch", "en")

    def test_unrelated_names_do_not_match(self):
        assert not alike("Yalo", "Marcus", "en")
        assert not alike("Mika", "Miki", "en")

    def test_case_accents_and_punctuation_are_ignored(self):
        assert key("Élodie-Rose", "en") == key("elodie rose", "en")


class TestSimilarRule:
    def test_empty_and_short_keys(self):
        assert not ph.similar("", "")
        assert not ph.similar("", "abc")
        assert ph.similar("ab", "ab")                 # identical short keys still match
        assert not ph.similar("ab", "ac")             # one slip in two letters is another name
        assert key("", "en") == "" and key("!!!", "en") == ""

    def test_threshold_edge(self):
        # 1 edit in 5 letters is 0.2: exactly the limit, allowed. 1 in 4 is 0.25: refused.
        assert ph.similar("sakul", "sakun", "ja")
        assert not ph.similar("saku", "sakn", "ja")
        assert ph.key_distance("sakul", "sakun") == pytest.approx(0.2)

    def test_tail_edits_are_cheaper_than_inner_ones(self):
        assert ph.key_distance("yalo", "yaloku") < ph.key_distance("yalo", "yamoku")
        assert ph.key_distance("yalo", "yaloku") == pytest.approx(ph.TAIL_EDIT_COST * 2 / 6)

    def test_distance_is_symmetric_and_bounded(self):
        for a, b in [("yalo", "yaloku"), ("abc", "xyz"), ("", "a")]:
            assert ph.key_distance(a, b) == ph.key_distance(b, a)
            assert 0 <= ph.key_distance(a, b) <= 1


class TestAvailability:
    def test_korean_and_latin_need_nothing(self):
        assert ph.availability("ko")["available"] and ph.availability("en")["available"]

    def test_missing_library_reports_a_hint_instead_of_crashing(self, monkeypatch):
        import builtins
        real = builtins.__import__

        def fake(name, *a, **k):
            if name in ("pykakasi", "pypinyin"):
                raise ImportError(name)
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake)
        ph._kanji_to_hira.cache_clear()
        ph._hanzi_pinyin.cache_clear()
        monkeypatch.setattr(ph, "_KAKASI", None)
        for lang, pkg in (("ja", "pykakasi"), ("zh", "pypinyin")):
            status = ph.availability(lang)
            assert status["available"] is False and pkg in status["hint"]
        with pytest.raises(ph.PhoneticUnavailable):
            ph.phonetic_key("山田", "ja")
        with pytest.raises(ph.PhoneticUnavailable):
            ph.phonetic_key("亚洛", "zh")
        assert ph.phonetic_key("ヤロ", "ja") == "yalo"   # kana alone needs no library
        ph._kanji_to_hira.cache_clear()
        ph._hanzi_pinyin.cache_clear()
