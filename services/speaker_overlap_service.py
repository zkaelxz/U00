"""Talk-over lines: where saved speaker turns overlap and the quieter speaker has no line, add a blank flagged line for them."""
from collections import Counter

import db
import diarize
from lib.errors import ConflictError
from services import restructure_service as restructure
from core import Line

# Diarization flickers between speakers for a few hundredths of a second;
# only a stretch a person could hear as a second voice is worth a line.
MIN_OVERLAP_SECONDS = 0.4
# A speaker with lines over at least this share of the stretch already has it.
_COVERED_SHARE = 0.5
TALK_OVER_FLAG = "talk_over"
TALK_OVER_NOTE = "Another speaker talks here; type what they say or delete the line"


def _overlap(a_start, a_end, b_start, b_end) -> float:
    return min(a_end, b_end) - max(a_start, b_start)


def _valid_turns(turns) -> list:
    out = []
    for t in turns or []:
        try:
            start, end = float(t["start"]), float(t["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start and t.get("speaker"):
            out.append({"start": start, "end": end, "speaker": str(t["speaker"])})
    return sorted(out, key=lambda t: (t["start"], t["end"]))


def _speaker_names(turns, lines) -> dict:
    """Turn label -> the name its lines carry. Lines get their speaker from the
    turn they overlap most and may be renamed since, so the name is read off
    the lines, weighted by length; a label with no lines keeps its own."""
    seconds: dict = {}
    for ln in lines:
        if not ln.speaker or float(ln.end) <= float(ln.start):
            continue
        label = diarize.assign_speaker_to_line(float(ln.start), float(ln.end), turns)
        if label is not None:
            seconds.setdefault(label, Counter())[ln.speaker] += float(ln.end) - float(ln.start)
    return {label: seconds[label].most_common(1)[0][0] for label in seconds}


def _covered(name: str, start: float, end: float, lines) -> float:
    spans = sorted((max(float(ln.start), start), min(float(ln.end), end))
                   for ln in lines if ln.speaker == name and float(ln.end) > float(ln.start)
                   and _overlap(float(ln.start), float(ln.end), start, end) > 0)
    total, reach = 0.0, start
    for s, e in spans:
        s = max(s, reach)
        if e > s:
            total += e - s
            reach = e
    return total


def find_talk_over(turns, lines) -> list:
    """[{start, end, speaker}] for each stretch where two speakers' turns overlap,
    one of them has lines there and the other has none: the other's missing
    line. A stretch nobody has a line over is an untranscribed gap, not talk-over."""
    turns = _valid_turns(turns)
    names = _speaker_names(turns, lines)
    found: dict = {}
    for i, a in enumerate(turns):
        for b in turns[i + 1:]:
            if b["start"] >= a["end"]:
                break
            if a["speaker"] == b["speaker"]:
                continue
            start, end = max(a["start"], b["start"]), min(a["end"], b["end"])
            if end - start < MIN_OVERLAP_SECONDS:
                continue
            share = {t["speaker"]: _covered(names.get(t["speaker"], t["speaker"]), start, end, lines)
                     / (end - start) for t in (a, b)}
            missing = [s for s, v in share.items() if v < _COVERED_SHARE]
            if len(missing) != 1:
                continue
            found.setdefault(missing[0], []).append((start, end))
    out = []
    for label, spans in found.items():
        merged = []
        for s, e in sorted(spans):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        out.extend({"start": round(s, 3), "end": round(e, 3), "speaker": names.get(label, label)}
                   for s, e in merged if e - s >= MIN_OVERLAP_SECONDS)
    return sorted(out, key=lambda x: x["start"])


def add_talk_over_lines(drama_id: int) -> dict:
    """Adds one blank line (flag TALK_OVER_FLAG, speaker set by hand) per
    find_talk_over stretch, overlapping the line it is spoken over, in one
    history snapshot. Returns the structural write's result plus
    `added_count` and `new_line_ids`. ConflictError when no speaker detection
    is saved or a job runs on the title; NotFoundError for an unknown drama."""
    restructure._require_drama(drama_id)
    turns = diarize.load_turns(db.drama_dir(drama_id))
    if turns is None:
        raise ConflictError("No saved speaker detection for this title; run Detect speakers first.")
    current = db.load_line_objects(drama_id)
    if not find_talk_over(turns, current):
        return {"added_count": 0, "new_line_ids": [], "line_ids": [ln.id for ln in current],
                "history_id": None, "lines_fingerprint": None}
    expected = [ln.id for ln in current]

    def build(lines):
        new = []
        # Founded on the lines as they are under the lock, not as they were read above.
        for stretch in find_talk_over(turns, lines):
            pos = 1 + max((i for i, ln in enumerate(lines)
                           if float(ln.end) > float(ln.start) and float(ln.start) <= stretch["start"]),
                          default=-1)
            # speaker_manual: re-running detection must not relabel a line made for one voice.
            line = Line(idx=pos, start=stretch["start"], end=stretch["end"], zh="", en="",
                        speaker=stretch["speaker"], speaker_manual=True,
                        flag=TALK_OVER_FLAG, flag_note=TALK_OVER_NOTE)
            lines = lines[:pos] + [line] + lines[pos:]
            new.append(line)
        return lines, new

    result = restructure.structural_write(drama_id, expected, "before adding talk-over lines", build)
    return {**result, "added_count": len(result["lines"]),
            "new_line_ids": [ln["id"] for ln in result["lines"]]}
