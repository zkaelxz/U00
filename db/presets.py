"""Workspace-configuration presets: library-level, reusable across unrelated
series; applying one copies its fields onto a drama, nothing links back."""

import contextlib
import datetime

import db


def save_preset(name: str, translation_engine: str = None, engine_model: str = None,
                 style_preset: str = None, locale: str = None,
                 default_female_pronouns: bool = False, include_genre_notes: bool = True,
                 options_json: str = None) -> int:
    """Creates a new preset, or overwrites the existing one with this exact
    name -- "Save as preset" under a name that's already taken replaces
    its captured fields rather than failing on the name's UNIQUE
    constraint, the same "save as" behavior as most apps. Overwriting
    never touches any drama the old version was previously applied to,
    since applying one only ever copies its fields onto a drama at that
    moment (see the `presets` table comment) -- there's no live link to
    break."""
    now = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO presets (name, translation_engine, engine_model, style_preset, locale,
                                  default_female_pronouns, include_genre_notes, options_json,
                                  created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                translation_engine = excluded.translation_engine,
                engine_model = excluded.engine_model,
                style_preset = excluded.style_preset,
                locale = excluded.locale,
                default_female_pronouns = excluded.default_female_pronouns,
                include_genre_notes = excluded.include_genre_notes,
                options_json = excluded.options_json,
                updated_at = excluded.updated_at
        """, (name, translation_engine, engine_model, style_preset, locale,
              int(bool(default_female_pronouns)), int(bool(include_genre_notes)), options_json,
              now, now))
        conn.commit()
        # cur.lastrowid isn't reliable on the UPDATE branch of an upsert --
        # look the row up by its own UNIQUE name instead of trusting it.
        preset_id = conn.execute("SELECT id FROM presets WHERE name = ?", (name,)).fetchone()["id"]
    return preset_id


def insert_preset(name: str, translation_engine: str = None, engine_model: str = None,
                  style_preset: str = None, locale: str = None,
                  default_female_pronouns: bool = False, include_genre_notes: bool = True,
                  options_json: str = None) -> int:
    """Insert-only twin of save_preset: a taken name raises
    sqlite3.IntegrityError (UNIQUE(name)) instead of replacing the row, so
    a caller can refuse an overwrite without a check-then-write race."""
    now = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("""
            INSERT INTO presets (name, translation_engine, engine_model, style_preset, locale,
                                  default_female_pronouns, include_genre_notes, options_json,
                                  created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, translation_engine, engine_model, style_preset, locale,
              int(bool(default_female_pronouns)), int(bool(include_genre_notes)), options_json,
              now, now))
        conn.commit()
        return cur.lastrowid


def set_preset_engine_model(preset_id: int, engine_model: str, expected_model: str = None) -> bool:
    """The guided switch: changes only a preset's model, and only if it
    still holds expected_model (when given). False when nothing changed."""
    with contextlib.closing(db.get_conn()) as conn:
        if expected_model is None:
            cur = conn.execute("UPDATE presets SET engine_model = ?, updated_at = ? WHERE id = ?",
                               (engine_model, datetime.datetime.utcnow().isoformat(), preset_id))
        else:
            cur = conn.execute(
                "UPDATE presets SET engine_model = ?, updated_at = ? WHERE id = ? AND engine_model = ?",
                (engine_model, datetime.datetime.utcnow().isoformat(), preset_id, expected_model))
        conn.commit()
    return cur.rowcount > 0


def list_presets():
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM presets ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def rename_preset(preset_id: int, new_name: str):
    """Updates only the name -- every captured field is left exactly as
    saved."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE presets SET name = ?, updated_at = ? WHERE id = ?",
                     (new_name, datetime.datetime.utcnow().isoformat(), preset_id))
        conn.commit()


def delete_preset(preset_id: int):
    """Removes only the presets row. No drama row (or any other table)
    references a preset's id, so a drama this preset was previously
    applied to is completely unaffected -- see the `presets` table's own
    comment in init_db."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
        conn.commit()


def update_preset(preset_id: int, name: str, translation_engine: str = None, engine_model: str = None,
                  style_preset: str = None, locale: str = None,
                  default_female_pronouns: bool = False, include_genre_notes: bool = True,
                  options_json: str = None) -> bool:
    """Replaces every captured field of one preset (and its name), keeping
    its id and whether it is the default. A name another preset holds raises
    sqlite3.IntegrityError. False when no preset has this id."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("""
            UPDATE presets SET name = ?, translation_engine = ?, engine_model = ?, style_preset = ?,
                               locale = ?, default_female_pronouns = ?, include_genre_notes = ?,
                               options_json = ?, updated_at = ?
            WHERE id = ?
        """, (name, translation_engine, engine_model, style_preset, locale,
              int(bool(default_female_pronouns)), int(bool(include_genre_notes)), options_json,
              datetime.datetime.utcnow().isoformat(), preset_id))
        conn.commit()
    return cur.rowcount > 0


def set_default_preset(preset_id: int, is_default: bool) -> bool:
    """Makes this preset the one default (clearing any other) or, with
    is_default=False, clears it. One transaction, so there is never a moment
    with two defaults. False when no preset has this id."""
    with contextlib.closing(db.get_conn()) as conn:
        if conn.execute("SELECT 1 FROM presets WHERE id = ?", (preset_id,)).fetchone() is None:
            return False
        if is_default:
            conn.execute("UPDATE presets SET is_default = (id = ?)", (preset_id,))
        else:
            conn.execute("UPDATE presets SET is_default = 0 WHERE id = ?", (preset_id,))
        conn.commit()
    return True
