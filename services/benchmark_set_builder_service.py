"""
services/benchmark_set_builder_service.py -- turns a title's reviewed lines
into Benchmark Lab cases, so the owner's own corrections are the reference
translations. UI-free.

What counts as "reviewed" (there is no per-line reviewed flag in the app):
- edited: the title has an edit sample (db.edit_samples, written whenever a
  saved line's English is rewritten by hand) for this source text whose
  rewritten version is exactly the line's English now. A later find-and-replace
  or version restore changes the English without a sample, so that line drops
  out instead of passing off machine text as a correction.
- approved: for a title in a series, translation memory holds this exact
  source -> English pair (it is stored when a line is edited and saved).
A line you read and left as it was has neither mark. Callers who want those
too pass include="all".

Lines are grouped into scene-sized cases (a few consecutive lines, split at a
pause or at a skipped line), so a case has some context but stays under
benchmark_lab_service.MAX_TEXT_CHARS. Cases go in a named "application" set.
"""
import db
from core import SOURCE_LANGUAGES
from services import benchmark_lab_service as lab
from services import ownership_service
from services.service_errors import InvalidInputError, NotFoundError

INCLUDES = ("reviewed", "all")
DEFAULT_LINES_PER_CASE = 4
MAX_LINES_PER_CASE = 10
# A silence this long between two lines starts a new scene.
SCENE_GAP_SECONDS = 6.0
_EDIT_SAMPLE_LIMIT = 1_000_000


def _reviewed_ids(drama: dict, lines: list) -> set:
    edited = {}
    for s in db.list_edit_samples(drama["id"], limit=_EDIT_SAMPLE_LIMIT):
        edited.setdefault((s.get("zh") or "").strip(), set()).add((s.get("user_version") or "").strip())
    approved = {}
    if drama.get("series_id"):
        approved = {(m.get("source_text") or "").strip(): (m.get("translation") or "").strip()
                    for m in db.list_translation_memory(drama["series_id"])}
    out = set()
    for ln in lines:
        zh, en = (ln.get("zh") or "").strip(), (ln.get("en") or "").strip()
        if en and (en in edited.get(zh, ()) or approved.get(zh) == en):
            out.add(ln["id"])
    return out


def _eligible(ln: dict, include: str, reviewed: set, drama_lang: str):
    """The line's source language when it can be a case line, else None.
    Flagged lines are still open questions, so "reviewed" leaves them out."""
    if ln.get("sfx") or not (ln.get("zh") or "").strip() or not (ln.get("en") or "").strip():
        return None
    if include == "reviewed" and (ln["id"] not in reviewed or ln.get("flag")):
        return None
    lang = ln.get("lang") or drama_lang
    return lang if lang in SOURCE_LANGUAGES else drama_lang


def _group_cases(lines: list, lines_per_case: int) -> list:
    """[(language, [lines])]: consecutive lines of one language, split at
    lines_per_case, at a pause (SCENE_GAP_SECONDS), where a line was skipped,
    or before either side of the case would pass MAX_TEXT_CHARS."""
    groups, current, size_zh, size_en = [], None, 0, 0
    for lang, ln in lines:
        zh, en = ln["zh"].strip(), ln["en"].strip()
        if len(zh) > lab.MAX_TEXT_CHARS or len(en) > lab.MAX_TEXT_CHARS:
            continue
        if current is not None:
            prev = current[1][-1]
            breaks = (lang != current[0] or len(current[1]) >= lines_per_case
                      or ln["idx"] != prev["idx"] + 1
                      or (ln.get("start") or 0) - (prev.get("end") or 0) > SCENE_GAP_SECONDS
                      or size_zh + 1 + len(zh) > lab.MAX_TEXT_CHARS
                      or size_en + 1 + len(en) > lab.MAX_TEXT_CHARS)
            if breaks:
                groups.append(current)
                current = None
        if current is None:
            current, size_zh, size_en = (lang, []), -1, -1
        current[1].append(ln)
        size_zh += 1 + len(zh)
        size_en += 1 + len(en)
    if current is not None:
        groups.append(current)
    return groups


def _evenly_spaced(items: list, count: int) -> list:
    if count >= len(items):
        return items
    if count == 1:
        return [items[len(items) // 2]]
    picks = sorted({round(i * (len(items) - 1) / (count - 1)) for i in range(count)})
    return [items[i] for i in picks]


def build_set(principal, drama_id: int, set_name: str, include: str = "reviewed",
              line_start: int = None, line_end: int = None, scene_count: int = None,
              lines_per_case: int = DEFAULT_LINES_PER_CASE, dry_run: bool = False) -> dict:
    """Creates (or with dry_run only counts) benchmark cases from a title's lines.
    line_start/line_end are 1-based line numbers, inclusive; scene_count keeps
    that many evenly spaced scenes of what is left. A case already in the set
    (same source text) is skipped, so building twice adds nothing."""
    set_name = lab._clean_text(set_name, "set_name", max_len=lab.MAX_SET_NAME_CHARS)
    if include not in INCLUDES:
        raise InvalidInputError(f"include must be one of {', '.join(INCLUDES)}.")
    if not 1 <= lines_per_case <= MAX_LINES_PER_CASE:
        raise InvalidInputError(f"lines_per_case must be 1 to {MAX_LINES_PER_CASE}.")
    if scene_count is not None and scene_count < 1:
        raise InvalidInputError("scene_count must be at least 1.")
    if line_start is not None and line_end is not None and line_end < line_start:
        raise InvalidInputError("The last line can't come before the first.")
    ownership_service.require_visible(principal, "drama", drama_id)
    drama = db.get_drama(drama_id)
    if not drama:
        raise NotFoundError(f"Drama {drama_id} not found.")
    drama_lang = drama.get("source_language") if drama.get("source_language") in SOURCE_LANGUAGES else "zh"

    all_lines = db.load_lines(drama_id)
    in_range = [ln for ln in all_lines
                if (line_start is None or ln["idx"] + 1 >= line_start)
                and (line_end is None or ln["idx"] + 1 <= line_end)]
    reviewed = _reviewed_ids(drama, in_range)
    usable = [(lang, ln) for ln in in_range
              if (lang := _eligible(ln, include, reviewed, drama_lang))]
    groups = _group_cases(usable, lines_per_case)
    if scene_count is not None:
        groups = _evenly_spaced(groups, scene_count)
    if not groups:
        raise InvalidInputError(
            "No lines to use. Review and save some translations first, or choose all lines."
            if include == "reviewed" else "No lines with both a source and a translation in that range.")
    if len(groups) > lab.MAX_IMPORT_CASES:
        raise InvalidInputError(
            f"That makes {len(groups)} cases; at most {lab.MAX_IMPORT_CASES} per set. "
            "Narrow the line range or ask for fewer scenes.")

    # The per-build check above can't see earlier builds, so repeated builds
    # would otherwise grow one set without bound.
    existing = [c for c in db.list_benchmark_cases("translation") if c.get("set_name") == set_name]
    known = {c.get("source_text") for c in existing}
    new_cases = sum(1 for _lang, g in groups if "\n".join(ln["zh"].strip() for ln in g) not in known)
    if len(existing) + new_cases > lab.MAX_IMPORT_CASES:
        raise InvalidInputError(
            f"Set '{set_name}' has {len(existing)} cases and this build adds {new_cases}; a set "
            f"holds at most {lab.MAX_IMPORT_CASES} cases in total. Use a new set name, delete "
            "cases, or narrow the line range.")

    result = {"set_name": set_name, "tier": "application", "include": include,
              "lines_in_range": len(in_range), "reviewed_lines": len(reviewed),
              "lines_used": sum(len(g[1]) for g in groups), "case_count": len(groups),
              "added": 0, "skipped": 0, "dry_run": dry_run}
    if dry_run:
        return result
    title = drama.get("title_en") or drama.get("title_zh") or f"Drama {drama_id}"
    for lang, group in groups:
        source = "\n".join(ln["zh"].strip() for ln in group)
        reference = "\n".join(ln["en"].strip() for ln in group)
        if db.find_benchmark_case(set_name, source):
            result["skipped"] += 1
            continue
        first, last = group[0], group[-1]
        db.create_benchmark_lab_case(
            f"{title[:80]} · lines {first['idx'] + 1}-{last['idx'] + 1}", "translation", lang,
            source, reference, "application", set_name,
            content_type=drama.get("content_mode") or "audio_drama",
            origin_drama_id=drama_id, origin_line_id=first["id"])
        result["added"] += 1
    return result
