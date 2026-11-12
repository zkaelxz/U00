"""services/novel_cast_service.py -- the cast proposal for a novel: the people the glossary scan found, with their pronouns, offered for approval; applying sets pronouns only where none are set."""

import re

import db
import translation_guide as tguide
from lib.errors import InvalidInputError
from services import characters_service, glossary_extract_service, ownership_service

MAX_PEOPLE = 40
MAX_APPLY = 200
# The scan prompt (translation_guide._PERSON_PRONOUN_INSTRUCTION) makes the note start with the pronouns.
_LEAD = re.compile(r"^\W*(?:pronouns?\W+)?(she/her|he/him|they/them)\b[\s.,;:\-–—]*", re.IGNORECASE)


def _split_note(reason: str) -> tuple:
    """(pronouns, rest of the note): "" when the scan said unclear or gave none."""
    m = _LEAD.match(reason or "")
    if not m:
        return "", (reason or "").strip()
    return m.group(1).lower(), reason[m.end():].strip()


def _norm(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def _known(drama_id: int, drama: dict) -> tuple:
    """({name: pronouns of a speaker in this title}, {name: pronouns of a series character})."""
    speakers = {}
    for c in characters_service.list_characters(drama_id):
        pronouns = tguide.normalize_pronouns(c.get("pronouns")) or tguide.normalize_pronouns(c.get("series_pronouns"))
        for name in (c.get("character_name"), c.get("speaker_label")):
            if _norm(name):
                speakers.setdefault(_norm(name), pronouns)
    sid = drama.get("series_id")
    series = {_norm(s.get("character_name")): tguide.normalize_pronouns(s.get("gender"))
              for s in (db.list_series_characters(sid) if sid else [])}
    return speakers, series


def proposal(drama_id: int) -> dict:
    """The people from the finished glossary scan. Reads the scan's result; makes no model call,
    so it costs nothing and shows only what the scan already found."""
    drama = ownership_service.require_drama(drama_id)
    status = glossary_extract_service.get_novel_glossary_status(drama_id)
    proposals = ((status.get("result") or {}).get("proposals") or []) if status.get("status") == "done" else []
    speakers, series = _known(drama_id, drama)
    people = []
    for p in proposals:
        name = (p.get("suggested_translation") or "").strip()
        if p.get("category") != "person_name" or not name:
            continue
        pronouns, who = _split_note(p.get("reason") or "")
        key = _norm(name)
        current = speakers.get(key) or series.get(key) or ""
        people.append({
            "term": p["term"], "name": name, "pronouns": pronouns, "who": who,
            "occurrences": p.get("occurrences") or 0,
            "current_pronouns": current,
            "exists": key in speakers or key in series})
    people.sort(key=lambda x: -x["occurrences"])
    return {"drama_id": drama_id, "ready": status.get("status") == "done",
            "has_series": bool(drama.get("series_id")), "people": people[:MAX_PEOPLE]}


def _clean_entries(entries) -> list:
    if not isinstance(entries, (list, tuple)) or not entries or len(entries) > MAX_APPLY:
        raise InvalidInputError("Choose at least one character.")
    out = []
    for e in entries:
        name = " ".join(str((e or {}).get("name") or "").split())
        pronouns = str((e or {}).get("pronouns") or "").strip()
        if not name or len(name) > 100 or pronouns not in tguide.PRONOUN_PRESETS:
            raise InvalidInputError("Each character needs a name and she/her, he/him or they/them.")
        out.append((name, pronouns))
    return out


def apply(drama_id: int, entries) -> dict:
    """Sets the chosen pronouns, never replacing any already set: a speaker of this title without
    pronouns gets them; otherwise a series character is added (or its empty pronouns filled) when the
    title has a series. `kept` are names that already had pronouns, `not_applied` names with nowhere to go
    (no speaker of that name yet and no series)."""
    drama = ownership_service.require_drama(drama_id)
    chosen = _clean_entries(entries)
    speakers = {}
    for c in characters_service.list_characters(drama_id):
        for name in (c.get("character_name"), c.get("speaker_label")):
            if _norm(name):
                speakers.setdefault(_norm(name), c)
    sid = drama.get("series_id")
    series = {_norm(s["character_name"]): s for s in (db.list_series_characters(sid) if sid else [])}
    report = {"set": [], "added": [], "kept": [], "not_applied": []}
    for name, pronouns in chosen:
        key = _norm(name)
        speaker, known = speakers.get(key), series.get(key)
        if speaker is not None:
            if tguide.normalize_pronouns(speaker.get("pronouns")) or tguide.normalize_pronouns(speaker.get("series_pronouns")):
                report["kept"].append(name)
            else:
                characters_service.update_character(drama_id, speaker["speaker_label"], pronouns=pronouns)
                report["set"].append(name)
        elif known is not None:
            if tguide.normalize_pronouns(known.get("gender")):
                report["kept"].append(name)
            else:
                db.update_series_character(known["id"], gender=pronouns)
                report["set"].append(name)
        elif sid:
            db.upsert_series_character(sid, name, gender=pronouns)
            report["added"].append(name)
        else:
            report["not_applied"].append(name)
    return report
