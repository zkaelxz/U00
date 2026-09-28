"""
services/characters_service.py -- per-drama characters / voice config
(migration Slice 42), the UI-free half of the Translate tab's "People &
pronouns" panel and "Section 6" per-character voice config in
`tabs/workspace_tab.py`.

Covers: listing a drama's speakers with their character/voice settings,
a validated field-scoped partial update, series-character listing, the
clone-engine picklist (Step 26c language rule), and the voice bank
(list + apply).

Speaker set: like the tab (`sorted({ln.speaker for ln in lines if
ln.speaker})`), a speaker label is known for a drama when it appears on
one of that drama's lines OR already has a `characters` row for it.

Every read and write is keyed by (drama_id, speaker_label); nothing here
touches another drama's rows.

Update semantics follow db.upsert_character: None leaves a stored value
untouched, "" clears it.

Results never contain filesystem paths, URLs, or the reference-audio
filename -- only `has_ref_audio` / `ref_text_present` booleans.

Deliberately NOT here:
  - Reference-audio upload / auto-extract (multipart / ffmpeg, a
    different risk tier).
  - Character deletion and series-character create/rename/delete/link
    (only listing is covered).
  - Dub generation itself.

No Streamlit or FastAPI import.
"""
import dub
from db import (apply_voice_bank_entry as _db_apply_voice_bank_entry, drama_dir, get_drama,
                get_voice_bank_entry, list_characters_with_series_names,
                list_series_characters as _db_list_series_characters,
                list_voice_bank_entries, load_lines, upsert_character)
from services.service_errors import InvalidInputError, NotFoundError

MAX_NAME_LEN = 200
MAX_PRONOUNS_LEN = 40
MAX_VOICE_LEN = 200
MAX_VOICE_DESIGN_LEN = 1000
MAX_REF_TEXT_LEN = 5000


def _require_drama(drama_id: int) -> dict:
    drama = get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _source_language(drama: dict) -> str:
    return drama.get("source_language") or "zh"


def _character_dict(row: dict, line_count: int) -> dict:
    return {
        "speaker_label": row["speaker_label"],
        "character_name": row.get("character_name") or "",
        "pronouns": row.get("pronouns") or "",
        "tts_voice": row.get("tts_voice") or "",
        "offline_voice": row.get("offline_voice") or "",
        "clone_engine": row.get("clone_engine") or "",
        "voice_design": row.get("voice_design") or "",
        "has_ref_audio": bool(row.get("ref_audio_filename")),
        "ref_text_present": bool((row.get("ref_text") or "").strip()),
        "series_character_id": row.get("series_character_id"),
        "series_character_name": row.get("series_character_name") or "",
        "line_count": line_count,
    }


def list_characters(drama_id: int) -> list:
    """One entry per known speaker (line speakers plus stored character
    rows), sorted by label. A speaker with no stored row gets blank
    fields. A linked series character's current name wins for
    character_name (db.list_characters_with_series_names). Raises
    NotFoundError for an unknown drama."""
    _require_drama(drama_id)
    counts = {}
    for ln in load_lines(drama_id):
        if ln.get("speaker"):
            counts[ln["speaker"]] = counts.get(ln["speaker"], 0) + 1
    rows = {r["speaker_label"]: r for r in list_characters_with_series_names(drama_id)}
    out = []
    for label in sorted(set(counts) | set(rows)):
        row = rows.get(label) or {"speaker_label": label}
        out.append(_character_dict(row, counts.get(label, 0)))
    return out


def _known_speakers(drama_id: int) -> set:
    labels = {ln["speaker"] for ln in load_lines(drama_id) if ln.get("speaker")}
    labels |= {r["speaker_label"] for r in list_characters_with_series_names(drama_id)}
    return labels


def _get_one(drama_id: int, speaker_label: str) -> dict:
    for c in list_characters(drama_id):
        if c["speaker_label"] == speaker_label:
            return c
    raise NotFoundError(f"No speaker {speaker_label!r} in drama {drama_id}.")


def _check_len(name: str, value: str, cap: int):
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be a string.")
    if len(value) > cap:
        raise InvalidInputError(f"{name} is too long (max {cap} characters).")


def update_character(drama_id: int, speaker_label: str, *, character_name: str = None,
                     pronouns: str = None, tts_voice: str = None, offline_voice: str = None,
                     clone_engine: str = None, voice_design: str = None,
                     ref_text: str = None) -> dict:
    """Field-scoped partial update of one speaker's character row. None
    = leave alone; "" = clear (except character_name, which must be
    non-blank). pronouns: a preset or any custom text (the tab's picker
    offers presets plus free-text "Custom..."), stripped and length
    capped. clone_engine must be in dub.CLONE_ENGINES and support the
    drama's source_language (Step 26c). Raises NotFoundError (unknown
    drama or speaker), InvalidInputError. Returns the speaker's
    list_characters entry."""
    drama = _require_drama(drama_id)
    if speaker_label not in _known_speakers(drama_id):
        raise NotFoundError(f"No speaker {speaker_label!r} in drama {drama_id}.")

    fields = {}
    if character_name is not None:
        _check_len("character_name", character_name, MAX_NAME_LEN)
        if not character_name.strip():
            raise InvalidInputError("character_name can't be blank.")
        fields["character_name"] = character_name.strip()
    if pronouns is not None:
        _check_len("pronouns", pronouns, MAX_PRONOUNS_LEN)
        fields["pronouns"] = pronouns.strip()
    for key, value, cap in (("tts_voice", tts_voice, MAX_VOICE_LEN),
                            ("offline_voice", offline_voice, MAX_VOICE_LEN),
                            ("voice_design", voice_design, MAX_VOICE_DESIGN_LEN),
                            ("ref_text", ref_text, MAX_REF_TEXT_LEN)):
        if value is not None:
            _check_len(key, value, cap)
            fields[key] = value.strip()
    if clone_engine is not None:
        if clone_engine != "":
            if clone_engine not in dub.CLONE_ENGINES:
                raise InvalidInputError(f"Unknown clone_engine {clone_engine!r}.")
            lang = _source_language(drama)
            if not dub.clone_engine_supports_language(clone_engine, lang):
                raise InvalidInputError(
                    f"clone_engine {clone_engine!r} doesn't support source language {lang!r}.")
        fields["clone_engine"] = clone_engine

    if fields:
        upsert_character(drama_id, speaker_label, **fields)
    return _get_one(drama_id, speaker_label)


def list_series_characters(series_id: int) -> list:
    """A series' characters (id, name, aliases, notes, pronouns). No
    fingerprint data. Empty list for an unknown series."""
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
    lang = _source_language(_require_drama(drama_id))
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
    """Mirrors the tab's apply: db.apply_voice_bank_entry copies the
    bank's clip into this drama's own folder and sets the speaker's
    ref audio / ref_text / clone_engine / voice_design (only this
    drama's row). Raises NotFoundError for an unknown drama, speaker, or
    bank entry. Returns the speaker's list_characters entry."""
    _require_drama(drama_id)
    if speaker_label not in _known_speakers(drama_id):
        raise NotFoundError(f"No speaker {speaker_label!r} in drama {drama_id}.")
    if get_voice_bank_entry(voice_bank_id) is None:
        raise NotFoundError(f"No voice bank entry with id {voice_bank_id}.")
    _db_apply_voice_bank_entry(voice_bank_id, drama_dir(drama_id), drama_id, speaker_label)
    return _get_one(drama_id, speaker_label)
