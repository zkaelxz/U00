"""SQL helpers for the library restore (services/workspace_job_service)."""
import os


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
