"""Shared glossary groups (a glossary several series inherit) and the one effective-glossary read every consumer uses."""

import contextlib
import unicodedata

import db
from lib import clock

_TERM_COLUMNS = ("term_original", "term_translation", "notes", "category", "policy",
                 "enforce_exact", "aliases", "banned_translations")


def term_key(text) -> str:
    """How two original texts are compared when one overrides the other."""
    return unicodedata.normalize("NFKC", str(text or "")).strip().casefold()


def create_group(name: str, owner_user_id: int | None) -> int:
    """Raises sqlite3.IntegrityError when the exact name is taken."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("INSERT INTO shared_glossary_groups (name, owner_user_id, created_at) "
                           "VALUES (?, ?, ?)", (name, owner_user_id, clock.utcnow().isoformat()))
        conn.commit()
        return db.inserted_id(cur)


def list_groups() -> list:
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT g.*, (SELECT COUNT(*) FROM shared_glossary_terms WHERE group_id = g.id) AS term_count, "
            "(SELECT COUNT(*) FROM series WHERE shared_group_id = g.id) AS series_count "
            "FROM shared_glossary_groups g ORDER BY g.name").fetchall()
    return [dict(r) for r in rows]


def get_group(group_id: int):
    return next((g for g in list_groups() if g["id"] == group_id), None)


def delete_group(group_id: int) -> None:
    """Unlinks every series that joined it and drops its terms; the series' own
    terms are untouched."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE series SET shared_group_id = NULL WHERE shared_group_id = ?", (group_id,))
        conn.execute("DELETE FROM shared_glossary_terms WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM shared_glossary_groups WHERE id = ?", (group_id,))
        conn.commit()


def detach_owner_groups(owner_user_id: int) -> int:
    """Unlinks every series from the groups this user created and returns how
    many series were unlinked. The groups and their terms stay, so the PC can
    still read or delete them."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "UPDATE series SET shared_group_id = NULL WHERE shared_group_id IN "
            "(SELECT id FROM shared_glossary_groups WHERE owner_user_id = ?)", (owner_user_id,))
        conn.commit()
        return cur.rowcount


def get_series_group_id(series_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT shared_group_id FROM series WHERE id = ?", (series_id,)).fetchone()
    return row["shared_group_id"] if row else None


def set_series_group(series_id: int, group_id: int | None) -> None:
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE series SET shared_group_id = ? WHERE id = ?", (group_id, series_id))
        conn.commit()


def list_group_terms(group_id: int) -> list:
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM shared_glossary_terms WHERE group_id = ? "
                            "ORDER BY term_original", (group_id,)).fetchall()
    return [dict(r) for r in rows]


def save_group_term(group_id: int, term_id: int | None, term_original: str, term_translation: str,
                    notes: str, category, policy, enforce_exact: bool, aliases: str,
                    banned_translations: str) -> None:
    """Updates `term_id` in place, or adds the term (replacing a same-text one's fields)."""
    values = (term_original, term_translation, notes, category, policy, int(enforce_exact),
              aliases, banned_translations)
    with contextlib.closing(db.get_conn()) as conn:
        if term_id is not None:
            conn.execute("UPDATE shared_glossary_terms SET term_original = ?, term_translation = ?, "
                         "notes = ?, category = ?, policy = ?, enforce_exact = ?, aliases = ?, "
                         "banned_translations = ? WHERE id = ? AND group_id = ?",
                         (*values, term_id, group_id))
        else:
            sets = ", ".join(f"{c} = excluded.{c}" for c in _TERM_COLUMNS[1:])
            conn.execute(f"INSERT INTO shared_glossary_terms (group_id, {', '.join(_TERM_COLUMNS)}) "
                         f"VALUES (?{', ?' * len(_TERM_COLUMNS)}) "
                         f"ON CONFLICT(group_id, term_original) DO UPDATE SET {sets}",
                         (group_id, *values))
        conn.commit()


def delete_group_term(group_id: int, term_id: int) -> bool:
    with contextlib.closing(db.get_conn()) as conn:
        n = conn.execute("DELETE FROM shared_glossary_terms WHERE id = ? AND group_id = ?",
                         (term_id, group_id)).rowcount
        conn.commit()
    return n > 0


def list_effective_glossary_terms(series_id: int) -> list:
    """What a series translates, checks and transcribes with: its group's
    terms overlaid by its own, the series winning on the same source text.
    Each row carries `origin` ("series" or "shared") and `origin_name`.
    A shared row's `id` is negative (minus its shared_glossary_terms id) so it
    can never be mistaken for a series term by an id lookup that edits terms."""
    own = [{**t, "origin": "series", "origin_name": None} for t in db.list_glossary_terms(series_id)]
    group_id = get_series_group_id(series_id)
    group = get_group(group_id) if group_id else None
    if group is None:
        return own
    taken = {term_key(t["term_original"]) for t in own}
    inherited = [{**t, "id": -t["id"], "series_id": series_id, "origin": "shared",
                  "origin_name": group["name"]}
                 for t in list_group_terms(group["id"]) if term_key(t["term_original"]) not in taken]
    return sorted(own + inherited, key=lambda t: t["term_original"] or "")
