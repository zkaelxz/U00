"""The blocked-phrases list drops stock lines Whisper invents (title 103 on the V100), even next to speech."""
import asr_backend
import whisper_hallucination as wh
import whisper_transcribe as wt
from lib import settings_schema
from services import settings_service, timing_settings_service
from tests.test_rehear_marks import _run

AMARA = "本集的字幕由 Amara.org 社群提供"
SUBSCRIBE = "在此之前,如果您有興趣的話,請點贊和分享,並且訂閱我們的頻道"
REAL = "我今天去了學校然後回家吃飯"


def _seg(text, start):
    return {"start": start, "end": start + 2.0, "text": text}


def test_the_default_text_in_settings_matches_the_module_default():
    assert settings_schema.BY_KEY["blocked_phrases"].default == wh.BLOCKED_PHRASES_DEFAULT


def test_the_amara_credit_with_an_opening_is_a_stock_phrase():
    assert wh.is_stock_phrase(AMARA)


def test_a_credit_next_to_speech_is_dropped_and_real_speech_stays():
    segs = [_seg(REAL, 0.0), _seg(AMARA, 2.0), _seg(SUBSCRIBE, 4.0), _seg("please like, share and subscribe", 6.0),
            _seg("Subtitles by Amara.org", 8.0), _seg(REAL, 10.0)]
    kept = wh.drop_blocked_phrases(segs, wh.parse_blocked_phrases(wh.BLOCKED_PHRASES_DEFAULT))
    assert [s["text"] for s in kept] == [REAL, REAL]


def test_an_edited_list_blocks_the_new_phrase_and_short_entries_are_ignored():
    keys = wh.parse_blocked_phrases("Thank you all\nok\n\n  ")
    assert keys == ("thankyouall",)
    kept = wh.drop_blocked_phrases([_seg("Thank you, all!", 0.0), _seg(AMARA, 3.0)], keys)
    assert [s["text"] for s in kept] == [AMARA]


def test_an_empty_list_blocks_nothing():
    assert wh.drop_blocked_phrases([_seg(AMARA, 0.0)], wh.parse_blocked_phrases("")) == [_seg(AMARA, 0.0)]


def test_a_stock_phrase_heard_again_is_not_written_into_a_thin_stretch():
    thin = {"start": 0.0, "end": 10.0, "text": "嗯"}
    out = _run([(1.0, 4.0, AMARA)], [thin])
    assert out == [thin]


def test_a_run_takes_the_saved_list(isolated_db):
    settings_service.set_settings({"blocked_phrases": "Thank you all"})
    tuning = timing_settings_service.asr_tuning(timing_settings_service.values())
    assert tuning.blocked_phrases == ("thankyouall",)
    settings_service.set_settings({"blocked_phrases": ""})
    assert timing_settings_service.asr_tuning(timing_settings_service.values()).blocked_phrases == ()


def test_a_run_with_no_saved_list_uses_the_default(isolated_db):
    assert timing_settings_service.asr_tuning().blocked_phrases == asr_backend.DEFAULT_TUNING.blocked_phrases
    assert wh.parse_blocked_phrases(wh.BLOCKED_PHRASES_DEFAULT) == asr_backend.DEFAULT_TUNING.blocked_phrases
