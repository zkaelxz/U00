"""Series and their shared glossaries (terms, their ignore list), and the
per-series translation memory of hand-approved translations."""

import contextlib
from lib import clock

import db


def get_or_create_series(name: str, owner_user_id: int | None = None, is_private: bool = False) -> int:
    """`owner_user_id`/`is_private` only stamp a newly
    created row; an existing series is returned unchanged. Not
    visibility-aware: non-admin callers use
    ownership_service.get_or_create_series_for, which refuses a name taken
    by a series they can't see."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT id FROM series WHERE name = ?", (name,)).fetchone()
        if row:
            conn.close()
            return row["id"]
        cur = conn.execute("INSERT INTO series (name, created_at, owner_user_id, is_private) "
                           "VALUES (?, ?, ?, ?)",
                            (name, clock.utcnow().isoformat(), owner_user_id,
                             int(bool(is_private))))
        conn.commit()
        new_id = db.inserted_id(cur)
    return new_id


def create_series(name: str, owner_user_id: int | None = None, is_private: bool = False) -> int:
    """Insert-only. Raises sqlite3.IntegrityError when the
    name is taken (series.name is UNIQUE) -- never returns an existing id."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("INSERT INTO series (name, created_at, owner_user_id, is_private) "
                           "VALUES (?, ?, ?, ?)",
                           (name, clock.utcnow().isoformat(), owner_user_id,
                            int(bool(is_private))))
        conn.commit()
        return db.inserted_id(cur)


def get_series_id_by_name(name: str):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT id FROM series WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def list_series(visible_to: int | None = None):
    """`visible_to`: a non-admin user id; when set, only
    that user's own or non-private series. None = unfiltered."""
    with contextlib.closing(db.get_conn()) as conn:
        if visible_to is None:
            rows = conn.execute("SELECT * FROM series ORDER BY name").fetchall()
        else:
            rows = conn.execute("SELECT * FROM series WHERE owner_user_id = ? "
                                "OR COALESCE(is_private, 0) = 0 ORDER BY name",
                                (visible_to,)).fetchall()
    return [dict(r) for r in rows]


def upsert_glossary_term(series_id: int, term_original: str, term_translation: str, notes: str = "",
                          category: str | None = None, policy: str | None = None, enforce_exact: bool = False,
                          aliases: str | None = None, banned_translations: str | None = None):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO glossary_terms (series_id, term_original, term_translation, notes,
                                         category, policy, enforce_exact, aliases, banned_translations)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, term_original) DO UPDATE SET
                term_translation = excluded.term_translation,
                notes = excluded.notes,
                category = COALESCE(excluded.category, glossary_terms.category),
                policy = COALESCE(excluded.policy, glossary_terms.policy),
                enforce_exact = excluded.enforce_exact,
                aliases = COALESCE(excluded.aliases, glossary_terms.aliases),
                banned_translations = COALESCE(excluded.banned_translations,
                                                glossary_terms.banned_translations)
        """, (series_id, term_original, term_translation, notes, category, policy, int(enforce_exact),
              aliases, banned_translations))
        conn.commit()



def insert_glossary_term_if_absent(series_id: int, term_original: str, term_translation: str,
                                   notes: str = "", category: str | None = None, policy: str | None = None,
                                   enforce_exact: bool = False) -> bool:
    """Adds a term only if the series has none with this original text (a
    term added meanwhile is left alone). Returns True when it was added."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("""
            INSERT INTO glossary_terms (series_id, term_original, term_translation, notes,
                                         category, policy, enforce_exact)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, term_original) DO NOTHING
        """, (series_id, term_original, term_translation, notes, category, policy,
              int(enforce_exact)))
        conn.commit()
        return cur.rowcount == 1


def record_translation_memory(series_id: int, source_text: str, translation: str):
    """Stores a translation the translator approved by hand. The same pair
    again counts as another use; a different translation for the same
    source replaces the old one and restarts its count."""
    source_text, translation = (source_text or "").strip(), (translation or "").strip()
    if not series_id or not source_text or not translation:
        return
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO translation_memory (series_id, source_text, translation, use_count, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(series_id, source_text) DO UPDATE SET
                use_count = CASE WHEN translation_memory.translation = excluded.translation
                                 THEN translation_memory.use_count + 1 ELSE 1 END,
                translation = excluded.translation,
                updated_at = excluded.updated_at
        """, (series_id, source_text, translation, clock.utcnow().isoformat()))
        conn.commit()


def list_translation_memory(series_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM translation_memory WHERE series_id = ? ORDER BY id",
                            (series_id,)).fetchall()
    return [dict(r) for r in rows]


def import_translation_memory(series_id: int, pairs) -> dict:
    """Adds (source, translation, use_count) pairs in one transaction. A source the series
    already has keeps its stored translation (that one was approved by hand); when the
    translations agree the larger use_count wins. Returns {"added", "skipped"}."""
    added = skipped = 0
    now = clock.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        have = {r["source_text"]: r["translation"] for r in conn.execute(
            "SELECT source_text, translation FROM translation_memory WHERE series_id = ?", (series_id,))}
        for source, translation, uses in pairs:
            if source in have:
                skipped += 1
                conn.execute(
                    "UPDATE translation_memory SET use_count = MAX(use_count, ?) "
                    "WHERE series_id = ? AND source_text = ? AND translation = ?",
                    (uses, series_id, source, translation))
                continue
            conn.execute(
                "INSERT INTO translation_memory (series_id, source_text, translation, use_count, updated_at) "
                "VALUES (?, ?, ?, ?, ?)", (series_id, source, translation, uses, now))
            have[source] = translation
            added += 1
        conn.commit()
    return {"added": added, "skipped": skipped}


def bump_translation_memory_use(entry_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE translation_memory SET use_count = use_count + 1, updated_at = ? WHERE id = ?",
                     (clock.utcnow().isoformat(), entry_id))
        conn.commit()


def update_translation_memory_after_replace(series_id: int, old_translation: str,
                                            new_translation: str) -> int:
    """Find-and-replace just changed a line's translation from old to new:
    any stored entry still holding the old text is corrected to match (or
    dropped, if the replacement emptied it), so memory doesn't keep
    suggesting the mistake that was just fixed. Returns rows touched."""
    old, new = (old_translation or "").strip(), (new_translation or "").strip()
    if not series_id or not old or old == new:
        return 0
    with contextlib.closing(db.get_conn()) as conn:
        if new:
            cur = conn.execute(
                "UPDATE translation_memory SET translation = ?, updated_at = ? "
                "WHERE series_id = ? AND translation = ?",
                (new, clock.utcnow().isoformat(), series_id, old))
        else:
            cur = conn.execute("DELETE FROM translation_memory WHERE series_id = ? AND translation = ?",
                               (series_id, old))
        conn.commit()
    return cur.rowcount


def update_glossary_term(term_id: int, term_original: str, term_translation: str,
                          notes: str = "", category: str | None = None, policy: str | None = None,
                          enforce_exact: bool = False, aliases: str | None = None,
                          banned_translations: str | None = None):
    """Updates an existing glossary term by its own id -- unlike
    upsert_glossary_term (keyed on term_original, for the extract-and-add
    flow), this lets a term's original text itself be corrected without
    orphaning the old row as a stale duplicate entry."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            UPDATE glossary_terms
            SET term_original = ?, term_translation = ?, notes = ?, category = ?,
                policy = ?, enforce_exact = ?, aliases = ?, banned_translations = ?
            WHERE id = ?
        """, (term_original, term_translation, notes, category, policy, int(enforce_exact),
              aliases, banned_translations, term_id))
        conn.commit()


def list_glossary_terms(series_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM glossary_terms WHERE series_id = ? ORDER BY term_original",
                             (series_id,)).fetchall()
    return [dict(r) for r in rows]


def count_glossary_terms_by_series() -> dict:
    """{series_id: term count} for every series that has terms."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT series_id, COUNT(*) FROM glossary_terms GROUP BY series_id").fetchall()
    return {r[0]: r[1] for r in rows}


def list_glossary_dismissals(series_id: int) -> list:
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT term_original, created_at FROM glossary_dismissals "
                            "WHERE series_id = ? ORDER BY term_original", (series_id,)).fetchall()
    return [dict(r) for r in rows]


def list_dismissed_glossary_terms(series_id: int, terms) -> set:
    """Which of `terms` are on the series' ignore list (chunked to stay under
    SQLite's bound-variable limit)."""
    terms = list(dict.fromkeys(terms))
    found = set()
    with contextlib.closing(db.get_conn()) as conn:
        for i in range(0, len(terms), 500):
            chunk = terms[i:i + 500]
            rows = conn.execute(
                "SELECT term_original FROM glossary_dismissals WHERE series_id = ? "
                f"AND term_original IN ({','.join('?' * len(chunk))})", (series_id, *chunk))
            found.update(r["term_original"] for r in rows)
    return found


def add_glossary_dismissals(series_id: int, terms) -> None:
    now = clock.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        conn.executemany("INSERT OR IGNORE INTO glossary_dismissals "
                         "(series_id, term_original, created_at) VALUES (?, ?, ?)",
                         [(series_id, t, now) for t in terms])
        conn.commit()


def remove_glossary_dismissals(series_id: int, terms) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        n = sum(conn.execute("DELETE FROM glossary_dismissals WHERE series_id = ? "
                             "AND term_original = ?", (series_id, t)).rowcount for t in terms)
        conn.commit()
    return n


def delete_glossary_term(term_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM glossary_terms WHERE id = ?", (term_id,))
        conn.commit()
