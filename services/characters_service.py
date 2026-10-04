"""
services/characters_service.py -- per-drama characters / voice config:
the "People & pronouns" panel and the per-character voice config.

Covers: listing a drama's speakers with their character/voice settings
(plus a couple of sample lines and the linked series character's
pronoun default), a validated field-scoped partial update,
series-character listing, the clone-engine picklist (Step 26c language
rule), the voice bank (list + apply), recurring-voice suggestions
("sounds like X": list, accept, reject) and "remember as a known series
character".

Speaker set (`sorted({ln.speaker for ln in lines if ln.speaker})`): a
speaker label is known for a drama when it appears on
one of that drama's lines OR already has a `characters` row for it.

Every read and write is keyed by (drama_id, speaker_label); nothing here
touches another drama's rows.

Update semantics follow db.upsert_character: None leaves a stored value
untouched, "" clears it.

Results never contain filesystem paths, URLs, or the reference-audio
filename -- only `has_ref_audio` / `ref_text_present` booleans.

Deliberately NOT here:
  - Reference-audio upload / auto-extract, saving to the voice bank and
    the series-character link: services/voice_clone_service.py.
  - Character deletion and series-character rename/delete (the only
    series write here is "remember as a known series character").
  - Dub generation itself.

No FastAPI import.
"""
import os
import unicodedata

import db
import dub
import translation_guide as tguide
from db import (apply_voice_bank_entry as _db_apply_voice_bank_entry, drama_dir, get_drama,
                get_voice_bank_entry, list_characters_with_series_names,
                list_series_characters as _db_list_series_characters,
                list_voice_bank_entries, load_lines, upsert_character)
from services import drama_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

MAX_NAME_LEN = 200
MAX_PRONOUNS_LEN = 40
MAX_VOICE_LEN = 200
MAX_VOICE_DESIGN_LEN = 1000
MAX_REF_TEXT_LEN = 5000
MAX_ID = 2**31 - 1  # sqlite ints are 64-bit; anything larger is an OverflowError (500)
MAX_SPEAKER_LABEL_LEN = 100  # only enforced where the label becomes a filename
# C04: the first line and the middle one; each clipped so
# the list payload stays bounded however long a line is.
MAX_SAMPLE_LINES = 2
MAX_SAMPLE_CHARS = 160


def check_id(name: str, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(f"{name} must be a whole number.")
    if value < 1 or value > MAX_ID:
        raise InvalidInputError(f"{name} is out of range.")


def require_drama(drama_id: int) -> dict:
    check_id("drama_id", drama_id)
    drama = get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _source_language(drama: dict) -> str:
    return drama.get("source_language") or "zh"


def _clip_sample(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_SAMPLE_CHARS else text[:MAX_SAMPLE_CHARS - 1] + "\u2026"


def _samples(texts: list) -> list:
    """The speaker's first line and, when there's more
    than one, the middle one (so the two usually come from different
    scenes)."""
    if not texts:
        return []
    picked = [texts[0]]
    if len(texts) > 1:
        picked.append(texts[len(texts) // 2])
    return [_clip_sample(t) for t in picked[:MAX_SAMPLE_LINES]]


def _character_dict(row: dict, line_count: int, sample_lines: list = ()) -> dict:
    return {
        "speaker_label": row["speaker_label"],
        "character_name": row.get("character_name") or "",
        "voice_actor": row.get("voice_actor") or "",
        "pronouns": row.get("pronouns") or "",
        "tts_voice": row.get("tts_voice") or "",
        "offline_voice": row.get("offline_voice") or "",
        "clone_engine": row.get("clone_engine") or "",
        "voice_design": row.get("voice_design") or "",
        "has_ref_audio": bool(row.get("ref_audio_filename")),
        "ref_text_present": bool((row.get("ref_text") or "").strip()),
        "series_character_id": row.get("series_character_id"),
        "series_character_name": row.get("series_character_name") or "",
        # The linked series character's pronouns: shown as the default
        # when this drama sets none, never copied into this drama's row.
        "series_pronouns": tguide.normalize_pronouns(row.get("series_pronouns")),
        "line_count": line_count,
        "sample_lines": list(sample_lines),
    }


def list_characters(drama_id: int) -> list:
    """One entry per known speaker (line speakers plus stored character
    rows), sorted by label. A speaker with no stored row gets blank
    fields. A linked series character's current name wins for
    character_name (db.list_characters_with_series_names). sample_lines:
    up to MAX_SAMPLE_LINES of the speaker's non-blank source lines, each
    clipped to MAX_SAMPLE_CHARS. Raises NotFoundError for an unknown
    drama."""
    require_drama(drama_id)
    counts, texts = {}, {}
    for ln in load_lines(drama_id):
        if ln.get("speaker"):
            counts[ln["speaker"]] = counts.get(ln["speaker"], 0) + 1
            if (ln.get("zh") or "").strip():
                texts.setdefault(ln["speaker"], []).append(ln["zh"])
    rows = {r["speaker_label"]: r for r in list_characters_with_series_names(drama_id)}
    out = []
    for label in sorted(set(counts) | set(rows)):
        row = rows.get(label) or {"speaker_label": label}
        out.append(_character_dict(row, counts.get(label, 0), _samples(texts.get(label, []))))
    return out


def known_speakers(drama_id: int) -> set:
    labels = {ln["speaker"] for ln in load_lines(drama_id) if ln.get("speaker")}
    labels |= {r["speaker_label"] for r in list_characters_with_series_names(drama_id)}
    return labels


def get_one(drama_id: int, speaker_label: str) -> dict:
    for c in list_characters(drama_id):
        if c["speaker_label"] == speaker_label:
            return c
    raise NotFoundError("No such speaker in this drama.")


def check_len(name: str, value: str, cap: int):
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be a string.")
    if len(value) > cap:
        raise InvalidInputError(f"{name} is too long (max {cap} characters).")


def update_character(drama_id: int, speaker_label: str, *, character_name: str = None,
                     voice_actor: str = None, pronouns: str = None, tts_voice: str = None,
                     offline_voice: str = None, clone_engine: str = None, voice_design: str = None,
                     ref_text: str = None) -> dict:
    """Field-scoped partial update of one speaker's character row. None
    = leave alone; "" = clear (except character_name, which must be
    non-blank). pronouns: a preset or any custom text, stripped and length
    capped. clone_engine must be in dub.CLONE_ENGINES and support the
    drama's source_language (Step 26c). Raises NotFoundError (unknown
    drama or speaker), InvalidInputError. Returns the speaker's
    list_characters entry."""
    drama = require_drama(drama_id)
    if speaker_label not in known_speakers(drama_id):
        raise NotFoundError("No such speaker in this drama.")

    fields = {}
    if character_name is not None:
        check_len("character_name", character_name, MAX_NAME_LEN)
        if not character_name.strip():
            raise InvalidInputError("character_name can't be blank.")
        fields["character_name"] = character_name.strip()
    if pronouns is not None:
        check_len("pronouns", pronouns, MAX_PRONOUNS_LEN)
        fields["pronouns"] = pronouns.strip()
    for key, value, cap in (("voice_actor", voice_actor, MAX_NAME_LEN),
                            ("tts_voice", tts_voice, MAX_VOICE_LEN),
                            ("offline_voice", offline_voice, MAX_VOICE_LEN),
                            ("voice_design", voice_design, MAX_VOICE_DESIGN_LEN),
                            ("ref_text", ref_text, MAX_REF_TEXT_LEN)):
        if value is not None:
            check_len(key, value, cap)
            fields[key] = value.strip()
    if clone_engine is not None:
        if clone_engine != "":
            if clone_engine not in dub.CLONE_ENGINES:
                raise InvalidInputError("Unknown clone_engine.")
            lang = _source_language(drama)
            if not dub.clone_engine_supports_language(clone_engine, lang):
                raise InvalidInputError(
                    f"That clone_engine doesn't support the drama's source language ({lang}).")
        fields["clone_engine"] = clone_engine

    if fields:
        upsert_character(drama_id, speaker_label, **fields)
    return get_one(drama_id, speaker_label)


def list_series_characters(series_id: int) -> list:
    """A series' characters (id, name, aliases, notes, pronouns). No
    fingerprint data. Empty list for an unknown series."""
    check_id("series_id", series_id)
    return [{
        "id": r["id"],
        "character_name": r["character_name"],
        "aliases": r.get("aliases") or "",
        "notes": r.get("notes") or "",
        "pronouns": r.get("gender") or "",
    } for r in _db_list_series_characters(series_id)]


def get_clone_engine_options(drama_id: int) -> dict:
    """Clone engines usable for this drama's source language (Step 26c:
    never offer an engine that can't speak it), with capability flags.
    Raises NotFoundError for an unknown drama."""
    lang = _source_language(require_drama(drama_id))
    engines = []
    for engine, label in dub.CLONE_ENGINES.items():
        if not dub.clone_engine_supports_language(engine, lang):
            continue
        engines.append({
            "id": engine,
            "label": label,
            "is_default": engine == dub.DEFAULT_CLONE_ENGINE,
            "language_gated": engine in dub.CLONE_ENGINE_ORIGINAL_LANGUAGES,
            "local_model": engine in dub.LOCAL_MODEL_ENGINES,
        })
    return {"source_language": lang, "default_engine": dub.DEFAULT_CLONE_ENGINE,
            "engines": engines}


def list_voice_bank() -> list:
    """Voice bank picklist (alphabetical): metadata only, never the clip
    filename or path."""
    return [{
        "id": e["id"],
        "name": e["name"],
        "clone_engine": e.get("clone_engine") or "",
        "voice_design": e.get("voice_design") or "",
        "language": e.get("language") or "",
        "notes": e.get("notes") or "",
        "ref_text_present": bool((e.get("ref_text") or "").strip()),
    } for e in list_voice_bank_entries()]


def apply_voice_bank_entry(drama_id: int, speaker_label: str, voice_bank_id: int) -> dict:
    """db.apply_voice_bank_entry copies the
    bank's clip into this drama's own folder and sets the speaker's
    ref audio / ref_text / clone_engine / voice_design (only this
    drama's row). Raises NotFoundError for an unknown drama, speaker, or
    bank entry, or a bank entry whose clip file is missing.
    InvalidInputError for a speaker label that can't be a filename part
    (db builds `voicebank_{id}_{label}{ext}` from it): a slash, backslash,
    "..", control character, or more than MAX_SPEAKER_LABEL_LEN chars.

    The entry's clone_engine must support the drama's source language, exactly as update_character
    requires (Step 26c). Returns the speaker's list_characters entry."""
    drama = require_drama(drama_id)
    check_id("voice_bank_id", voice_bank_id)
    if (not isinstance(speaker_label, str) or len(speaker_label) > MAX_SPEAKER_LABEL_LEN
            or ".." in speaker_label or "/" in speaker_label or chr(92) in speaker_label
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in speaker_label)):
        raise InvalidInputError("speaker_label can't be used for a voice bank apply.")
    if speaker_label not in known_speakers(drama_id):
        raise NotFoundError("No such speaker in this drama.")
    entry = get_voice_bank_entry(voice_bank_id)
    if entry is None:
        raise NotFoundError(f"No voice bank entry with id {voice_bank_id}.")
    clip = entry.get("clip_filename") or ""
    # db.VOICE_BANK_DIR is re-read each call (it's reassigned when the library moves).
    if not clip or not os.path.isfile(os.path.join(db.VOICE_BANK_DIR, clip)):
        raise NotFoundError("That voice bank entry's audio clip is missing.")
    engine = entry.get("clone_engine") or ""
    if engine and not dub.clone_engine_supports_language(engine, _source_language(drama)):
        raise InvalidInputError(
            "That voice bank entry's clone_engine doesn't support the drama's source language.")
    _db_apply_voice_bank_entry(voice_bank_id, drama_dir(drama_id), drama_id, speaker_label)
    return get_one(drama_id, speaker_label)


# --- C02: recurring-voice suggestions ("sounds like X") ------------------------

def _series_characters_of(drama: dict) -> list:
    series_id = drama.get("series_id")
    return db.list_series_characters(series_id) if series_id else []


def _load_voice_embeddings(drama_id: int) -> dict:
    """The drama's stored diarization voice embeddings, or {} when there
    are none (no speaker run yet, pyannote 3.x, an unreadable file)."""
    try:
        import diarize
        embeddings = diarize.load_embeddings(drama_dir(drama_id))
    except (ImportError, OSError, ValueError):
        return {}
    return embeddings if isinstance(embeddings, dict) else {}


def _current_suggestions(drama_id: int, drama: dict):
    """(suggestions, embeddings): only for a
    drama in a series with characters and stored embeddings; speakers
    that already have a name and dismissed pairs are skipped."""
    series_chars = _series_characters_of(drama)
    if not series_chars:
        return [], {}
    # A renamed speaker leaves its old label in the saved embeddings; offering a
    # match for it would re-create that label.
    known = known_speakers(drama_id)
    embeddings = {k: v for k, v in _load_voice_embeddings(drama_id).items() if k in known}
    if not embeddings:
        return [], {}
    try:
        import voice_id
    except ImportError:
        return [], {}
    already_named = {c["speaker_label"] for c in list_characters(drama_id) if c["character_name"]}
    try:
        suggestions = voice_id.suggest_speaker_matches(
            embeddings, series_chars, already_named=already_named,
            dismissed=db.list_dismissed_voice_suggestions(drama_id))
    except (TypeError, ValueError):
        # A malformed stored embedding: no suggestions rather than a 500.
        return [], {}
    return [{"speaker_label": s["speaker_label"],
             "series_character_id": s["series_character_id"],
             "character_name": s["character_name"],
             "similarity": round(float(s["similarity"]), 4)} for s in suggestions], embeddings


def list_voice_suggestions(drama_id: int) -> list:
    """Experimental "this speaker sounds like <series character>"
    suggestions, best first. Empty (never an error) when the drama has no
    series, the series has no characters with a voice fingerprint, or no
    voice embeddings were stored by speaker detection. Nothing here names
    a speaker; accept/reject do that on an explicit request. Raises
    NotFoundError for an unknown drama."""
    drama = require_drama(drama_id)
    return _current_suggestions(drama_id, drama)[0]


def _check_suggestion_args(speaker_label, series_character_id):
    if (not isinstance(speaker_label, str) or not speaker_label
            or len(speaker_label) > MAX_SPEAKER_LABEL_LEN):
        raise InvalidInputError("speaker_label is missing or too long.")
    check_id("series_character_id", series_character_id)


def accept_voice_suggestion(drama_id: int, speaker_label: str, series_character_id: int) -> dict:
    """Accept one CURRENTLY offered suggestion: name the
    speaker after the series character, link it (series_character_id)
    and blend this drama's embedding into that character's voice
    fingerprint. Only this drama's row and that one series character are
    written. A pair that isn't offered any more (already named,
    dismissed, below threshold, embeddings gone) is a NotFoundError.
    Returns {"character": list_characters entry, "suggestions": [...]}."""
    drama = require_drama(drama_id)
    _check_suggestion_args(speaker_label, series_character_id)
    suggestions, embeddings = _current_suggestions(drama_id, drama)
    match = next((s for s in suggestions if s["speaker_label"] == speaker_label
                  and s["series_character_id"] == series_character_id), None)
    if match is None:
        raise NotFoundError("That voice suggestion isn't offered any more.")
    # One conditional transaction: a concurrent accept (or a name saved
    # meanwhile) makes this a no-op, never a second link or blend.
    if not db.accept_voice_link(drama_id, speaker_label, series_character_id,
                                match["character_name"], embeddings[speaker_label]):
        raise NotFoundError("That voice suggestion isn't offered any more.")
    return {"character": get_one(drama_id, speaker_label),
            "suggestions": _current_suggestions(drama_id, drama)[0]}


def reject_voice_suggestion(drama_id: int, speaker_label: str, series_character_id: int) -> dict:
    """Record that this (speaker, series character) suggestion was wrong
    for this drama (db.dismiss_voice_suggestion; idempotent). Nothing
    else changes; a different candidate for the same speaker can still
    surface. The series character must belong to the drama's series and
    the speaker must be known to this drama or its stored embeddings.
    Returns {"character": None, "suggestions": [...]}."""
    drama = require_drama(drama_id)
    _check_suggestion_args(speaker_label, series_character_id)
    if not any(sc["id"] == series_character_id for sc in _series_characters_of(drama)):
        raise NotFoundError("No such character in this drama's series.")
    if (speaker_label not in known_speakers(drama_id)
            and speaker_label not in _load_voice_embeddings(drama_id)):
        raise NotFoundError("No such speaker in this drama.")
    db.dismiss_voice_suggestion(drama_id, speaker_label, series_character_id)
    return {"character": None, "suggestions": _current_suggestions(drama_id, drama)[0]}


# --- C08: remember as a known series character ----------------------------------

def remember_series_character(drama_id: int, speaker_label: str) -> dict:
    """The opt-in "Remember <name> as a known character in this
    series": uses the speaker's SAVED name (never a half-typed one), adds
    it to the drama's series (db.upsert_series_character, with this
    drama's pronouns as the series default when set) and links the
    speaker to it. When the series already has that name, the speaker is
    linked to the existing character and nothing about it (aliases,
    notes, pronouns) is overwritten. Voice: when speaker detection stored
    an embedding for this speaker, it is blended into the character's
    voice fingerprint (as an accepted suggestion does), so later dramas
    can be offered the match.

    Not repeatable: a speaker already linked to a series character (by an
    earlier Remember or an accepted voice suggestion) is refused with
    ConflictError and nothing changes -- no relink, no second blend of the
    same embedding into the fingerprint.

    Raises NotFoundError (unknown drama or speaker), InvalidInputError
    (the drama has no series, or the speaker has no saved name),
    ConflictError (the speaker is already linked to a series character).
    Returns {"character": entry, "series_character": series entry,
    "created": bool}."""
    drama = require_drama(drama_id)
    if not isinstance(speaker_label, str) or speaker_label not in known_speakers(drama_id):
        raise NotFoundError("No such speaker in this drama.")
    series_id = drama.get("series_id")
    if not series_id:
        raise InvalidInputError("This drama isn't in a series, so there is no series cast to add to.")
    entry = get_one(drama_id, speaker_label)
    if entry["series_character_id"]:
        raise ConflictError("This speaker is already linked to a character in this series.")
    name = entry["character_name"].strip()
    if not name:
        raise InvalidInputError("Save a name for this speaker first.")
    embedding = _load_voice_embeddings(drama_id).get(speaker_label)
    # One conditional transaction (link only while still unlinked and still
    # so named; insert-only into the series cast).
    linked = db.remember_speaker_as_series_character(
        drama_id, speaker_label, series_id, name,
        gender=tguide.normalize_pronouns(entry["pronouns"]) or None,
        embedding=embedding if isinstance(embedding, list) and embedding else None)
    if linked is None:
        raise ConflictError("This speaker was linked or renamed meanwhile; reload and try again.")
    sc_id, created = linked
    series_entry = next(sc for sc in list_series_characters(series_id) if sc["id"] == sc_id)
    return {"character": get_one(drama_id, speaker_label), "series_character": series_entry,
            "created": created}


# --- Rename a speaker once ----------------------------------------------------

_RENAME_CONFLICT = ("Another speaker already has that name. Merging speakers isn't "
                    "supported here; pick a different name.")
_BUSY = ("A background job is still running for this drama -- wait for it "
         "to finish or cancel it before {what}.")


_BAD_CATEGORIES = ("Cc", "Cf", "Cs", "Cn", "Co", "Zl", "Zp")


def _clean_label(field: str, value) -> str:
    """Strip and check a speaker name by the voice-bank label rules (it can end
    up in a filename): 1..MAX_SPEAKER_LABEL_LEN chars, no slashes or '..', and
    no control, format (zero-width, bidi), surrogate, unassigned, private-use or
    line/paragraph separator characters."""
    if not isinstance(value, str):
        raise InvalidInputError(f"{field} must be a string.")
    v = value.strip()
    if not v:
        raise InvalidInputError("The name can't be blank.")
    if len(v) > MAX_SPEAKER_LABEL_LEN:
        raise InvalidInputError(f"{field} is too long (max {MAX_SPEAKER_LABEL_LEN} characters).")
    if (".." in v or "/" in v or chr(92) in v
            or any(unicodedata.category(ch) in _BAD_CATEGORIES for ch in v)):
        raise InvalidInputError(f"{field} can't contain slashes, '..' or invisible/control characters.")
    return v


def _norm(name) -> str:
    text = unicodedata.normalize("NFKC", name or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return " ".join(text.casefold().split())


def _lines_with_label(drama_id: int, label: str) -> list:
    return [ln for ln in db.load_line_objects(drama_id) if ln.speaker == label]


def _check_target(drama_id: int, name: str, own_label: str) -> dict:
    """Refuses a name that matches (normalised) another speaker's label or
    shown name, series names included, and a speaker linked to a series
    character. Returns the speaker's own characters row ({} if none)."""
    rows = list_characters_with_series_names(drama_id)
    taken = {_norm(x) for x in known_speakers(drama_id) if x != own_label}
    for r in rows:
        if r["speaker_label"] != own_label:
            taken |= {_norm(r.get("character_name")), _norm(r.get("series_character_name"))} - {""}
    if _norm(name) in taken:
        raise ConflictError(_RENAME_CONFLICT)
    row = next((r for r in rows if r["speaker_label"] == own_label), {})
    if row.get("series_character_id"):
        raise ConflictError("This speaker is linked to a series character; unlink it first "
                            "or rename the series character.")
    return row


def _move(drama_id: int, old_label: str, new_label: str, character_name, updates: list) -> int:
    result = db.rename_speaker_atomic(drama_id, old_label, new_label, character_name, updates)
    if result == "changed":
        raise ConflictError("Some lines changed meanwhile; nothing was changed. Try again.")
    if result == "name_taken":
        raise ConflictError(_RENAME_CONFLICT)
    return result


def rename_speaker(drama_id: int, speaker_label: str, new_name: str) -> dict:
    """Names a speaker once: every line labelled speaker_label gets new_name as
    its speaker, and the speaker's Characters row (voice, pronouns) moves to
    new_name with it as character_name, so translation context sees the name.
    The lines are marked set by hand, so re-assigning from saved turns keeps
    the name. Lines and row change in one database transaction. A speaker with
    no lines moves only its row. Raises NotFoundError (unknown drama or
    speaker), InvalidInputError (bad, blank or unchanged name, or a current
    label that undo could not restore), ConflictError (a job is running, the
    name matches another speaker, the speaker is linked to a series character,
    or a line changed meanwhile). On any failure nothing is changed. Returns
    {"characters": list_characters, "renamed": n, "undo": {...}}; pass undo to
    undo_rename_speaker."""
    require_drama(drama_id)
    if not isinstance(speaker_label, str) or speaker_label not in known_speakers(drama_id):
        raise NotFoundError("No such speaker in this drama.")
    if _clean_label("speaker_label", speaker_label) != speaker_label:
        raise InvalidInputError("This speaker's current name has characters that can't be "
                                "renamed here; fix it in the line editor first.")
    name = _clean_label("new_name", new_name)
    if name == speaker_label:
        raise InvalidInputError("That is already this speaker's name.")
    row = _check_target(drama_id, name, speaker_label)
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY.format(what="renaming a speaker"))
    lines = _lines_with_label(drama_id, speaker_label)
    previous = [{"id": ln.id, "speaker": speaker_label, "speaker_manual": bool(ln.speaker_manual)}
                for ln in lines]
    updates = [{"id": p["id"], "expect_speaker": speaker_label, "expect_manual": p["speaker_manual"],
                "speaker": name, "manual": True} for p in previous]
    n = _move(drama_id, speaker_label, name, name, updates)
    undo = {"speaker_label": name, "previous_label": speaker_label,
            "previous_character_name": row.get("character_name"), "previous": previous}
    return {"characters": list_characters(drama_id), "renamed": n, "undo": undo}


def undo_rename_speaker(drama_id: int, undo: dict) -> dict:
    """Reverses rename_speaker from the undo it returned, under the same name,
    uniqueness and series-link rules as a rename (undo is not a free rename).
    The undo must cover exactly the lines that carry the new name now; if
    lines were added, moved or edited since (a re-split, say) it is refused.
    Lines get their previous label and manual flag back and the Characters row
    moves back in one transaction. Raises NotFoundError (unknown drama, or the
    name is no longer a speaker), InvalidInputError (bad undo data),
    ConflictError (job running, clash, or the lines changed). Returns
    {"characters": ..., "renamed": n, "undo": None}."""
    require_drama(drama_id)
    new_label, old_label = undo.get("speaker_label"), undo.get("previous_label")
    previous, old_name = undo.get("previous"), undo.get("previous_character_name")
    if not isinstance(new_label, str) or not isinstance(previous, list):
        raise InvalidInputError("That undo isn't valid.")
    old_label = _clean_label("previous_label", old_label)
    if old_name is not None:
        check_len("previous_character_name", old_name, MAX_NAME_LEN)
    for p in previous:
        if (not isinstance(p, dict) or isinstance(p.get("id"), bool)
                or not isinstance(p.get("id"), int) or str(p.get("speaker")).strip() != old_label
                or not isinstance(p.get("speaker_manual"), bool)):
            raise InvalidInputError("That undo isn't valid.")
    known = known_speakers(drama_id)
    if new_label not in known:
        raise NotFoundError("That speaker was renamed again or removed; nothing to undo.")
    if old_label in known:
        raise ConflictError("The old label is in use again, so the rename can't be undone.")
    _check_target(drama_id, old_label, new_label)
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY.format(what="undoing"))
    lines = _lines_with_label(drama_id, new_label)
    by_id = {p["id"]: p for p in previous}
    if {ln.id for ln in lines} != set(by_id) or len(by_id) != len(previous):
        raise ConflictError("The lines changed since the rename, so it can't be undone.")
    updates = [{"id": ln.id, "expect_speaker": new_label, "expect_manual": bool(ln.speaker_manual),
                "speaker": old_label, "manual": by_id[ln.id]["speaker_manual"]} for ln in lines]
    n = _move(drama_id, new_label, old_label, old_name, updates)
    return {"characters": list_characters(drama_id), "renamed": n, "undo": None}


# --- Merge two speakers -------------------------------------------------------

_MERGE_TEXT_FIELDS = tuple(f for f in db.MERGE_ROW_FIELDS if f != "series_character_id")


def _row_snapshot(row):
    return None if row is None else {f: row.get(f) for f in db.MERGE_ROW_FIELDS}


def _check_snapshot(drama: dict, snap, *, allow_none: bool):
    """Validates a Characters-row snapshot from an undo (it comes from the
    client). The reference clip is a bare file name that must already sit in
    the drama's folder, so a forged undo can't point a row at another path."""
    if snap is None and allow_none:
        return None
    if not isinstance(snap, dict) or set(snap) != set(db.MERGE_ROW_FIELDS):
        raise InvalidInputError("That undo isn't valid.")
    for f in _MERGE_TEXT_FIELDS:
        if snap[f] is not None:
            check_len(f, snap[f], 100000)
    clip = snap["ref_audio_filename"]
    if clip and (os.path.basename(clip) != clip or clip in (".", "..") or "\\" in clip
                 or not os.path.isfile(os.path.join(drama_dir(drama["id"]), clip))):
        raise InvalidInputError("That undo isn't valid.")
    sc = snap["series_character_id"]
    if sc is not None and (isinstance(sc, bool) or not isinstance(sc, int)
                           or not drama.get("series_id")
                           or sc not in {x["id"] for x in _db_list_series_characters(drama["series_id"])}):
        raise InvalidInputError("That undo isn't valid.")
    return snap


def _merge_result(drama_id: int, n: int, undo) -> dict:
    return {"characters": list_characters(drama_id), "moved": n, "undo": undo}


def merge_speakers(drama_id: int, source: str, target: str) -> dict:
    """Merges speaker `source` into `target`: every line labelled source gets
    target as its speaker (marked set by hand, like a rename), and the
    source's Characters row is folded into the target's -- a blank target
    field (name, voice, pronouns, clip, series link...) takes the source's
    value, a filled one is kept -- then removed, so no duplicate or orphaned
    row is left. Only the speaker columns of lines change. One database
    transaction. Raises NotFoundError (unknown drama or speaker),
    InvalidInputError (same speaker twice, or a label undo couldn't restore),
    ConflictError (job running, the two are linked to different series
    characters, or a line changed meanwhile). Returns {"characters": ...,
    "moved": n lines, "undo": {...}}; pass undo to
    undo_merge_speakers."""
    require_drama(drama_id)
    known = known_speakers(drama_id)
    for label in (source, target):
        if not isinstance(label, str) or label not in known:
            raise NotFoundError("No such speaker in this drama.")
    if source == target:
        raise InvalidInputError("Pick two different speakers.")
    if _clean_label("source", source) != source or _clean_label("target", target) != target:
        raise InvalidInputError("A speaker name has characters that can't be merged here; "
                                "fix it in the line editor first.")
    rows = {r["speaker_label"]: r for r in db.list_characters(drama_id)}
    src_row, tgt_row = rows.get(source), rows.get(target)
    src_link = (src_row or {}).get("series_character_id")
    tgt_link = (tgt_row or {}).get("series_character_id")
    if src_link and tgt_link and src_link != tgt_link:
        raise ConflictError("These speakers are linked to different series characters; "
                            "unlink one of them first.")
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY.format(what="merging speakers"))
    previous = [{"id": ln.id, "speaker": source, "speaker_manual": bool(ln.speaker_manual)}
                for ln in _lines_with_label(drama_id, source)]
    updates = [{"id": p["id"], "expect_speaker": source, "expect_manual": p["speaker_manual"],
                "speaker": target, "manual": True} for p in previous]
    n = db.merge_speakers_atomic(drama_id, source, target, updates)
    if n == "changed":
        raise ConflictError("Some lines changed meanwhile; nothing was changed. Try again.")
    undo = {"source_label": source, "target_label": target, "source_row": _row_snapshot(src_row or {}), "target_row": _row_snapshot(tgt_row),
            "previous": previous}
    return _merge_result(drama_id, n, undo)


def undo_merge_speakers(drama_id: int, undo: dict) -> dict:
    """Reverses merge_speakers from the undo it returned: the moved lines get
    their label and manual flag back, the source's Characters row returns and
    the target's fields go back to what they were. Refused when the source
    label is in use again, or when any moved line no longer carries the target
    label (it was edited, deleted or re-split since). Raises NotFoundError
    (unknown drama, or the target is gone), InvalidInputError (bad undo data),
    ConflictError. Returns {"characters": ..., "moved": n, "undo": None}."""
    drama = require_drama(drama_id)
    source, target = undo.get("source_label"), undo.get("target_label")
    previous = undo.get("previous")
    if not isinstance(source, str) or not isinstance(target, str) or not isinstance(previous, list):
        raise InvalidInputError("That undo isn't valid.")
    source, target = _clean_label("source_label", source), _clean_label("target_label", target)
    if source == target:
        raise InvalidInputError("That undo isn't valid.")
    for p in previous:
        if (not isinstance(p, dict) or isinstance(p.get("id"), bool)
                or not isinstance(p.get("id"), int) or str(p.get("speaker")).strip() != source
                or not isinstance(p.get("speaker_manual"), bool)):
            raise InvalidInputError("That undo isn't valid.")
    restore = {"source": _check_snapshot(drama, undo.get("source_row"), allow_none=False),
               "target": _check_snapshot(drama, undo.get("target_row"), allow_none=True)}
    known = known_speakers(drama_id)
    if target not in known:
        raise NotFoundError("The speaker these were merged into was renamed or removed; "
                            "nothing to undo.")
    if source in known:
        raise ConflictError("The old speaker label is in use again, so the merge can't be undone.")
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY.format(what="undoing"))
    by_id = {p["id"]: p for p in previous}
    if len(by_id) != len(previous):
        raise InvalidInputError("That undo isn't valid.")
    on_target = {ln.id: ln for ln in _lines_with_label(drama_id, target)}
    if not set(by_id) <= set(on_target):
        raise ConflictError("The lines changed since the merge, so it can't be undone.")
    updates = [{"id": i, "expect_speaker": target, "expect_manual": bool(on_target[i].speaker_manual),
                "speaker": source, "manual": by_id[i]["speaker_manual"]} for i in by_id]
    n = db.merge_speakers_atomic(drama_id, source, target, updates, restore_row=restore)
    if n == "changed":
        raise ConflictError("Some lines changed meanwhile; nothing was changed. Try again.")
    if n == "name_taken":
        raise ConflictError("The old speaker label is in use again, so the merge can't be undone.")
    return _merge_result(drama_id, n, None)
