"""
services/asr_timing_service.py -- how well recognised lines sit on a reference's
lines in time: the matching and silence measures tools/asr_bench.py reports, and
the same measures per Benchmark Lab case. UI-free; reads no database or setting.

A "line" is {"start", "end", "text"} in seconds on one clock (a title's, or a
clip's). A hypothesis line covers a reference line when they overlap by
COVER_FRACTION of the shorter one.
"""
import statistics

import asr_backend
import vad_segments

# A hypothesis line "covers" a reference line when they overlap by this share of the shorter one.
COVER_FRACTION = 0.3
# A line with less than this share of its length over detected speech sits in silence.
SPEECH_UNDER_LINE_FRACTION = 0.2
# A start or end this close to the reference counts as right.
WITHIN_S = 0.3
# A Lab clip carries only a quarter second of its neighbours, so a line that reaches
# this far past the reference on both sides has swallowed speech that is not its own.
MERGED_EDGE_S = 0.2


def overlap(a: dict, b: dict) -> float:
    return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def covers(hyp: dict, ref: dict) -> bool:
    shorter = max(0.05, min(hyp["end"] - hyp["start"], ref["end"] - ref["start"]))
    return overlap(hyp, ref) >= COVER_FRACTION * shorter


def match(reference: list, lines: list) -> dict:
    """covering[i] = indexes of the hypothesis lines that cover reference line i."""
    return {i: [j for j, h in enumerate(lines) if covers(h, r)] for i, r in enumerate(reference)}


def speech_spans(wav: str, offset: float = 0.0):
    """Speech spans (seconds, plus `offset`) under the app's default detector, or None when
    the detector is not installed or fails."""
    try:
        spans = vad_segments.speech_spans(asr_backend.load_audio_16k(wav), 16000)
    except Exception:
        return None
    return [(offset + s.start_s, offset + s.end_s) for s in spans]


def count_in_silence(lines: list, spans) -> int | None:
    if spans is None:
        return None
    count = 0
    for ln in lines:
        length = max(ln["end"] - ln["start"], 1e-6)
        speech = sum(max(0.0, min(ln["end"], e) - max(ln["start"], s)) for s, e in spans)
        count += speech / length < SPEECH_UNDER_LINE_FRACTION
    return count


def case_timing(lines: list, ref_start: float, ref_end: float, spans=None) -> dict:
    """The timing of one clip holding one reference line: how far the best-covering
    line's edges are from the reference's (seconds, None when nothing covers it), and
    whether the reference was missed, split over several lines, or swallowed into one
    that runs past it. `silence` counts the lines in the clip that sit in silence
    (None without speech spans)."""
    ref = {"start": ref_start, "end": ref_end}
    covering = [ln for ln in lines if covers(ln, ref)]
    out = {"lines": len(lines), "missed": not covering, "split": len(covering) >= 2,
           "merged": False, "start_error_s": None, "end_error_s": None,
           "silence": count_in_silence(lines, spans)}
    if covering:
        best = max(covering, key=lambda ln: overlap(ln, ref))
        out["start_error_s"] = round(abs(best["start"] - ref_start), 3)
        out["end_error_s"] = round(abs(best["end"] - ref_end), 3)
        out["merged"] = (len(covering) == 1 and best["start"] <= ref_start - MERGED_EDGE_S
                         and best["end"] >= ref_end + MERGED_EDGE_S)
    return out


def summarise(timings: list) -> dict:
    """A run's timing over its cases' `case_timing` dicts (None for a case without
    reference times). Medians are milliseconds over the cases that were matched;
    `within` is the share of all timed cases whose start and end are both within
    WITHIN_S. {"cases": 0} when no case was timed."""
    timed = [t for t in timings if t]
    if not timed:
        return {"cases": 0}
    starts = [t["start_error_s"] for t in timed if t["start_error_s"] is not None]
    ends = [t["end_error_s"] for t in timed if t["end_error_s"] is not None]
    within = sum(1 for t in timed if t["start_error_s"] is not None
                 and t["start_error_s"] <= WITHIN_S and t["end_error_s"] <= WITHIN_S)
    silence = [t["silence"] for t in timed if t.get("silence") is not None]
    return {"cases": len(timed),
            "start_median_ms": round(statistics.median(starts) * 1000) if starts else None,
            "end_median_ms": round(statistics.median(ends) * 1000) if ends else None,
            "within": round(within / len(timed), 4),
            "merged": sum(1 for t in timed if t["merged"]),
            "split": sum(1 for t in timed if t["split"]),
            "missed": sum(1 for t in timed if t["missed"]),
            "silence": sum(silence) if silence else None}
