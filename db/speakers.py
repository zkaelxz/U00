"""Characters and speakers: a drama's speaker labels and their character,
voice and dubbing settings, speaker rename and merge with its undo, series
characters and voice links, and the voice bank of reusable cloned voices.
The tables are created by db.init_db."""

import contextlib
from lib import clock
import json
import os
import time
from typing import List

import db


# ---------------------------------------------------------------------------
# Characters CRUD (speaker-label -> character name / voice actor / TTS voice)
# ---------------------------------------------------------------------------

def upsert_character(drama_id: int, speaker_label: str, character_name: str | None = None,
                      voice_actor: str | None = None, tts_voice: str | None = None,
                      ref_audio_filename: str | None = None, ref_text: str | None = None,
                      elevenlabs_voice_id: str | None = None, series_character_id: int | None = None,
                      pronouns: str | None = None, clone_engine: str | None = None, voice_design: str | None = None,
                      offline_voice: str | None = None):
    """pronouns/voice_design: None leaves an existing value untouched; ""
    clears it."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO characters (drama_id, speaker_label, character_name, voice_actor, tts_voice,
                                     ref_audio_filename, ref_text, elevenlabs_voice_id, series_character_id,
                                     pronouns, clone_engine, voice_design, offline_voice)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, speaker_label) DO UPDATE SET
                clone_engine = COALESCE(excluded.clone_engine, characters.clone_engine),
                voice_design = COALESCE(excluded.voice_design, characters.voice_design),
                pronouns = COALESCE(excluded.pronouns, characters.pronouns),
                character_name = COALESCE(excluded.character_name, characters.character_name),
                voice_actor = COALESCE(excluded.voice_actor, characters.voice_actor),
                tts_voice = COALESCE(excluded.tts_voice, characters.tts_voice),
                offline_voice = COALESCE(excluded.offline_voice, characters.offline_voice),
                ref_audio_filename = COALESCE(excluded.ref_audio_filename, characters.ref_audio_filename),
                ref_text = COALESCE(excluded.ref_text, characters.ref_text),
                elevenlabs_voice_id = COALESCE(excluded.elevenlabs_voice_id, characters.elevenlabs_voice_id),
                series_character_id = COALESCE(excluded.series_character_id, characters.series_character_id)
        """, (drama_id, speaker_label, character_name, voice_actor, tts_voice, ref_audio_filename, ref_text,
              elevenlabs_voice_id, series_character_id, pronouns, clone_engine, voice_design,
              offline_voice))
        conn.commit()


def list_characters(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM characters WHERE drama_id = ? ORDER BY speaker_label", (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def clear_character_series_link(drama_id: int, speaker_label: str):
    """Unlinks one speaker from its series character (upsert_character's
    COALESCE can't write NULL). The character keeps its own name."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE characters SET series_character_id = NULL "
                     "WHERE drama_id = ? AND speaker_label = ?", (drama_id, speaker_label))
        conn.commit()


def rename_speaker_atomic(drama_id: int, old_label: str, new_label: str, character_name,
                          line_updates: list):
    """Renames a speaker in ONE transaction: each of line_updates ({id,
    expect_speaker, expect_manual, speaker, manual}) is written only while the
    row still holds the expected speaker and manual flag, then the characters
    row (voice, pronouns, series link) and dismissed voice matches move from
    old_label to new_label with character_name set (None clears it; a speaker
    with no row gets one). Returns the number of lines written, or "changed"
    (a line no longer matched) / "name_taken" (new_label already has a row);
    in those cases, and on any error, nothing is written."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        written = 0
        for u in line_updates:
            cur = conn.execute(
                "UPDATE lines SET speaker = ?, speaker_manual = ? WHERE id = ? AND drama_id = ? "
                "AND COALESCE(speaker, '') = ? AND COALESCE(speaker_manual, 0) = ?",
                (u["speaker"], int(bool(u["manual"])), u["id"], drama_id,
                 u["expect_speaker"], int(bool(u["expect_manual"]))))
            if cur.rowcount != 1:
                conn.rollback()
                return "changed"
            written += 1
        # A line outside the update still on either label would end up merged
        # with the renamed speaker or stranded on the old label.
        ids = {u["id"] for u in line_updates}
        if any(r["id"] not in ids for r in conn.execute(
                "SELECT id FROM lines WHERE drama_id = ? AND COALESCE(speaker, '') IN (?, ?)",
                (drama_id, old_label, new_label)).fetchall()):
            conn.rollback()
            return "changed"
        if conn.execute("SELECT 1 FROM characters WHERE drama_id = ? AND speaker_label = ?",
                        (drama_id, new_label)).fetchone():
            conn.rollback()
            return "name_taken"
        conn.execute("INSERT OR IGNORE INTO characters (drama_id, speaker_label) VALUES (?, ?)",
                     (drama_id, old_label))
        conn.execute("UPDATE characters SET speaker_label = ?, character_name = ? "
                     "WHERE drama_id = ? AND speaker_label = ?",
                     (new_label, character_name, drama_id, old_label))
        conn.execute("UPDATE OR IGNORE voice_suggestion_dismissals SET speaker_label = ? "
                     "WHERE drama_id = ? AND speaker_label = ?", (new_label, drama_id, old_label))
        conn.commit()
        return written
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


MERGE_ROW_FIELDS = ("character_name", "voice_actor", "tts_voice", "offline_voice",
                    "ref_audio_filename", "ref_text", "elevenlabs_voice_id", "clone_engine",
                    "voice_design", "pronouns", "series_character_id")
# One reference voice: a clip, its transcript, the engine it was made for, or a
# designed voice. Merging takes all four from one speaker, never a mix, since
# dubbing clones from the clip using the transcript and engine stored with it.
_MERGE_VOICE_GROUP = ("ref_audio_filename", "ref_text", "clone_engine", "voice_design")
MERGE_UNDO_TTL_SECONDS = 15 * 60


def _blank(value) -> bool:
    return value is None or value == ""


def _merge_row(conn, drama_id: int, label: str):
    row = conn.execute("SELECT * FROM characters WHERE drama_id = ? AND speaker_label = ?",
                       (drama_id, label)).fetchone()
    return None if row is None else {c: row[c] for c in db.MERGE_ROW_FIELDS}


def _merge_dismissals(conn, drama_id: int, labels: tuple) -> list:
    marks = ", ".join("?" * len(labels))
    return [dict(r) for r in conn.execute(
        "SELECT speaker_label, series_character_id, created_at FROM voice_suggestion_dismissals "
        f"WHERE drama_id = ? AND speaker_label IN ({marks}) ORDER BY id", (drama_id, *labels))]


def _folded_row(src: dict, tgt: dict) -> dict:
    out = dict(tgt)
    for col in db.MERGE_ROW_FIELDS:
        if col not in _MERGE_VOICE_GROUP and _blank(tgt[col]):
            out[col] = src[col]
    if (_blank(tgt["ref_audio_filename"]) and _blank(tgt["voice_design"])
            and not (_blank(src["ref_audio_filename"]) and _blank(src["voice_design"]))):
        for col in _MERGE_VOICE_GROUP:
            out[col] = src[col]
    return out


def merge_speakers_atomic(drama_id: int, source: str, target: str, user_id, now: float,
                          undo_id: str):
    """Merges speaker `source` into `target` in ONE transaction and records
    the undo server-side. The lines on source, and both Characters rows, are
    read inside the transaction, so what is merged is exactly what is stored.
    Lines get target as speaker (marked set by hand); source's row is folded
    into target's (a blank target field takes source's value, a filled one is
    kept; the reference voice moves as one group, see _MERGE_VOICE_GROUP) and
    deleted; source's dismissed voice matches move to target.

    The undo record holds both rows as they were, the target row as the merge
    wrote it, the moved lines' previous flags and both labels' dismissed voice
    matches, scoped to drama_id and user_id. Returns "link_conflict" (the rows
    are linked to different series characters; nothing written) or
    {"moved": n}. Undo records of this drama naming either speaker are
    retired in the same transaction."""
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        src, tgt = _merge_row(conn, drama_id, source), _merge_row(conn, drama_id, target)
        blank_row = {c: None for c in db.MERGE_ROW_FIELDS}
        src_full, tgt_full = src or blank_row, tgt or blank_row
        if (src_full["series_character_id"] and tgt_full["series_character_id"]
                and src_full["series_character_id"] != tgt_full["series_character_id"]):
            conn.rollback()
            return "link_conflict"
        _retire_merge_undos(conn, drama_id, (source, target))
        moved = [{"id": r["id"], "speaker_manual": bool(r["speaker_manual"])} for r in conn.execute(
            "SELECT id, speaker_manual FROM lines WHERE drama_id = ? AND COALESCE(speaker, '') = ? "
            "ORDER BY id", (drama_id, source))]
        conn.execute("UPDATE lines SET speaker = ?, speaker_manual = 1 "
                     "WHERE drama_id = ? AND COALESCE(speaker, '') = ?", (target, drama_id, source))
        dismissals_before = _merge_dismissals(conn, drama_id, (source, target))
        merged = _folded_row(src_full, tgt_full)
        conn.execute("INSERT OR IGNORE INTO characters (drama_id, speaker_label) VALUES (?, ?)",
                     (drama_id, target))
        conn.execute(
            f"UPDATE characters SET {', '.join(c + ' = ?' for c in db.MERGE_ROW_FIELDS)} "
            "WHERE drama_id = ? AND speaker_label = ?",
            (*(merged[c] for c in db.MERGE_ROW_FIELDS), drama_id, target))
        conn.execute("DELETE FROM characters WHERE drama_id = ? AND speaker_label = ?",
                     (drama_id, source))
        conn.execute("UPDATE OR IGNORE voice_suggestion_dismissals SET speaker_label = ? "
                     "WHERE drama_id = ? AND speaker_label = ?", (target, drama_id, source))
        conn.execute("DELETE FROM voice_suggestion_dismissals WHERE drama_id = ? "
                     "AND speaker_label = ?", (drama_id, source))
        src_clip = src_full["ref_audio_filename"]
        orphan = src_clip if src_clip and merged["ref_audio_filename"] != src_clip else None
        record = {"source_row": src, "target_before": tgt, "target_after": merged, "moved": moved,
                  "dismissals_before": dismissals_before,
                  "dismissals_after": _merge_dismissals(conn, drama_id, (target,)),
                  "orphan_clip": orphan}
        conn.execute(
            "INSERT INTO speaker_merge_undos (id, drama_id, user_id, source_label, target_label, "
            "snapshot, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (undo_id, drama_id, user_id, source, target, json.dumps(record), now,
             now + db.MERGE_UNDO_TTL_SECONDS))
        conn.commit()
        return {"moved": len(moved)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def undo_merge_speakers_atomic(drama_id: int, undo_id: str, user_id, now, clip_exists):
    """Reverses the merge recorded under undo_id, in ONE transaction. The
    record must belong to this drama and user and be unexpired; it is
    single-use, deleted whatever the outcome.

    Nothing is written unless everything the merge left is still as it left
    it: the target row (all fields) and its dismissed voice matches, the
    source label unused, and every moved line still on target and marked
    manual. Otherwise "stale". clip_exists(rel) checks the stored clip file
    before the source row gets its clip link back; a missing file is
    "clip_missing". Returns {"status": "ok" | "missing" | "stale" |
    "clip_missing", "moved": n}."""
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if now is None:
            # Read after the write lock is held: waiting for the lock can take
            # seconds, and an expiry decided before it would let an undo run
            # after live_speaker_merge_undo_clips already reported its clip unused.
            now = time.time()
        rec = conn.execute(
            "SELECT * FROM speaker_merge_undos WHERE id = ? AND drama_id = ? "
            "AND COALESCE(user_id, -1) = COALESCE(?, -1)", (undo_id, drama_id, user_id)).fetchone()
        if rec is None:
            conn.rollback()
            return {"status": "missing", "moved": 0}
        snap = json.loads(rec["snapshot"])
        conn.execute("DELETE FROM speaker_merge_undos WHERE id = ?", (undo_id,))
        source, target = rec["source_label"], rec["target_label"]
        if rec["expires_at"] <= now:
            conn.commit()
            return {"status": "missing", "moved": 0}
        if rec["stale"]:
            conn.commit()
            return {"status": "stale", "moved": 0}

        def refuse(status):
            conn.commit()
            return {"status": status, "moved": 0}

        moved_ids = [m["id"] for m in snap["moved"]]
        on_target = {r["id"]: r for r in conn.execute(
            "SELECT id, speaker_manual FROM lines WHERE drama_id = ? AND COALESCE(speaker, '') = ?",
            (drama_id, target))}
        if (_merge_row(conn, drama_id, target) != snap["target_after"]
                or _merge_row(conn, drama_id, source) is not None
                or conn.execute("SELECT 1 FROM lines WHERE drama_id = ? AND COALESCE(speaker, '') = ?",
                                (drama_id, source)).fetchone()
                or _merge_dismissals(conn, drama_id, (target,)) != snap["dismissals_after"]
                or any(i not in on_target or not on_target[i]["speaker_manual"] for i in moved_ids)):
            return refuse("stale")
        src = snap["source_row"]
        if src and src["ref_audio_filename"] and not clip_exists(src["ref_audio_filename"]):
            return refuse("clip_missing")
        for m in snap["moved"]:
            conn.execute("UPDATE lines SET speaker = ?, speaker_manual = ? "
                         "WHERE id = ? AND drama_id = ?",
                         (source, int(m["speaker_manual"]), m["id"], drama_id))
        if src is not None:
            conn.execute(
                f"INSERT INTO characters (drama_id, speaker_label, {', '.join(db.MERGE_ROW_FIELDS)}) "
                f"VALUES (?, ?{', ?' * len(db.MERGE_ROW_FIELDS)})",
                (drama_id, source, *(src[c] for c in db.MERGE_ROW_FIELDS)))
        before = snap["target_before"]
        if before is None:
            conn.execute("DELETE FROM characters WHERE drama_id = ? AND speaker_label = ?",
                         (drama_id, target))
        else:
            conn.execute(
                f"UPDATE characters SET {', '.join(c + ' = ?' for c in db.MERGE_ROW_FIELDS)} "
                "WHERE drama_id = ? AND speaker_label = ?",
                (*(before[c] for c in db.MERGE_ROW_FIELDS), drama_id, target))
        conn.execute("DELETE FROM voice_suggestion_dismissals WHERE drama_id = ? "
                     "AND speaker_label IN (?, ?)", (drama_id, source, target))
        for d in snap["dismissals_before"]:
            conn.execute(
                "INSERT INTO voice_suggestion_dismissals (drama_id, speaker_label, "
                "series_character_id, created_at) VALUES (?, ?, ?, ?)",
                (drama_id, d["speaker_label"], d["series_character_id"], d["created_at"]))
        conn.commit()
        return {"status": "ok", "moved": len(moved_ids)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def drop_speaker_merge_undos(expired_before: float):
    """Deletes undo records expired before the given time (database rows only)."""
    conn = db.get_conn()
    try:
        # Characters writes call this; skip the write lock when nothing expired.
        if conn.execute("SELECT 1 FROM speaker_merge_undos WHERE expires_at <= ? LIMIT 1",
                        (expired_before,)).fetchone() is None:
            return
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM speaker_merge_undos WHERE expires_at <= ?", (expired_before,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def live_speaker_merge_undo_clips(drama_id: int, now: float) -> set:
    """Clip files that a live (unexpired, not retired) undo record of the
    drama would give back to the source row. Listing unused clips must count
    these as in use: a merge never deletes a clip file."""
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT snapshot FROM speaker_merge_undos "
                            "WHERE drama_id = ? AND stale = 0 AND expires_at > ?",
                            (drama_id, now)).fetchall()
        return {c for c in (json.loads(r["snapshot"])["orphan_clip"] for r in rows) if c}
    finally:
        conn.close()


def _retire_merge_undos(conn, drama_id: int, labels: tuple):
    marks = ", ".join("?" * len(labels))
    conn.execute(
        "UPDATE speaker_merge_undos SET stale = 1 WHERE drama_id = ? AND stale = 0 "
        f"AND (source_label IN ({marks}) OR target_label IN ({marks}))",
        (drama_id, *labels, *labels))


def retire_speaker_merge_undos(drama_id: int, labels: tuple):
    """Marks the drama's live undo records that name one of labels as stale
    (an edit to those speakers means they can't be applied). The record stays
    until its id is used or it expires, so the undo answers with a plain 409
    rather than looking like an unknown id."""
    if not labels:
        return
    conn = db.get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _retire_merge_undos(conn, drama_id, labels)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Series-level characters -- persist across every drama in a series (a
# streamer's whole archive, or a book series), independent of any one
# drama's own diarization labels. See the series_characters table comment
# in init_db() for why this has to be a separate concept from `characters`.
# ---------------------------------------------------------------------------

def upsert_series_character(series_id: int, character_name: str, aliases: str = "",
                             notes: str = "", gender: str | None = None):
    """Creates or updates a named character for a series. Matching is on
    (series_id, character_name) -- renaming isn't done through this
    function (it would create a new row); use update_series_character.

    gender: the character's pronoun text ("she/her", "they/them", a custom
    value like "xe/xem"), or "" / None. Legacy rows may still hold
    "female"/"male" -- translation_guide.normalize_pronouns maps those.
    Named `gender` only because the column predates free-text pronouns.
    Feeds translation as a fixed
    pronoun hint for this character -- Mandarin's spoken 他/她/它 are
    homophones, so the character Whisper happens to transcribe for a
    pronoun isn't a reliable gender signal, and misgendering a NAMED
    character reads as a much more obvious error than an ambiguous
    unnamed one. None leaves an existing gender value untouched (so
    re-running "remember this character" from a different tab doesn't
    silently clear a gender set earlier); pass "" explicitly to clear it.
    """
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO series_characters (series_id, character_name, aliases, notes, gender, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, character_name) DO UPDATE SET
                aliases = excluded.aliases,
                notes = excluded.notes,
                gender = COALESCE(?, series_characters.gender)
        """, (series_id, character_name, aliases, notes, gender,
              clock.utcnow().isoformat(), gender))
        conn.commit()


def insert_series_character(series_id: int, character_name: str, aliases: str = "",
                            notes: str = "", gender: str = "") -> int:
    """Plain INSERT (unlike upsert_series_character, which would overwrite
    an existing same-named row's aliases/notes). Raises
    sqlite3.IntegrityError when the series already has that name."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO series_characters (series_id, character_name, aliases, notes, gender, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (series_id, character_name, aliases, notes, gender,
             clock.utcnow().isoformat()))
        conn.commit()
        return db.inserted_id(cur)


def update_series_character(series_character_id: int, *, character_name: str | None = None,
                            aliases: str | None = None, notes: str | None = None, gender: str | None = None):
    """Field-scoped update of one series character by id, in one statement:
    None leaves a column alone, "" clears it. Fixed column list (no
    caller-supplied keys reach the SQL). Raises sqlite3.IntegrityError
    (nothing written) when the new name is already taken in the series."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(
            "UPDATE series_characters SET character_name = COALESCE(?, character_name), "
            "aliases = COALESCE(?, aliases), notes = COALESCE(?, notes), "
            "gender = COALESCE(?, gender) WHERE id = ?",
            (character_name, aliases, notes, gender, series_character_id))
        conn.commit()


def list_series_characters(series_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM series_characters WHERE series_id = ? ORDER BY character_name",
            (series_id,)).fetchall()
    return [dict(r) for r in rows]


def count_series_characters_by_series() -> dict:
    """{series_id: character count} for every series that has characters."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT series_id, COUNT(*) FROM series_characters GROUP BY series_id").fetchall()
    return {r[0]: r[1] for r in rows}


def delete_series_character(series_character_id: int):
    """Only removes the series-level record. Any drama's `characters` row
    still pointing at it keeps its own character_name (already copied in
    at assignment time) -- it just stops being linked for future rename
    propagation, rather than losing the name it already had."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE characters SET series_character_id = NULL WHERE series_character_id = ?",
                     (series_character_id,))
        conn.execute("DELETE FROM series_characters WHERE id = ?", (series_character_id,))
        conn.commit()


def _blend_voice_fingerprint(conn, series_character_id: int, new_embedding: list):
    row = conn.execute(
        "SELECT voice_fingerprint, voice_fingerprint_samples FROM series_characters WHERE id = ?",
        (series_character_id,)).fetchone()
    if row is None:
        return
    existing = json.loads(row["voice_fingerprint"]) if row["voice_fingerprint"] else None
    n = row["voice_fingerprint_samples"] or 0
    if existing and len(existing) == len(new_embedding):
        blended = [(e * n + v) / (n + 1) for e, v in zip(existing, new_embedding)]
    else:
        # No prior fingerprint, or a dimension mismatch (a different
        # embedding model produced it) -- start over from this sample
        # rather than averaging incompatible vectors.
        blended, n = list(new_embedding), 0
    conn.execute(
        "UPDATE series_characters SET voice_fingerprint = ?, voice_fingerprint_samples = ? WHERE id = ?",
        (json.dumps(blended), n + 1, series_character_id))


def update_series_character_voice_fingerprint(series_character_id: int, new_embedding: list):
    """Blends a newly-confirmed voice embedding into this
    character's running-average fingerprint (simple incremental mean,
    weighted by how many samples went into the average so far), so later
    dramas compare against an average across every drama where this
    character's voice was confirmed, not just the first one. Only ever
    called from an explicit user action -- accepting a voice suggestion,
    or "Remember as a known series character" for a speaker not yet
    linked (services/characters_service.py) -- never automatically."""
    with contextlib.closing(db.get_conn()) as conn:
        _blend_voice_fingerprint(conn, series_character_id, new_embedding)
        conn.commit()


def accept_voice_link(drama_id: int, speaker_label: str, series_character_id: int,
                      character_name: str, embedding: list | None = None) -> bool:
    """An accepted voice suggestion, in one transaction: names the speaker
    and links it to the series character ONLY while the speaker is still
    unnamed and unlinked, then blends `embedding` into that character's
    fingerprint. False (nothing written) when the speaker was named or
    linked meanwhile -- so two concurrent accepts can't both link it or
    blend the same embedding twice."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO characters (drama_id, speaker_label) VALUES (?, ?)",
                     (drama_id, speaker_label))
        cur = conn.execute(
            "UPDATE characters SET character_name = ?, series_character_id = ? "
            "WHERE drama_id = ? AND speaker_label = ? AND series_character_id IS NULL "
            "AND COALESCE(TRIM(character_name), '') = ''",
            (character_name, series_character_id, drama_id, speaker_label))
        if cur.rowcount == 0:
            conn.rollback()
            return False
        if embedding:
            _blend_voice_fingerprint(conn, series_character_id, embedding)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remember_speaker_as_series_character(drama_id: int, speaker_label: str, series_id: int,
                                         name: str, gender: str | None = None,
                                         embedding: list | None = None):
    """"Remember as a known series character", in one transaction: only
    while the speaker is still unlinked and its saved name (trimmed) is
    still `name`, adds `name` to the series if it isn't there (insert-only:
    an existing character's aliases, notes and pronouns are never
    touched), links the speaker to it and blends `embedding` into its
    fingerprint. Returns (series_character_id, created), or None (nothing
    written) when the speaker was linked or renamed meanwhile."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT 1 FROM characters WHERE drama_id = ? AND speaker_label = ? "
            "AND series_character_id IS NULL AND TRIM(character_name) = ?",
            (drama_id, speaker_label, name)).fetchone()
        if row is None:
            conn.rollback()
            return None
        created = conn.execute("""
            INSERT INTO series_characters (series_id, character_name, aliases, notes, gender, created_at)
            VALUES (?, ?, '', '', ?, ?)
            ON CONFLICT(series_id, character_name) DO NOTHING
        """, (series_id, name, gender, clock.utcnow().isoformat())).rowcount == 1
        sc_id = conn.execute(
            "SELECT id FROM series_characters WHERE series_id = ? AND character_name = ?",
            (series_id, name)).fetchone()["id"]
        conn.execute("UPDATE characters SET series_character_id = ? "
                     "WHERE drama_id = ? AND speaker_label = ? AND series_character_id IS NULL",
                     (sc_id, drama_id, speaker_label))
        if embedding:
            _blend_voice_fingerprint(conn, sc_id, embedding)
        conn.commit()
        return sc_id, created
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dismiss_voice_suggestion(drama_id: int, speaker_label: str, series_character_id: int):
    """Records that this exact (drama, speaker, candidate) voice-match
    suggestion was rejected, so it's never shown again for that
    combination. A different candidate for the same speaker isn't
    affected."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO voice_suggestion_dismissals
                (drama_id, speaker_label, series_character_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (drama_id, speaker_label, series_character_id, clock.utcnow().isoformat()))
        conn.commit()


def list_dismissed_voice_suggestions(drama_id: int) -> set:
    """{(speaker_label, series_character_id), ...} already rejected for
    this drama -- checked before generating suggestions so a dismissed
    one doesn't silently reappear on the next render."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT speaker_label, series_character_id FROM voice_suggestion_dismissals WHERE drama_id = ?",
            (drama_id,)).fetchall()
    return {(r["speaker_label"], r["series_character_id"]) for r in rows}


def list_characters_with_series_names(drama_id: int):
    """Like list_characters, but a character linked to a series_characters
    row shows that row's current character_name (so a series-level rename
    reflects here immediately) instead of the possibly-stale name copied
    into `characters` at the time it was first assigned."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("""
            SELECT c.*, sc.character_name AS series_character_name,
                   sc.gender AS series_pronouns
            FROM characters c
            LEFT JOIN series_characters sc ON sc.id = c.series_character_id
            WHERE c.drama_id = ?
            ORDER BY c.speaker_label
        """, (drama_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("series_character_name"):
            d["character_name"] = d["series_character_name"]
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Voice bank -- reuse a cloned voice across projects
# ---------------------------------------------------------------------------

def save_voice_bank_entry(name: str, clip_source_path: str, ref_text: str = "",
                           clone_engine: str | None = None, voice_design: str = "",
                           language: str | None = None, notes: str = "",
                           source_drama: str | None = None, source_speaker: str | None = None) -> int:
    """Copies clip_source_path into the shared library's voice bank folder
    (never a reference into the source drama's own folder) and records a
    new voice_bank row -- so deleting the source drama afterward leaves
    this entry's own clip intact. Returns the new entry's id."""
    import shutil
    import uuid
    os.makedirs(db.VOICE_BANK_DIR, exist_ok=True)
    ext = os.path.splitext(clip_source_path)[1]
    clip_filename = f"{uuid.uuid4().hex}{ext}"
    shutil.copyfile(clip_source_path, os.path.join(db.VOICE_BANK_DIR, clip_filename))
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("""
            INSERT INTO voice_bank (name, clip_filename, ref_text, clone_engine, voice_design,
                                     language, notes, source_drama, source_speaker, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, clip_filename, ref_text, clone_engine, voice_design, language, notes,
              source_drama, source_speaker, clock.utcnow().isoformat()))
        conn.commit()
        new_id = db.inserted_id(cur)
    return new_id


def list_voice_bank_entries() -> List[dict]:
    """Alphabetical by name -- this is a picklist, not an activity log."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM voice_bank ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_voice_bank_entry(entry_id: int) -> dict | None:
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM voice_bank WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def apply_voice_bank_entry(entry_id: int, drama_dir: str, drama_id: int, speaker_label: str) -> str:
    """Copies a bank entry's clip into drama_dir (a new file, not shared
    with the bank's own copy or any other drama's) and sets the matching
    character clone fields. Returns the clip's filename relative to
    drama_dir, as stored in characters.ref_audio_filename."""
    import shutil
    entry = db.get_voice_bank_entry(entry_id)
    if not entry:
        raise ValueError(f"No voice bank entry with id {entry_id}")
    ext = os.path.splitext(entry["clip_filename"])[1]
    dest_filename = f"voicebank_{entry_id}_{speaker_label}{ext}"
    os.makedirs(drama_dir, exist_ok=True)
    shutil.copyfile(os.path.join(db.VOICE_BANK_DIR, entry["clip_filename"]),
                     os.path.join(drama_dir, dest_filename))
    db.upsert_character(drama_id, speaker_label, ref_audio_filename=dest_filename,
                     ref_text=entry["ref_text"] or "", clone_engine=entry["clone_engine"],
                     voice_design=entry["voice_design"] or "")
    return dest_filename


def rename_voice_bank_entry(entry_id: int, new_name: str):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE voice_bank SET name = ? WHERE id = ?", (new_name, entry_id))
        conn.commit()


def delete_voice_bank_entry(entry_id: int):
    entry = db.get_voice_bank_entry(entry_id)
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM voice_bank WHERE id = ?", (entry_id,))
        conn.commit()
    if entry:
        clip_path = os.path.join(db.VOICE_BANK_DIR, entry["clip_filename"])
        if os.path.exists(clip_path):
            os.remove(clip_path)
