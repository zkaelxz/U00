"""Helpers for the library restore: its SQL, and which zip members are
unsafe to extract (services/workspace_job_service, library_admin_service)."""
import os
import re
import zipfile


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
