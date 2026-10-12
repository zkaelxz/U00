"""The "Source text looks off" check: pure rules built from real transcription
slips, negatives for ordinary short lines and Japanese/Korean script mix, and
the service writing flag/flag_note only."""
import pytest

from core import Line, line_from_row
from engine_backends.flag_reasons import flag_reason_label
from services import source_suspect_service
from services.service_errors import NotFoundError
from source_suspect import SUSPECT_FLAG, find_suspects, repeated_text, script_mix


def _lines(*texts, gap=0.0, speaker=None):
    out, t = [], 0.0
    for i, text in enumerate(texts):
        out.append(Line(idx=i, start=t, end=t + 2.0, zh=text, id=100 + i, speaker=speaker))
        t += 2.0 + gap
    return out


PUNCTUATED = ["你好。", "再见。", "我们走吧。", "好的。", "天气真好。"]


class TestSplitMidPhrase:
    def test_owner_example_flags_the_head_line(self):
        lines = _lines(*PUNCTUATED, "他不甘願的做起", "身。", "然後離開了。")
        found = find_suspects(lines, "zh")
        assert list(found) == [105]
        assert "身" in found[105]

    def test_normal_short_lines_do_not_flag(self):
        lines = _lines(*PUNCTUATED, "你去哪裡？", "嗯。", "我回家。", "好。")
        assert find_suspects(lines, "zh") == {}

    def test_interjection_after_unpunctuated_line_does_not_flag(self):
        lines = _lines(*PUNCTUATED, "你確定嗎", "嗯", "那就走吧。")
        assert find_suspects(lines, "zh") == {}

    def test_different_speakers_are_not_one_phrase(self):
        lines = _lines(*PUNCTUATED, "你確定了嗎", "是", "走吧。")
        lines[-2].speaker, lines[-3].speaker = "B", "A"
        assert find_suspects(lines, "zh") == {}

    def test_a_long_pause_is_not_a_split(self):
        lines = _lines(*PUNCTUATED, "他不甘願的做起", "身。", gap=3.0)
        assert find_suspects(lines, "zh") == {}

    def test_transcript_without_punctuation_is_left_alone(self):
        lines = _lines("你好", "再見", "我們走吧", "好的", "天氣真好", "他不甘願的做起", "身")
        assert find_suspects(lines, "zh") == {}

    def test_japanese_split_flags(self):
        lines = _lines("こんにちは。", "元気です。", "ありがとう。", "さようなら。", "またね。",
                       "彼女は静かに歩き出", "す。")
        assert list(find_suspects(lines, "ja")) == [105]


class TestRepeats:
    def test_character_run_flags(self):
        assert repeated_text("然後他說啊啊啊啊啊啊啊啊") is not None

    def test_phrase_loop_flags(self):
        assert repeated_text("我不知道我不知道我不知道我不知道") is not None

    @pytest.mark.parametrize("text", ["哈哈哈哈", "嗯嗯嗯。", "好好好", "えーーーー", "ああ……", "......", "ハハハハ"])
    def test_ordinary_repeats_do_not_flag(self, text):
        assert repeated_text(text) is None


class TestScriptMix:
    def test_hangul_and_mostly_latin_in_chinese_flag(self):
        assert script_mix("他說 안녕하세요 然後", "zh")
        assert script_mix("where are you going tonight 好", "zh")

    def test_short_latin_in_chinese_does_not_flag(self):
        assert script_mix("我用 iPhone 打電話給 CEO", "zh") is None
        assert script_mix("OK 沒問題", "zh") is None

    @pytest.mark.parametrize("text", ["これはCDです", "ありがとう、Tom さん", "今日の天気は良いですね。", "ハングル"])
    def test_japanese_lines_do_not_flag(self, text):
        assert script_mix(text, "ja") is None

    @pytest.mark.parametrize("text", ["안녕하세요", "오늘 meeting 있어요", "그는 漢字를 썼다"])
    def test_korean_lines_do_not_flag(self, text):
        assert script_mix(text, "ko") is None

    def test_kana_in_chinese_and_hangul_in_japanese_flag(self):
        assert script_mix("你好こんにちは", "zh")
        assert script_mix("こんにちは안녕", "ja")

    def test_a_line_marked_english_is_not_checked(self):
        lines = [Line(idx=0, start=0, end=1, zh="Hello there my friend", lang="en", id=1)]
        assert find_suspects(lines, "zh") == {}


def test_flag_has_a_reason_label():
    assert flag_reason_label(SUSPECT_FLAG).startswith("Source text looks off")


def _drama(db, language="zh"):
    return db.create_drama(title_en="T", source_language=language)


class TestService:
    def test_flags_only_flag_fields_and_keeps_other_flags(self, isolated_db):
        did = _drama(isolated_db)
        rows = _lines(*PUNCTUATED, "他不甘願的做起", "身。", "啊啊啊啊啊啊啊啊啊")
        for ln in rows:
            ln.id = None
        rows[-1].flag, rows[-1].flag_note = "timing_overlap", "overlaps"
        rows[0].en = "Hello."
        isolated_db.save_lines(did, rows)

        result = source_suspect_service.check_source_text(did)

        assert result == {"flagged_count": 1, "cleared_count": 0}
        saved = isolated_db.load_lines(did)
        assert [r["flag"] for r in saved][-3:] == [SUSPECT_FLAG, None, "timing_overlap"]
        assert [r["zh"] for r in saved] == [ln.zh for ln in rows]
        assert saved[0]["en"] == "Hello."

    def test_list_names_the_next_line_for_merging(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, _lines(*PUNCTUATED, "他不甘願的做起", "身。"))
        source_suspect_service.check_source_text(did)
        saved = isolated_db.load_lines(did)

        listed = source_suspect_service.list_suspects(did)

        assert listed["count"] == 1
        item = listed["items"][0]
        assert (item["line_id"], item["next_line_id"], item["next_zh"]) == (saved[5]["id"], saved[6]["id"], "身。")

    def test_recheck_clears_its_own_flag_after_a_fix(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, _lines(*PUNCTUATED, "他不甘願的做起", "身。"))
        source_suspect_service.check_source_text(did)
        lines = [line_from_row(r) for r in isolated_db.load_lines(did)]
        lines[5].zh = "他不甘願的做起身。"
        isolated_db.save_lines(did, lines, fields=("zh", "flag", "flag_note"))

        result = source_suspect_service.check_source_text(did)

        assert result["cleared_count"] == 1
        assert isolated_db.load_lines(did)[5]["flag"] is None

    def test_japanese_title_uses_its_own_script_rules(self, isolated_db):
        did = _drama(isolated_db, "ja")
        isolated_db.save_lines(did, _lines("これはCDです", "ありがとう、Tom さん"))
        assert source_suspect_service.check_source_text(did)["flagged_count"] == 0

    def test_unknown_drama_is_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            source_suspect_service.check_source_text(999999)
        with pytest.raises(NotFoundError):
            source_suspect_service.list_suspects(999999)
