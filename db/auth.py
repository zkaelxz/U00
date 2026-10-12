"""Auth storage: users, permissions, sessions and the audit log, plus the
ownership and sharing fields of dramas and series. The tables are created
by db.init_db."""

import contextlib
import datetime
import time

import db


# ---------------------------------------------------------------------------
# Auth storage (users, permissions, sessions, audit log).
# Plain data access only; policy lives in services/auth_service.py. Every
# writable column is whitelisted here because the UPDATE is built from keys.
# ---------------------------------------------------------------------------

_USER_WRITABLE = ("google_sub", "email", "display_name", "is_admin", "is_active",
                  "share_by_default")


def auth_create_user(email: str, display_name: str = "", is_admin: bool = False) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            # share_by_default is explicit: a database from before new items
            # became private by default has the column with DEFAULT 1.
            "INSERT INTO users (email, display_name, is_admin, is_active, share_by_default, "
            "created_at) VALUES (?, ?, ?, 1, 0, ?)",
            (email, display_name or "", int(bool(is_admin)),
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        conn.commit()
        return cur.lastrowid


def auth_update_user(user_id: int, **fields) -> bool:
    bad = set(fields) - set(_USER_WRITABLE)
    if bad:
        raise ValueError(f"not a writable user field: {sorted(bad)}")
    if not fields:
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)   # keys are whitelisted above
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user_id))
        conn.commit()
        return cur.rowcount > 0


def get_item_ownership(kind: str, item_id: int):
    """The ownership fields of one drama or series, or None
    if it doesn't exist. For a drama, `series_is_private` and
    `series_owner_user_id` are included."""
    with contextlib.closing(db.get_conn()) as conn:
        if kind == "drama":
            row = conn.execute(
                "SELECT d.id, d.owner_user_id, COALESCE(d.is_private, 0) AS is_private, d.series_id, "
                "COALESCE(s.is_private, 0) AS series_is_private, "
                "s.owner_user_id AS series_owner_user_id "
                "FROM dramas d LEFT JOIN series s ON s.id = d.series_id WHERE d.id = ?",
                (item_id,)).fetchone()
        elif kind == "series":
            row = conn.execute("SELECT id, owner_user_id, COALESCE(is_private, 0) AS is_private "
                               "FROM series WHERE id = ?", (item_id,)).fetchone()
        else:
            raise ValueError(f"unknown ownership kind: {kind!r}")
    return dict(row) if row else None


# A drama "owned by someone else" than series `s`: NULL-owned dramas belong
# to the PC owner, who sees everything anyway, so they never count.
_OTHERS_DRAMA_SQL = ("d.owner_user_id IS NOT NULL AND d.owner_user_id IS NOT s.owner_user_id")


def assign_drama_series(drama_id: int, series_id: int) -> bool:
    """Moves a drama into a series in one conditional write,
    so it can't race the series being made private. Refused (False) when
    the series is private and the drama belongs to someone other than the
    series owner. Also clears the drama's own private flag (user decision
    4). False also for an unknown drama."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "UPDATE dramas SET series_id = ?, is_private = 0, updated_at = ? WHERE id = ? "
            "AND NOT EXISTS (SELECT 1 FROM series s, dramas d WHERE s.id = ? AND d.id = ? "
            f"AND COALESCE(s.is_private, 0) = 1 AND {_OTHERS_DRAMA_SQL})",
            (series_id, datetime.datetime.utcnow().isoformat(), drama_id, series_id, drama_id))
        conn.commit()
        return cur.rowcount > 0


def unassign_drama_series(drama_id: int) -> bool:
    """Parity P11: takes a drama out of its series in one write. A drama
    leaving a private series becomes private itself (assigning cleared its
    own flag, so otherwise it would turn visible to everyone). False for an
    unknown drama."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "UPDATE dramas SET is_private = CASE WHEN EXISTS (SELECT 1 FROM series s "
            "WHERE s.id = dramas.series_id AND COALESCE(s.is_private, 0) = 1) THEN 1 "
            "ELSE COALESCE(is_private, 0) END, series_id = NULL, updated_at = ? WHERE id = ?",
            (datetime.datetime.utcnow().isoformat(), drama_id))
        conn.commit()
        return cur.rowcount > 0


def set_item_private(kind: str, item_id: int, private: bool) -> bool:
    """Field-scoped write of is_private only. Making private
    is one conditional write, so it can't race a drama being moved: a
    series is refused while it holds another user's drama, a drama while
    it is in a series. False when refused or the item doesn't exist."""
    if kind == "series":
        guard = ("NOT EXISTS (SELECT 1 FROM dramas d, series s WHERE s.id = series.id "
                 f"AND d.series_id = s.id AND {_OTHERS_DRAMA_SQL})")
        table = "series"
    elif kind == "drama":
        guard, table = "series_id IS NULL", "dramas"
    else:
        raise ValueError(f"unknown ownership kind: {kind!r}")
    sql = f"UPDATE {table} SET is_private = ? WHERE id = ?"
    if private:
        sql += f" AND {guard}"
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(sql, (int(bool(private)), item_id))
        conn.commit()
        return cur.rowcount > 0


def series_has_unowned_drama(series_id: int) -> bool:
    """Whether a series holds a drama with no owner (made at the PC)."""
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute("SELECT 1 FROM dramas WHERE series_id = ? AND owner_user_id IS NULL "
                            "LIMIT 1", (series_id,)).fetchone() is not None


def rename_series(series_id: int, name: str) -> bool:
    """Raises sqlite3.IntegrityError when another series has this exact name
    (series.name is UNIQUE). False for an unknown series."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("UPDATE series SET name = ? WHERE id = ?", (name, series_id))
        conn.commit()
        return cur.rowcount > 0


# What deleting a series takes with it (the series_id foreign keys cascade)
# or detaches; also what the Series page shows.
_SERIES_COUNTS_SQL = (
    "SELECT (SELECT COUNT(*) FROM dramas WHERE series_id = s.id) AS titles, "
    "(SELECT COUNT(*) FROM glossary_terms WHERE series_id = s.id) AS glossary_terms, "
    "(SELECT COUNT(*) FROM series_characters WHERE series_id = s.id) AS characters, "
    "(SELECT COUNT(*) FROM translation_memory WHERE series_id = s.id) AS translation_memory, "
    "(SELECT COUNT(*) FROM glossary_dismissals WHERE series_id = s.id) AS dismissals, "
    "TRIM(COALESCE(s.instructions, '')) != '' AS has_instructions "
    "FROM series s WHERE s.id = ?")


def series_content_counts(series_id: int):
    """Counts of what a series holds, plus `title_lines` ({drama_id: line
    count} for its titles), or None for an unknown series."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute(_SERIES_COUNTS_SQL, (series_id,)).fetchone()
        if row is None:
            return None
        lines = conn.execute(
            "SELECT d.id, (SELECT COUNT(*) FROM lines l WHERE l.drama_id = d.id) AS n "
            "FROM dramas d WHERE d.series_id = ?", (series_id,)).fetchall()
    out = dict(row)
    out["has_instructions"] = bool(out["has_instructions"])
    out["title_lines"] = {r["id"]: r["n"] for r in lines}
    return out


def delete_series(series_id: int):
    """Deletes a series in one transaction and returns the counts of what it
    held (series_content_counts, without title_lines), or None for an
    unknown series. Its titles are kept and leave the series: dramas.series_id
    has no foreign key, so it is cleared here, and a title leaving a private
    series turns private itself (as unassign_drama_series does). Drama
    characters linked to its people keep their copied name. Glossary terms,
    people, translation memory and dismissals go by ON DELETE CASCADE.
    Touches no files."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.isolation_level = None   # explicit BEGIN/COMMIT below
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(_SERIES_COUNTS_SQL, (series_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE dramas SET is_private = CASE WHEN EXISTS (SELECT 1 FROM series s "
                "WHERE s.id = dramas.series_id AND COALESCE(s.is_private, 0) = 1) THEN 1 "
                "ELSE COALESCE(is_private, 0) END, series_id = NULL, updated_at = ? "
                "WHERE series_id = ?", (datetime.datetime.utcnow().isoformat(), series_id))
            conn.execute("UPDATE characters SET series_character_id = NULL WHERE "
                         "series_character_id IN (SELECT id FROM series_characters "
                         "WHERE series_id = ?)", (series_id,))
            conn.execute("DELETE FROM series WHERE id = ?", (series_id,))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    out = dict(row)
    out["has_instructions"] = bool(out["has_instructions"])
    return out


def list_item_sharing(limit: int, offset: int):
    """Every series and drama with its sharing fields, for the admin
    Sharing screen: (total, rows). A series comes just before its dramas;
    groups are sorted by series name or solo drama title. Owner names come
    from users.display_name only (never the email)."""
    items = (
        "SELECT 'series' AS kind, 0 AS kind_order, s.id, s.name AS title, s.owner_user_id, "
        "COALESCE(s.is_private, 0) AS is_private, NULL AS series_id, NULL AS series_name, "
        "NULL AS series_is_private, s.name AS sort_key, s.id AS group_id FROM series s "
        "UNION ALL "
        "SELECT 'drama', 1, d.id, COALESCE(NULLIF(d.title_en, ''), NULLIF(d.title_zh, ''), ''), "
        "d.owner_user_id, COALESCE(d.is_private, 0), d.series_id, s.name, "
        "CASE WHEN s.id IS NULL THEN NULL ELSE COALESCE(s.is_private, 0) END, "
        "COALESCE(s.name, NULLIF(d.title_en, ''), d.title_zh, ''), d.series_id "
        "FROM dramas d LEFT JOIN series s ON s.id = d.series_id")
    with contextlib.closing(db.get_conn()) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM ({items})").fetchone()[0]
        rows = conn.execute(
            f"SELECT i.*, u.id AS owner_found, u.display_name AS owner_display_name "
            f"FROM ({items}) i LEFT JOIN users u ON u.id = i.owner_user_id "
            "ORDER BY i.sort_key COLLATE NOCASE, i.sort_key, i.group_id IS NULL, i.group_id, "
            "i.kind_order, i.title COLLATE NOCASE, i.id LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
    return total, [dict(r) for r in rows]


def auth_get_user(user_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def auth_get_user_by_email(email: str):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def auth_get_user_by_sub(google_sub: str):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        return dict(row) if row else None


def auth_list_users():
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id").fetchall()]


def auth_get_permissions(user_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        return sorted(r[0] for r in conn.execute(
            "SELECT permission FROM user_permissions WHERE user_id = ?", (user_id,)).fetchall())


def auth_grant_permission(user_id: int, permission: str):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("INSERT OR IGNORE INTO user_permissions (user_id, permission) VALUES (?, ?)",
                     (user_id, permission))
        conn.commit()


def auth_revoke_permission(user_id: int, permission: str):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM user_permissions WHERE user_id = ? AND permission = ?",
                     (user_id, permission))
        conn.commit()


def auth_insert_session(id_hash: str, user_id: int, created_at: float, expires_at: float,
                        device_label: str, ip_prefix: str, csrf_hash: str) -> int:
    """The raw user agent is not stored: only the coarse `device_label`."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO auth_sessions (id_hash, user_id, created_at, expires_at, last_seen_at, "
            "user_agent_short, device_label, ip_prefix, csrf_hash) "
            "VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)",
            (id_hash, user_id, created_at, expires_at, created_at, device_label,
             ip_prefix, csrf_hash))
        conn.commit()
        return cur.lastrowid


def auth_rotate_session(session_id: int, user_id: int, id_hash: str, csrf_hash: str,
                        now: float, ip_prefix: str):
    """Replaces the user's session `session_id` with a new row under new
    hashes, in one transaction. The new row keeps the old one's sign-in time,
    expiry and device label, so rotating never extends a session. Returns the
    new id, or None if that session is already gone."""
    with contextlib.closing(db.get_conn()) as conn:
        with conn:
            old = conn.execute(
                "SELECT created_at, expires_at, device_label FROM auth_sessions "
                "WHERE id = ? AND user_id = ?", (session_id, user_id)).fetchone()
            if old is None:
                return None
            conn.execute("DELETE FROM auth_sessions WHERE id = ?", (session_id,))
            cur = conn.execute(
                "INSERT INTO auth_sessions (id_hash, user_id, created_at, expires_at, "
                "last_seen_at, user_agent_short, device_label, ip_prefix, csrf_hash) "
                "VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)",
                (id_hash, user_id, old["created_at"], old["expires_at"], now,
                 old["device_label"] or "", ip_prefix, csrf_hash))
            return cur.lastrowid


def auth_get_session_by_hash(id_hash: str):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM auth_sessions WHERE id_hash = ?", (id_hash,)).fetchone()
        return dict(row) if row else None


def auth_touch_session(session_id: int, now: float):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (now, session_id))
        conn.commit()


def auth_delete_session(session_id: int, user_id: int = None) -> bool:
    """`user_id`, when given, scopes the delete to that user's own session."""
    sql, args = "DELETE FROM auth_sessions WHERE id = ?", [session_id]
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount > 0


def auth_delete_user_sessions(user_id: int, except_id: int = None) -> int:
    """Every session of the user, or every one but `except_id` (the caller's own)."""
    sql, args = "DELETE FROM auth_sessions WHERE user_id = ?", [user_id]
    if except_id is not None:
        sql += " AND id != ?"
        args.append(except_id)
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount


def auth_delete_stale_sessions(now: float, idle_cutoff: float) -> int:
    """Deletes every session a lookup would refuse anyway: past its absolute
    expiry, idle since `idle_cutoff` or earlier, or held by a deactivated or
    missing user. Returns how many rows went."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ? OR last_seen_at <= ? "
            "OR user_id NOT IN (SELECT id FROM users WHERE is_active = 1)",
            (now, idle_cutoff))
        conn.commit()
        return cur.rowcount


def auth_list_sessions(user_id: int):
    """Never selects id_hash or csrf_hash."""
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, user_id, created_at, expires_at, last_seen_at, user_agent_short, "
            "device_label, ip_prefix FROM auth_sessions WHERE user_id = ? ORDER BY id",
            (user_id,)).fetchall()]


def auth_insert_audit(user_id, action: str, detail_redacted: str):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("INSERT INTO audit_log (ts, user_id, action, detail_redacted) "
                     "VALUES (?, ?, ?, ?)",
                     (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), user_id, action,
                      detail_redacted))
        conn.commit()


def auth_list_audit(limit: int = 100, before_id: int = None, action: str = None,
                    user_id: int = None):
    """Newest first. `before_id` pages back (rows with a smaller id);
    `action` and `user_id` are exact-match filters."""
    where, args = [], []
    if before_id is not None:
        where.append("id < ?")
        args.append(int(before_id))
    if action is not None:
        where.append("action = ?")
        args.append(action)
    if user_id is not None:
        where.append("user_id = ?")
        args.append(int(user_id))
    sql = "SELECT id, ts, user_id, action, detail_redacted FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?",
                                              (*args, int(limit))).fetchall()]


def auth_list_audit_actions(limit: int = 200):
    with contextlib.closing(db.get_conn()) as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT action FROM audit_log ORDER BY action LIMIT ?",
            (int(limit),)).fetchall()]


def auth_deactivate_user_keeping_an_admin(user_id: int) -> bool:
    """Sets is_active=0 unless that would leave no active admin. One
    statement, so two admins deactivating each other at once can't both
    succeed. False when refused or the user doesn't exist."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            # Truthiness as auth_service reads the flags: NULL is off, any
            # non-zero value is on.
            "UPDATE users SET is_active = 0 WHERE id = ? AND (COALESCE(is_admin, 0) = 0 "
            "OR COALESCE(is_active, 0) = 0 OR EXISTS (SELECT 1 FROM users o WHERE o.id != ? "
            "AND COALESCE(o.is_admin, 0) != 0 AND COALESCE(o.is_active, 0) != 0))",
            (user_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def auth_revoke_admin_keeping_an_admin(user_id: int) -> bool:
    """Sets is_admin=0 on an admin unless that would leave no active admin.
    One statement, so two admins demoting each other at once can't both
    succeed. False when refused, not an admin, or the user doesn't exist."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            # Flag truthiness as in auth_deactivate_user_keeping_an_admin.
            "UPDATE users SET is_admin = 0 WHERE id = ? AND COALESCE(is_admin, 0) != 0 "
            "AND (COALESCE(is_active, 0) = 0 OR EXISTS (SELECT 1 FROM users o WHERE o.id != ? "
            "AND COALESCE(o.is_admin, 0) != 0 AND COALESCE(o.is_active, 0) != 0))",
            (user_id, user_id))
        conn.commit()
        return cur.rowcount > 0
