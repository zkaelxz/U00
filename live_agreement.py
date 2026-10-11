"""LocalAgreement-2 for streaming recognition: turns a sequence of
overlapping Whisper hypotheses over a sliding window into committed
subtitle cues that are never revised and never repeated.

The caller owns the audio and Whisper; this object only sees words with
absolute stream times. It is pure so every timing rule can be tested with
a scripted recogniser and an injected clock.
"""
import re
import time
from dataclasses import dataclass

import core
from live_tokens import CJK_CHARS, HANGUL_CHARS, agreement_tokens

MIN_WINDOW_SECONDS = 2.0
MAX_WINDOW_SECONDS = 15.0

# Agreement needs two matching hypotheses, so a transcription that keeps
# flipping (kana vs kanji for the same sound) would never commit; this
# bounds how long speech can sit unshown.
FORCE_COMMIT_AGE_SECONDS = 6.0
# A word ending right at the window edge is probably cut mid-word, so
# even a forced commit leaves it for the next window.
FORCE_COMMIT_EDGE_GUARD_SECONDS = 1.0

LINE_CLOSE_SILENCE_SECONDS = 0.7
LINE_MAX_SECONDS = 7.0
LINE_MAX_CJK_CHARS = 40

_SENTENCE_END_RE = re.compile(r"(?:[。！？!?…]|\.)[\"'”’」』）)\]]*$")
_CJK_RE = re.compile(rf"[{CJK_CHARS}{HANGUL_CHARS}]")


@dataclass(frozen=True)
class Word:
    text: str  # keeps Whisper's leading space so Latin words join back correctly
    start: float
    end: float


@dataclass(frozen=True)
class Cue:
    text: str
    start: float
    end: float


def _cjk_count(words) -> int:
    return sum(len(_CJK_RE.findall(w.text)) for w in words)


def _cue_from(words) -> Cue:
    return Cue("".join(w.text for w in words).strip(), words[0].start, words[-1].end)


def _is_stock_hallucination(words) -> bool:
    text = "".join(w.text for w in words)
    segment = {"text": text, "start": words[0].start, "end": words[-1].end}
    return not core.filter_hallucinated_segments([segment])


class StreamAgreement:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._committed_end = 0.0
        self._tail = []  # newest hypothesis' words that are not committed yet
        self._tail_tokens = []
        self._tail_since = None  # clock time the oldest tail word first appeared
        self._open = []  # committed words of the line still being built

    @property
    def window_start(self) -> float:
        """Audio before this is final; the next window should begin here."""
        return self._committed_end

    @property
    def open_text(self) -> str:
        """Committed words of the line that has not closed yet."""
        return "".join(w.text for w in self._open).strip()

    @property
    def pending_text(self) -> str:
        """The not-yet-confirmed tail, for display only (never a cue)."""
        return "".join(w.text for w in self._tail).strip()

    def audio_window(self, stream_end: float):
        start = max(self._committed_end, stream_end - MAX_WINDOW_SECONDS)
        if stream_end - start < MIN_WINDOW_SECONDS:
            # Too little audio for Whisper to be reliable; the committed
            # words re-heard in the extra audio are dropped again in feed().
            start = max(0.0, stream_end - MIN_WINDOW_SECONDS)
        return start, stream_end

    def feed(self, words, window_start: float, window_end: float):
        """Takes one hypothesis for the audio [window_start, window_end]
        (words with absolute times) and returns the cues that closed
        because of what it committed."""
        # Audio older than the window can't be re-heard, so tail words
        # that fell out of it get their last chance now instead of being lost.
        cues = []
        while self._tail and self._tail[0].end <= window_start:
            cues += self._commit(self._tail[:1])
            self._set_tail(self._tail[1:])
        # Midpoint, not start: the same word re-timed by a few tenths of a
        # second must still count as already committed.
        words = [w for w in words if w.text.strip() and (w.start + w.end) / 2 >= self._committed_end]
        if words and _is_stock_hallucination(words):
            words = []
        if not words:
            self._set_tail([])
            return cues

        word_tokens = [agreement_tokens(w.text) for w in words]
        flat = [t for toks in word_tokens for t in toks]
        agreed = 0
        for old, new in zip(self._tail_tokens, flat):
            if old != new:
                break
            agreed += 1

        # Whole words only: committing half a word would freeze a spelling
        # the next hypothesis may still fix.
        count = 0
        seen = 0
        if agreed:
            for toks in word_tokens:
                if seen + len(toks) > agreed:
                    break
                seen += len(toks)
                count += 1

        now = self._clock()
        if self._tail_since is None:
            self._tail_since = now
        if (now - self._tail_since > FORCE_COMMIT_AGE_SECONDS
                or window_end - window_start >= MAX_WINDOW_SECONDS):
            safe = 0
            for w in words:
                if w.end > window_end - FORCE_COMMIT_EDGE_GUARD_SECONDS:
                    break
                safe += 1
            count = max(count, safe)

        cues += self._commit(words[:count])
        self._set_tail(words[count:], restart_clock=count > 0)
        return cues

    def silence(self, silent_for: float):
        """Reports VAD silence. No hypothesis is produced during silence, so
        a long enough one confirms the tail and closes the open line. Feed a
        final hypothesis covering the speech up to the silence first."""
        if silent_for < LINE_CLOSE_SILENCE_SECONDS:
            return []
        return self.finish()

    def finish(self):
        cues = self._commit(self._tail)
        self._set_tail([])
        if self._open:
            cues.append(_cue_from(self._open))
            self._open = []
        return cues

    def _set_tail(self, words, restart_clock=False):
        self._tail = list(words)
        self._tail_tokens = [t for w in words for t in agreement_tokens(w.text)]
        if not words:
            self._tail_since = None
        elif restart_clock:
            # Per-word first-seen times aren't tracked; restarting is
            # conservative and the window cap still bounds the wait.
            self._tail_since = self._clock()

    def _commit(self, words):
        cues = []
        for w in words:
            self._committed_end = max(self._committed_end, w.end)
            if self._open and (_cjk_count(self._open) + _cjk_count([w]) > LINE_MAX_CJK_CHARS
                               or w.end - self._open[0].start > LINE_MAX_SECONDS):
                cues.append(_cue_from(self._open))
                self._open = []
            self._open.append(w)
            if _SENTENCE_END_RE.search(w.text.strip()):
                cues.append(_cue_from(self._open))
                self._open = []
        return [c for c in cues if c.text]
