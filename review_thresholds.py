"""Which review-check thresholds apply to a title.

Drama dialogue is paced and has short pauses, so the defaults in
core.diagnose_line_coverage and translate_engines.smart_segment_lines are
tuned for it and stay the "default" profile. Streamer speech is faster and
has long silent stretches (reading chat, gameplay), so those same limits
flag most of a transcript and bury the lines that need a look; the
"streamer" profile only flags clear outliers.
"""
from dataclasses import dataclass

PROFILE_DEFAULT = "default"
PROFILE_STREAMER = "streamer"
_STREAMER_MODE = "streamer_vod"


@dataclass(frozen=True)
class ReviewThresholds:
    profile: str
    # core.diagnose_line_coverage
    gap_seconds: float
    long_duration_seconds: float
    # translate_engines.smart_segment_lines
    pacing_wpm: float
    too_long_margin: float
    short_min_seconds: float
    short_ratio: float

    def coverage_kwargs(self) -> dict:
        return {"gap_seconds": self.gap_seconds, "long_duration_seconds": self.long_duration_seconds}

    def pacing_kwargs(self) -> dict:
        return {"target_wpm": self.pacing_wpm, "too_long_margin": self.too_long_margin,
                "min_seconds": self.short_min_seconds, "short_ratio": self.short_ratio}


# Same numbers as the function defaults, so drama behaviour is unchanged.
DEFAULT_THRESHOLDS = ReviewThresholds(
    PROFILE_DEFAULT, gap_seconds=3.0, long_duration_seconds=12.0,
    pacing_wpm=160, too_long_margin=1.15, short_min_seconds=1.2, short_ratio=0.4)

# Judgement calls, not measured on real streams: ~200 wpm is ordinary
# excited English, a line may overrun its slot by 50% before it counts as
# unreadable, and a slot only reads as "a long pause" when most of a long
# line is silent. Gaps under 15 s are normal between chat reads.
STREAMER_THRESHOLDS = ReviewThresholds(
    PROFILE_STREAMER, gap_seconds=15.0, long_duration_seconds=20.0,
    pacing_wpm=200, too_long_margin=1.5, short_min_seconds=6.0, short_ratio=0.2)


def profile_of(drama) -> str:
    drama = drama or {}
    # source_service sets media_type too, but a title created with only one
    # of the two fields is still a streamer VOD.
    if _STREAMER_MODE in (drama.get("content_mode"), drama.get("media_type")):
        return PROFILE_STREAMER
    return PROFILE_DEFAULT


def thresholds_for(drama) -> ReviewThresholds:
    return STREAMER_THRESHOLDS if profile_of(drama) == PROFILE_STREAMER else DEFAULT_THRESHOLDS
