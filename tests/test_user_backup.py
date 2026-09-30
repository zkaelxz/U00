"""Backup of one person's items (library_admin_service.start_user_backup):
every table classified, and the round trip -- two users' dramas (private
and in shared series) exported for user A, restored into an empty
library -- keeps A's rows and media and nothing of B's, of the PC's or of
any auth data, checked across every table and every file in the zip."""
import hashlib
import io
import itertools
import os
import sqlite3
import time
import zipfile

import pytest

import background_jobs
import db
from services import auth_service
from services import library_admin_service as las
from services.service_errors import InvalidInputError, NotFoundError

OTHER = "zzotherzz"   # must never leave the library in A's backup
MINE = "zzminezz"
HOUSE = "zzhousezz"   # household-wide tables kept on purpose

_counter = itertools.count(1)


def _wait(job_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _cols(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


# ---- classification -----------------------------------------------------------

def test_every_table_is_classified(isolated_db):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        tables = set(_tables(conn))
        assert tables - set(las.USER_BACKUP_TABLES) == set(), \
            "classify the new table in library_admin_service.USER_BACKUP_TABLES"
        assert set(las.USER_BACKUP_TABLES) - tables == set(), "stale entry"
        for t in tables:
            rule = las.USER_BACKUP_TABLES[t][0]
            cols = set(_cols(conn, t))
            if "drama_id" in cols:
                assert rule in ("drama", "series_character", "empty"), t
            if "series_id" in cols and t != "dramas":
                assert rule in ("series", "empty"), t
            if "page_id" in cols:
                assert rule in ("page", "empty"), t
            if cols & {"user_id", "owner_user_id"} and t not in ("dramas", "series"):
                assert rule in ("user", "empty"), t
            if rule == "keep":
                assert not cols & {"drama_id", "series_id", "user_id", "owner_user_id",
                                   "line_id", "page_id", "profile_id"}, t
    finally:
        conn.close()


def test_bad_user_refused(isolated_db):
    with pytest.raises(NotFoundError):
        las.start_user_backup(424242)
    for bad in (0, -1, True, "1", 1.0):
        with pytest.raises(InvalidInputError):
            las.start_user_backup(bad)
    assert background_jobs.list_all_jobs() == {}


# ---- the round trip -------------------------------------------------------------

def _seed_row(conn, table, fixed, marker):
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    single_int_pk = sum(1 for r in info if r[5]) == 1
    cols, vals = [], []
    for _cid, name, ctype, _notnull, _dflt, pk in info:
        ctype = (ctype or "").upper()
        if name in fixed:
            value = fixed[name]
        elif pk and single_int_pk and "INT" in ctype:
            continue
        elif "INT" in ctype:
            value = 1
        elif "REAL" in ctype:
            value = 1.0
        else:
            value = f"{marker}_{table}_{name}_{next(_counter)}"
        cols.append(f'"{name}"')
        vals.append(value)
    conn.execute(f'INSERT INTO "{table}" ({", ".join(cols)}) '
                 f'VALUES ({", ".join("?" for _ in vals)})', vals)


def _write(rel, data):
    path = os.path.join(db.LIBRARY_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _library_state():
    """Every row of every table (except the backup job's own job mirror
    and timing rows) and every file outside backups/ and logs/, hashed."""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        rows = {}
        for t in _tables(conn):
            if t in ("job_records", "job_stage_timings"):
                rows[t] = sorted(repr(r) for r in conn.execute(
                    f"SELECT * FROM {t} WHERE job_id != ?", (las.USER_BACKUP_JOB_ID,)))
            else:
                rows[t] = sorted(repr(r) for r in conn.execute(f'SELECT * FROM "{t}"'))
    finally:
        conn.close()
    files = {}
    for root, dirs, names in os.walk(db.LIBRARY_DIR):
        if root == db.LIBRARY_DIR:
            dirs[:] = [d for d in dirs if d not in ("backups", "logs")]
        for n in names:
            full = os.path.join(root, n)
            if n.startswith("library.db"):
                continue
            with open(full, "rb") as f:
                files[os.path.relpath(full, db.LIBRARY_DIR)] = hashlib.sha256(f.read()).hexdigest()
    return rows, files


def _build_library():
    a = auth_service.add_user(f"{OTHER}-a@example.com", display_name=f"{OTHER} A")["id"]
    b = auth_service.add_user(f"{OTHER}-b@example.com", display_name=f"{OTHER} B")["id"]
    auth_service.grant_permission(a, "lines.edit")
    auth_service.create_session(a, "pytest", "203.0.113.9")
    auth_service.create_session(b, "pytest", "203.0.113.9")
    auth_service.write_audit(b, "test.action", f"{OTHER} detail")

    sa = db.create_series(f"{MINE} series", owner_user_id=a)            # A's, shared
    sb = db.create_series(f"{OTHER} series", owner_user_id=b)           # B's, shared
    d = {
        "a_in_sa": db.create_drama(title_en=f"{MINE} 1", owner_user_id=a, series_id=sa),
        "a_in_sb": db.create_drama(title_en=f"{MINE} 2", owner_user_id=a, series_id=sb),
        "a_private": db.create_drama(title_en=f"{MINE} 3", owner_user_id=a, is_private=1),
        "b_in_sa": db.create_drama(title_en=f"{OTHER} 1", owner_user_id=b, series_id=sa),
        "b_in_sb": db.create_drama(title_en=f"{OTHER} 2", owner_user_id=b, series_id=sb),
        "b_private": db.create_drama(title_en=f"{OTHER} 3", owner_user_id=b, is_private=1),
        "pc": db.create_drama(title_en=f"{OTHER} pc"),
    }
    mine = {d["a_in_sa"], d["a_in_sb"], d["a_private"]}

    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        chars = {}
        for sid, marker in ((sa, MINE), (sb, OTHER)):
            for t in ("series_characters", "glossary_terms", "translation_memory"):
                _seed_row(conn, t, {"series_id": sid}, marker)
            chars[sid] = conn.execute("SELECT id FROM series_characters WHERE series_id = ?",
                                      (sid,)).fetchone()[0]
        series_of = {did: sid for did, sid in conn.execute("SELECT id, series_id FROM dramas")}
        drama_tables = [t for t in _tables(conn)
                        if t != "dramas" and "drama_id" in _cols(conn, t)]
        for did in d.values():
            marker = MINE if did in mine else OTHER
            for t in drama_tables:
                fixed = {"drama_id": did}
                if "series_character_id" in _cols(conn, t):
                    fixed["series_character_id"] = chars[series_of[did] or sb]
                _seed_row(conn, t, fixed, marker)
        for page_id, did in conn.execute("SELECT id, drama_id FROM pages").fetchall():
            _seed_row(conn, "bubbles", {"page_id": page_id}, MINE if did in mine else OTHER)
        for t in ("usage_log", "vocab_lookups", "edit_samples"):
            _seed_row(conn, t, {"drama_id": None}, OTHER)         # nobody's rows
        for uid, marker in ((a, MINE), (b, OTHER), (None, OTHER)):
            _seed_row(conn, "translate_history", {"user_id": uid}, marker)
        for scope, marker in (("global", OTHER), (f"series:{sa}", MINE), (f"series:{sb}", OTHER)):
            _seed_row(conn, "style_profile", {"scope": scope}, marker)
        _seed_row(conn, "profiles", {}, OTHER)                    # an unreferenced profile
        seeded = set(drama_tables) | {"series_characters", "glossary_terms", "bubbles",
                                      "translation_memory", "translate_history",
                                      "style_profile", "profiles", "dramas", "series", "users",
                                      "user_permissions", "auth_sessions", "audit_log"}
        for t in _tables(conn):
            if t not in seeded:
                rule = las.USER_BACKUP_TABLES[t][0]
                _seed_row(conn, t, {}, HOUSE if rule == "keep" else OTHER)
        # A pre-Step-2 migration copy and a table nobody classified yet.
        conn.execute("CREATE TABLE _backup_step2_translation_notes AS "
                     "SELECT * FROM translation_notes")
        conn.execute("CREATE TABLE zz_unclassified (drama_id INTEGER, note TEXT)")
        conn.execute("INSERT INTO zz_unclassified VALUES (?, ?)", (d["a_in_sa"], f"{OTHER} x"))
        conn.commit()
    finally:
        conn.close()

    for did in d.values():
        marker = (MINE if did in mine else OTHER).encode()
        _write(f"dramas/{did}/audio.wav", marker + b" audio")
        _write(f"dramas/{did}/pages/p1.png", marker + b" page")
    _write("dramas/999999/audio.wav", OTHER.encode())             # no such drama
    _write("dramas/stray.txt", OTHER.encode())
    _write("voice_bank/clip.wav", OTHER.encode())
    _write("benchmark_cases/case.wav", OTHER.encode())
    _write("stray.txt", OTHER.encode())
    _write(".env", OTHER.encode())
    _write(f"dramas/{d['a_in_sa']}/.env", OTHER.encode())
    try:
        os.symlink(os.path.join(db.DRAMAS_DIR, str(d["b_private"]), "audio.wav"),
                   os.path.join(db.DRAMAS_DIR, str(d["a_in_sa"]), "link.wav"))
        os.symlink(os.path.join(db.DRAMAS_DIR, str(d["b_private"])),
                   os.path.join(db.DRAMAS_DIR, str(d["a_in_sa"]), "linkdir"))
    except (OSError, NotImplementedError):
        pass
    return a, b, sa, sb, d, mine, drama_tables


def _assert_clean_db_file(path):
    for p in (path, path + "-wal"):
        if os.path.exists(p):
            with open(p, "rb") as f:
                leaked = OTHER.encode() in f.read().lower()
            assert not leaked, p


def _assert_no_other_rows(conn):
    for t in _tables(conn):
        for row in conn.execute(f'SELECT * FROM "{t}"'):
            assert OTHER not in repr(row).lower(), (t, row)


def test_user_backup_round_trip(isolated_db, tmp_path):
    a, b, sa, sb, d, mine, drama_tables = _build_library()
    before = _library_state()

    r = las.start_user_backup(a)
    assert r == {"job_id": las.USER_BACKUP_JOB_ID}
    st = _wait(r["job_id"])
    assert st["status"] == "done", st.get("error")
    assert set(st["result"]) == {"name", "size"}
    info = las.latest_admin_artifact("user_backup")
    assert db.LIBRARY_DIR not in repr(info) and OTHER not in info["name"]
    assert info["name"].startswith("baihe_my_items_backup_") and info["name"].endswith(".zip")
    with open(las.admin_artifact_path("user_backup")["path"], "rb") as f:
        data = f.read()

    # The live library was only read.
    assert _library_state() == before
    assert {x["id"] for x in db.list_dramas()} == set(d.values())

    # Every file in the zip: only A's drama folders and the database, and
    # no byte of anyone else's data (the database file included, so no
    # deleted row survives in a free page).
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        for n in names:
            leaked = OTHER.encode() in zf.read(n).lower()
            assert not leaked, n
    allowed = {"library.db"} | {f"dramas/{did}/{f}" for did in mine
                                for f in ("audio.wav", "pages/p1.png")}
    assert set(names) == allowed
    las.validate_backup_zip(data)

    # Restore into an empty library.
    fresh = tmp_path / "fresh" / "library"
    fresh.mkdir(parents=True)
    db.configure_library_dir(str(fresh))
    db.init_db()
    assert db.list_dramas() == []
    background_jobs.clear_all_jobs()
    assert las.restore_backup(data, confirm=True, confirm_text="RESTORE")["restored"] is True

    conn = sqlite3.connect(db.DB_PATH)
    try:
        _assert_no_other_rows(conn)
        tables = set(_tables(conn))
        assert "zz_unclassified" not in tables
        assert not [t for t in tables if t.startswith("_backup_step2_")]
        # A's dramas and series, no owner id from the old library.
        assert sorted(r[0] for r in conn.execute("SELECT id FROM dramas")) == sorted(mine)
        assert [tuple(r) for r in conn.execute("SELECT id, owner_user_id FROM series")] == \
            [(sa, None)]
        series_of = dict(conn.execute("SELECT id, series_id FROM dramas").fetchall())
        assert series_of == {d["a_in_sa"]: sa, d["a_in_sb"]: None, d["a_private"]: None}
        assert {r[0] for r in conn.execute("SELECT owner_user_id FROM dramas")} == {None}
        assert conn.execute("SELECT is_private FROM dramas WHERE id = ?",
                            (d["a_private"],)).fetchone()[0] == 1
        # Every drama table: A's rows, each drama's intact.
        for t in drama_tables:
            if las.USER_BACKUP_TABLES[t][0] != "drama":
                continue
            got = {r[0] for r in conn.execute(f'SELECT drama_id FROM "{t}"')}
            assert got == mine, t
        assert [tuple(r) for r in conn.execute(
            "SELECT drama_id, series_character_id FROM voice_suggestion_dismissals")] == \
            [(d["a_in_sa"], conn.execute("SELECT id FROM series_characters").fetchone()[0])]
        # Links to B's series' characters are cleared, A's series' kept.
        links = dict(conn.execute("SELECT drama_id, series_character_id FROM characters"))
        assert links[d["a_in_sb"]] is None and links[d["a_in_sa"]] is not None
        a_pages = {r[0] for r in conn.execute("SELECT id FROM pages")}
        assert {r[0] for r in conn.execute("SELECT page_id FROM bubbles")} == a_pages
        for t in ("glossary_terms", "series_characters", "translation_memory"):
            assert {r[0] for r in conn.execute(f"SELECT series_id FROM {t}")} == {sa}, t
        assert [tuple(r) for r in conn.execute("SELECT user_id FROM translate_history")] == \
            [(None,)]
        assert [r[0] for r in conn.execute("SELECT scope FROM style_profile")] == [f"series:{sa}"]
        assert [r[0] for r in conn.execute("SELECT name FROM profiles")] == ["Me"]
        for t in ("known_titles", "presets"):
            assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 1, t
        # No auth data: the fresh install's own (none) plus the restore's audit line.
        for t in ("users", "user_permissions", "auth_sessions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
        assert [r[0] for r in conn.execute("SELECT action FROM audit_log")] == ["library.restore"]
        for t, (rule, _why) in las.USER_BACKUP_TABLES.items():
            if rule == "empty" and t not in ("audit_log", "job_records"):
                assert conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == 0, t
    finally:
        conn.close()
    _assert_clean_db_file(db.DB_PATH)

    # A's media intact; nothing else came along.
    files = set()
    for root, dirs, names in os.walk(db.LIBRARY_DIR):
        for n in names:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, db.LIBRARY_DIR).replace(os.sep, "/")
            if rel.startswith("library.db") or rel.startswith("backups/"):
                continue
            files.add(rel)
            with open(full, "rb") as f:
                leaked = OTHER.encode() in f.read().lower()
            assert not leaked, rel
    assert files == allowed - {"library.db"}
    for did in mine:
        with open(os.path.join(db.DRAMAS_DIR, str(did), "audio.wav"), "rb") as f:
            assert f.read() == MINE.encode() + b" audio"


def test_pc_owner_backup_keeps_only_unowned_items(isolated_db):
    uid = auth_service.add_user("someone@example.com")["id"]
    pc = db.create_drama(title_en="PC drama")
    theirs = db.create_drama(title_en="Their drama", owner_user_id=uid)
    _write(f"dramas/{pc}/a.wav", b"pc")
    _write(f"dramas/{theirs}/a.wav", b"theirs")
    st = _wait(las.start_user_backup(None)["job_id"])
    assert st["status"] == "done", st.get("error")
    with open(las.admin_artifact_path("user_backup")["path"], "rb") as f:
        data = f.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert set(zf.namelist()) == {"library.db", f"dramas/{pc}/a.wav"}
        raw = zf.read("library.db")
    assert b"PC drama" in raw and b"Their drama" not in raw and b"someone@" not in raw
    assert db.get_drama(theirs) is not None


def test_user_backup_refused_during_maintenance(isolated_db):
    assert background_jobs.enter_maintenance()
    try:
        with pytest.raises(las.ConflictError):
            las.start_user_backup(None)
    finally:
        background_jobs.exit_maintenance()
    assert background_jobs.list_all_jobs() == {}
