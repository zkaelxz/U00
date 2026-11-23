"""The blocked-phrases list drops stock lines Whisper invents (title 103 on the V100), but not the same words said for real."""
import asr_backend
import whisper_hallucination as wh
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


KEYS = wh.parse_blocked_phrases(wh.BLOCKED_PHRASES_DEFAULT)


def _texts(segs):
    return [s["text"] for s in segs]


def _heard(*spans):
    """A detector that hears `spans`, recording the windows it was asked about."""
    asked = []

    def detector(windows):
        asked.append(list(windows))
        return list(spans)
    detector.asked = asked
    return detector


def test_a_phrase_over_a_stretch_with_no_speech_is_dropped_and_real_speech_stays():
    segs = [_seg(REAL, 0.0), _seg(SUBSCRIBE, 4.0), _seg(REAL, 8.0)]
    kept = wh.drop_blocked_phrases(segs, KEYS, speech_spans=_heard((0.0, 2.0), (8.0, 10.0)))
    assert _texts(kept) == [REAL, REAL]


def test_a_streamer_saying_it_mid_stream_with_speech_around_it_is_kept():
    stream = [_seg(REAL, 0.0), _seg("好了兄弟们记得点赞和分享", 2.1), _seg(REAL, 4.2),
              _seg("点赞和分享", 6.3), _seg(REAL, 8.4)]
    assert wh.drop_blocked_phrases(stream, KEYS, speech_spans=_heard((0.0, 10.4))) == stream


def test_a_line_just_off_the_detected_speech_is_kept_since_whisper_times_drift():
    late = [_seg(REAL, 0.0), _seg("点赞和分享", 2.8)]
    assert wh.drop_blocked_phrases(late, KEYS, speech_spans=_heard((0.0, 2.0))) == late


def test_only_the_phrase_alone_in_silence_is_kept_when_the_detector_hears_speech_under_it():
    # A streamer pausing in a game, saying it, and pausing again: Whisper sees silence around it,
    # the detector hears the voice.
    lone = [_seg(REAL, 0.0), _seg("请点赞和分享", 12.0), _seg(REAL, 22.0)]
    assert wh.drop_blocked_phrases(lone, KEYS, speech_spans=_heard((0.0, 2.0), (12.2, 13.8), (22.0, 24.0))) == lone


def test_an_outro_made_up_over_silence_is_dropped():
    outro = [_seg(REAL, 0.0), _seg(REAL, 2.1), _seg("订阅我们的频道", 9.0), _seg("点赞和分享", 11.5)]
    detector = _heard((0.0, 4.1))
    assert _texts(wh.drop_blocked_phrases(outro, KEYS, speech_spans=detector)) == [REAL, REAL]
    # Asked once for the whole run, and only around the two lines that hold a phrase.
    assert detector.asked == [[(7.0, 13.0), (9.5, 15.5)]]


def test_the_detector_is_not_asked_when_no_line_holds_a_phrase():
    detector = _heard()
    assert wh.drop_blocked_phrases([_seg(REAL, 0.0)], KEYS, speech_spans=detector) == [_seg(REAL, 0.0)]
    assert detector.asked == []


def test_without_the_detector_only_the_phrase_alone_in_silence_is_dropped():
    close = [_seg(REAL, 0.0), _seg(SUBSCRIBE, 2.0), _seg(REAL, 4.0)]
    assert wh.drop_blocked_phrases(close, KEYS) == close
    assert wh.drop_blocked_phrases(close, KEYS, speech_spans=lambda windows: None) == close
    # A longer line alone in silence may be real: only the phrase itself (3 letters spare) goes.
    longer = [_seg(REAL, 0.0), _seg("好了兄弟们记得点赞和分享", 12.0)]
    assert wh.drop_blocked_phrases(longer, KEYS) == longer
    assert _texts(wh.drop_blocked_phrases([_seg(REAL, 0.0), _seg("请订阅我们的频道", 12.0)], KEYS)) == [REAL]
    near = [_seg(REAL, 0.0), _seg("订阅我们的频道", 3.0), _seg(REAL, 5.5)]
    assert wh.drop_blocked_phrases(near, KEYS) == near


def test_a_dropped_phrase_is_recorded_as_a_stock_phrase_drop():
    with wh.recording_drops() as box:
        wh.drop_blocked_phrases([_seg("点赞和分享", 0.0)], KEYS, speech_spans=_heard())
    assert [d["reason"] for d in box] == [wh.DROP_STOCK_PHRASE]


def test_an_edited_list_blocks_the_new_phrase_and_short_entries_are_ignored():
    keys = wh.parse_blocked_phrases("Thank you all\nok\n\n  ")
    assert keys == ("thankyouall",)
    kept = wh.drop_blocked_phrases([_seg("Thank you, all!", 0.0), _seg(AMARA, 3.0)], keys, speech_spans=_heard())
    assert _texts(kept) == [AMARA]


def test_an_empty_list_blocks_nothing():
    assert wh.drop_blocked_phrases([_seg(AMARA, 0.0)], wh.parse_blocked_phrases(""), speech_spans=_heard()) == [_seg(AMARA, 0.0)]


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


def _pipeline(tmp_path, monkeypatch, segments, detector, timing=None):
    """Lines of a Whisper run over `segments`, with `detector` standing in for the voice detector."""
    import vad_segments
    import whisper_models
    from services import transcribe_pipeline as tp
    asked = []
    monkeypatch.setattr(tp, "transcribe_for_timing", lambda *a, **k: [dict(s) for s in segments])
    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: None)
    monkeypatch.setattr(whisper_models, "get_whisper_device_info", lambda *a, **k: {})
    monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: None)
    monkeypatch.setattr(vad_segments, "speech_spans_in_file",
                        lambda path, windows=None: asked.append(windows) or detector)
    out = tp._transcribe_pipeline(
        tp._ThreadReporter(None), str(tmp_path / "a.wav"), "whisper", None, "zh",
        "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "",
        False, "whisper", "whisper_diff", timing=timing or timing_settings_service.defaults())
    return [ln.zh for ln in out["lines"]], asked


LIVESTREAM = [_seg("欢迎来到直播间今天我们继续打游戏", 0.0), _seg("觉得好看的记得点赞和分享", 2.5),
              _seg("我们现在开始第一关", 5.0), _seg("订阅我们的频道", 30.0)]


def test_a_run_keeps_the_streamers_own_words_and_drops_the_made_up_outro(tmp_path, monkeypatch):
    texts, asked = _pipeline(tmp_path, monkeypatch, LIVESTREAM, [(0.0, 7.0)])
    assert texts == [s["text"] for s in LIVESTREAM[:3]]
    assert len(asked) == 1


def test_a_difficult_audio_run_never_believes_the_detectors_silence(tmp_path, monkeypatch):
    from services import difficult_audio_service as difficult
    timing = {**timing_settings_service.defaults(), difficult.DETECTOR_UNTRUSTED: True}
    # The detector would hear nothing at all; the lines are judged by their gaps alone.
    texts, asked = _pipeline(tmp_path, monkeypatch, LIVESTREAM, [], timing=timing)
    assert asked == []
    assert texts == [s["text"] for s in LIVESTREAM[:3]]


def test_unreadable_audio_keeps_every_line_said_near_speech(tmp_path, monkeypatch):
    texts, asked = _pipeline(tmp_path, monkeypatch, LIVESTREAM, None)
    assert len(asked) == 1
    assert texts == [s["text"] for s in LIVESTREAM[:3]]


def test_every_difficult_audio_run_is_marked_detector_untrusted(tmp_path):
    from services import difficult_audio_service as difficult

    class _Rep:
        def progress(self, *a):
            pass

        def cancelled(self):
            return False
    timing = difficult.mark({"a": 1}, {"difficult_audio": 1, "separate_vocals_first": 1})
    for backend, mode, groq, mixed in (("whisper", "whisper", False, False), ("qwen3_asr_vad", "whisper", False, False),
                                       ("anime_whisper", "whisper", False, False), ("whisper", "whisper", False, True),
                                       ("whisper", "whisper", True, False)):
        prepared = difficult.prepare(_Rep(), str(tmp_path / "a.wav"), str(tmp_path), mode, backend, groq, mixed, timing)
        assert prepared.timing[difficult.DETECTOR_UNTRUSTED] is True, backend
    assert difficult.DETECTOR_UNTRUSTED not in difficult.prepare(
        _Rep(), str(tmp_path / "a.wav"), str(tmp_path), "whisper", "whisper", False, False, {"a": 1}).timing


def test_the_detector_hears_only_the_windows_it_is_given(monkeypatch):
    import sys
    import types
    import numpy as np
    import vad_segments
    sr = 16000
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(
        decode_audio=lambda path, sampling_rate: np.zeros(60 * sr, dtype=np.float32)))
    heard = []

    def silero(audio, rate, *a):
        heard.append(audio.shape[0] / rate)
        return [(0.5, 1.5)]
    monkeypatch.setattr(vad_segments, "_silero_spans", silero)
    spans = vad_segments.speech_spans_in_file("x.wav", [(10.0, 14.0), (12.0, 16.0), (50.0, 70.0)])
    assert heard == [6.0, 10.0]
    assert spans == [(10.5, 11.5), (50.5, 51.5)]
