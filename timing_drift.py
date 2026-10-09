"""timing_drift.py -- do the lines' times agree with where the audio has speech?

Pure comparison of saved lines against speech spans (seconds, sorted, merged)
from a voice-activity pass; nothing here reads the database, the audio or
settings. Built for Qwen3-ASR runs without Whisper, whose text is good but
whose line times can sit in silence or run past the speech. The check only
looks at the speech/silence envelope, so it is deliberately conservative:
a threshold is crossed by a clear margin before a line is flagged, and a
file where the detector hears next to nothing is reported as broken audio
instead of flagging every line.
"""
from dataclasses import dataclass, field
from typing import Optional

TIMING_DRIFT_FLAG = "timing_drift"

# A line may start this long before the speech it covers (and end this long
# after it): Qwen3-ASR spans and word-timing trimming are never frame exact.
EARLY_START_S = 0.7
LATE_END_S = 0.7
# Below this share of a line's own span being speech, the line is mostly silence.
MIN_SPEECH_FRACTION = 0.4
# Two consecutive lines may touch; flag only a real overlap.
OVERLAP_TOLERANCE_S = 0.1
# Streamer VODs have music and noise the detector can take for speech, so every
# tolerance is relaxed by this factor and the note says the check is unsure.
CONSERVATIVE_FACTOR = 1.5
# When speech is under this share of the file the audio is treated as broken
# (silent, wrong track, corrupt) and no line is judged against it.
BROKEN_AUDIO_SPEECH_FRACTION = 0.02
# A snap moves an edge by at most this much; a bigger move needs a human.
SNAP_MAX_SHIFT_S = 3.0
# Kept on each side of the detected speech so word edges survive a snap.
SNAP_PAD_S = 0.1
# Smaller moves are not worth a one-click action.
SNAP_MIN_SHIFT_S = 0.05
MIN_SNAPPED_DURATION_S = 0.3

MUSIC_CAVEAT = "Speech detection can mistake music for speech; check by ear."
BROKEN_AUDIO_NOTICE = ("No speech was found in this audio, so no line timing was checked. "
                       "The audio may be silent, the wrong track or damaged.")


@dataclass
class Finding:
    line_id: int
    note: str
    # (start, end) the snap action would apply, or None when only a person can fix it.
    suggestion: Optional[tuple] = None


@dataclass
class DriftReport:
    findings: list = field(default_factory=list)
    checked: int = 0
    broken_audio: bool = False


def _overlapping(spans, start: float, end: float) -> list:
    return [s for s in spans if s[1] > start and s[0] < end]


def _speech_seconds(spans, start: float, end: float) -> float:
    return sum(min(end, e) - max(start, s) for s, e in spans)


def _seconds(value: float) -> str:
    return f"{value:.1f} s"


def _suggest(line, covering) -> Optional[tuple]:
    """Tightens the line to the speech it overlaps. Only ever shrinks it, so a
    snap can't create an overlap with a neighbour."""
    start = max(line.start, covering[0][0] - SNAP_PAD_S)
    end = min(line.end, covering[-1][1] + SNAP_PAD_S)
    if start - line.start > SNAP_MAX_SHIFT_S:
        start = line.start
    if line.end - end > SNAP_MAX_SHIFT_S:
        end = line.end
    if (start - line.start < SNAP_MIN_SHIFT_S and line.end - end < SNAP_MIN_SHIFT_S) \
            or end - start < MIN_SNAPPED_DURATION_S:
        return None
    return round(start, 2), round(end, 2)


def find_drift(lines, spans, audio_seconds: Optional[float], *, conservative: bool = False,
               dismissed=frozenset()) -> DriftReport:
    """Findings for the lines whose times disagree with `spans`.

    `lines` are core.Line objects; lines the user dismissed (by id), sound
    effect lines and lines without a usable span are not judged. Overlap is
    judged between neighbours in start order, and blamed on the earlier line,
    as flag_overlapping_lines does."""
    speech_total = sum(e - s for s, e in spans)
    if not spans or (audio_seconds and speech_total < BROKEN_AUDIO_SPEECH_FRACTION * audio_seconds):
        return DriftReport(broken_audio=True)

    factor = CONSERVATIVE_FACTOR if conservative else 1.0
    early, late = EARLY_START_S * factor, LATE_END_S * factor
    min_fraction = MIN_SPEECH_FRACTION / factor
    caveat = f" {MUSIC_CAVEAT}" if conservative else ""

    ordered = sorted((ln for ln in lines if not ln.sfx and ln.end > ln.start),
                     key=lambda ln: (ln.start, ln.idx))
    next_start = {a.id: b.start for a, b in zip(ordered, ordered[1:])}
    report = DriftReport(checked=len(ordered))
    for ln in ordered:
        if ln.id in dismissed:
            continue
        covering = _overlapping(spans, ln.start, ln.end)
        reasons = []
        if not covering:
            reasons.append("sits in silence (no speech detected)")
        else:
            lead = covering[0][0] - ln.start
            tail = ln.end - covering[-1][1]
            if lead > early:
                reasons.append(f"starts {_seconds(lead)} before speech")
            if tail > late:
                reasons.append(f"ends {_seconds(tail)} after speech")
            fraction = _speech_seconds(covering, ln.start, ln.end) / (ln.end - ln.start)
            if fraction < min_fraction:
                reasons.append(f"mostly silence ({fraction:.0%} speech)")
        over = ln.end - next_start.get(ln.id, ln.end)
        if over > OVERLAP_TOLERANCE_S:
            reasons.append(f"overlaps the next line by {_seconds(over)}")
        if reasons:
            text = "; ".join(reasons)
            report.findings.append(Finding(
                ln.id, f"{text[0].upper()}{text[1:]}.{caveat}",
                _suggest(ln, covering) if covering else None))
    return report
