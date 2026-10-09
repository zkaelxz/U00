"""
services/transcribe_gap_service.py -- "Transcribe this gap" in Review: find the
stretches of a title no subtitle line covers, and fill one with blank, flagged
lines that the many-line re-transcription (retranscribe_many_service) then
hears.

Detection is the saved lines alone (no audio decoding, no GPU): a stretch
longer than MIN_GAP_SECONDS between the end of everything before it and the
next line's start. If the Transcribe stage's speech coverage check has run, a
gap it found speech in is marked `speech` true and the stretches it found
beyond the last line (or before the first) are added; without it `speech` is
None, never false, because a check that ran before a line was deleted would
make that stretch look silent.
"""
import math
from typing import Optional

import core as core_module
import db
from services import restructure_service as restructure
from services import speech_coverage_service, transcribe_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)

MIN_GAP_SECONDS = 2.0
# One transcription window per added line: Whisper does best on short windows,
# and a failed or misheard 30 s piece costs little to redo.
MAX_PIECE_SECONDS = 30.0
MIN_PIECE_SECONDS = 1.0
# 20 pieces; a longer stretch is added in parts so one request can't create
# hundreds of lines.
MAX_ADD_SECONDS = 600.0
MIN_ADD_SECONDS = 0.5
# Silences shorter than this are breaths, not places to cut.
MIN_PAUSE_SECONDS = 0.2
# Slack when checking that the stretch is still free: line times are stored at
# millisecond precision and the waveform rounds.
_EDGE_TOLERANCE_S = 0.05
# A coverage gap counts as the line gap's when they share at least this much.
_SPEECH_OVERLAP_S = 0.5
_COVERED_BY_LINE_S = 0.3
GAP_FLAG = "gap_untranscribed"
GAP_FLAG_NOTE = "Added for a stretch with no subtitle line; check the text"


def find_line_gaps(lines, min_gap: float = MIN_GAP_SECONDS) -> list:
    """Stretches between lines of at least `min_gap` seconds, as {start, end,
    after_line_id, before_line_id}. The cursor only moves forward over a
    line's end, so a line that overlaps or sits inside an earlier one leaves
    no gap under it. A line with no timing window covers nothing. The stretch
    before the first line counts (after_line_id None); the one after the last
    needs the audio length and comes from the coverage check instead."""
    gaps, cursor, last_id = [], 0.0, None
    for ln in sorted((ln for ln in lines if float(ln.end) > float(ln.start)),
                     key=lambda ln: (float(ln.start), float(ln.end))):
        if float(ln.start) - cursor >= min_gap:
            gaps.append({"start": cursor, "end": float(ln.start),
                         "after_line_id": last_id, "before_line_id": ln.id})
        if float(ln.end) > cursor:
            cursor, last_id = float(ln.end), ln.id
    return gaps


def _overlap(a_start, a_end, b_start, b_end) -> float:
    return min(a_end, b_end) - max(a_start, b_start)


def _coverage_gaps(drama_id: int) -> Optional[list]:
    report = speech_coverage_service.get_speech_coverage(drama_id)["result"]
    if not isinstance(report, dict) or not isinstance(report.get("gaps"), list):
        return None
    return [(float(g["start"]), float(g["end"])) for g in report["gaps"]]


def list_gaps(drama_id: int, min_gap: float = MIN_GAP_SECONDS) -> dict:
    """{gaps: [{start, end, seconds, pieces, after_line_id, before_line_id,
    speech}], speech_checked}. NotFoundError for an unknown drama."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    lines = db.load_line_objects(drama_id)
    gaps = find_line_gaps(lines, min_gap)
    coverage = _coverage_gaps(drama_id)
    for g in gaps:
        g["speech"] = (True if coverage is not None and any(
            _overlap(g["start"], g["end"], s, e) >= _SPEECH_OVERLAP_S for s, e in coverage)
            else None)
    if coverage:
        windowed = [ln for ln in lines if float(ln.end) > float(ln.start)]
        for s, e in coverage:
            if (e - s < min_gap
                    or any(_overlap(s, e, g["start"], g["end"]) > 0 for g in gaps)
                    or any(_overlap(s, e, float(ln.start), float(ln.end)) > _COVERED_BY_LINE_S
                           for ln in windowed)):
                continue
            before = [ln for ln in windowed if float(ln.end) <= s + _EDGE_TOLERANCE_S]
            after = [ln for ln in windowed if float(ln.start) >= e - _EDGE_TOLERANCE_S]
            gaps.append({"start": s, "end": e,
                         "after_line_id": max(before, key=lambda ln: float(ln.end)).id
                         if before else None,
                         "before_line_id": min(after, key=lambda ln: float(ln.start)).id
                         if after else None,
                         "speech": True})
        gaps.sort(key=lambda g: g["start"])
    for g in gaps:
        g["start"], g["end"] = round(g["start"], 3), round(g["end"], 3)
        g["seconds"] = round(g["end"] - g["start"], 3)
        g["pieces"] = max(1, math.ceil(g["seconds"] / MAX_PIECE_SECONDS - 1e-9))
    return {"gaps": gaps, "speech_checked": coverage is not None}


def _even_cuts(start: float, end: float, pieces: int) -> list:
    return [start + (end - start) * k / pieces for k in range(1, pieces)]


def split_gap(start: float, end: float, pauses=None,
              max_piece: float = MAX_PIECE_SECONDS) -> tuple:
    """([(start, end)], snapped): pieces no longer than `max_piece` that tile
    the stretch; snapped says whether the cuts sit on pauses.
    One piece when it fits. Otherwise as few pieces as possible, each cut
    moved to the nearest pause (time in seconds, a silence's midpoint) within
    a quarter of a piece's length of its even position, so words aren't cut.
    Any set of pauses that would leave a piece too long or too short, or no
    pauses, gives the even split."""
    length = end - start
    pieces = max(1, math.ceil(length / max_piece - 1e-9))
    cuts, snapped_to_pauses = _even_cuts(start, end, pieces), False
    if pieces > 1 and pauses:
        reach = length / pieces / 4
        snapped, floor = [], start
        for cut in cuts:
            near = [p for p in pauses if abs(p - cut) <= reach and p > floor]
            snapped.append(min(near, key=lambda p: abs(p - cut)) if near else cut)
            floor = snapped[-1]
        edges = [start] + snapped + [end]
        sizes = [b - a for a, b in zip(edges, edges[1:])]
        if snapped != cuts and all(
                MIN_PIECE_SECONDS <= size <= max_piece + 1e-9 for size in sizes):
            cuts, snapped_to_pauses = snapped, True
    edges = [start] + [round(c, 3) for c in cuts] + [end]
    return list(zip(edges, edges[1:])), snapped_to_pauses


def _pauses(audio_path: str, start: float, end: float) -> Optional[list]:
    """Midpoints of the silences between speech in [start, end), from the same
    local detector the coverage check uses, on just this stretch. None when
    the detector or the audio can't be read: the split is then even."""
    try:
        chunk = speech_coverage_service._decode_chunk(audio_path, start, end - start)
        spans = speech_coverage_service._detect_speech(chunk)
    except Exception:
        return None
    return [start + (a_end + b_start) / 2
            for (_s, a_end), (b_start, _e) in zip(spans, spans[1:])
            if b_start - a_end >= MIN_PAUSE_SECONDS]


def add_gap_lines(drama_id: int, expected_line_ids, *, start, end,
                  after_line_id: Optional[int] = None) -> dict:
    """Adds blank lines (flag GAP_FLAG) tiling [start, end), after
    `after_line_id` (None = at the start), in one history snapshot. Returns the
    structural write's result plus `new_line_ids` (in time order) and
    `split` ("single", "pauses" or "even").

    InvalidInputError for a bad window; UnsupportedOperationError with no
    stored audio (nothing could transcribe the lines); ConflictError when the
    stretch isn't free any more (a line now overlaps it, or the line list
    changed) or a job is running on the title; NotFoundError for an unknown
    after_line_id."""
    start, end = restructure._number("start", start), restructure._number("end", end)
    if not MIN_ADD_SECONDS <= end - start <= MAX_ADD_SECONDS:
        raise InvalidInputError(
            f"A gap to add must be {MIN_ADD_SECONDS:g} to {MAX_ADD_SECONDS:g} seconds long.")
    drama = restructure._require_drama(drama_id)
    audio_path = transcribe_service._drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError(f"No audio available for drama {drama_id}.")
    needs_split = end - start > MAX_PIECE_SECONDS
    pauses = _pauses(audio_path, start, end) if needs_split else None
    pieces, on_pauses = split_gap(start, end, pauses)
    how = "single" if len(pieces) == 1 else "pauses" if on_pauses else "even"

    def build(lines):
        pos = 0 if after_line_id is None else restructure._index_of(lines, after_line_id) + 1
        if (any(float(ln.end) > start + _EDGE_TOLERANCE_S for ln in lines[:pos])
                or any(float(ln.start) < end - _EDGE_TOLERANCE_S for ln in lines[pos:])):
            raise ConflictError("A line now covers part of this stretch -- reload and try again.")
        new = [core_module.Line(idx=pos + i, start=s, end=e, zh="", en="", flag=GAP_FLAG,
                                flag_note=GAP_FLAG_NOTE)
               for i, (s, e) in enumerate(pieces)]
        return lines[:pos] + new + lines[pos:], new
    result = restructure.structural_write(drama_id, expected_line_ids, "before adding gap lines",
                                          build)
    return {**result, "new_line_ids": [ln["id"] for ln in result["lines"]], "split": how}
