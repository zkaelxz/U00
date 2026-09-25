"""
raw_transcript.py -- the untouched output of each transcription run, kept
on disk next to the drama (Step 3, R1-lite).

Lines get edited, merged, split and re-transcribed; this file never does.
The first run writes drama_dir/raw_transcript.json; every later run writes
raw_transcript.<timestamp>.json instead -- nothing here ever overwrites or
rewrites an existing file. The Review step reads the newest one back to
show "what did the transcription originally say for this line?" and to
restore a single line's text from it.
"""

import datetime
import glob
import json
import os

RAW_NAME = "raw_transcript.json"


def write_raw_transcript(drama_dir: str, segments, lines, backend: str, model: str = "",
                         language: str = "", mode: str = "") -> str:
    """segments: the transcription backend's own output (start/end/text per
    segment). lines: the lines as first created from it (after alignment,
    before any edit) -- saved already, so each has its permanent id.
    Returns the path written. Opens with mode "x", so an existing file is
    never overwritten even if two runs race on the same timestamp."""
    os.makedirs(drama_dir, exist_ok=True)
    payload = {
        "created_at": datetime.datetime.utcnow().isoformat(),
        "backend": backend,
        "model": model or "",
        "language": language or "",
        "mode": mode or "",
        "text": "\n".join((s.get("text") or "").strip() for s in segments),
        "segments": list(segments),
        "lines": [{"id": getattr(ln, "id", None), "idx": ln.idx, "start": ln.start,
                   "end": ln.end, "text": ln.zh} for ln in lines],
    }
    path = os.path.join(drama_dir, RAW_NAME)
    n = 0
    while True:
        try:
            with open(path, "x", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            return path
        except FileExistsError:
            stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
            n += 1
            path = os.path.join(drama_dir, f"raw_transcript.{stamp}{'' if n == 1 else f'-{n}'}.json")


def list_raw_transcripts(drama_dir: str) -> list:
    """Paths oldest first: raw_transcript.json, then the timestamped runs
    (their names sort chronologically)."""
    first = os.path.join(drama_dir, RAW_NAME)
    later = sorted(glob.glob(os.path.join(drama_dir, "raw_transcript.*.json")))
    return ([first] if os.path.exists(first) else []) + later


def load_latest(drama_dir: str):
    """The most recent run's transcript -- the one the drama's current
    lines came from -- or None if there isn't one."""
    paths = list_raw_transcripts(drama_dir)
    if not paths:
        return None
    with open(paths[-1], encoding="utf-8") as f:
        return json.load(f)


def original_text_for_line(raw: dict, line) -> str:
    """The originally transcribed text for `line`: every original line whose
    midpoint falls inside this line's time range, joined -- so a line merged
    from two shows both parts -- falling back to the original line with the
    same permanent id if nothing overlaps (e.g. its timing was moved).
    None if neither finds anything."""
    if not raw:
        return None
    originals = raw.get("lines") or []
    hits = [o for o in originals
            if line.start - 0.01 <= (o["start"] + o["end"]) / 2 <= line.end + 0.01]
    if hits:
        return "".join((o.get("text") or "").strip() for o in hits)
    line_id = getattr(line, "id", None)
    if line_id is not None:
        for o in originals:
            if o.get("id") == line_id:
                return o.get("text") or ""
    return None
