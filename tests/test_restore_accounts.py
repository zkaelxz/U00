"""Tests for what a library restore does with accounts, sign-ins, title owners, followed channels and personal data."""

import os
import time

import pytest

import db
from services import library_backup_service as lbs
from lib.errors import InvalidInputError
import sqlite3
import tempfile

from services import auth_service
from sources import store as src_store
from tests.test_library_admin_service import _backup_bytes, _new, _restore, _rewrite, _zip_db


def test_restore_revokes_sessions_and_audits(isolated_db):
    _new("A")
    uid = auth_service.add_user("a@example.com")["id"]
    data = _backup_bytes()
    tok = auth_service.create_session(uid)["session_token"]
    assert auth_service.resolve_session(tok)
    r = _restore(data)
    assert r["sessions_revoked"] == 1
    assert auth_service.resolve_session(tok) is None
    assert any(e["action"] == "library.restore" for e in auth_service.list_audit())


def test_restore_keeps_current_auth_tables(isolated_db):
    _new("A")
    keep = auth_service.add_user("current@example.com")["id"]
    data = _backup_bytes()
    # Tamper: the zip's DB gains an admin user plus a session whose token
    # the attacker chose, and loses the current user.
    path = _zip_db(data)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO users (email, is_admin, is_active) VALUES ('evil@x', 1, 1)")
        evil = conn.execute("SELECT id FROM users WHERE email = 'evil@x'").fetchone()[0]
        now = time.time()
        conn.execute("INSERT INTO auth_sessions (id_hash, user_id, created_at, expires_at, "
                     "last_seen_at, csrf_hash) VALUES (?, ?, ?, ?, ?, 'x')",
                     (auth_service._hash("chosen-token"), evil, now, now + 9999, now))
        conn.execute("DELETE FROM users WHERE email = 'current@example.com'")
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            raw = f.read()
    finally:
        os.remove(path)
    later = auth_service.add_user("later@example.com")["id"]
    audit_before = len(auth_service.list_audit(500))
    _restore(_rewrite(data, replace={"library.db": raw}))
    assert {u["email"] for u in db.auth_list_users()} == {"current@example.com",
                                                          "later@example.com"}
    assert db.auth_get_user(keep) and db.auth_get_user(later)
    assert auth_service.resolve_session("chosen-token") is None
    assert len(auth_service.list_audit(500)) == audit_before + 1


def test_restore_never_takes_extension_device_tokens_from_the_upload(isolated_db):
    from services import device_token_service
    _new("A")
    uid = auth_service.add_user("a@example.com")["id"]
    auth_service.grant_permission(uid, "extension.send")
    me = {"user_id": uid, "is_admin": False, "permissions": ["extension.send"]}
    old = device_token_service.create_token(me, "Old laptop")
    data = _backup_bytes()
    device_token_service.revoke_own(uid, old["device_token"]["id"])
    new = device_token_service.create_token(me, "New laptop")
    # Tamper: the zip's DB gains a token whose value the attacker chose.
    path = _zip_db(data)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO extension_device_tokens (user_id, label, token_hash, created_at) "
                     "VALUES (?, 'planted', ?, ?)",
                     (uid, device_token_service._hash("baihe_dt_" + "c" * 43), time.time()))
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            raw = f.read()
    finally:
        os.remove(path)
    _restore(_rewrite(data, replace={"library.db": raw}))

    def works(token):
        try:
            return bool(device_token_service.authenticate([f"Bearer {token}"], ip="192.0.2.1"))
        except Exception:
            return False
    assert not works(old["token"])               # stays revoked
    assert works(new["token"])                   # made after the backup, still there
    assert not works("baihe_dt_" + "c" * 43)     # never planted
    assert [t["label"] for t in device_token_service.list_own(uid)] == ["New laptop", "Old laptop"]


def _fresh_pc():
    """The live library loses every account, as on a rebuilt PC whose new
    library never had any."""
    with db.get_conn() as conn:
        for table in ("auth_sessions", "extension_device_tokens", "user_permissions", "users"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'users'")


def _with_planted_secrets(data, uid):
    """The zip's library.db gains a session and a device token for `uid`
    whose values a crafted backup chose."""
    from services import device_token_service
    path = _zip_db(data)
    try:
        conn = sqlite3.connect(path)
        now = time.time()
        conn.execute("INSERT INTO auth_sessions (id_hash, user_id, created_at, expires_at, "
                     "last_seen_at, csrf_hash) VALUES (?, ?, ?, ?, ?, 'x')",
                     (auth_service._hash("chosen-token"), uid, now, now + 9999, now))
        conn.execute("INSERT INTO extension_device_tokens (user_id, label, token_hash, created_at) "
                     "VALUES (?, 'planted', ?, ?)",
                     (uid, device_token_service._hash("baihe_dt_" + "d" * 43), now))
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            raw = f.read()
    finally:
        os.remove(path)
    return _rewrite(data, replace={"library.db": raw})


def test_restore_into_a_library_without_accounts_brings_the_backups_accounts(isolated_db):
    uid = auth_service.add_user("member@example.com", display_name="Mei")["id"]
    auth_service.grant_permission(uid, "lines.edit")
    did = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _with_planted_secrets(_backup_bytes(), uid)
    _fresh_pc()
    result = _restore(data)
    assert result["accounts_restored"] == 1
    owner = db.get_drama(did)["owner_user_id"]
    assert owner == uid
    assert db.auth_get_user(owner)["email"] == "member@example.com"
    assert "lines.edit" in db.auth_get_permissions(uid)
    assert auth_service.resolve_session("chosen-token") is None
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM extension_device_tokens").fetchone()[0] == 0


def test_restore_into_a_library_with_accounts_keeps_the_live_accounts(isolated_db):
    uid = auth_service.add_user("old@example.com")["id"]
    db.create_drama(title_zh="t", owner_user_id=uid)
    data = _backup_bytes()
    _fresh_pc()
    auth_service.add_user("new-pc@example.com")
    result = _restore(data)
    assert result["accounts_restored"] == 0
    assert {u["email"] for u in db.auth_list_users()} == {"new-pc@example.com"}


def test_restored_accounts_are_not_reused_by_a_new_account(isolated_db):
    """A title owned by an account the restore did not bring must not pass
    to whoever signs up next under the same id."""
    first = auth_service.add_user("a@example.com")["id"]
    gone = auth_service.add_user("b@example.com")["id"]
    did = db.create_drama(title_zh="t", owner_user_id=gone, created_by_user_id=gone,
                          is_private=1)
    data = _backup_bytes()
    _fresh_pc()
    auth_service.add_user("only@example.com")
    _restore(data)
    newcomer = auth_service.add_user("next@example.com")["id"]
    assert newcomer not in (first, gone)
    assert db.get_drama(did)["owner_user_id"] is None
    assert db.get_drama(did)["created_by_user_id"] is None


def _owner_and_private(kind, item_id):
    table = "dramas" if kind == "drama" else "series"
    with db.get_conn() as conn:
        return tuple(conn.execute(f"SELECT owner_user_id, is_private FROM {table} WHERE id = ?",
                                  (item_id,)).fetchone())


def test_kept_accounts_get_restored_titles_by_email_never_by_id(isolated_db):
    """Ids repeat across libraries: a backup's account 1 is not the live
    account 1, so each title goes to the live account with the same email,
    or to the PC owner, and keeps its private flag."""
    gone = auth_service.add_user("gone@example.com")["id"]      # 1, not on the new PC
    mei = auth_service.add_user("mei@example.com")["id"]        # 2, on the new PC as 1
    gone_title = db.create_drama(title_zh="gone's", owner_user_id=gone, is_private=1)
    mei_title = db.create_drama(title_zh="mei's", owner_user_id=mei, is_private=1)
    mei_series = db.create_series("Mei's series", owner_user_id=mei, is_private=True)
    pc_title = db.create_drama(title_zh="pc's")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO translate_history (source_language, target_language, engine, "
                     "source_text, translated_text, user_id) VALUES ('zh', 'en', 'e', 'a', 'b', ?)",
                     (mei,))
        conn.execute("INSERT INTO speaker_merge_undos (id, drama_id, user_id, source_label, "
                     "target_label, snapshot, created_at, expires_at) "
                     "VALUES ('u', ?, ?, 'A', 'B', '{}', 0, 9e9)", (mei_title, gone))
    data = _backup_bytes()
    _fresh_pc()
    live_mei = auth_service.add_user("mei@example.com")["id"]
    stranger = auth_service.add_user("stranger@example.com")["id"]
    assert (live_mei, stranger) == (gone, mei)                  # the ids collide

    result = _restore(data)

    assert result["accounts_restored"] == 0
    assert result["titles_reassigned"] == 2
    assert _owner_and_private("drama", gone_title) == (None, 1)
    assert _owner_and_private("drama", mei_title) == (live_mei, 1)
    assert _owner_and_private("series", mei_series) == (live_mei, 1)
    assert _owner_and_private("drama", pc_title) == (None, 0)
    with db.get_conn() as conn:
        assert conn.execute("SELECT user_id FROM translate_history").fetchone()[0] == live_mei
        assert conn.execute("SELECT user_id FROM speaker_merge_undos").fetchone()[0] is None
    assert {u["email"] for u in db.auth_list_users()} == {"mei@example.com",
                                                          "stranger@example.com"}
    assert db.auth_get_permissions(stranger) == db.auth_get_permissions(live_mei)


def test_an_owner_with_no_live_account_of_that_email_goes_to_the_pc_owner(isolated_db):
    uid = auth_service.add_user("old@example.com")["id"]
    did = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _backup_bytes()
    _fresh_pc()
    other = auth_service.add_user("new-pc@example.com")["id"]
    assert other == uid
    result = _restore(data)
    assert result["titles_reassigned"] == 1
    assert _owner_and_private("drama", did) == (None, 1)


def test_the_same_email_with_another_google_sign_in_is_not_a_match(isolated_db):
    uid = auth_service.add_user("mei@example.com")["id"]
    db.auth_update_user(uid, google_sub="sub-of-the-first-mei")
    did = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _backup_bytes()
    _fresh_pc()
    auth_service.add_user("someone@example.com")
    live = auth_service.add_user("MEI@example.com")["id"]
    db.auth_update_user(live, google_sub="sub-of-another-person")
    result = _restore(data)
    assert result["titles_reassigned"] == 1
    assert _owner_and_private("drama", did) == (None, 1)


def test_a_backup_account_without_a_google_sign_in_matches_by_email(isolated_db):
    uid = auth_service.add_user("mei@example.com")["id"]
    did = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _backup_bytes()
    _fresh_pc()
    auth_service.add_user("someone@example.com")
    live = auth_service.add_user("mei@example.com")["id"]
    db.auth_update_user(live, google_sub="sub-of-mei")
    result = _restore(data)
    assert result["titles_reassigned"] == 1
    assert _owner_and_private("drama", did) == (live, 1)


def test_two_accounts_whose_ids_swapped_each_keep_their_own_titles(isolated_db):
    a = auth_service.add_user("a@example.com")["id"]
    b = auth_service.add_user("b@example.com")["id"]
    a_title = db.create_drama(title_zh="a's", owner_user_id=a, is_private=1)
    b_title = db.create_drama(title_zh="b's", owner_user_id=b, is_private=1)
    with db.get_conn() as conn:
        for job, owner in (("job-a", a), ("job-b", b)):
            conn.execute("INSERT INTO job_records (job_id, status, updated_at, owner_user_id) "
                         "VALUES (?, 'done', 0, ?)", (job, owner))
    data = _backup_bytes()
    _fresh_pc()
    live_b = auth_service.add_user("b@example.com")["id"]
    live_a = auth_service.add_user("a@example.com")["id"]
    assert (live_a, live_b) == (b, a)
    result = _restore(data)
    assert result["titles_reassigned"] == 2
    assert _owner_and_private("drama", a_title) == (live_a, 1)
    assert _owner_and_private("drama", b_title) == (live_b, 1)
    with db.get_conn() as conn:
        jobs = {r[0]: r[1] for r in conn.execute(
            "SELECT job_id, owner_user_id FROM job_records WHERE job_id IN ('job-a', 'job-b')")}
    assert jobs == {"job-a": live_a, "job-b": live_b}


def test_tracked_series_links_and_extension_marks_follow_the_email_match(isolated_db):
    from sources import extension_marker
    gone = auth_service.add_user("gone@example.com")["id"]
    mei = auth_service.add_user("mei@example.com")["id"]
    did = db.create_drama(title_zh="mei's", owner_user_id=mei, is_private=1)
    src_store.track_series("site", "s1", "Mei's series", drama_id=did, linked_by_user_id=mei)
    src_store.track_series("site", "s2", "Gone's series", linked_by_user_id=gone)
    src_store.set_tracked_save("site", "s1", True, by_user_id=mei)
    extension_marker.mark("site", user_id=mei)
    from services import sources_save_service as saves
    saves.record_saved_by("Site", "Mei's", mei)
    saves.record_saved_by("Site", "Gone's", gone)
    data = _backup_bytes()
    _fresh_pc()
    live_mei = auth_service.add_user("mei@example.com")["id"]
    stranger = auth_service.add_user("stranger@example.com")["id"]
    assert (live_mei, stranger) == (gone, mei)
    _restore(data)
    links = {r["series_id"]: r["linked_by_user_id"] for r in src_store.list_tracked_series()}
    assert links == {"s1": live_mei, "s2": None}
    assert extension_marker.get("site")["marked_by_user_id"] == live_mei
    saved_for = {r["series_id"]: r["save_cbz_by_user_id"] for r in src_store.list_tracked_series()}
    assert saved_for == {"s1": live_mei, "s2": None}
    assert saves.series_saved_by(live_mei) == {("Site", "Mei's")}
    assert saves.series_saved_by(stranger) == set()


def _with_backup_users(data, rows, drop=False, id_type="PRIMARY KEY"):
    """The zip's library.db with its users table replaced by an untyped one
    holding (id, email) rows a crafted backup chose, or dropped. An untyped
    PRIMARY KEY may hold NULL."""
    path = _zip_db(data)
    try:
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE users")
        if not drop:
            conn.execute(f"CREATE TABLE users (id {id_type}, google_sub, email, display_name, "
                         "is_admin, is_active, created_at, share_by_default)")
            conn.executemany("INSERT INTO users (id, email) VALUES (?, ?)", rows)
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            raw = f.read()
    finally:
        os.remove(path)
    return _rewrite(data, replace={"library.db": raw})


@pytest.mark.parametrize("rows, drop", [
    ([], True),                                                       # no users table
    ([(None, "mei@example.com")], False),                            # no id
    ([("1", "mei@example.com")], False),                             # an id that isn't a number
    ([(1, 42)], False),                                              # an email that isn't text
    ([(1, b"mei@example.com")], False),
])
def test_a_crafted_backups_accounts_name_no_one(isolated_db, rows, drop):
    """Whatever a crafted backup's users table holds, its owner ids find no
    live account by accident: the title goes to the PC owner, never a 500."""
    uid = auth_service.add_user("someone@example.com")["id"]
    did = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _with_backup_users(_backup_bytes(), rows, drop)
    _fresh_pc()
    auth_service.add_user("stranger@example.com")
    auth_service.add_user("mei@example.com")
    result = _restore(data)
    assert result["titles_reassigned"] == 1
    assert _owner_and_private("drama", did) == (None, 1)


def test_a_backup_naming_one_account_id_twice_is_refused(isolated_db):
    uid = auth_service.add_user("someone@example.com")["id"]
    did = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _with_backup_users(_backup_bytes(), [(1, "mei@example.com"),
                                                (1, "stranger@example.com")], id_type="")
    _fresh_pc()
    auth_service.add_user("mei@example.com")
    with pytest.raises(InvalidInputError):
        _restore(data)
    assert _owner_and_private("drama", did) == (uid, 1)


def test_a_restore_that_fails_while_matching_owners_leaves_the_live_library(
        isolated_db, monkeypatch):
    from services import library_restore_sql
    uid = auth_service.add_user("mei@example.com")["id"]
    kept = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    src_store.track_series("site", "s1", "Series", linked_by_user_id=uid)
    data = _backup_bytes()
    added = db.create_drama(title_zh="added later", owner_user_id=uid)
    auth_service.add_user("added-later@example.com")
    real = library_restore_sql.match_source_owners

    def fails_partway(path, mapping):
        real(path, mapping)
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(library_restore_sql, "match_source_owners", fails_partway)
    with pytest.raises(InvalidInputError):
        _restore(data)
    assert {d["id"] for d in db.list_dramas()} == {kept, added}
    assert _owner_and_private("drama", kept) == (uid, 1)
    assert not [e for e in auth_service.list_audit(500) if e["action"] == "library.restore"]


def test_an_account_on_both_with_the_same_id_keeps_its_titles(isolated_db):
    uid = auth_service.add_user("mei@example.com")["id"]
    did = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _backup_bytes()
    auth_service.add_user("added-later@example.com")
    result = _restore(data)
    assert result["titles_reassigned"] == 0
    assert _owner_and_private("drama", did) == (uid, 1)


def _without_google_sub(data):
    """The backup's users table as an older app wrote it, before google_sub."""
    path = _zip_db(data)
    try:
        conn = sqlite3.connect(path)
        conn.executescript(
            "PRAGMA foreign_keys = OFF;"
            "CREATE TABLE users_old (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, "
            "display_name TEXT DEFAULT '', is_admin INTEGER DEFAULT 0, "
            "is_active INTEGER DEFAULT 1, created_at TEXT);"
            "INSERT INTO users_old SELECT id, email, display_name, is_admin, is_active, "
            "created_at FROM users;"
            "DROP TABLE users; ALTER TABLE users_old RENAME TO users;")
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            return _rewrite(data, replace={"library.db": f.read()})
    finally:
        os.remove(path)


def test_a_backup_whose_accounts_cannot_be_read_says_it_has_none(isolated_db):
    uid = auth_service.add_user("mei@example.com")["id"]
    db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _without_google_sub(_backup_bytes())
    result = _restore(data)
    assert result["backup_has_accounts"] is False
    assert result["titles_reassigned"] == 1


def test_a_backup_with_readable_accounts_says_it_has_them(isolated_db):
    uid = auth_service.add_user("mei@example.com")["id"]
    db.create_drama(title_zh="t", owner_user_id=uid)
    assert _restore(_backup_bytes())["backup_has_accounts"] is True


def test_a_restore_into_a_library_without_accounts_reassigns_nothing(isolated_db):
    uid = auth_service.add_user("mei@example.com")["id"]
    did = db.create_drama(title_zh="t", owner_user_id=uid, is_private=1)
    data = _backup_bytes()
    _fresh_pc()
    result = _restore(data)
    assert (result["accounts_restored"], result["titles_reassigned"]) == (1, 0)
    assert _owner_and_private("drama", did) == (uid, 1)


def test_snapshot_strips_sessions_and_device_tokens_but_keeps_accounts(isolated_db):
    from services import device_token_service
    uid = auth_service.add_user("a@example.com")["id"]
    auth_service.grant_permission(uid, "extension.send")
    auth_service.create_session(uid)
    device_token_service.create_token({"user_id": uid, "is_admin": False,
                                       "permissions": ["extension.send"]}, "Laptop")
    dest = os.path.join(tempfile.mkdtemp(), "snap.db")
    lbs._sanitized_snapshot(dest)
    conn = sqlite3.connect(dest)
    try:
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM extension_device_tokens").fetchone()[0] == 0
        assert conn.execute("SELECT email FROM users").fetchall() == [("a@example.com",)]
        assert conn.execute("SELECT COUNT(*) FROM user_permissions").fetchone()[0] == \
            len(db.auth_get_permissions(uid))
    finally:
        conn.close()


def test_backups_never_carry_the_personal_vtuber_names(isolated_db):
    """The dd-center/vdb list is personal use only: the "Known names" block a
    run put in a scheduled bulk job's prompt arguments stays out of every
    backup, while the live job keeps it."""
    import json
    did = _new("A")
    guidelines = "House style.\n\nKnown names (keep as written): Inventa Kiraboshi, Testuko Mochimaru"
    job = db.create_bulk_job(did, "deepseek", "deepseek-chat", "scheduled", [],
                             translate_args={"style_guidelines": guidelines, "style_preset": "x"})
    database = os.path.join(tempfile.mkdtemp(), "db.db")
    lbs._sanitized_snapshot(database)
    zipped = _zip_db(_backup_bytes())
    try:
        for path in (database, zipped):
            conn = sqlite3.connect(path)
            raw = conn.execute("SELECT translate_args FROM bulk_jobs WHERE id = ?", (job,)).fetchone()[0]
            conn.close()
            assert "Inventa" not in raw and "Testuko" not in raw and "Known names" not in raw
            assert json.loads(raw) == {"style_guidelines": "House style.", "style_preset": "x"}
            with open(path, "rb") as f:
                assert b"Inventa" not in f.read()
    finally:
        os.remove(zipped)
    assert db.get_bulk_job(job)["translate_args"]["style_guidelines"] == guidelines


def test_names_of_a_deleted_title_are_not_left_in_a_backups_free_pages(isolated_db):
    did = _new("A")
    names = "Known names (keep as written): Inventa Kiraboshi " + "x" * 6000
    db.create_bulk_job(did, "deepseek", "deepseek-chat", "scheduled", [],
                       translate_args={"style_guidelines": "Style.\n\n" + names})
    # As on a SQLite built without secure delete (Python's own Windows build):
    # the deleted row's pages go to the free list with their text intact.
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("PRAGMA secure_delete = OFF")
    conn.execute("DELETE FROM dramas WHERE id = ?", (did,))
    conn.execute("DELETE FROM bulk_jobs WHERE drama_id = ?", (did,))
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    with open(db.DB_PATH, "rb") as f:
        assert b"Inventa" in f.read()   # the live file still holds them
    dest = os.path.join(tempfile.mkdtemp(), "db.db")
    lbs._sanitized_snapshot(dest)
    with open(dest, "rb") as f:
        assert b"Inventa" not in f.read()


def test_an_unreadable_live_library_keeps_its_accounts(isolated_db, monkeypatch):
    from services import library_restore_sql
    real = sqlite3.connect

    class Locked:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a):
            raise sqlite3.OperationalError("database is locked")

        def close(self):
            pass
    monkeypatch.setattr(library_restore_sql.sqlite3, "connect", lambda *a, **k: Locked())
    assert library_restore_sql.has_accounts(db.DB_PATH) is True
    monkeypatch.setattr(library_restore_sql.sqlite3, "connect", real)


def test_the_restore_audit_entry_says_how_many_accounts_came(isolated_db):
    uid = auth_service.add_user("member@example.com")["id"]
    data = _backup_bytes()
    _fresh_pc()
    _restore(data)
    [entry] = [e for e in auth_service.list_audit(500) if e["action"] == "library.restore"]
    assert "accounts restored from the backup: 1" in entry["detail_redacted"]
    assert "titles reassigned to a live account or the PC owner: 0" in entry["detail_redacted"]
    assert db.auth_get_user(uid)


def test_the_restore_audit_entry_says_how_many_titles_were_reassigned(isolated_db):
    uid = auth_service.add_user("old@example.com")["id"]
    db.create_drama(title_zh="t", owner_user_id=uid)
    data = _backup_bytes()
    _fresh_pc()
    auth_service.add_user("new-pc@example.com")
    _restore(data)
    [entry] = [e for e in auth_service.list_audit(500) if e["action"] == "library.restore"]
    assert "accounts restored from the backup: 0" in entry["detail_redacted"]
    assert "titles reassigned to a live account or the PC owner: 1" in entry["detail_redacted"]
    assert "example.com" not in entry["detail_redacted"]


def test_a_live_library_that_cannot_be_read_keeps_its_accounts_through_a_restore(
        isolated_db, monkeypatch):
    """The account check can't read the live library (locked): the restore must
    keep the live accounts, not take the backup's."""
    from services import library_restore_sql
    auth_service.add_user("in-backup@example.com")
    data = _backup_bytes()
    auth_service.add_user("added-later@example.com")
    live_uri = library_restore_sql._ro_uri(db.DB_PATH)
    real_connect = sqlite3.connect

    def locked_for_the_check(target, *a, **k):
        if target == live_uri:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(target, *a, **k)
    monkeypatch.setattr(library_restore_sql.sqlite3, "connect", locked_for_the_check)
    result = _restore(data)
    monkeypatch.setattr(library_restore_sql.sqlite3, "connect", real_connect)
    assert result["accounts_restored"] == 0
    assert {u["email"] for u in db.auth_list_users()} == {"in-backup@example.com",
                                                          "added-later@example.com"}


def _follow(owner, pc_owner=True, mode="auto"):
    from sources import live_follow_store
    return live_follow_store.add(owner, pc_owner, "https://www.youtube.com/@chan/live", "chan",
                                 mode, "{}", 60, auto_check=True)


def test_followed_channels_keep_their_owner_when_accounts_are_restored(isolated_db):
    from sources import live_follow_store
    uid = auth_service.add_user("owner@example.com")["id"]
    fid = _follow(uid)
    data = _backup_bytes()
    _fresh_pc()
    _restore(data)
    row = live_follow_store.get(fid)
    assert (row["owner_user_id"], row["pc_owner"], row["auto_check"], row["mode"]) == \
        (uid, 1, 1, "auto")


def test_followed_channels_pass_to_the_pc_owner_when_live_accounts_are_kept(isolated_db):
    from sources import live_follow_store
    uid = auth_service.add_user("owner@example.com")["id"]
    fid = _follow(uid)
    data = _backup_bytes()
    _fresh_pc()
    auth_service.add_user("someone-else@example.com")
    _restore(data)
    row = live_follow_store.get(fid)
    assert (row["owner_user_id"], row["pc_owner"], row["auto_check"], row["mode"]) == \
        (None, 0, 0, "notify")


def test_restored_chapter_marks_follow_the_email_match_and_a_strangers_are_dropped(isolated_db):
    a = auth_service.add_user("a@example.com")["id"]
    b = auth_service.add_user("b@example.com")["id"]
    gone = auth_service.add_user("gone@example.com")["id"]
    did = db.create_drama(title_zh="comic")
    db.set_chapter_marks(did, ["pc"], read=True)
    db.set_chapter_marks(did, ["a", "both"], read=True, user_id=a)
    db.set_chapter_marks(did, ["b", "both"], bookmarked=True, user_id=b)
    db.set_chapter_marks(did, ["gone"], read=True, user_id=gone)
    for who, note in ((0, "pc"), (a, "a"), (b, "b"), (gone, "gone")):
        db.add_watch_bookmark(did, who, 1.0, note, 10)
    data = _backup_bytes()
    _fresh_pc()
    live_b = auth_service.add_user("b@example.com")["id"]
    live_a = auth_service.add_user("a@example.com")["id"]
    stranger = auth_service.add_user("stranger@example.com")["id"]
    assert (live_a, live_b, stranger) == (b, a, gone)           # the ids collide

    _restore(data)

    assert set(db.get_chapter_marks(did)) == {"pc"}
    assert db.get_chapter_marks(did, live_a) == {"a": {"read": True, "bookmarked": False},
                                                 "both": {"read": True, "bookmarked": False}}
    assert db.get_chapter_marks(did, live_b) == {"b": {"read": False, "bookmarked": True},
                                                 "both": {"read": False, "bookmarked": True}}
    assert db.get_chapter_marks(did, stranger) == {}
    notes = lambda who: [x["note"] for x in db.list_watch_bookmarks(did, who)]   # noqa: E731
    assert (notes(0), notes(live_a), notes(live_b), notes(stranger)) == (["pc"], ["a"], ["b"], [])
    # The next new account must not get an id the restored rows still name.
    assert auth_service.add_user("new@example.com")["id"] > max(a, b, gone)
