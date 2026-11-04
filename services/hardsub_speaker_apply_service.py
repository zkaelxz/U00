"""
services/hardsub_speaker_apply_service.py -- puts a finished hard-sub reading's speaker groups on the
title's transcript lines as their speakers. A line takes the group whose captions cover most of it; the
reading's own timing is too coarse to become lines. UI-free.
"""
import db
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import drama_service, hardsub_speaker_read_service as reading, ownership_service

# A group labels a line only when its captions cover this share of the line: a caption that merely
# brushes the line's edge belongs to the neighbouring line.
MIN_COVERAGE = 0.4
MAX_PERSON_CHARS = 60
MAX_COLOUR_NAMES = 24


def colour_names(drama_id: int) -> dict:
    """{colour group: person} remembered for the title's series (a creator keeps their colours across
    videos); {} for a title outside any series."""
    drama = db.get_drama(drama_id)
    return db.get_series_speaker_colours(drama["series_id"]) if drama and drama.get("series_id") else {}


def _clean_names(names) -> dict:
    if not isinstance(names, dict) or len(names) > MAX_COLOUR_NAMES:
        raise InvalidInputError(f"Colour names must be an object of at most {MAX_COLOUR_NAMES} entries.")
    cleaned = {}
    for key, person in names.items():
        if not isinstance(key, str) or not isinstance(person, str) or not key.strip():
            raise InvalidInputError("Each colour name maps a colour group to a person's name.")
        if len(person.strip()) > MAX_PERSON_CHARS:
            raise InvalidInputError(f"A person's name is at most {MAX_PERSON_CHARS} characters.")
        cleaned[key.strip()] = person.strip()
    return cleaned


def _remember(principal, drama_id: int, names: dict) -> dict:
    """Merges `names` into the series' map (a blank person forgets that colour). A title with no series
    uses them for this run only."""
    known = dict(colour_names(drama_id))
    for key, person in names.items():
        if person:
            known[key] = person
        else:
            known.pop(key, None)
    drama = db.get_drama(drama_id)
    if drama and drama.get("series_id"):
        ownership_service.require_editable(principal, "series", drama["series_id"])
        db.set_series_speaker_colours(drama["series_id"], known)
    return known


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def label_for(line, groups: list):
    """The key of the group covering most of `line`, or None when none covers enough or two tie."""
    length = line.end - line.start
    if length <= 0:
        return None
    covered = {}
    for group in groups:
        seconds = sum(_overlap(line.start, line.end, c["start"], c["end"]) for c in group["lines"])
        if seconds > 0:
            covered[group["key"]] = seconds
    ranked = sorted(covered.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] / length < MIN_COVERAGE:
        return None
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def apply(principal, drama_id: int, names=None) -> dict:
    """Sets the speaker of every line the latest finished reading can place, as the person the group's
    colour stands for in this title's series (`names` adds to that map and is remembered), else the colour
    group itself. Lines whose speaker was set by hand are kept.
    Returns {"labelled", "kept_manual", "unplaced", "speakers"}."""
    ownership_service.require_editable(principal, "drama", drama_id)
    wanted = _clean_names(names) if names else {}
    got = reading.result(principal, drama_id)
    if got["status"] != "done" or not got.get("result") or not got["result"]["groups"]:
        raise NotFoundError("This title has no finished hard-sub reading to take speakers from.")
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this title -- wait for it "
                            "to finish or cancel it first.")
    people = _remember(principal, drama_id, wanted) if wanted else colour_names(drama_id)
    groups = got["result"]["groups"]
    lines = db.load_line_objects(drama_id)
    labelled = kept_manual = unplaced = 0
    speakers = set()
    for ln in lines:
        if ln.speaker_manual:
            kept_manual += 1
            continue
        label = label_for(ln, groups)
        if label is None:
            unplaced += 1
            continue
        label = people.get(label) or label
        ln.speaker = label
        speakers.add(label)
        labelled += 1
    for label in sorted(speakers):
        db.upsert_character(drama_id, label)
    # Only where the database still holds what was loaded, so a speaker set meanwhile is kept.
    db.save_lines(drama_id, lines, fields=("speaker", "speaker_manual"), only_if_unchanged=True)
    return {"labelled": labelled, "kept_manual": kept_manual, "unplaced": unplaced, "speakers": sorted(speakers)}
