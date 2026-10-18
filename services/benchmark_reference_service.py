"""
services/benchmark_reference_service.py -- a title's own corrections as
transcription references for the Benchmark Lab and for auto-tune. UI-free.

A reference line is a saved line whose source text the owner changed since the
transcription run that made it: the raw transcript says what the recogniser
heard, the line says what was actually said. Lines the owner left alone are not
references (nothing says they were read), and neither are flagged or sound-effect
lines, which are still open questions.

"Changed" is judged on the text CER compares (script folded, punctuation and
spacing dropped), so a punctuation-only edit does not become a reference that
no scorer could tell apart from the raw text.
"""
import os
import shutil
import subprocess
import threading
import uuid

import db
import raw_transcript
import video_export
from core import SOURCE_LANGUAGES
from services import benchmark_lab_service as lab
from services import benchmark_scoring_service as scoring
from services import ownership_service
from services.media_upload_service import check_room_for
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError)
from services.transcribe_service import _drama_audio_path

# Speech just outside a line's edges is kept so a clip does not start mid-syllable.
CLIP_PAD_SECONDS = 0.25
# A longer line is more likely a mis-merged block than one utterance, and a
# long clip makes a slow, memory-hungry Lab case.
MAX_LINE_SECONDS = 60.0
MIN_LINE_SECONDS = 0.2
CLIP_SAMPLE_RATE = 16000
CLIP_TIMEOUT_SECONDS = 120
WAV_HEADER_BYTES = 44
# A long add holds clips on disk before their rows exist, where the clip import's
# leftover sweep could take them; a smaller batch keeps that window to seconds.
MAX_LINES_PER_ADD = 100
_adding_lock = threading.Lock()
_adding_sets = set()


def _unit(language: str) -> str:
    return "char" if language in SOURCE_LANGUAGES else "word"


def _comparable(text: str, normaliser: str, unit: str) -> str:
    return scoring.normalise_text(scoring._to_simplified(text, normaliser), unit)


def _drama_language(drama: dict) -> str:
    return drama.get("source_language") if drama.get("source_language") in SOURCE_LANGUAGES else "zh"


def reference_lines(drama_id: int, drama: dict = None) -> dict:
    """{"lines": [{id, idx, start, end, language, raw, edited}], "usable_lines",
    "raw_available"} for a title. `raw` is what the recogniser originally heard
    for the line (raw_transcript.original_text_for_line), `edited` the line now;
    only lines where the two differ as CER sees them are listed. `usable_lines`
    counts the lines that could have been compared (spoken, not flagged)."""
    drama = drama or ownership_service.require_drama(drama_id)
    rows = db.load_lines(drama_id)
    try:
        return _compare_with_raw(drama_id, drama, rows)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # A truncated or hand-edited raw transcript must not fail auto-tune or the Lab:
        # it counts as having no original to compare with.
        return {"lines": [], "usable_lines": 0, "raw_available": False}


def _compare_with_raw(drama_id: int, drama: dict, rows: list) -> dict:
    raw = raw_transcript.load_latest(db.drama_dir(drama_id))
    if not raw:
        return {"lines": [], "usable_lines": 0, "raw_available": False}
    drama_lang = _drama_language(drama)
    out, usable = [], 0
    for row in rows:
        edited = (row.get("zh") or "").strip()
        if row.get("sfx") or row.get("flag") or not edited:
            continue
        original = raw_transcript.original_text_for_line(raw, _Span(row))
        if original is None:
            continue
        usable += 1
        lang = row.get("lang") or drama_lang
        lang = lang if lang in SOURCE_LANGUAGES else drama_lang
        normaliser = scoring.script_normaliser("transcription", lang)
        unit = _unit(lang)
        if _comparable(original, normaliser, unit) == _comparable(edited, normaliser, unit):
            continue
        out.append({"id": row["id"], "idx": row["idx"], "start": row["start"], "end": row["end"],
                    "language": lang, "raw": original, "edited": edited})
    return {"lines": out, "usable_lines": usable, "raw_available": True}


class _Span:
    """The three line attributes original_text_for_line reads, from a row."""

    def __init__(self, row: dict):
        self.start, self.end, self.id = row["start"], row["end"], row.get("id")


def raw_to_edited_cer(references: list) -> float:
    """Characters (or words) the owner changed, over all reference characters:
    the edit distance from each line's raw text to its edited text, summed,
    divided by the summed length of the edited text. None without references."""
    edits = total = 0
    for ref in references:
        lang = ref["language"]
        normaliser = scoring.script_normaliser("transcription", lang)
        unit = _unit(lang)
        edited = _comparable(ref["edited"], normaliser, unit)
        length = len(edited) if unit == "char" else len(edited.split())
        if not length:
            continue
        edits += scoring.error_rate(_comparable(ref["raw"], normaliser, unit), edited, unit) * length
        total += length
    return round(edits / total, 4) if total else None


def title_stats(principal, drama_id: int) -> dict:
    """What the Lab shows for a title: how many lines were corrected and how far
    the raw transcript was from them."""
    ownership_service.require_visible(principal, "drama", drama_id)
    found = reference_lines(drama_id)
    return {"drama_id": drama_id, "raw_available": found["raw_available"],
            "lines_compared": found["usable_lines"], "lines_corrected": len(found["lines"]),
            "raw_to_edited_cer": raw_to_edited_cer(found["lines"])}


def score_against_references(segments, references: list) -> dict:
    """A candidate transcription's error rate against reference lines: for each
    line, the candidate segments whose midpoint falls inside it are joined and
    compared with the edited text, then edits are summed over all lines (the
    same midpoint rule original_text_for_line uses, so a candidate that splits
    or merges lines differently is still placed fairly). A line with no
    segment counts as fully missed. -> {"cer", "lines"}."""
    segments = [s for s in segments or [] if (s.get("text") or "").strip()]
    edits = total = 0.0
    for ref in references:
        lang = ref["language"]
        normaliser = scoring.script_normaliser("transcription", lang)
        unit = _unit(lang)
        edited = _comparable(ref["edited"], normaliser, unit)
        length = len(edited) if unit == "char" else len(edited.split())
        if not length:
            continue
        heard = "".join((s["text"] or "").strip() for s in segments
                        if ref["start"] - 0.01 <= (s["start"] + s["end"]) / 2 <= ref["end"] + 0.01)
        edits += scoring.error_rate(_comparable(heard, normaliser, unit), edited, unit) * length
        total += length
    return {"cer": round(edits / total, 4) if total else None, "lines": len(references)}


def _cut_clip(source: str, start: float, end: float, what: str = "a line") -> str:
    """Writes [start, end] of the title's audio to a new clip_<16hex>.wav in
    the clip folder and returns its name. A failed cut leaves no file; `what`
    names the line in the error."""
    name = f"clip_{uuid.uuid4().hex[:16]}.wav"
    final = os.path.join(db.BENCHMARK_DIR, name)
    part = final + ".part"
    cmd = ["ffmpeg", "-v", "error", "-y", *video_export.local_input(),
           "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", source, "-vn", "-ac", "1",
           "-ar", str(CLIP_SAMPLE_RATE), "-map_metadata", "-1", "-bitexact", "-f", "wav", part]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=CLIP_TIMEOUT_SECONDS)
        # Half the expected PCM: a source that ends early gives a short clip, not a usable one.
        if os.path.getsize(part) < WAV_HEADER_BYTES + (end - start) * CLIP_SAMPLE_RATE:
            raise OSError("short clip")
        os.replace(part, final)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        for leftover in (part, final):
            try:
                os.remove(leftover)
            except OSError:
                pass
        # str() of these carries the ffmpeg command line, i.e. absolute paths.
        raise DependencyUnavailableError(
            f"ffmpeg could not cut a clip for {what} from this title's audio.") from None
    return name


def add_title_lines(principal, drama_id: int, set_name: str, dry_run: bool = False) -> dict:
    """See _add_title_lines. One add per set at a time, so a double click cannot
    read "not yet in the set" twice and duplicate the cases."""
    if dry_run:
        return _add_title_lines(principal, drama_id, set_name, dry_run)
    with _adding_lock:
        if set_name in _adding_sets:
            raise ConflictError(f"Lines are already being added to '{set_name}'. Wait for that to finish.")
        _adding_sets.add(set_name)
    try:
        return _add_title_lines(principal, drama_id, set_name, dry_run)
    finally:
        with _adding_lock:
            _adding_sets.discard(set_name)


def _add_title_lines(principal, drama_id: int, set_name: str, dry_run: bool) -> dict:
    """Adds a transcription case for each corrected line of a title to a set:
    the line's audio, clipped, scored against the edited text. ref_start and
    ref_end are the line's edges in seconds from the clip's start, for scoring
    line timing later; they are the title's current timing, so time the title in
    Review first. A line already in the set is skipped, so adding twice adds
    nothing; unedited lines are never added."""
    set_name = lab._clean_text(set_name, "set_name", max_len=lab.MAX_SET_NAME_CHARS)
    ownership_service.require_visible(principal, "drama", drama_id)
    drama = db.get_drama(drama_id)
    if not drama:
        raise NotFoundError(f"Drama {drama_id} not found.")
    found = reference_lines(drama_id, drama)
    if not found["raw_available"]:
        raise InvalidInputError("This title has no original transcription to compare with.")
    candidates = [r for r in found["lines"]
                  if MIN_LINE_SECONDS <= r["end"] - r["start"] <= MAX_LINE_SECONDS]
    too_long = len(found["lines"]) - len(candidates)
    if not candidates:
        raise InvalidInputError(
            "No corrected lines to add. Edit a line's text in Review first."
            if not found["lines"] else "The corrected lines are too short or too long for a clip.")
    existing = [c for c in db.list_benchmark_cases("transcription") if c.get("set_name") == set_name]
    already = {c.get("origin_line_id") for c in existing
               if c.get("origin_drama_id") == drama_id and c.get("origin_line_id") is not None}
    pending = [r for r in candidates if r["id"] not in already]
    fresh = pending[:MAX_LINES_PER_ADD]
    if len(existing) + len(fresh) > lab.MAX_IMPORT_CASES:
        raise InvalidInputError(
            f"Set '{set_name}' has {len(existing)} cases and this adds {len(fresh)}; a set holds "
            f"at most {lab.MAX_IMPORT_CASES} cases in total. Use a new set name or delete cases.")
    result = {"set_name": set_name, "lines_compared": found["usable_lines"],
              "lines_corrected": len(found["lines"]), "unedited_skipped": found["usable_lines"] - len(found["lines"]),
              "unclippable_skipped": too_long, "case_count": len(fresh),
              "remaining": len(pending) - len(fresh), "added": 0, "skipped": len(candidates) - len(pending), "dry_run": dry_run}
    if dry_run or not fresh:
        return result
    return _write_cases(drama_id, drama, fresh, set_name, result)


def _write_cases(drama_id: int, drama: dict, fresh: list, set_name: str, result: dict) -> dict:
    source = _drama_audio_path(drama_id, drama)
    if source is None:
        raise InvalidInputError("This title has no audio to clip.")
    if shutil.which("ffmpeg") is None:
        raise DependencyUnavailableError("ffmpeg is not installed or not on PATH.")
    os.makedirs(db.BENCHMARK_DIR, exist_ok=True)
    check_room_for(db.BENCHMARK_DIR, int(sum(r["end"] - r["start"] + 2 * CLIP_PAD_SECONDS for r in fresh)
                                         * CLIP_SAMPLE_RATE * 2))
    title = (drama.get("title_en") or drama.get("title_zh") or f"Drama {drama_id}")[:60]
    written, rows = [], []
    try:
        for ref in fresh:
            origin = max(0.0, ref["start"] - CLIP_PAD_SECONDS)
            name = _cut_clip(source, origin, ref["end"] + CLIP_PAD_SECONDS, f"line {ref['idx'] + 1}")
            written.append(name)
            rows.append({"label": f"{title} · line {ref['idx'] + 1}", "stage": "transcription",
                         "content_type": drama.get("content_mode") or "audio_drama",
                         "source_language": ref["language"], "input_filename": name,
                         "reference_text": ref["edited"], "tier": "application", "set_name": set_name,
                         "title": title, "scene_order": ref["idx"] + 1,
                         "origin_drama_id": drama_id, "origin_line_id": ref["id"],
                         "ref_start": round(ref["start"] - origin, 3),
                         "ref_end": round(ref["end"] - origin, 3)})
        db.create_benchmark_clip_cases(rows)
    except BaseException:
        for name in written:
            try:
                os.remove(os.path.join(db.BENCHMARK_DIR, name))
            except OSError:
                pass
        raise
    result["added"] = len(rows)
    return result
