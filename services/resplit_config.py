"""What a Review re-split cuts: the sensitivity presets, an optional duration cap, and the
fresh-transcription limits a line falls back to (Settings > Timing and splitting).

Moved out of restructure_service, which hands each line to `_Resplit.split`.
"""
import dataclasses
from typing import Optional

import long_line_split
import subtitle_formats
from segment_splitting import (DEFAULT_FLOORS, MIN_WORD_GAP_SECONDS, SPLIT_MAX_CJK_CHARS,
                               SPLIT_MAX_SECONDS, PieceFloors, SplitRules, line_words)

# Lines already past restructure_service.MAX_LINE_TEXT_CHARS (merged before it existed; ten
# times that) are not re-split by the rules presets, whose quote and abbreviation checks
# cost more per character.
RESPLIT_MAX_CHARS = 20_000

RESPLIT_SENSITIVITIES = {"more": "More", "sentence": "Sentence by sentence"}


@dataclasses.dataclass(frozen=True)
class _Resplit:
    """What to cut: the sensitivity preset, an optional duration cap and the
    title language lines fall back to. `line_seconds`, `max_cjk_chars` and
    `floors` are the fresh-transcription limits (Settings > Timing and splitting)."""
    language: str = "zh"
    sensitivity: Optional[str] = None
    max_seconds: Optional[float] = None
    min_pause: float = MIN_WORD_GAP_SECONDS
    line_seconds: float = SPLIT_MAX_SECONDS
    max_cjk_chars: int = SPLIT_MAX_CJK_CHARS
    floors: PieceFloors = DEFAULT_FLOORS

    @property
    def label(self) -> str:
        return RESPLIT_SENSITIVITIES.get(self.sensitivity, "Default limits") + (
            f", {self.max_seconds:g} s cap" if self.max_seconds else "")

    def rules(self, ln) -> Optional[SplitRules]:
        """None is the fresh-transcription limits (8 s / 40 CJK characters unless
        Settings changed them). The cap replaces the time limit rather than adding to
        it: a cap above it would otherwise do nothing."""
        if self.sensitivity is None and not self.max_seconds:
            return None
        seconds = self.max_seconds or self.line_seconds
        if self.sensitivity is None:
            return SplitRules(seconds, self.max_cjk_chars, count_latin=False)
        if self.sensitivity == "more":
            return SplitRules(
                seconds, subtitle_formats.line_char_limit(ln.lang or self.language))
        return SplitRules(self.max_seconds, None, per_sentence=True)

    def too_long(self, ln) -> bool:
        return len(ln.zh) > RESPLIT_MAX_CHARS and self.rules(ln) is not None

    def split(self, ln) -> list:
        """Pieces cut by split_long_segments; with the line's valid stored words
        it cuts at real pauses with real times and hands each piece its words."""
        seg = {"start": ln.start, "end": ln.end, "text": ln.zh}
        if self.too_long(ln):
            return [seg]
        words = line_words(ln)
        kw = {"rules": self.rules(ln), "min_pause": self.min_pause,
              "max_seconds": self.line_seconds, "max_cjk_chars": self.max_cjk_chars,
              "floors": self.floors}
        if not words:
            return long_line_split.split_long_segments([seg], **kw)
        pieces = long_line_split.split_long_segments([{**seg, "words": words}], **kw)
        # Word times tighten a piece to its speech, but the line's outer edges
        # may have been re-timed on purpose (by hand or a re-time run); the
        # Review split and the AI re-split keep them, so this does too.
        if len(pieces) >= 2:
            pieces[0] = {**pieces[0], "start": ln.start}
            pieces[-1] = {**pieces[-1], "end": ln.end}
        return pieces
