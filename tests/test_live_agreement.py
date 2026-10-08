import random

import pytest

import live_agreement
from live_agreement import Cue, StreamAgreement, Word
from live_tokens import agreement_tokens


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def w(text, start, end):
    return Word(text, start, end)


def feed(agreement, words, window_end, window_start=0.0):
    return agreement.feed(words, window_start, window_end)


def cue_text(cues):
    return "".join(c.text for c in cues)


class FakeASR:
    """Returns the scripted words that lie inside a window. Words ending
    near the window edge are unstable, like a real cut-off word."""

    def __init__(self, script, rng, jitter=0.03, unstable_edge=0.9):
        self.script = script
        self.rng = rng
        self.jitter = jitter
        self.unstable_edge = unstable_edge
        self.calls = 0

    def hypothesis(self, window_start, window_end):
        self.calls += 1
        out = []
        for word in self.script:
            if word.start < window_start - 0.05 or word.end > window_end:
                continue
            text = word.text
            if word.end > window_end - self.unstable_edge:
                text = f"乱{self.calls}"
            j = self.jitter
            out.append(Word(text, word.start + self.rng.uniform(-j, j), word.end + self.rng.uniform(-j, j)))
        return out


def make_script(rng, count=120):
    pool = "春夏秋冬山川花鳥風月雨雪光影梦海"
    words, t = [], 0.5
    for i in range(count):
        text = rng.choice(pool) + rng.choice(pool)
        if i % 7 == 6:
            text += "。"
        words.append(Word(text, t, t + 0.4))
        t += 0.5 + (rng.random() < 0.1) * rng.uniform(0.5, 3.0)
    return words


class TestAgreementTokens:
    def test_punctuation_and_case_ignored(self):
        assert agreement_tokens("Hello, World!") == agreement_tokens("hello world")
        assert agreement_tokens("北京。") == agreement_tokens("北京，")

    def test_nfkc_folds_width_variants(self):
        assert agreement_tokens("ｶﾀｶﾅ") == agreement_tokens("カタカナ")
        assert agreement_tokens("ＡＢＣ１２") == ["abc12"]

    def test_hangul_is_compared_by_character(self):
        assert agreement_tokens("안녕 하세요") == agreement_tokens("안녕하세요") == list("안녕하세요")

    def test_letters_stay_one_token_per_run(self):
        assert agreement_tokens("don't stop") == ["don", "t", "stop"]


class TestCommitRule:
    def test_commits_common_prefix_of_two_hypotheses_only(self):
        a = StreamAgreement(Clock())
        assert feed(a, [w("你好", 0, .5), w("世界", .5, 1)], 1.5) == []
        assert a.pending_text == "你好世界"
        feed(a, [w("你好", 0, .5), w("世界", .5, 1), w("朋友", 1, 1.5)], 2.5)
        assert a.open_text == "你好世界"
        assert a.pending_text == "朋友"
        assert a.window_start == 1

    def test_differing_punctuation_does_not_block_agreement(self):
        a = StreamAgreement(Clock())
        feed(a, [w("北京，", 0, .5)], 2)
        cues = feed(a, [w("北京。", 0, .5), w("很大", .5, 1)], 3)
        assert cue_text(cues) == "北京。"

    def test_display_text_comes_from_the_newer_hypothesis(self):
        a = StreamAgreement(Clock())
        feed(a, [w("Hello,", 0, .5)], 2)
        cues = feed(a, [w(" hello!", 0, .5), w(" there", .5, 1)], 3)
        assert cue_text(cues) == "hello!"

    def test_word_is_not_committed_until_all_its_tokens_agree(self):
        a = StreamAgreement(Clock())
        feed(a, [w("안녕하세여", 0, 1)], 3)
        feed(a, [w("안녕하세요", 0, 1)], 4)
        assert a.open_text == ""

    def test_hangul_respacing_still_agrees(self):
        a = StreamAgreement(Clock())
        feed(a, [w(" 안녕", 0, .5), w(" 하세요", .5, 1)], 3)
        feed(a, [w(" 안녕하세요", 0, 1), w(" 여러분", 1, 1.5)], 4)
        assert a.open_text == "안녕하세요"

    def test_committed_words_are_not_repeated_when_the_window_overlaps(self):
        a = StreamAgreement(Clock())
        feed(a, [w("你好", 0, .5)], 2)
        feed(a, [w("你好", 0, .5), w("世界。", .5, 1)], 3)
        feed(a, [w("你好", .02, .52), w("世界。", .5, 1.0), w("再见", 1, 1.5)], 4)
        cues = feed(a, [w("世界。", .52, 1.02), w("再见", 1, 1.5), w("朋友", 1.5, 2)], 5)
        assert cue_text(cues) == ""
        assert a.open_text == "再见"

    def test_empty_hypothesis_resets_agreement(self):
        a = StreamAgreement(Clock())
        feed(a, [w("你好", 0, .5)], 2)
        feed(a, [], 3)
        feed(a, [w("你好", 0, .5)], 4)
        assert a.open_text == ""


class TestForcedCommit:
    def flipping(self, a, clock, steps):
        cues = []
        for i in range(steps):
            clock.now += 1.5
            kana = i % 2 == 0
            words = [w("きょう" if kana else "今日", 0, .6), w("は", .6, .8), w("晴れ", .8, 1.4)]
            cues += feed(a, words, 10 + i * 1.5)
        return cues

    def test_kana_kanji_flip_stalls_agreement(self):
        clock = Clock()
        a = StreamAgreement(clock)
        self.flipping(a, clock, 3)  # 4.5 s of wall clock
        assert a.open_text == ""
        assert a.window_start == 0.0

    def test_stalled_words_are_force_committed_after_six_seconds(self):
        clock = Clock()
        a = StreamAgreement(clock)
        self.flipping(a, clock, 6)  # window end is far past the words
        assert a.open_text != ""
        assert a.window_start == 1.4

    def test_forced_commit_leaves_words_near_the_window_edge(self):
        clock = Clock()
        a = StreamAgreement(clock)
        feed(a, [w("甲", 4, 4.5), w("乙", 8.5, 9.4)], 10)
        clock.now += 7
        feed(a, [w("甲", 4, 4.5), w("乙", 8.5, 9.4)], 10)
        assert a.open_text == "甲乙"  # agreement, not the force, committed both
        a2 = StreamAgreement(clock)
        feed(a2, [w("甲", 4, 4.5), w("丙", 8.5, 9.4)], 10)
        clock.now += 7
        feed(a2, [w("甲", 4, 4.5), w("丁", 8.5, 9.4)], 10)
        assert a2.open_text == "甲"
        assert a2.pending_text == "丁"

    def test_full_window_forces_a_commit_without_waiting(self):
        a = StreamAgreement(Clock())
        feed(a, [w("甲", 0, .5), w("乙", 3, 3.5), w("丙", 14.5, 14.9)], 14.9)
        feed(a, [w("甲", 0, .5), w("丁", 3, 3.5), w("戊", 14.5, 14.9)], 15.0)
        assert a.open_text == "甲丁"
        assert a.window_start == 3.5


class TestLines:
    def agree(self, a, words, end):
        return feed(a, words, end) + feed(a, words, end + 1)

    def test_sentence_punctuation_closes_a_line(self):
        a = StreamAgreement(Clock())
        cues = self.agree(a, [w("你好。", 0, .5), w("再见", .5, 1)], 5)
        assert cues == [Cue("你好。", 0, .5)]
        assert a.open_text == "再见"

    def test_latin_sentence_end_needs_a_period_at_word_end(self):
        a = StreamAgreement(Clock())
        cues = self.agree(a, [w(" It", 0, .3), w(" costs", .3, .6), w(" 3.5", .6, .9), w(" now.", .9, 1.2)], 5)
        assert [c.text for c in cues] == ["It costs 3.5 now."]

    def test_silence_closes_the_line_and_confirms_the_tail(self):
        a = StreamAgreement(Clock())
        feed(a, [w("你好", 0, .5), w("世界", .5, 1)], 2)
        assert a.silence(0.6) == []
        assert a.pending_text == "你好世界"
        assert a.silence(0.7) == [Cue("你好世界", 0, 1)]
        assert a.pending_text == "" and a.open_text == ""
        assert a.silence(5) == []

    def test_cjk_cap_starts_a_new_line(self):
        a = StreamAgreement(Clock())
        words = [w("字" * 10, i, i + .5) for i in range(5)]
        cues = self.agree(a, words, 20)
        assert [len(c.text) for c in cues] == [40]
        assert a.open_text == "字" * 10

    def test_seven_second_cap_starts_a_new_line(self):
        a = StreamAgreement(Clock())
        words = [w("字", i * 2, i * 2 + .5) for i in range(6)]
        cues = self.agree(a, words, 20)
        assert [c.text for c in cues] == ["字字字字"]
        assert cues[0].end - cues[0].start <= live_agreement.LINE_MAX_SECONDS

    def test_finish_flushes_everything(self):
        a = StreamAgreement(Clock())
        feed(a, [w("你好", 0, .5)], 2)
        assert cue_text(a.finish()) == "你好"


class TestHallucination:
    def test_stock_phrase_hypothesis_never_commits(self):
        a = StreamAgreement(Clock())
        words = [w(" Thanks", 0, .4), w(" for", .4, .6), w(" watching", .6, 1)]
        for end in (3, 4.5, 6):
            assert feed(a, words, end) == []
        assert a.pending_text == ""
        assert a.finish() == []

    def test_japanese_outro_never_commits(self):
        a = StreamAgreement(Clock())
        for end in (3, 4.5):
            feed(a, [w("ご視聴ありがとうございました", 0, 2)], end)
        assert a.finish() == []

    def test_no_hypothesis_during_silence_commits_nothing(self):
        a = StreamAgreement(Clock())
        for t in (5, 10, 15):
            assert a.silence(t) == []


class TestAudioWindow:
    def test_window_starts_at_the_committed_end(self):
        a = StreamAgreement(Clock())
        feed(a, [w("你好", 0, 4)], 6)
        feed(a, [w("你好", 0, 4), w("世界", 4, 5)], 8)
        assert a.audio_window(12) == (4, 12)

    def test_window_is_capped_at_fifteen_seconds(self):
        assert StreamAgreement(Clock()).audio_window(40) == (25, 40)

    def test_window_is_at_least_two_seconds(self):
        a = StreamAgreement(Clock())
        feed(a, [w("你好", 0, 9.5)], 10)
        feed(a, [w("你好", 0, 9.5), w("世界", 9.5, 10)], 11)
        assert a.audio_window(11) == (9, 11)
        assert StreamAgreement(Clock()).audio_window(1) == (0.0, 1)


@pytest.mark.parametrize("seed", range(25))
def test_committed_text_equals_the_script_exactly_once(seed):
    rng = random.Random(seed)
    script = make_script(rng)
    asr = FakeASR(script, rng)
    clock = Clock()
    a = StreamAgreement(clock)
    cues = []
    t = 0.0
    end = script[-1].end + 3
    while t < end:
        hop = rng.choice([1.5, 1.5, 0.5, 2.5, 4.0, 9.0])
        t += hop
        clock.now += hop
        start, stop = a.audio_window(t)
        cues += a.feed(asr.hypothesis(start, stop), start, stop)
    cues += a.finish()
    assert cue_text(cues) == "".join(x.text for x in script)
    assert all(c.start <= c.end for c in cues)
    assert [c.start for c in cues] == sorted(c.start for c in cues)
