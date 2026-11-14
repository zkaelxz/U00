"""Speaker guesses for Review lines that have no speaker: offered for one-tap use, never written."""
import bisect
import os
from collections import Counter, defaultdict

import db
import diarize
from lib.errors import InvalidInputError
from services import ownership_service
from services.review_lines_service import needs_speaker

MAX_LINES = 200
# Listed in this order when two guesses have the same support. On a hand-labelled
# 11-speaker title the line above was the speaker as often as the line below, and
# both more often than the detection's pick, whose voices had merged several people.
_REASON_ORDER = ("above", "below", "voice")


def _valid_turns(turns) -> list:
    out = []
    for t in turns or []:
        try:
            start, end, speaker = float(t["start"]), float(t["end"]), t["speaker"]
        except (KeyError, TypeError, ValueError):
            continue
        if end > start and isinstance(speaker, str) and speaker:
            out.append({"start": start, "end": end, "speaker": speaker})
    return sorted(out, key=lambda t: t["start"])


class _TurnIndex:
    """The turns that can overlap a stretch, without scanning every turn per line:
    a long title has thousands of both."""

    def __init__(self, turns):
        self.turns = turns
        self.starts = [t["start"] for t in turns]
        self.longest = max((t["end"] - t["start"] for t in turns), default=0.0)

    def near(self, start: float, end: float) -> list:
        lo = bisect.bisect_left(self.starts, start - self.longest)
        hi = bisect.bisect_left(self.starts, end)
        return [t for t in self.turns[lo:hi] if t["end"] > start]


def _detector_names(lines, index: _TurnIndex) -> dict:
    """Each detection label's speaker now: whoever's lines hold most of that label's
    speech. Detection keeps its own labels (SPEAKER_00) in the saved turns while the
    lines get renamed, so the label alone would name nobody on screen."""
    seconds: dict = defaultdict(Counter)
    for ln in lines:
        if not ln.speaker:
            continue
        for t in index.near(ln.start, ln.end):
            overlap = min(ln.end, t["end"]) - max(ln.start, t["start"])
            if overlap > 0:
                seconds[t["speaker"]][ln.speaker] += overlap
    return {label: c.most_common(1)[0][0] for label, c in seconds.items()}


def _check_ids(line_ids) -> list:
    if (not isinstance(line_ids, (list, tuple)) or not line_ids or len(line_ids) > MAX_LINES
            or any(isinstance(i, bool) or not isinstance(i, int) for i in line_ids)):
        raise InvalidInputError(f"line_ids must be 1 to {MAX_LINES} integers.")
    return list(line_ids)


def suggest_speakers(drama_id: int, line_ids) -> dict:
    """Up to three speakers for each named line that still needs one (needs_speaker),
    most-supported first: the nearest line above with a speaker, the nearest below, and
    the saved detection's pick for the line (the one it was too unsure of to keep),
    renamed to the speaker now on that voice's lines. Ids that aren't this title's
    lines, or lines with no guess, are left out. Raises NotFoundError (unknown drama)
    or InvalidInputError. Returns {"lines": [{line_id, speakers: [{speaker, reasons}]}]}."""
    wanted = set(_check_ids(line_ids))
    ownership_service.require_drama(drama_id)
    lines = db.load_line_objects(drama_id)
    index = _TurnIndex(_valid_turns(diarize.load_turns(os.path.join(db.DRAMAS_DIR, str(drama_id)))))
    names = _detector_names(lines, index) if index.turns else {}
    labelled = [pos for pos, ln in enumerate(lines) if ln.speaker]
    out = []
    for pos, ln in enumerate(lines):
        if ln.id not in wanted or not needs_speaker(ln):
            continue
        at = bisect.bisect_left(labelled, pos)
        clues = [
            ("above", lines[labelled[at - 1]].speaker if at > 0 else None),
            ("below", lines[labelled[at]].speaker if at < len(labelled) else None),
            ("voice", names.get(diarize.assign_speaker_to_line(ln.start, ln.end, index.near(ln.start, ln.end)))),
        ]
        reasons: dict = {}
        for reason, speaker in clues:
            if speaker:
                reasons.setdefault(speaker, []).append(reason)
        ranked = sorted(reasons.items(), key=lambda kv: (-len(kv[1]), _REASON_ORDER.index(kv[1][0])))
        if ranked:
            out.append({"line_id": ln.id, "speakers": [{"speaker": s, "reasons": r} for s, r in ranked]})
    return {"lines": out}
