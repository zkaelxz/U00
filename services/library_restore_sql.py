"""Helpers for the library restore: its SQL, and which zip members are
unsafe to extract (services/library_restore_service, library_admin_service)."""
import contextlib
import logging
import os
import re
import sqlite3
import zipfile

import db


def copy_rows(conn, src: str, table: str):
    """Replaces main.table's rows with src.table's, for the columns both
    sides have (a column only the app's schema has gets its default)."""
    main_cols = [r[1] for r in conn.execute(f'PRAGMA main.table_info("{table}")').fetchall()]
    src_cols = {r[1] for r in conn.execute(f'PRAGMA {src}.table_info("{table}")').fetchall()}
    cols = [c for c in main_cols if c in src_cols]
    if not cols:
        return
    col_sql = ", ".join(f'"{c}"' for c in cols)
    conn.execute(f'DELETE FROM main."{table}"')   # rows init_db seeded (e.g. profiles)
    conn.execute(f'INSERT INTO main."{table}" ({col_sql}) SELECT {col_sql} FROM {src}."{table}"')


def _ro_uri(path: str, ro: bool = True) -> str:
    from urllib.parse import quote
    return f"file:{quote(os.path.abspath(path))}" + ("?mode=ro" if ro else "")


def _open_carry_conn(path: str):
    import sqlite3
    conn = sqlite3.connect(_ro_uri(path, ro=False), uri=True, isolation_level=None)
    conn.execute("PRAGMA trusted_schema = OFF")
    return conn


def _table_names(conn, schema: str) -> list:
    return [r[0] for r in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]


def _schema_sql_allowed(sql: str) -> bool:
    """True only for CREATE TABLE / CREATE [UNIQUE] INDEX text (comments
    and whitespace ignored, case-insensitive). Everything else -- triggers,
    views, virtual tables, TEMP objects -- is refused, whatever
    sqlite_master's `type` column claims (SQLite re-parses `sql` and
    ignores `type`, so a writable_schema-planted row can lie about it)."""
    import re
    text = re.sub(r"--[^\n]*|/\*.*?(\*/|$)", " ", sql, flags=re.S)
    text = " ".join(text.split()).upper()
    if not re.match(r"CREATE (TABLE|(UNIQUE )?INDEX)[ \"'`\[(]", text + " "):
        return False
    # Generated columns: quick_check and the row copy would evaluate them.
    return "GENERATED" not in text and not re.search(r"\bAS ?\(", text)


def _current_state_marker(library_dir: str):
    """(max audit_log id, sources settings rows) of the live library,
    compared right before the swap so a sign-in or settings change made
    while the restore was preparing isn't silently dropped.

    Known limits: a change landing between this last comparison and the
    rename is still lost (a small window, no cross-process lock), and
    only audited auth changes move the audit id -- a session created or
    revoked without an audit entry, or a sources.db table other than
    settings, isn't covered (sessions are revoked by the restore anyway)."""
    import sqlite3
    marker = []
    for name, sql in (("library.db", "SELECT MAX(id) FROM audit_log"),
                      ("sources.db", "SELECT key, value FROM settings ORDER BY key")):
        path = os.path.join(library_dir, name)
        if not os.path.isfile(path):
            marker.append(None)
            continue
        try:
            conn = sqlite3.connect(_ro_uri(path), uri=True)
            try:
                marker.append(tuple(tuple(r) for r in conn.execute(sql).fetchall()))
            finally:
                conn.close()
        except sqlite3.Error:
            marker.append(None)
    return tuple(marker)


def _unsafe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    norm = name.replace("\\", "/")
    if not norm or "\x00" in norm or norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return True
    parts = (norm[:-1] if info.is_dir() else norm).split("/")
    # "." / empty parts are dropped by zipfile (./backups -> backups), and a
    # part ending in a dot or space names the same file as without it on
    # Windows -- either could slip a member past the kept-name filter.
    if any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts):
        return True
    return (info.external_attr >> 16) & 0o170000 == 0o120000  # symlink entry


def _fsync_dir(path: str) -> None:
    with contextlib.suppress(OSError):   # durability only: never fails the restore
        db.fsync_dir(path)


def flush_tree(path: str) -> None:
    """Best effort: what the zip put in `path` reaches the disk before the
    previous library, the other copy of it, is removed."""
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                with open(os.path.join(root, name), "r+b") as fh:
                    os.fsync(fh.fileno())
        _fsync_dir(root)


def _titles(db_path: str) -> dict:
    conn = sqlite3.connect(_ro_uri(db_path), uri=True)
    try:
        return {str(i): c for i, c in conn.execute("SELECT id, created_at FROM dramas") if c}
    finally:
        conn.close()


def _raise_drama_sequence(db_path: str, floor: int) -> None:
    conn = _open_carry_conn(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO sqlite_sequence (name, seq) "
                     "SELECT 'dramas', 0 WHERE NOT EXISTS "
                     "(SELECT 1 FROM sqlite_sequence WHERE name = 'dramas')")
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'dramas'", (floor,))
    finally:
        conn.close()


def _carry_titles(src: str, dst: str, library_dir: str, old_dir: str) -> bool:
    """Media folders are named by title id, and ids repeat across libraries
    and after a restore: a folder moves only when both databases hold the
    same title under its id (same created_at). Leftovers stay in `src`, and
    new ids start above every one of them. True when any folder moved."""
    restored = _titles(os.path.join(library_dir, "library.db"))
    previous = _titles(os.path.join(old_dir, "library.db"))
    os.makedirs(dst, exist_ok=True)
    moved = False
    for name in os.listdir(src):
        if name in restored and restored[name] == previous.get(name):
            os.rename(os.path.join(src, name), os.path.join(dst, name))
            moved = True
    _raise_drama_sequence(os.path.join(library_dir, "library.db"),
                          max((int(n) for n in os.listdir(src) if n.isdigit()), default=0))
    return moved


def carry_across(old_dir: str, library_dir: str, names, media) -> tuple:
    """Moves `names` from the previous library into the restored one;
    `media` are the media folders the zip had none of. (media kept,
    everything moved: only then may the previous library be removed)."""
    media_kept, carried = False, True
    for name in names:
        src, dst = os.path.join(old_dir, name), os.path.join(library_dir, name)
        if not os.path.lexists(src):
            continue
        try:
            if name == "dramas" and name in media:
                media_kept = _carry_titles(src, dst, library_dir, old_dir) or media_kept
                carried = carried and not os.listdir(src)
                _fsync_dir(dst)
            else:
                has_files = name in media and bool(os.listdir(src))
                os.rename(src, dst)
                media_kept = media_kept or has_files
        except (OSError, sqlite3.Error):
            carried = False
            logging.getLogger(__name__).warning(
                "Restore: could not carry %s across; the previous library was left in "
                "place beside the restored one.", name)
    _fsync_dir(library_dir)
    return media_kept, carried


def has_accounts(db_path: str) -> bool:
    """Whether a library database holds any account: False only when it, or
    its users table, is missing. A database that can't be read (locked,
    damaged) counts as having accounts, so the live ones are kept."""
    if not os.path.isfile(db_path):
        return False
    try:
        conn = sqlite3.connect(_ro_uri(db_path), uri=True)
        try:
            return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        return "no such table" not in str(e)
    except sqlite3.Error:
        return True


# Columns naming an account. An id one of them still names must never go to
# a new account: it would inherit that person's private titles and history.
_ACCOUNT_ID_COLUMNS = (("dramas", "owner_user_id"), ("series", "owner_user_id"),
                       ("job_records", "owner_user_id"), ("translate_history", "user_id"),
                       ("speaker_merge_undos", "user_id"), ("audit_log", "user_id"))


def settle_accounts(conn, live_path: str) -> None:
    """On the staged library.db (main): drops permissions of accounts it
    doesn't hold, and starts new account ids above every id its rows or the
    live library ever used."""
    conn.execute("DELETE FROM main.user_permissions WHERE user_id NOT IN "
                 "(SELECT id FROM main.users)")
    floor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM main.users").fetchone()[0]
    for table, column in _ACCOUNT_ID_COLUMNS:
        cols = {r[1] for r in conn.execute(f'PRAGMA main.table_info("{table}")')}
        if column in cols:
            floor = max(floor, conn.execute(
                f'SELECT COALESCE(MAX("{column}"), 0) FROM main."{table}" '
                f"WHERE typeof(\"{column}\") = 'integer'").fetchone()[0])
    if os.path.isfile(live_path):
        conn.execute("ATTACH DATABASE ? AS cur", (_ro_uri(live_path),))
        try:
            row = conn.execute("SELECT seq FROM cur.sqlite_sequence WHERE name = 'users'") \
                .fetchone()
            floor = max(floor, int(row[0] or 0) if row else 0)
        except sqlite3.OperationalError:
            pass
        finally:
            conn.execute("DETACH DATABASE cur")
    conn.execute("INSERT INTO main.sqlite_sequence (name, seq) SELECT 'users', 0 WHERE NOT EXISTS "
                 "(SELECT 1 FROM main.sqlite_sequence WHERE name = 'users')")
    conn.execute("UPDATE main.sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'users'",
                 (floor,))


# The account-naming columns of rows that come from the backup when the live
# accounts are kept (audit_log is the live library's own then).
_RESTORED_ACCOUNT_ID_COLUMNS = tuple(c for c in _ACCOUNT_ID_COLUMNS if c[0] != "audit_log")


def _accounts(conn, schema: str) -> list:
    try:
        return conn.execute(f"SELECT id, email, google_sub FROM {schema}.users").fetchall()
    except sqlite3.OperationalError:
        return []


def match_owners(conn, backup_path: str) -> int:
    """On the staged library.db (main), which holds the live accounts: each
    account id the backup's rows name becomes the live account with the
    same email, or NULL (the PC owner) when there is none. Ids repeat
    across libraries, so an id alone would hand a member's private titles
    to a stranger. Emails match only if the two accounts' Google sign-ins
    don't differ: that would be another person given the address since.
    Private flags are left as they are. Returns how many titles changed
    owner."""
    live = {}
    for uid, email, sub in _accounts(conn, "main"):
        live[(email or "").strip().lower()] = (uid, sub)
    conn.execute("ATTACH DATABASE ? AS up", (_ro_uri(backup_path),))
    try:
        backup = _accounts(conn, "up")
    finally:
        conn.execute("DETACH DATABASE up")
    pairs = []
    for uid, email, sub in backup:
        match = live.get((email or "").strip().lower())
        if match and not (sub and match[1] and sub != match[1]):
            pairs.append((uid, match[0]))
    conn.execute("CREATE TEMP TABLE owner_map (old INTEGER PRIMARY KEY, new INTEGER)")
    try:
        conn.executemany("INSERT INTO temp.owner_map (old, new) VALUES (?, ?)", pairs)
        reassigned = conn.execute(
            "SELECT COUNT(*) FROM main.dramas WHERE owner_user_id IS NOT NULL AND "
            "owner_user_id IS NOT (SELECT new FROM temp.owner_map "
            "WHERE old = dramas.owner_user_id)").fetchone()[0]
        for table, column in _RESTORED_ACCOUNT_ID_COLUMNS:
            cols = {r[1] for r in conn.execute(f'PRAGMA main.table_info("{table}")')}
            if column in cols:
                # One statement per table, so a swap (1 -> 2, 2 -> 1) can't chain.
                conn.execute(f'UPDATE main."{table}" SET "{column}" = (SELECT new FROM '
                             f'temp.owner_map WHERE old = main."{table}"."{column}") '
                             f'WHERE "{column}" IS NOT NULL')
    finally:
        conn.execute("DROP TABLE temp.owner_map")
    return reassigned


def hand_follows_to_pc_owner(sources_path: str) -> None:
    """The restored followed channels name accounts of the backup's library,
    which the live accounts kept by the restore are not: each passes to the
    PC owner (no account), without the owner's powers or automatic checks
    until someone turns them back on."""
    conn = _open_carry_conn(sources_path)
    try:
        if "followed_channels" in _table_names(conn, "main"):
            conn.execute("UPDATE followed_channels SET owner_user_id = NULL, pc_owner = 0, "
                         "auto_check = 0, mode = 'notify'")
    finally:
        conn.close()


def folders_left_behind(old_dir: str, skip) -> list:
    """Names of the folders still in the previous library that are not in
    `skip` (what the zip held, media, folders the app rebuilds)."""
    try:
        with os.scandir(old_dir) as entries:
            return sorted(e.name for e in entries
                          if e.is_dir(follow_symlinks=False) and e.name not in skip)
    except OSError:
        return []
