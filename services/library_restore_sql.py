"""SQL helpers for the library restore (services/workspace_job_service)."""


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
