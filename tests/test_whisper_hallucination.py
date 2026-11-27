"""Tests for dropping Whisper hallucinations: the segment filter, stock phrases, silence thresholds, difficult-audio and re-hearing rules."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import whisper_models

from whisper_hallucination import filter_hallucinated_segments


class TestFilterHallucinatedSegments:
    """Regression coverage for a real, well-documented Whisper failure
    mode: looping a short phrase across many separate segments on silence
    or background music. Each individual repeated segment can look
    perfectly confident on its own (faster-whisper's own per-segment
    compression_ratio/log_prob/no_speech thresholds don't catch it) --
    it's the run of identical segments that's the actual tell."""

    def _seg(self, start, end, text):
        return {"start": start, "end": end, "text": text}

    def test_a_dense_list_is_dropped_unless_lists_are_only_flagged(self):
        from whisper_hallucination import IMPLAUSIBLE_LIST_NOTE
        seg = self._seg(10.0, 11.5, "はい、はい、はい、はい、はい、はい")
        assert filter_hallucinated_segments([seg]) == []
        out = filter_hallucinated_segments([seg], drop_lists=False)
        assert [(s["text"], s["flag"], s["flag_note"]) for s in out] == [
            (seg["text"], "text_uncertain", IMPLAUSIBLE_LIST_NOTE)]

    def test_a_short_run_is_left_untouched(self):
        segments = [self._seg(0, 1, "hello"), self._seg(1, 2, "hello"),
                    self._seg(2, 3, "world")]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == segments

    def test_a_repeat_count_under_two_turns_the_run_collapse_off(self):
        segments = [self._seg(i, i + 1, "好的") for i in range(3)]
        for count in (0, 1):
            assert filter_hallucinated_segments(segments, min_repeat_count=count) == segments

    def test_a_one_character_answer_said_back_to_back_is_kept_and_flagged(self):
        segments = [self._seg(i * 0.6, i * 0.6 + 0.6, "有") for i in range(9)]
        result = filter_hallucinated_segments(segments)
        assert [s["text"] for s in result] == ["有"] * 9
        assert all(s.get("flag") == "text_uncertain" for s in result)

    def test_a_lowered_repeat_limit_still_collapses_a_one_character_run(self):
        segments = [self._seg(i, i + 0.5, "嗯") for i in range(3)]
        assert filter_hallucinated_segments(segments, min_repeat_count=3) == [segments[0]]

    def test_a_one_character_run_of_ten_is_a_loop(self):
        segments = [self._seg(i, i + 1, "有。") for i in range(10)]
        assert filter_hallucinated_segments(segments) == [segments[0]]

    def test_the_same_reply_minutes_apart_is_kept_and_sent_to_review(self):
        # Real replies, or a loop over music where each burst came out alone: kept, and checked.
        from whisper_hallucination import REPEAT_GAP_NOTE
        segments = [self._seg(i * 120, i * 120 + 0.8, "はい。") for i in range(4)]
        out = filter_hallucinated_segments(segments)
        assert [(s["start"], s["text"]) for s in out] == [(s["start"], s["text"]) for s in segments]
        assert [s.get("flag_note") for s in out] == [None] + [REPEAT_GAP_NOTE] * 3
        assert {s.get("flag") for s in out[1:]} == {"text_uncertain"}

    def test_a_reply_heard_now_and_then_too_few_times_to_loop_is_not_flagged(self):
        segments = [self._seg(i * 120, i * 120 + 0.8, "はい。") for i in range(3)]
        assert filter_hallucinated_segments(segments) == segments

    def test_a_long_gap_ends_a_run_and_the_close_copies_still_collapse(self, monkeypatch):
        import applog
        messages = []

        class _Log:
            def info(self, message):
                messages.append(message)
        monkeypatch.setattr(applog, "get_logger", lambda *a, **k: _Log())
        loop = [self._seg(i, i + 1, "请订阅") for i in range(5)]
        later = self._seg(300, 301, "请订阅")
        from whisper_hallucination import REPEAT_GAP_NOTE
        out = filter_hallucinated_segments(loop + [later])
        assert [s["start"] for s in out] == [0, 300]
        assert out[0].get("flag") is None and out[1].get("flag_note") == REPEAT_GAP_NOTE
        assert messages == ["repeat filter: 4 copies of one line removed after 0.0s (to 5.0s)"]

    def test_repeats_that_differ_only_in_punctuation_collapse(self):
        segments = [self._seg(i, i + 1, text) for i, text in
                    enumerate(["好的。", "好的", "好的！", "好的,"])] + [self._seg(4, 5, "再见")]
        assert filter_hallucinated_segments(segments) == [segments[0], segments[4]]

    def test_a_phrase_looped_inside_one_segment_is_cut_to_one_copy(self):
        seg = {**self._seg(0, 30, "多谢" * 8 + "。"), "words": [{"word": "x"}]}
        assert filter_hallucinated_segments([seg]) == [self._seg(0, 30, "多谢。")]

    def test_a_single_character_run_loops_only_at_a_higher_count(self):
        loop = self._seg(0, 30, "谢" * 22)
        laugh = self._seg(40, 41, "哈哈哈哈哈哈")
        assert filter_hallucinated_segments([loop, laugh]) == [self._seg(0, 30, "谢"), laugh]

    def test_a_loop_after_real_speech_keeps_the_speech(self):
        seg = self._seg(0, 30, "我们走吧，好不好，好不好，好不好，好不好，好不好")
        assert filter_hallucinated_segments([seg]) == [self._seg(0, 30, "我们走吧，好不好")]

    @pytest.mark.parametrize("text", [
        "100,000,000", "$1,000,000,000", "no no no no", "Go! Go! Go! Go!",
        "はい。はい。はい。はい。", "好的好的好的", "哈" * 8, "哈" * 9, "ㅋ" * 9, "啊" * 9])
    def test_real_speech_and_numbers_are_never_cut(self, text):
        seg = self._seg(0, 30, text)
        assert filter_hallucinated_segments([seg]) == [seg]

    def test_a_real_loop_is_cut_and_logged(self, monkeypatch):
        import applog
        messages = []

        class _Log:
            def info(self, message):
                messages.append(message)
        # A fake logger: the real one's propagation depends on which tests ran before.
        monkeypatch.setattr(applog, "get_logger", lambda *a, **k: _Log())
        seg = self._seg(12.0, 40.0, "谢谢你" * 30)

        assert filter_hallucinated_segments([seg]) == [self._seg(12.0, 40.0, "谢谢你")]
        assert len(messages) == 1
        assert "12.0-40.0s" in messages[0] and "87 characters" in messages[0]

    def test_a_short_repeat_inside_a_segment_is_real_speech(self):
        seg = self._seg(0, 3, "好的好的好的")
        assert filter_hallucinated_segments([seg]) == [seg]

    def test_a_long_run_is_collapsed_to_the_first_occurrence(self):
        segments = ([self._seg(i, i + 1, "see you later") for i in range(5)]
                    + [self._seg(5, 6, "real dialogue")])
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == [self._seg(0, 1, "see you later"),
                           self._seg(5, 6, "real dialogue")]

    def test_exactly_at_the_threshold_is_collapsed(self):
        segments = [self._seg(i, i + 1, "x") for i in range(4)]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == [self._seg(0, 1, "x")]

    def test_one_below_the_threshold_is_untouched(self):
        segments = [self._seg(i, i + 1, "x") for i in range(3)]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == segments

    def test_whitespace_differences_still_count_as_the_same_run(self):
        segments = [self._seg(0, 1, "hi there"), self._seg(1, 2, "hi  there"),
                    self._seg(2, 3, " hithere "), self._seg(3, 4, "hi there")]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == [self._seg(0, 1, "hi there")]

    def test_blank_segments_are_never_collapsed(self):
        """A stretch of real silence correctly produces no text at all --
        collapsing blanks would be pointless (there's nothing to dedupe)
        and could accidentally hide a legitimate run of blank segments."""
        segments = [self._seg(i, i + 1, "") for i in range(6)]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == segments

    def test_multiple_separate_runs_are_each_collapsed(self):
        segments = ([self._seg(i, i + 1, "a") for i in range(4)]
                    + [self._seg(4, 5, "real line")]
                    + [self._seg(5 + i, 6 + i, "b") for i in range(5)])
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == [self._seg(0, 1, "a"), self._seg(4, 5, "real line"),
                           self._seg(5, 6, "b")]

    def test_empty_input_returns_empty(self):
        assert filter_hallucinated_segments([]) == []

    def test_default_threshold_is_four(self):
        import inspect
        params = inspect.signature(filter_hallucinated_segments).parameters
        assert params["min_repeat_count"].default == 4


class TestDropRecord:
    """What the filters remove is kept for the raw transcript, so the speech coverage
    check can name the filter instead of saying Whisper heard nothing."""

    def test_each_removed_segment_is_recorded_with_its_reason(self):
        from whisper_hallucination import recording_drops
        loop = [{"start": 10.0 + i, "end": 10.8 + i, "text": "谢谢"} for i in range(5)]
        outro = [{"start": 60.0, "end": 62.0, "text": "Thanks for watching!"}]
        with recording_drops() as dropped:
            kept = filter_hallucinated_segments([{"start": 0.0, "end": 2.0, "text": "你好"}, *loop, *outro])
        assert [s["text"] for s in kept] == ["你好", "谢谢"]
        assert [(d["start"], d["reason"]) for d in dropped] == [
            (11.0, "repeat"), (12.0, "repeat"), (13.0, "repeat"), (14.0, "repeat"), (60.0, "stock_phrase")]
        assert dropped[-1]["text"] == "Thanks for watching!"

    def test_a_filter_outside_a_run_records_nothing(self):
        from whisper_hallucination import recording_drops
        loop = [{"start": float(i), "end": i + 0.8, "text": "谢谢"} for i in range(5)]
        filter_hallucinated_segments(loop)
        with recording_drops() as dropped:
            pass
        assert dropped == []


class TestStockPhraseFilter:
    def _seg(self, start, end, text):
        return {"start": start, "end": end, "text": text}

    @staticmethod
    def _kept_and_flagged(result, original, flagged_index):
        """Every line survives with its text and times; only the one at `flagged_index` gains a flag."""
        plain = [{k: v for k, v in seg.items() if k not in ("flag", "flag_note")} for seg in result]
        return (plain == original and result[flagged_index].get("flag") == "text_uncertain"
                and all(not seg.get("flag") for i, seg in enumerate(result) if i != flagged_index))

    def test_credit_like_wordings_next_to_speech_are_kept_but_flagged(self):
        for text in ("本期影片就到這裏,謝謝觀看,記得訂閱、按讚、分享及分享唷!",
                     "為了獲得最新的影片請訂閱、按讚、分享及分享唷",
                     "So at the end of each video, please subscribe, press the like button, and share it with your friends.",
                     "字幕 by 沛隊字幕組"):
            segments = [self._seg(0, 2, "real line"), self._seg(2.5, 5, text), self._seg(5.5, 7, "another line")]
            assert self._kept_and_flagged(filter_hallucinated_segments(segments), segments, 1), text

    def test_a_sentence_that_only_mentions_subscribing_is_not_flagged(self):
        segments = [self._seg(0, 2, "real line"),
                    self._seg(2.5, 5, "I told him to subscribe to my channel if he liked the show"),
                    self._seg(5.5, 7, "another line")]
        assert filter_hallucinated_segments(segments) == segments

    def test_a_line_in_cyrillic_or_greek_is_flagged_not_dropped(self):
        segments = [self._seg(0, 2, "real line"), self._seg(2.5, 5, "косmos 日、月"), self._seg(5.5, 7, "another line")]
        assert self._kept_and_flagged(filter_hallucinated_segments(segments), segments, 1)
        mixed = [self._seg(0, 2, "碰撞一下Gordon Ramsay")]
        assert filter_hallucinated_segments(mixed) == mixed

    def test_isolated_stock_phrases_are_dropped(self):
        for text in ("Subtitles by the Amara.org community", "ご視聴ありがとうございました",
                     "请不吝点赞 订阅 转发 打赏", "字幕由Amara.org社群提供", "由 Amara.org 社群提供的字幕",
                     "MBC 뉴스 이덕영입니다", "Thanks for watching!", "字幕志愿者 李宗盛",
                     "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
                     "优优独播剧场——YoYo Television Series Exclusive",
                     "中文字幕——Yo Television Series Exclusive", "チャンネル登録よろしくお願いします",
                     "구독과 좋아요 부탁드립니다", "Like and subscribe.",
                     "Don't forget to subscribe to the channel."):
            segments = [self._seg(0, 2, "real line"), self._seg(30, 32, text),
                        self._seg(60, 62, "another line")]
            assert filter_hallucinated_segments(segments) == [segments[0], segments[2]], text

    def test_the_long_subscribe_outro_is_dropped_but_a_sentence_using_the_words_is_not(self):
        outro = "Please subscribe to my channel and click the bell icon to be notified."
        segments = [self._seg(0, 2, "real line"), self._seg(30, 34, outro), self._seg(60, 62, "another line")]
        assert filter_hallucinated_segments(segments) == [segments[0], segments[2]]
        speech = [self._seg(0, 2, "I told him to subscribe to my channel if he liked the show"),
                  self._seg(30, 32, "Please subscribe to my channel")]
        assert filter_hallucinated_segments(speech)[0] == speech[0]

    def test_a_stock_phrase_at_the_end_of_the_audio_is_dropped(self):
        segments = [self._seg(0, 2, "real line"), self._seg(40, 42, "Thanks for watching")]
        assert filter_hallucinated_segments(segments) == [segments[0]]

    def test_a_chinese_thanks_for_watching_between_speech_or_inside_a_sentence_is_kept(self):
        near = [self._seg(0, 2, "real line"), self._seg(2.5, 4, "感谢观看"), self._seg(4.5, 6, "another line")]
        assert self._kept_and_flagged(filter_hallucinated_segments(near), near, 1)
        inside = [self._seg(0, 2, "real line"), self._seg(30, 33, "感谢观看我们的节目，我们下次见"),
                  self._seg(60, 62, "another line")]
        assert filter_hallucinated_segments(inside) == inside

    def test_a_stock_phrase_inside_dialogue_is_kept(self):
        segments = [self._seg(0, 2, "real line"), self._seg(2.5, 4, "Thanks for watching"),
                    self._seg(4.5, 6, "another line")]
        assert self._kept_and_flagged(filter_hallucinated_segments(segments), segments, 1)

    def test_silent_on_one_side_only_is_kept(self):
        segments = [self._seg(0, 2, "real line"), self._seg(2.5, 4, "Thanks for watching"),
                    self._seg(60, 62, "another line")]
        assert self._kept_and_flagged(filter_hallucinated_segments(segments), segments, 1)

    def test_the_traditional_and_by_credit_wordings_are_stock_phrases(self):
        for text in ("請不吝點贊 訂閱 轉發 打賞支持明鏡與點點欄目", "字幕by索兰娅", "感谢观看", "謝謝觀看。"):
            segments = [self._seg(0, 2, "real line"), self._seg(30, 32, text), self._seg(60, 62, "another line")]
            assert filter_hallucinated_segments(segments) == [segments[0], segments[2]], text

    def test_real_dialogue_containing_the_words_is_kept_even_when_isolated(self):
        segments = [self._seg(0, 2, "real line"),
                    self._seg(30, 33, "He said thanks for watching over her all those years"),
                    self._seg(60, 62, "another line")]
        assert filter_hallucinated_segments(segments) == segments


class TestHallucinationSilenceThreshold:
    def _run(self, model_cls, **kwargs):
        import sys, types
        import whisper_transcribe
        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: model_cls()
        sys.modules["faster_whisper"] = fake_fw
        whisper_models._whisper_model_cache.clear()
        return whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", **kwargs)

    def test_off_by_default_and_overridable(self):
        seen = {}

        class Model:
            def transcribe(self, audio_path, **kwargs):
                seen.clear()
                seen.update(kwargs)
                return iter([]), None

        self._run(Model)
        assert "hallucination_silence_threshold" not in seen
        self._run(Model, hallucination_silence_sec=3.5)
        assert seen["hallucination_silence_threshold"] == 3.5

    def test_zero_does_not_pass_it(self):
        seen = {}

        class Model:
            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([]), None

        self._run(Model, hallucination_silence_sec=0)
        assert "hallucination_silence_threshold" not in seen

    def test_an_older_faster_whisper_without_the_parameter_still_runs(self):
        seen = {}

        class OldModelStrict:
            def transcribe(self, audio_path, language=None, vad_filter=False, beam_size=5,
                           vad_parameters=None, word_timestamps=False,
                           condition_on_previous_text=True, no_repeat_ngram_size=0,
                           repetition_penalty=1.0, initial_prompt=None):
                seen["ok"] = True
                return iter([]), None

        self._run(OldModelStrict)
        assert seen == {"ok": True}


class TestDifficultAudioSkipRule:
    """Difficult audio hands faster-whisper a relaxed skip rule; a default run hands it nothing new."""

    def _run(self, model_cls, **kwargs):
        return TestHallucinationSilenceThreshold()._run(model_cls, **kwargs)

    @staticmethod
    def _recording_model(seen):
        class Model:
            def transcribe(self, audio_path, **kwargs):
                seen.clear()
                seen.update(kwargs)
                return iter([]), None
        return Model

    def test_a_default_run_passes_neither_threshold(self):
        seen = {}
        self._run(self._recording_model(seen))
        assert "no_speech_threshold" not in seen and "log_prob_threshold" not in seen
        assert set(seen) == {"language", "vad_filter", "beam_size", "vad_parameters",
                             "word_timestamps", "condition_on_previous_text"}

    def test_difficult_audio_adds_the_two_thresholds_and_turns_the_detector_off(self):
        import sensitivity_preset
        base, relaxed = {}, {}
        self._run(self._recording_model(base))
        self._run(self._recording_model(relaxed), difficult_audio=True)
        assert {k: v for k, v in relaxed.items() if k not in base} == {
            "no_speech_threshold": sensitivity_preset.DIFFICULT_NO_SPEECH_THRESHOLD,
            "log_prob_threshold": sensitivity_preset.DIFFICULT_LOG_PROB_THRESHOLD}
        assert base["vad_filter"] is True and relaxed["vad_filter"] is False
        assert {k: v for k, v in relaxed.items() if k in base and k != "vad_filter"} == {
            k: v for k, v in base.items() if k != "vad_filter"}

    def test_fast_mode_keeps_the_detector_its_batches_are_cut_by(self):
        import sys
        import types
        import whisper_transcribe
        seen = {}

        class Batched:
            def __init__(self, model):
                pass

            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([]), None
        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: self._recording_model({})()
        fake_fw.BatchedInferencePipeline = Batched
        sys.modules["faster_whisper"] = fake_fw
        whisper_models._whisper_model_cache.clear()
        whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", difficult_audio=True, fast_mode=True)
        assert seen["vad_filter"] is True

    def test_the_numbers_can_be_overridden(self):
        seen = {}
        self._run(self._recording_model(seen), difficult_audio=True,
                  no_speech_threshold=0.95, log_prob_threshold=-2.0)
        assert (seen["no_speech_threshold"], seen["log_prob_threshold"]) == (0.95, -2.0)

    def test_thresholds_are_ignored_without_the_option(self):
        seen = {}
        self._run(self._recording_model(seen), no_speech_threshold=0.95, log_prob_threshold=-2.0)
        assert "no_speech_threshold" not in seen and "log_prob_threshold" not in seen

    def test_a_faster_whisper_that_lacks_a_threshold_is_not_given_it(self):
        seen = {}

        class OnlyLogProb:
            def transcribe(self, audio_path, language=None, vad_filter=False, beam_size=5,
                           vad_parameters=None, word_timestamps=False,
                           condition_on_previous_text=True, no_repeat_ngram_size=0,
                           repetition_penalty=1.0, log_prob_threshold=-1.0):
                seen["log_prob_threshold"] = log_prob_threshold
                return iter([]), None

        self._run(OnlyLogProb, difficult_audio=True)
        assert seen == {"log_prob_threshold": -1.5}

    def test_the_sensitivity_preset_argument_reaches_the_decode_settings(self, monkeypatch):
        import sensitivity_preset
        given = []
        real = sensitivity_preset.decode_kwargs
        monkeypatch.setattr(sensitivity_preset, "decode_kwargs",
                            lambda preset, *a, **k: given.append(preset) or real(preset, *a, **k))
        seen = {}

        class Model:
            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([]), None
        self._run(Model, sensitivity_preset="sensitive", difficult_audio=True)
        assert given == ["sensitive"]
        assert "no_repeat_ngram_size" not in seen and "repetition_penalty" not in seen

    def test_fast_mode_keeps_the_batched_pipelines_own_rule(self, monkeypatch):
        import sys, types
        seen = {}

        class Batched:
            def __init__(self, model):
                pass

            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([]), None
        sys.modules["faster_whisper"] = types.SimpleNamespace(BatchedInferencePipeline=Batched)

        class Model:
            def transcribe(self, audio_path, **kwargs):
                return iter([]), None
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: Model())
        import whisper_transcribe
        whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", fast_mode=True, difficult_audio=True)
        assert "no_speech_threshold" not in seen and "log_prob_threshold" not in seen

    def test_repeat_filter_still_runs_on_the_brought_back_lines(self):
        import types

        class Seg:
            def __init__(self, start, text):
                self.start, self.end, self.text, self.words = start, start + 1, text, None

        class Model:
            def transcribe(self, audio_path, **kwargs):
                return iter([Seg(i * 2, "谢谢") for i in range(6)]), types.SimpleNamespace(duration=12)

        # A run of six identical lines collapses to its first.
        assert [x["text"] for x in self._run(Model, difficult_audio=True)] == ["谢谢"]


class TestRehearSparse:
    """A segment that spans a long stretch with almost no text is Whisper having skipped
    speech; that stretch is heard again on its own, without the speech detector."""

    class Seg:
        def __init__(self, start, end, text):
            self.start, self.end, self.text, self.words = start, end, text, None

    def _run(self, first, again=(), sig="kwargs", **kwargs):
        calls = []
        Seg = self.Seg

        def transcribe(audio_path, **kw):
            calls.append(kw)
            return iter([Seg(*s) for s in (again if "clip_timestamps" in kw else first)]), None

        class Model:
            pass
        if sig == "kwargs":
            Model.transcribe = lambda self, audio_path, **kw: transcribe(audio_path, **kw)
        else:  # a faster-whisper from before clip_timestamps
            Model.transcribe = lambda self, audio_path, language=None, vad_filter=False, beam_size=5, \
                vad_parameters=None, word_timestamps=False, condition_on_previous_text=True: transcribe(
                    audio_path, language=language, vad_filter=vad_filter)
        out = TestHallucinationSilenceThreshold()._run(Model, **kwargs)
        return [(s["start"], s["end"], s["text"]) for s in out], calls

    def test_a_skipped_stretch_is_heard_again_without_the_detector(self):
        out, calls = self._run(
            first=[(0.0, 2.0, "我的肉给我好吗"), (3.0, 37.0, "吃肉只是不记得")],
            again=[(3.5, 5.0, "我花了两百多块买"), (6.0, 7.0, "我要吃"), (35.0, 37.0, "吃肉只是不记得")],
            language="zh", beam_size=7)
        assert out == [(0.0, 2.0, "我的肉给我好吗"), (3.5, 5.0, "我花了两百多块买"), (6.0, 7.0, "我要吃"),
                       (35.0, 37.0, "吃肉只是不记得")]
        first, again = calls
        assert again["clip_timestamps"] == [3.0, 37.0] and again["vad_filter"] is False
        assert "vad_parameters" not in again
        assert {k: v for k, v in again.items() if k not in ("clip_timestamps", "vad_filter")} == {
            k: v for k, v in first.items() if k not in ("vad_parameters", "vad_filter")}

    def test_the_line_is_kept_when_hearing_it_again_finds_no_more(self):
        out, calls = self._run(first=[(0.0, 20.0, "好吃")], again=[(1.0, 2.0, "好")])
        assert out == [(0.0, 20.0, "好吃")] and len(calls) == 2

    def test_lines_with_their_fill_of_text_are_not_heard_again(self):
        out, calls = self._run(first=[(0.0, 3.0, "好"), (5.0, 12.0, "你把我的拖把拿去哪里了还给我")])
        assert len(calls) == 1 and len(out) == 2

    def test_only_the_sparse_stretches_are_sent(self):
        _, calls = self._run(first=[(0.0, 10.0, "啊"), (10.0, 12.0, "好"), (20.0, 40.0, "嗯")])
        assert calls[1]["clip_timestamps"] == [0.0, 10.0, 20.0, 40.0]

    def test_a_segment_at_the_shared_edge_of_two_sparse_stretches_is_kept_once(self):
        out, _ = self._run(first=[(0.0, 10.0, "啊"), (10.0, 20.0, "嗯")],
                           again=[(9.8, 10.3, "好的呀"), (12.0, 14.0, "我要吃饭了现在")])
        assert [t for _, _, t in out].count("好的呀") == 1

    def test_a_stretch_the_words_narrowed_is_still_heard_again(self):
        import types
        word = types.SimpleNamespace(start=0.0, end=1.0, word="好")

        class Narrow(self.Seg):
            def __init__(self, start, end, text):
                super().__init__(start, end, text)
                self.words = [word]

        # Whisper opened 0-40 s for one word; the word times alone make it look 1 s long.
        calls = []

        class Model:
            def transcribe(self, audio_path, **kw):
                calls.append(kw)
                if "clip_timestamps" in kw:
                    return iter([self_seg(2.0, 6.0, "后面还说了很多话呢")]), None
                return iter([Narrow(0.0, 40.0, "好")]), None

        self_seg = self.Seg
        out = TestHallucinationSilenceThreshold()._run(Model)
        assert calls[1]["clip_timestamps"] == [0.0, 40.0]
        assert [s["text"] for s in out] == ["后面还说了很多话呢"]
        assert not any("_span" in s for s in out)

    def test_a_stock_sentence_heard_again_is_not_taken_for_the_speech(self):
        out, _ = self._run(first=[(0.0, 2.0, "我的肉给我好吗"), (3.0, 37.0, "吃肉只是不记得")],
                           again=[(3.5, 30.0, "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目")])
        assert out == [(0.0, 2.0, "我的肉给我好吗"), (3.0, 37.0, "吃肉只是不记得")]

    def test_a_stock_sentence_over_a_stretch_is_replaced_by_what_is_heard_again(self):
        out, _ = self._run(first=[(0.0, 2.0, "我的肉给我好吗"), (2.5, 20.0, "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目")],
                           again=[(3.0, 5.0, "拜托给你两百块重买吧")])
        assert out == [(0.0, 2.0, "我的肉给我好吗"), (3.0, 5.0, "拜托给你两百块重买吧")]

    def test_a_faster_whisper_without_clip_timestamps_keeps_the_lines(self):
        out, calls = self._run(first=[(0.0, 20.0, "好吃")], sig="old")
        assert out == [(0.0, 20.0, "好吃")] and len(calls) == 1


class TestTranscribeForTimingHallucinationFilter:
    def _stub_faster_whisper(self, texts):
        import sys, types

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeModel:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, audio_path, **kwargs):
                def gen():
                    for i, t in enumerate(texts):
                        yield FakeSegment(float(i), float(i + 1), t)
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        sys.modules["faster_whisper"] = fake_fw

    def test_hallucination_filter_is_applied_by_default(self):
        import whisper_transcribe
        whisper_models._whisper_model_cache.clear()
        self._stub_faster_whisper(["see you later"] * 5 + ["real line"])

        result = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3")

        assert result == [{"start": 0.0, "end": 1.0, "text": "see you later"},
                           {"start": 5.0, "end": 6.0, "text": "real line"}]

    def test_filter_can_be_disabled(self):
        import whisper_transcribe
        whisper_models._whisper_model_cache.clear()
        self._stub_faster_whisper(["thanks for watching"] * 5 + ["real line"])

        result = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", filter_hallucination_repeats=0)

        assert len(result) == 6  # nothing collapsed


class TestPunctuationOnlySegmentsDropped:
    def test_a_lone_bracket_is_dropped_but_real_and_tag_lines_stay(self):
        import sys, types
        import whisper_transcribe

        class Seg:
            def __init__(self, start, text):
                self.start, self.end, self.text, self.words = start, start + 1.0, text, None

        class Model:
            def transcribe(self, audio_path, **kwargs):
                texts = ["[", "你好", "...", "[Music]", "  —  ", "こんにちは。", "7"]
                return iter([Seg(float(i), t) for i, t in enumerate(texts)]), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: Model()
        sys.modules["faster_whisper"] = fake_fw
        whisper_models._whisper_model_cache.clear()

        got = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3")

        assert [g["text"] for g in got] == ["你好", "[Music]", "こんにちは。", "7"]


class TestLaughsAreNotFlagged:
    def _flags(self, text, start=4.28, end=4.56):
        out = filter_hallucinated_segments([{"start": start, "end": end, "text": text}])
        return [s.get("flag") for s in out]

    @pytest.mark.parametrize("text", ["哈哈哈", "哈哈哈哈哈哈哈哈哈！", "呵呵呵", "嗯嗯嗯", "あはは", "ㅋㅋㅋ"])
    def test_a_laugh_or_interjection_in_a_split_second_is_not_flagged(self, text):
        assert self._flags(text) == [None]
        assert self._flags(text, 4.0, 4.1) == [None]

    def test_a_fast_laugh_is_not_flagged_for_its_rate(self):
        assert self._flags("哈哈哈哈哈哈哈哈哈哈", 0.0, 0.6) == [None]

    @pytest.mark.parametrize("text", ["うん", "ああ", "おお", "ほんほん"])
    def test_kana_words_without_a_laughing_syllable_are_still_flagged(self, text):
        assert self._flags(text, 4.0, 4.1) == ["text_uncertain"]

    def test_a_long_run_of_interjections_is_still_flagged(self):
        assert self._flags("嗯啊" * 6, 4.0, 4.1) == ["text_uncertain"]

    def test_other_text_in_the_same_split_second_is_still_flagged(self):
        assert self._flags("今天这根本") == ["text_uncertain"]
        assert self._flags("哈哈今天") == ["text_uncertain"]
