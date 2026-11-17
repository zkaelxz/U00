"""Tests that a library restore refuses tampered or crafted backups and migrates old-schema ones."""

import io
import os
import zipfile

import pytest

from jobs import job_launch
import db
from services import library_admin_service as las
from services import library_backup_restore_service as lbr
from services import library_restore_service as lrs
from lib.errors import ConflictError, InvalidInputError, NotFoundError
import sqlite3
import tempfile

import page_server
from services import auth_service
from sources import store as src_store
from tests.test_library_admin_service import (_backup_bytes, _leftovers, _new, _restore, _rewrite, _write,
                                              _zip)


# ---- security re-review follow-ups ----------------------------------------

def _tampered(data, sql_statements, name="library.db"):
    """data with its `name` database changed by sql_statements."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        raw = zf.read(name)
    fd, path = tempfile.mkstemp(suffix=".db")
    os.write(fd, raw)
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        for sql in sql_statements:
            conn.execute(sql)
        conn.commit()
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.close()
        with open(path, "rb") as f:
            return _rewrite(data, replace={name: f.read()})
    finally:
        os.remove(path)


def _refused_unchanged(data):
    a_count = len(db.list_dramas())
    users = {u["email"] for u in db.auth_list_users()}
    with pytest.raises(InvalidInputError) as ei:
        _restore(data)
    assert db.LIBRARY_DIR not in str(ei.value)
    assert len(db.list_dramas()) == a_count
    assert {u["email"] for u in db.auth_list_users()} == users
    assert not _leftovers()


def test_trigger_in_library_db_refused(isolated_db):
    _new("A")
    data = _tampered(_backup_bytes(), [
        "CREATE TRIGGER t AFTER UPDATE ON job_records BEGIN "
        "INSERT INTO users (email, is_admin, is_active) VALUES ('evil@x', 1, 1); END"])
    _new("B")
    _refused_unchanged(data)
    assert db.auth_get_user_by_email("evil@x") is None


def test_view_named_lines_refused(isolated_db):
    _new("A")
    data = _tampered(_backup_bytes(), ["DROP TABLE lines",
                                       "CREATE VIEW lines AS SELECT 1 AS id"])
    _refused_unchanged(data)


def test_trigger_in_sources_db_refused(isolated_db):
    _new("A")
    src_store.set_setting("http_proxy_url", "")
    data = _tampered(_backup_bytes(), [
        "CREATE TRIGGER t AFTER INSERT ON access_attempts BEGIN "
        "UPDATE settings SET value = '\"http://evil:1\"' WHERE key = 'http_proxy_url'; END"],
        name="sources.db")
    _refused_unchanged(data)
    assert src_store.get_setting("http_proxy_url") == ""


@pytest.mark.parametrize("member", ["./backups/exports/planted.zip",
                                    "extension_token.txt.", "backups/ /x", "a//b",
                                    "NUL", "media/con.txt", "CONIN$", "lpt1/x.wav"])
def test_kept_name_variants_rejected_by_validation(isolated_db, member):
    with pytest.raises(InvalidInputError):
        lbr.validate_backup_zip(_zip({"library.db": b"x", member: b"x"}))


@pytest.mark.parametrize("member", ["./backups/exports/planted.zip", "../outside.txt",
                                    "/abs.txt", "C:/x.txt", "extension_token.txt.", "a//b",
                                    "media/Aux.mp3", "CONOUT$"])
def test_restore_core_refuses_unsafe_names_without_the_service_check(isolated_db, member):
    """Direct call, no service validation: the core checks names itself, so
    a second caller can't extract an unchecked zip."""
    _new("A")
    _write(page_server.TOKEN_FILENAME, b"current-token")
    count = len(db.list_dramas())
    data = _rewrite(_backup_bytes(), extra={member: b"x"})
    with pytest.raises(ValueError, match="unsafe file path"):
        lrs.restore_library_backup(data, db.LIBRARY_DIR)
    assert len(db.list_dramas()) == count and not _leftovers()
    with open(os.path.join(db.LIBRARY_DIR, page_server.TOKEN_FILENAME), "rb") as f:
        assert f.read() == b"current-token"
    with pytest.raises(NotFoundError):
        las.admin_artifact_path("export")


def test_kept_name_variants_removed_from_staging(isolated_db, tmp_path):
    """Direct call (no service validation): a name that passes the core's own
    check but that the filesystem resolves to a kept name (a case variant on
    Windows) is removed from staging. "./backups/..." is refused outright now
    (test_restore_core_refuses_unsafe_names_without_the_service_check)."""
    _new("A")
    _write(page_server.TOKEN_FILENAME, b"current-token")
    data = _rewrite(_backup_bytes(), extra={
        "BACKUPS/planted.zip": b"x",
    })
    lrs.restore_library_backup(data, db.LIBRARY_DIR)
    with pytest.raises(NotFoundError):
        las.admin_artifact_path("export")
    with open(os.path.join(db.LIBRARY_DIR, page_server.TOKEN_FILENAME), "rb") as f:
        assert f.read() == b"current-token"
    # Canonical removal: a planted plain FILE named "backups" is removed too.
    lib2 = str(tmp_path / "lib2")
    os.makedirs(os.path.join(lib2, "backups"))
    with open(os.path.join(lib2, "backups", "keep.zip"), "wb") as f:
        f.write(b"k")
    lrs.restore_library_backup(_rewrite(data, extra={"backups": b"a file"}), lib2)
    assert os.path.isfile(os.path.join(lib2, "backups", "keep.zip"))


def test_sidecar_members_not_extracted(isolated_db):
    _new("A")
    data = _rewrite(_backup_bytes(), extra={"library.db-wal": b"junk", "sources.db-shm": b"j",
                                            "library.db-journal": b"j"})
    lrs.restore_library_backup(data, db.LIBRARY_DIR)
    for n in ("library.db-journal",):
        assert not os.path.exists(os.path.join(db.LIBRARY_DIR, n))
    assert db.list_dramas()


def test_old_schema_backup_restores(isolated_db):
    """A backup from before job_records.cancel_requested (Slice 22) and the
    auth tables existed still restores; the staged copy is migrated."""
    _new("Old")
    data = _tampered(_backup_bytes(), [
        "DROP TABLE users", "DROP TABLE user_permissions", "DROP TABLE auth_sessions",
        "DROP TABLE audit_log",
        "CREATE TABLE jr_old AS SELECT job_id, status, progress, message, error, description, "
        "started_at, finished_at, updated_at FROM job_records",
        "DROP TABLE job_records", "ALTER TABLE jr_old RENAME TO job_records"])
    auth_service.add_user("current@example.com")
    _restore(data)
    assert [d["title_en"] for d in db.list_dramas()] == ["Old"]
    assert {u["email"] for u in db.auth_list_users()} == {"current@example.com"}
    assert db.get_job_record(las.BACKUP_JOB_ID)["status"] == "cancelled"


def test_restore_aborts_if_auth_changed_meanwhile(isolated_db, monkeypatch):
    a = _new("A")
    data = _backup_bytes()
    b = _new("B")
    orig = las.any_job_running
    calls = []

    def running():
        calls.append(1)
        if len(calls) == 2:   # the before_swap re-check: a sign-in happens now
            auth_service.add_user("new@example.com")
        return orig()
    monkeypatch.setattr(lbr, "any_job_running", running)
    with pytest.raises(ConflictError):
        _restore(data)
    assert db.get_drama(a) and db.get_drama(b)
    assert db.auth_get_user_by_email("new@example.com")
    assert not _leftovers()


def test_maintenance_blocks_restore(isolated_db):
    assert job_launch.enter_maintenance()
    try:
        with pytest.raises(ConflictError):
            _restore(_zip({"library.db": b"x"}))
    finally:
        job_launch.exit_maintenance()
    assert job_launch.acquire_exclusive("t")
    job_launch.release_exclusive()


def test_restore_audit_actor(isolated_db):
    _new("A")
    uid = auth_service.add_user("admin@example.com")["id"]
    lbr.restore_backup(_backup_bytes(), confirm=True, confirm_text="RESTORE", actor_id=uid)
    entry = [e for e in auth_service.list_audit() if e["action"] == "library.restore"][0]
    assert entry["user_id"] == uid


# ---- round 2: the upload never becomes a live schema (N1-N3) ---------------

def _raw_tampered(data, fn):
    """data with library.db changed by fn(conn) on a raw connection."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        raw = zf.read("library.db")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.write(fd, raw)
    os.close(fd)
    try:
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = DELETE")
        fn(conn)
        conn.close()
        with open(path, "rb") as f:
            return _rewrite(data, replace={"library.db": f.read()})
    finally:
        os.remove(path)


def test_mislabelled_trigger_row_refused(isolated_db):
    _new("A")
    evil_sql = ("CREATE TRIGGER t AFTER UPDATE ON job_records BEGIN "
                "INSERT INTO users (email, is_admin, is_active) VALUES ('evil@x', 1, 1); END")

    def plant(conn):
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute("INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) "
                      "VALUES ('table', 't', 'job_records', 0, ?)", (evil_sql,))
        conn.execute("PRAGMA writable_schema = OFF")
    data = _raw_tampered(_backup_bytes(), plant)
    before = {u["email"] for u in db.auth_list_users()}
    with pytest.raises(InvalidInputError):
        _restore(data)
    assert {u["email"] for u in db.auth_list_users()} == before
    assert db.auth_get_user_by_email("evil@x") is None
    assert not _leftovers()


def test_virtual_table_named_dramas_refused(isolated_db):
    _new("A")

    def plant(conn):
        conn.execute("DROP TABLE dramas")
        conn.execute("CREATE VIRTUAL TABLE dramas USING fts5(title)")
    try:
        data = _raw_tampered(_backup_bytes(), plant)
    except sqlite3.OperationalError:
        pytest.skip("this SQLite build has no fts5")
    with pytest.raises(InvalidInputError):
        _restore(data)
    assert db.list_dramas()


def test_uploaded_users_definition_has_no_effect(isolated_db):
    _new("A")
    data = _backup_bytes()

    def plant(conn):
        conn.execute("DROP TABLE users")
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, is_admin TEXT, "
                     "is_active INTEGER)")
        conn.execute("INSERT INTO users (email, is_admin, is_active) "
                     "VALUES ('evil@x', 'yes', 1)")
    data = _raw_tampered(data, plant)
    auth_service.add_user("current@example.com")
    _restore(data)
    assert {u["email"] for u in db.auth_list_users()} == {"current@example.com"}
    conn = sqlite3.connect(db.DB_PATH)
    col = [r for r in conn.execute("PRAGMA table_info(users)").fetchall() if r[1] == "is_admin"]
    conn.close()
    assert col[0][2] == "INTEGER"


def test_old_schema_backup_restores_dramas_and_lines(isolated_db):
    from core import Line
    did = _new("Old")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="句", en="Line")])
    data = _backup_bytes()

    def age(conn):   # drop a column newer code added, as an older backup would lack it
        conn.execute("ALTER TABLE vocab_lookups DROP COLUMN export_rich")
    data = _raw_tampered(data, age)
    _new("Newer")
    _restore(data)
    assert [d["title_en"] for d in db.list_dramas()] == ["Old"]
    assert [r["en"] for r in db.load_lines(did)] == ["Line"]


def test_round_trip_keeps_rows(isolated_db):
    from core import Line
    a = _new("A", status="translated")
    db.save_lines(a, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}", en=f"L{i}")
                      for i in range(3)])
    sid = db.get_or_create_series("S")
    db.update_drama(a, series_id=sid)
    data = _backup_bytes()
    db.update_drama(a, status="aligned")
    _restore(data)
    d = db.get_drama(a)
    assert d["status"] == "translated" and d["series_id"] == sid
    assert [r["en"] for r in db.load_lines(a)] == ["L0", "L1", "L2"]
    b = _new("After restore")   # autoincrement continues past restored ids
    assert b > a


@pytest.mark.parametrize("sql,ok", [
    ("CREATE TABLE x (a)", True),
    ("create  unique index i on x(a)", True),
    ("/* c */ CREATE TABLE \"x\"(a)", True),
    ("CREATE TRIGGER t AFTER INSERT ON x BEGIN SELECT 1; END", False),
    ("CREATE VIEW v AS SELECT 1", False),
    ("CREATE VIRTUAL TABLE v USING fts5(a)", False),
    ("-- CREATE TABLE\nCREATE TRIGGER t", False),
    ("CREATE TEMP TABLE x (a)", False),
])
def test_schema_sql_allowlist(sql, ok):
    assert lrs._schema_sql_allowed(sql) is ok


# ---- round 3: data migrations run on the uploaded rows (R3-1, R3-2) --------

def _q(sql, args=()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_pre_26e_backup_migrates_progress_and_notes(isolated_db):
    did = _new("Old")
    data = _backup_bytes()

    def age(conn):
        conn.execute("DELETE FROM profiles")
        conn.execute("DELETE FROM personal_notes")
        conn.execute("DROP TABLE progress")
        conn.execute("CREATE TABLE progress (drama_id INTEGER PRIMARY KEY, "
                     "last_line_idx INTEGER DEFAULT 0, audio_position_seconds REAL DEFAULT 0, "
                     "last_page INTEGER DEFAULT 1, percent_complete REAL DEFAULT 0, "
                     "last_accessed_at TEXT)")
        conn.execute("INSERT INTO progress (drama_id, last_line_idx, percent_complete) "
                     "VALUES (?, 7, 0.5)", (did,))
        conn.execute("UPDATE dramas SET personal_notes = 'my note' WHERE id = ?", (did,))
    _restore(_raw_tampered(data, age))
    profiles = _q("SELECT id, name FROM profiles")
    assert len(profiles) == 1
    pid = profiles[0][0]
    assert _q("SELECT drama_id, profile_id, last_line_idx FROM progress") == [(did, pid, 7)]
    assert _q("SELECT profile_id, drama_id, notes FROM personal_notes") == [
        (pid, did, "my note")]


def test_pre_step2_backup_maps_line_ids(isolated_db):
    from core import Line
    did = _new("Old")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}") for i in range(3)])
    data = _backup_bytes()

    def age(conn):
        for t, cols in (("translation_notes", "term TEXT, note_type TEXT, note TEXT"),
                        ("line_emotions", "emotion TEXT, intensity REAL, note TEXT")):
            conn.execute(f"DROP TABLE {t}")
            conn.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         f"drama_id INTEGER NOT NULL, line_idx INTEGER, {cols}, "
                         "created_at TEXT)")
        conn.execute("INSERT INTO translation_notes (drama_id, line_idx, term, note) "
                     "VALUES (?, 2, 'x', 'n')", (did,))
        conn.execute("INSERT INTO line_emotions (drama_id, line_idx, emotion) "
                     "VALUES (?, 1, 'sad')", (did,))
    _restore(_raw_tampered(data, age))
    ids = dict(_q("SELECT idx, id FROM lines WHERE drama_id = ?", (did,)))
    assert _q("SELECT line_id FROM translation_notes") == [(ids[2],)]
    assert _q("SELECT line_id FROM line_emotions") == [(ids[1],)]


@pytest.mark.parametrize("sql", [
    "CREATE TABLE x (a INTEGER, b INTEGER GENERATED ALWAYS AS (a * 2))",
    "CREATE TABLE x (a INTEGER, b INTEGER AS (a * 2) VIRTUAL)",
])
def test_generated_columns_refused(sql):
    assert lrs._schema_sql_allowed(sql) is False


def test_generated_column_backup_refused(isolated_db):
    _new("A")
    data = _raw_tampered(_backup_bytes(), lambda c: c.execute(
        "CREATE TABLE extra (a INTEGER, b INTEGER GENERATED ALWAYS AS (a + 1))"))
    with pytest.raises(InvalidInputError):
        _restore(data)


def test_sqlite_stat_tables_dropped(isolated_db):
    _new("A")
    data = _raw_tampered(_backup_bytes(), lambda c: c.execute("ANALYZE"))
    _restore(data)
    assert not _q("SELECT name FROM sqlite_master WHERE name LIKE 'sqlite_stat%'")
    assert db.list_dramas()
