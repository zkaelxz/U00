"""Tests for services/library_admin_service.py (migration E0 remainder)."""
import io
import json
import os
import time
import types
import zipfile

import pytest

import background_jobs
import db
from services import library_admin_service as las
from services import library_restore_service as lrs
from services import workspace_job_service as wjs
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)


def _new(title="T", status="aligned", translated=False):
    did = db.create_drama(title_en=title, source_language="zh")
    db.update_drama(did, status=status)
    if translated:
        from core import Line
        db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    return did


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _no_path(result, *roots):
    text = json.dumps(result)
    for root in roots:
        assert root not in text


def _fake_running(monkeypatch, drama_id):
    monkeypatch.setattr(las.drama_service, "job_running_for_drama",
                        lambda did: did == drama_id)


# ---- confirms -------------------------------------------------------------

@pytest.mark.parametrize("confirm,text", [(False, "DELETE"), (True, ""), (True, "delete"),
                                          ("true", "DELETE"), (True, "RESTORE")])
def test_bulk_delete_wrong_confirm_changes_nothing(isolated_db, confirm, text):
    a, b = _new("A"), _new("B")
    with pytest.raises(InvalidInputError):
        las.bulk_delete([a, b], confirm=confirm, confirm_text=text)
    assert db.get_drama(a) and db.get_drama(b)


def test_restore_wrong_confirm_changes_nothing(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a: called.append(a))
    good = _zip({"library.db": b"x"})
    for confirm, text in [(False, "RESTORE"), (True, "restore"), (True, "")]:
        with pytest.raises(InvalidInputError):
            las.restore_backup(good, confirm=confirm, confirm_text=text)
    assert not called


def test_storage_cleanup_wrong_confirm_changes_nothing(isolated_db):
    did = _new()
    clips = os.path.join(db.drama_dir(did), "dub_clips")
    os.makedirs(clips)
    with open(os.path.join(clips, "a.wav"), "wb") as f:
        f.write(b"0" * 100)
    with pytest.raises(InvalidInputError):
        las.storage_cleanup("balanced", confirm=True, confirm_text="clean")
    assert os.path.exists(os.path.join(clips, "a.wav"))


# ---- bulk writes ----------------------------------------------------------

def test_bulk_set_status_per_id_results(isolated_db):
    a, b = _new("A", translated=True), _new("B", translated=True)
    r = las.bulk_set_status([a, 999999, b], "translated")
    assert r["updated"] == 2
    assert r["results"] == [{"drama_id": a, "ok": True},
                            {"drama_id": 999999, "ok": False, "error": "not_found"},
                            {"drama_id": b, "ok": True}]
    assert db.get_drama(a)["status"] == "translated"
    with pytest.raises(InvalidInputError):
        las.bulk_set_status([a], "bogus")
    with pytest.raises(InvalidInputError):
        las.bulk_set_status([], "translated")
    with pytest.raises(InvalidInputError):
        las.bulk_set_status([True], "translated")


def test_bulk_set_tags_only_touches_that_tag(isolated_db):
    a = _new()
    db.update_drama(a, custom_tags="mine, On Hold")
    las.bulk_set_tags([a], "Favorite", True)
    tags = db.get_drama(a)["custom_tags"]
    assert "mine" in tags and "Favorite" in tags and "On Hold" in tags
    las.bulk_set_tags([a], "On Hold", False)
    assert "On Hold" not in db.get_drama(a)["custom_tags"]
    with pytest.raises(InvalidInputError):
        las.bulk_set_tags([a], "arbitrary", True)


def test_bulk_delete_refuses_running_job_per_drama(isolated_db, monkeypatch):
    a, b = _new("A"), _new("B")
    _fake_running(monkeypatch, a)
    r = las.bulk_delete([a, b, 424242], confirm=True, confirm_text="DELETE")
    assert r["deleted"] == 1
    by_id = {x["drama_id"]: x for x in r["results"]}
    assert by_id[a] == {"drama_id": a, "ok": False, "error": "job_running"}
    assert by_id[b]["ok"] is True
    assert by_id[424242]["error"] == "not_found"
    assert db.get_drama(a) is not None and db.get_drama(b) is None
    _no_path(r, db.LIBRARY_DIR)


def test_bulk_delete_real_running_job(isolated_db):
    a = _new()
    import threading
    gate = threading.Event()
    assert background_jobs.start_job(f"translate_{a}", gate.wait, 5)
    try:
        r = las.bulk_delete([a], confirm=True, confirm_text="DELETE")
        assert r["results"][0]["error"] == "job_running"
        assert db.get_drama(a) is not None
    finally:
        gate.set()
        _wait(f"translate_{a}")


def test_bulk_translate_uses_existing_job(isolated_db, monkeypatch):
    monkeypatch.setattr(las.settings_service, "get_monthly_cap_usd", lambda *a, **k: 50.0)
    a, b = _new("A", "aligned"), _new("B", "translated")
    captured = {}

    def fake_start(job_id, target, *args, **kwargs):
        captured.update(job_id=job_id, target=target, args=args, kwargs=kwargs)
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start)
    r = las.start_bulk_translate([a, b])
    assert r == {"job_id": "bulk_series_translate", "queued": [a],
                 "skipped": [{"drama_id": b, "reason": "not_aligned"}]}
    assert captured["target"] is wjs.run_bulk_series_translate_job
    assert captured["args"][1] == [a]
    with pytest.raises(InvalidInputError):
        las.start_bulk_translate([b])


# ---- export / backup jobs -------------------------------------------------

def _wait(job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_export_zip_job_and_artifact(isolated_db):
    from core import Line
    a = _new("Exported", "translated")
    db.save_lines(a, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    _new("Skip", "aligned")
    r = las.start_export_zip()
    assert r["drama_ids"] == [a]
    st = _wait(r["job_id"])
    assert st["status"] == "done", st.get("error")
    _no_path(st["result"], db.LIBRARY_DIR)
    art = las.latest_admin_artifact("export")
    assert set(art) == {"kind", "name", "size"} and art["size"] > 0
    _no_path(art, db.LIBRARY_DIR)
    with zipfile.ZipFile(las.admin_artifact_path("export")["path"]) as zf:
        assert any(n.endswith("english.srt") for n in zf.namelist())


def test_backup_job_then_valid_for_restore(isolated_db):
    _new("Backed up")
    r = las.start_backup()
    st = _wait(r["job_id"])
    assert st["status"] == "done", st.get("error")
    _no_path(st["result"], db.LIBRARY_DIR)
    with open(las.admin_artifact_path("backup")["path"], "rb") as f:
        data = f.read()
    las.validate_backup_zip(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert not any(n.startswith("backups/") for n in zf.namelist())


def _media(n=5):
    for i in range(n):
        d = os.path.join(db.DRAMAS_DIR, str(i))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "audio.bin"), "wb") as f:
            f.write(os.urandom(1000 * (i + 1)))


def _members(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist() if i.filename != "library.db"}


def test_backup_zip_progress_monotonic_and_same_members(isolated_db, tmp_path, monkeypatch):
    _new("P")
    _media()
    monkeypatch.setattr(las, "_BACKUP_PROGRESS_INTERVAL", 0)
    calls = []
    plain, with_cb = str(tmp_path / "a.zip"), str(tmp_path / "b.zip")
    las.write_backup_zip(plain)
    las.write_backup_zip(with_cb, progress=lambda f, m: calls.append((f, m)),
                         should_cancel=lambda: False)
    assert _members(plain) == _members(with_cb)
    with zipfile.ZipFile(plain) as a, zipfile.ZipFile(with_cb) as b:
        assert a.namelist() == b.namelist()
    fracs = [f for f, _ in calls]
    assert fracs == sorted(fracs) and 0 <= fracs[0] and fracs[-1] <= 1.0
    assert all(m for _, m in calls)
    assert calls[0][1] == "Counting files..."
    assert any("of 5 files" in m for _, m in calls)


def test_backup_job_reports_progress_and_ends_at_one(isolated_db):
    _media()
    st = _wait(las.start_backup()["job_id"])
    assert st["status"] == "done", st.get("error")
    assert st["progress"] == 1.0 and st["message"] == "Backup ready."


def test_backup_job_cancel_leaves_no_partial_or_zip(isolated_db, monkeypatch):
    _media()
    monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: True)
    with pytest.raises(background_jobs.JobCancelled):
        las._backup_job(las.BACKUP_JOB_ID)
    folder = las._artifact_dir("backup", create=True)
    assert os.listdir(folder) == []


def test_backup_job_cancel_marks_job_cancelled(isolated_db, monkeypatch):
    _media()
    monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: True)
    job_id = las.start_backup()["job_id"]
    end = time.time() + 10
    st = None
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.05)
    assert st["status"] == "cancelled"
    with pytest.raises(NotFoundError):
        las.latest_admin_artifact("backup")
    assert os.listdir(las._artifact_dir("backup", create=True)) == []


# ---- restore --------------------------------------------------------------

def test_restore_refused_while_job_running(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a: called.append(a))
    import threading
    gate = threading.Event()
    assert background_jobs.start_job("some_job", gate.wait, 5)
    try:
        with pytest.raises(ConflictError):
            las.restore_backup(_zip({"library.db": b"x"}), confirm=True, confirm_text="RESTORE")
    finally:
        gate.set()
        _wait("some_job")
    assert not called


def test_restore_refused_for_other_process_job_record(isolated_db, monkeypatch):
    monkeypatch.setattr(db, "list_job_records", lambda: [
        {"job_id": "transcribe_1", "status": "running", "updated_at": time.time()}])
    with pytest.raises(ConflictError):
        las.restore_backup(_zip({"library.db": b"x"}), confirm=True, confirm_text="RESTORE")


@pytest.mark.parametrize("payload", [
    b"not a zip",
    b"",
    "no_db",
    "traversal",
    "absolute",
    "backslash",
    "symlink",
])
def test_invalid_zip_rejected_before_change(isolated_db, monkeypatch, payload):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a: called.append(a))
    did = _new("Keep me")
    if payload == "no_db":
        payload = _zip({"other.txt": b"x"})
    elif payload == "traversal":
        payload = _zip({"library.db": b"x", "../evil.txt": b"x"})
    elif payload == "absolute":
        payload = _zip({"library.db": b"x", "/etc/evil": b"x"})
    elif payload == "backslash":
        payload = _zip({"library.db": b"x", "..\\evil.txt": b"x"})
    elif payload == "symlink":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("library.db", b"x")
            info = zipfile.ZipInfo("link")
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
        payload = buf.getvalue()
    with pytest.raises(InvalidInputError) as ei:
        las.restore_backup(payload, confirm=True, confirm_text="RESTORE")
    assert not called
    assert db.get_drama(did) is not None
    assert db.LIBRARY_DIR not in str(ei.value)


def test_oversized_zip_rejected_before_change(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a: called.append(a))
    data = _zip({"library.db": b"x" * 100, "big.bin": b"y" * 1000})
    monkeypatch.setattr(lrs, "MAX_RESTORE_MEMBER_BYTES", 500)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(lrs, "MAX_RESTORE_MEMBER_BYTES", 10 ** 9)
    monkeypatch.setattr(lrs, "MAX_RESTORE_TOTAL_BYTES", 900)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(lrs, "MAX_RESTORE_TOTAL_BYTES", 10 ** 9)
    monkeypatch.setattr(lrs, "MAX_RESTORE_MEMBERS", 1)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert not called


def test_valid_restore_calls_existing_swap(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup",
                        lambda b, d, before_swap=None: called.append(d))
    r = las.restore_backup(_zip({"library.db": b"x", "dramas/1/a.txt": b"x"}),
                           confirm=True, confirm_text="RESTORE")
    assert r["restored"] is True
    assert called == [db.LIBRARY_DIR]


def test_restore_that_flips_developer_mode_syncs_the_echo_engine(isolated_db, monkeypatch):
    from engine_backends import echo, engine_registry
    from services import assistant_settings_service as assistant
    echo.set_enabled(False)
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a, **k: db.set_app_setting(
        assistant._SETTINGS_PREFIX + "developer_mode", True))
    try:
        las.restore_backup(_zip({"library.db": b"x"}), confirm=True, confirm_text="RESTORE")
        assert echo.ENGINE_ID in engine_registry.ENGINES
        monkeypatch.setattr(lrs, "restore_library_backup", lambda *a, **k: db.set_app_setting(
            assistant._SETTINGS_PREFIX + "developer_mode", False))
        las.restore_backup(_zip({"library.db": b"x"}), confirm=True, confirm_text="RESTORE")
        assert echo.ENGINE_ID not in engine_registry.ENGINES
    finally:
        echo.set_enabled(False)


# ---- storage --------------------------------------------------------------

def test_storage_scan_then_cleanup(isolated_db, monkeypatch):
    a, b = _new("A"), _new("B")
    for did in (a, b):
        clips = os.path.join(db.drama_dir(did), "dub_clips")
        os.makedirs(clips)
        with open(os.path.join(clips, "c.wav"), "wb") as f:
            f.write(b"0" * 200)
    scan = las.storage_scan("balanced")
    assert scan["would_free_bytes"] == 400
    assert os.path.isdir(os.path.join(db.drama_dir(a), "dub_clips"))  # dry run
    _no_path(scan, db.LIBRARY_DIR)
    _fake_running(monkeypatch, b)
    r = las.storage_cleanup("balanced", confirm=True, confirm_text="CLEAN")
    assert r["freed_bytes"] == 200
    by_id = {x["drama_id"]: x for x in r["results"]}
    assert by_id[b]["error"] == "job_running"
    assert not os.path.isdir(os.path.join(db.drama_dir(a), "dub_clips"))
    assert os.path.isdir(os.path.join(db.drama_dir(b), "dub_clips"))
    _no_path(r, db.LIBRARY_DIR)
    with pytest.raises(InvalidInputError):
        las.storage_scan("nope")


# ---- real restore round-trips (fix-library-admin-restore) -----------------

import collections  # noqa: E402
import sqlite3  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402

import page_server  # noqa: E402
import shutil  # noqa: E402
from services import auth_service  # noqa: E402
from sources import store as src_store  # noqa: E402


def _backup_bytes():
    st = _wait(las.start_backup()["job_id"])
    assert st["status"] == "done", st.get("error")
    with open(las.admin_artifact_path("backup")["path"], "rb") as f:
        return f.read()


def _restore(data):
    return las.restore_backup(data, confirm=True, confirm_text="RESTORE")


def _write(rel, data=b"x"):
    path = os.path.join(db.LIBRARY_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _zip_db(data):
    """The backup zip's library.db, written to a temp file for inspection."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        raw = zf.read("library.db")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.write(fd, raw)
    os.close(fd)
    return path


def _rewrite(data, replace=None, extra=None):
    replace = replace or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, replace.get(info.filename, src.read(info.filename)))
        for n, b in (extra or {}).items():
            dst.writestr(n, b)
    return buf.getvalue()


def _leftovers():
    parent = os.path.dirname(db.LIBRARY_DIR)
    base = os.path.basename(db.LIBRARY_DIR)
    return [n for n in os.listdir(parent)
            if n.startswith(".restore_staging_") or n.startswith(base + ".pre_restore_")]


def test_restore_round_trip_keeps_backups_and_kept_entries(isolated_db):
    a = _new("Backed up")
    _write(os.path.join(src_store.BROWSER_PROFILES_DIRNAME, "site", "Cookies"), b"signin")
    _write(page_server.TOKEN_FILENAME, b"current-token")
    data = _backup_bytes()
    backup_name = las.latest_admin_artifact("backup")["name"]
    b = _new("Added later")
    r = _restore(data)
    assert r["restored"] is True
    assert db.get_drama(a) is not None and db.get_drama(b) is None
    assert las.latest_admin_artifact("backup")["name"] == backup_name   # backups/ survived
    with open(os.path.join(db.LIBRARY_DIR, src_store.BROWSER_PROFILES_DIRNAME, "site",
                           "Cookies"), "rb") as f:
        assert f.read() == b"signin"
    with open(os.path.join(db.LIBRARY_DIR, page_server.TOKEN_FILENAME), "rb") as f:
        assert f.read() == b"current-token"
    assert not _leftovers()


def test_backup_excludes_source_cache(isolated_db):
    # The sources raw-content cache is rebuildable (a missing file is a
    # cache miss), so full backups leave it out.
    from sources import store as src_store
    _new("A")
    cached = os.path.join(src_store.cache_dir(), "ab", "ab" + "0" * 62)
    _write(os.path.relpath(cached, db.LIBRARY_DIR), b"raw page bytes")
    _write("dramas/keep_me.txt", b"media")
    data = _backup_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert "dramas/keep_me.txt" in names
    assert not [n for n in names if n.startswith("source_cache/")]


def test_backup_excludes_token_and_sessions(isolated_db):
    _new("A")
    _write(page_server.TOKEN_FILENAME, b"tok")
    uid = auth_service.add_user("a@example.com")["id"]
    auth_service.create_session(uid)
    data = _backup_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert page_server.TOKEN_FILENAME not in zf.namelist()
    path = _zip_db(data)
    try:
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        conn.close()
    finally:
        os.remove(path)


def test_planted_kept_members_are_ignored(isolated_db):
    _new("A")
    _write(page_server.TOKEN_FILENAME, b"current-token")
    data = _rewrite(_backup_bytes(), extra={
        "backups/exports/planted.zip": _zip({"x": b"x"}),
        "backups/evil.zip": b"x",
        page_server.TOKEN_FILENAME: b"attacker-token",
        "source_profiles/evil.json": b"{}",
        src_store.BROWSER_PROFILES_DIRNAME + "/evil/Cookies": b"x",
    })
    _restore(data)
    with pytest.raises(NotFoundError):
        las.admin_artifact_path("export")
    assert not os.path.exists(os.path.join(db.LIBRARY_DIR, "backups", "evil.zip"))
    assert not os.path.exists(os.path.join(db.LIBRARY_DIR, "source_profiles", "evil.json"))
    assert not os.path.exists(os.path.join(db.LIBRARY_DIR, src_store.BROWSER_PROFILES_DIRNAME,
                                           "evil"))
    with open(os.path.join(db.LIBRARY_DIR, page_server.TOKEN_FILENAME), "rb") as f:
        assert f.read() == b"current-token"


@pytest.mark.parametrize("bad_db", ["garbage", "no_dramas"])
def test_bad_library_db_refused_nothing_changes(isolated_db, bad_db, tmp_path):
    a = _new("Keep me")
    data = _backup_bytes()
    if bad_db == "garbage":
        raw = b"this is not sqlite" * 100
    else:
        p = tmp_path / "x.db"
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE other (x)")
        conn.commit()
        conn.close()
        raw = p.read_bytes()
    data = _rewrite(data, replace={"library.db": raw})
    b = _new("Also keep")
    with pytest.raises(InvalidInputError) as ei:
        _restore(data)
    assert db.LIBRARY_DIR not in str(ei.value)
    assert db.get_drama(a) and db.get_drama(b)
    assert not _leftovers()
    assert not background_jobs.exclusive_active()


def test_backup_restore_restore_no_phantom_job(isolated_db):
    _new("A")
    data = _backup_bytes()
    # Root cause (F6): the snapshot is taken while the backup's own job row
    # is "running", so a restored DB carries a fresh phantom running row.
    path = _zip_db(data)
    try:
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT status FROM job_records WHERE job_id = ?",
                           (las.BACKUP_JOB_ID,)).fetchone()
        conn.close()
    finally:
        os.remove(path)
    assert row and row[0] == "running"
    _restore(data)
    _restore(data)   # was ConflictError (409) before the fix
    assert db.get_job_record(las.BACKUP_JOB_ID)["status"] == "cancelled"


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
    did = db.create_drama(title_zh="t", owner_user_id=gone, is_private=1)
    data = _backup_bytes()
    _fresh_pc()
    auth_service.add_user("only@example.com")
    _restore(data)
    newcomer = auth_service.add_user("next@example.com")["id"]
    assert newcomer not in (first, gone)
    assert db.get_drama(did)["owner_user_id"] is None


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
    las._sanitized_snapshot(dest)
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
    las._sanitized_snapshot(database)
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
    las._sanitized_snapshot(dest)
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


def test_restore_keeps_current_sources_settings(isolated_db):
    _new("A")
    src_store.set_setting("http_proxy_url", "http://evil.example:8080")
    src_store.set_setting("page_server_enabled", True)
    data = _backup_bytes()
    src_store.set_setting("http_proxy_url", "")
    src_store.set_setting("page_server_enabled", False)
    _restore(data)
    assert src_store.get_setting("http_proxy_url") == ""
    assert src_store.get_setting("page_server_enabled") is False


def _zip_sources_rows(data, sql):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        raw = zf.read("sources.db")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.write(fd, raw)
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()
    finally:
        os.remove(path)


@pytest.mark.parametrize("include_media", [True, False])
def test_backup_holds_a_consistent_sources_db_snapshot(isolated_db, include_media, tmp_path):
    _new("A")
    src_store.track_series("site", "s1", "Before")
    # An open read transaction stops a checkpoint, so the next write stays in
    # sources.db-wal, where a plain copy of sources.db would miss it.
    reader = sqlite3.connect(src_store.db_path())
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM tracked_series").fetchone()
        src_store.track_series("site", "s2", "In the log")
        dest = str(tmp_path / "b.zip")
        las.write_backup_zip(dest, include_media=include_media)
    finally:
        reader.close()
    with open(dest, "rb") as f:
        data = f.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert "sources.db" in names
    assert not [n for n in names if n.startswith("sources.db-")]
    assert sorted(r[0] for r in _zip_sources_rows(
        data, "SELECT series_id FROM tracked_series")) == ["s1", "s2"]


def test_backup_never_holds_the_sources_settings(isolated_db):
    # A proxy URL can carry a password; a restore keeps the live settings
    # anyway, so the backup has no use for them.
    _new("A")
    src_store.set_setting("http_proxy_url", "http://user:hunter2@proxy.example:8080")
    src_store.track_series("site", "s1", "Kept")
    data = _backup_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert not [n for n in zf.namelist() if b"hunter2" in zf.read(n)]
    assert _zip_sources_rows(data, "SELECT key FROM settings") == []
    _restore(data)
    assert [r["series_id"] for r in src_store.list_tracked_series()] == ["s1"]
    assert src_store.get_setting("http_proxy_url") == "http://user:hunter2@proxy.example:8080"


def test_busy_sources_db_fails_the_backup_with_the_fixed_message(isolated_db, monkeypatch):
    _new("A")
    src_store.track_series("site", "s1", "Kept")

    def busy(dest, source=None):
        raise src_store.SourcesDatabaseBusy()
    monkeypatch.setattr(src_store, "snapshot_to", busy)
    st = _wait(las.start_backup()["job_id"])
    assert st["status"] == "error"
    assert st["error"].endswith("The backup could not be written.")
    assert "Sources" not in st["error"] and "busy" not in st["error"]


def test_restore_without_sources_db_keeps_the_live_one(isolated_db):
    _new("A")
    src_store.track_series("site", "s1", "Backed up")
    with zipfile.ZipFile(io.BytesIO(_backup_bytes())) as src:
        data = _zip({n: src.read(n) for n in src.namelist() if n != "sources.db"})
    src_store.track_series("site", "s2", "Tracked later")
    src_store.set_setting("http_proxy_url", "http://127.0.0.1:8080")
    _restore(data)
    assert sorted(r["series_id"] for r in src_store.list_tracked_series()) == ["s1", "s2"]
    assert src_store.get_setting("http_proxy_url") == "http://127.0.0.1:8080"


def test_restored_sources_db_has_migrated_columns_and_wal(isolated_db):
    _new("A")
    src_store.track_series("site", "s1", "Saved as CBZ")
    assert src_store.set_tracked_save("site", "s1", True)
    data = _backup_bytes()
    _restore(data)
    [row] = src_store.list_tracked_series()
    assert row["save_cbz"] == 1
    assert src_store.set_tracked_save("site", "s1", False)
    with src_store.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_unreadable_carried_sources_db_leaves_the_live_library(isolated_db, monkeypatch):
    a = _new("A")
    src_store.track_series("site", "s1", "Live")
    with zipfile.ZipFile(io.BytesIO(_backup_bytes())) as src:
        data = _zip({n: src.read(n) for n in src.namelist() if n != "sources.db"})
    b = _new("B")

    def torn_snapshot(dest, source=None):
        with open(dest, "wb") as f:
            f.write(b"SQLite format 3\x00" + b"\xff" * 4000)
        return dest
    monkeypatch.setattr(src_store, "snapshot_to", torn_snapshot)
    with pytest.raises(InvalidInputError) as ei:
        _restore(data)
    assert db.LIBRARY_DIR not in str(ei.value)
    assert "sources.db" in str(ei.value) and "try again" in str(ei.value)
    assert db.get_drama(a) and db.get_drama(b)
    assert [r["series_id"] for r in src_store.list_tracked_series()] == ["s1"]
    assert not _leftovers()


def test_restore_keeps_the_live_auto_backup_settings_and_state(isolated_db):
    from services import auto_backup_service as abs_
    _new("A")
    db.set_app_setting(abs_.SETTINGS_KEY, {"enabled": True, "folder": "elsewhere"})
    db.set_app_setting(abs_.STATE_KEY, {"last_success_at": "from the backup"})
    data = _backup_bytes()
    db.set_app_setting(abs_.SETTINGS_KEY, {"enabled": False, "folder": ""})
    db.set_app_setting(abs_.STATE_KEY, {"last_success_at": "live"})
    _restore(data)
    assert db.get_app_setting(abs_.SETTINGS_KEY) == {"enabled": False, "folder": ""}
    assert db.get_app_setting(abs_.STATE_KEY) == {"last_success_at": "live"}


def test_restore_rechecks_jobs_before_swap(isolated_db, monkeypatch):
    a = _new("A")
    data = _backup_bytes()
    b = _new("B")
    answers = iter([False, True])
    monkeypatch.setattr(las, "any_job_running", lambda: next(answers))
    with pytest.raises(ConflictError):
        _restore(data)
    assert db.get_drama(a) and db.get_drama(b)
    assert not _leftovers()
    assert not background_jobs.exclusive_active()


def test_restore_waits_for_finished_job_threads_before_swap(isolated_db, monkeypatch):
    """A finished job's thread still writes timing/notification/GPU-lock
    rows; the library must not be swapped under it (as reset_library)."""
    _new("A")
    data = _backup_bytes()
    b = _new("B")
    calls = []
    monkeypatch.setattr(background_jobs, "wait_for_job_threads",
                        lambda timeout: calls.append(timeout) or False)
    with pytest.raises(ConflictError):
        _restore(data)
    assert calls
    assert db.get_drama(b)
    assert not _leftovers()
    assert not background_jobs.exclusive_active()


def test_no_job_or_delete_during_restore(isolated_db):
    a = _new("A")
    assert background_jobs.acquire_exclusive("test")
    try:
        assert background_jobs.start_job("x", lambda: None) is False
        with pytest.raises(ConflictError):
            las.start_backup()
        with pytest.raises(ConflictError):
            las.bulk_delete([a], confirm=True, confirm_text="DELETE")
        with pytest.raises(ConflictError):
            las.storage_cleanup("balanced", confirm=True, confirm_text="CLEAN")
        assert not background_jobs.acquire_exclusive("again")
    finally:
        background_jobs.release_exclusive()
    assert db.get_drama(a)
    assert background_jobs.start_job("x", lambda: None)
    _wait("x")


@pytest.mark.parametrize("job_id", [las.BACKUP_JOB_ID, las.EXPORT_JOB_ID])
def test_delete_and_cleanup_refused_while_archiving(isolated_db, job_id):
    a = _new("A")
    gate = threading.Event()
    assert background_jobs.start_job(job_id, gate.wait, 5)
    try:
        with pytest.raises(ConflictError):
            las.bulk_delete([a], confirm=True, confirm_text="DELETE")
        with pytest.raises(ConflictError):
            las.storage_cleanup("balanced", confirm=True, confirm_text="CLEAN")
    finally:
        gate.set()
        _wait(job_id)
    assert db.get_drama(a)


def test_delete_refused_for_other_process_backup(isolated_db):
    a = _new("A")
    db.save_job_record(las.BACKUP_JOB_ID, "running")
    with pytest.raises(ConflictError):
        las.bulk_delete([a], confirm=True, confirm_text="DELETE")
    assert db.get_drama(a)


def test_restore_caps(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a, **k: called.append(a))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("library.db", b"x" * 100)
        zf.writestr("big.bin", b"y" * 5000)   # compresses far below 5000 bytes
    data = buf.getvalue()
    monkeypatch.setattr(las, "RESTORE_MAX_MEMBERS", 1)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(las, "RESTORE_MAX_MEMBERS", 100)
    monkeypatch.setattr(las, "_RESTORE_MIN_TOTAL_BYTES", 1)
    monkeypatch.setattr(las, "_RESTORE_EXPANSION_FACTOR", 1)   # cap = upload size
    monkeypatch.setattr(las, "_library_size", lambda: 0)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(las, "_library_size", lambda: 10 ** 6)  # 2x library allows it
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(las.shutil, "disk_usage", lambda p: usage(1, 1, 10))
    with pytest.raises(InvalidInputError) as ei:
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert "disk" in str(ei.value)
    assert not called
    monkeypatch.setattr(las.shutil, "disk_usage", lambda p: usage(1, 1, 10 ** 12))
    las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert len(called) == 1
    # The library's parent folder is checked; free space that can't be read
    # doesn't block the restore.
    asked = []

    def unreadable(path):
        asked.append(path)
        raise OSError("not reported")
    monkeypatch.setattr(las.shutil, "disk_usage", unreadable)
    las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert len(called) == 2
    assert asked == [os.path.dirname(os.path.abspath(db.LIBRARY_DIR))]


def test_has_disk_room_needs_the_margin(monkeypatch):
    usage = collections.namedtuple("usage", "total used free")
    need = 1000
    monkeypatch.setattr(las.shutil, "disk_usage",
                        lambda p: usage(1, 1, need + las.RESTORE_DISK_MARGIN_BYTES))
    assert las.has_disk_room("x", need)
    assert not las.has_disk_room("x", need + 1)

    def unreadable(path):
        raise OSError("not reported")
    monkeypatch.setattr(las.shutil, "disk_usage", unreadable)
    assert las.has_disk_room("x", 10 ** 18)


def test_encrypted_member_is_invalid_input(isolated_db):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("library.db", b"x")
        zf.writestr("secret.bin", b"y")
        zf.getinfo("secret.bin").flag_bits |= 0x1   # central directory: encrypted
    with pytest.raises(InvalidInputError):
        las.validate_backup_zip(buf.getvalue())


def test_export_explicit_ids_one_result_each(isolated_db):
    a = _new("A", status="translated", translated=True)
    b = _new("B", status="aligned")
    r = las.start_export_zip([a, b, 999])
    _wait(r["job_id"])
    assert r["drama_ids"] == [a]
    assert r["results"] == [{"drama_id": a, "ok": True},
                            {"drama_id": b, "ok": False, "error": "not_translated"},
                            {"drama_id": 999, "ok": False, "error": "not_found"}]


def test_artifact_write_failure_has_no_path(isolated_db, monkeypatch):
    def boom(kind, create):
        raise OSError(f"{db.LIBRARY_DIR}/backups: denied")
    monkeypatch.setattr(las, "_artifact_dir", boom)
    t = _new("A", status="translated", translated=True)
    for start in (las.start_backup, las.start_database_backup,
                  lambda: las.start_export_zip([t])):
        st = _wait(start()["job_id"])
        assert st["status"] == "error"
        assert db.LIBRARY_DIR not in (st.get("error") or "")


def test_database_backup(isolated_db):
    _new("A")
    uid = auth_service.add_user("a@example.com")["id"]
    auth_service.create_session(uid)
    st = _wait(las.start_database_backup()["job_id"])
    assert st["status"] == "done", st.get("error")
    _no_path(st["result"], db.LIBRARY_DIR)
    info = las.admin_artifact_path("database")
    assert info["name"].endswith(".db")
    conn = sqlite3.connect(info["path"])
    assert conn.execute("SELECT COUNT(*) FROM dramas").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
    conn.close()


def test_bulk_translate_engines_and_skip_running(isolated_db, monkeypatch):
    monkeypatch.setattr(las.settings_service, "get_monthly_cap_usd", lambda *a, **k: 50.0)
    a = _new("A")
    b = _new("B")
    c = _new("C", status="translated")
    db.update_drama(b, translation_engine="deepseek")
    assert las.bulk_translate_engines([a, b, c]) == {"engines": ["claude", "deepseek"],
                                                     "by_drama": {a: "claude", b: "deepseek"}}
    _fake_running(monkeypatch, b)
    assert las.bulk_translate_engines([a, b, c])["engines"] == ["claude"]
    monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
    r = las.start_bulk_translate([a, b])
    assert r["queued"] == [a]
    assert {"drama_id": b, "reason": "job_running"} in r["skipped"]


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
                                    "extension_token.txt.", "backups/ /x", "a//b"])
def test_kept_name_variants_rejected_by_validation(isolated_db, member):
    with pytest.raises(InvalidInputError):
        las.validate_backup_zip(_zip({"library.db": b"x", member: b"x"}))


def test_kept_name_variants_removed_from_staging(isolated_db, tmp_path):
    """Direct wjs call (the tab's path, no service validation): anything
    the filesystem resolves to a kept name is removed from staging."""
    _new("A")
    _write(page_server.TOKEN_FILENAME, b"current-token")
    data = _rewrite(_backup_bytes(), extra={
        "./backups/exports/planted.zip": _zip({"x": b"x"}),
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
    monkeypatch.setattr(las, "any_job_running", running)
    with pytest.raises(ConflictError):
        _restore(data)
    assert db.get_drama(a) and db.get_drama(b)
    assert db.auth_get_user_by_email("new@example.com")
    assert not _leftovers()


def test_maintenance_blocks_restore(isolated_db):
    assert background_jobs.enter_maintenance()
    try:
        with pytest.raises(ConflictError):
            _restore(_zip({"library.db": b"x"}))
    finally:
        background_jobs.exit_maintenance()
    assert background_jobs.acquire_exclusive("t")
    background_jobs.release_exclusive()


def test_restore_audit_actor(isolated_db):
    _new("A")
    uid = auth_service.add_user("admin@example.com")["id"]
    las.restore_backup(_backup_bytes(), confirm=True, confirm_text="RESTORE", actor_id=uid)
    entry = [e for e in auth_service.list_audit() if e["action"] == "library.restore"][0]
    assert entry["user_id"] == uid


def test_bulk_translate_expected_engines(isolated_db, monkeypatch):
    monkeypatch.setattr(las.settings_service, "get_monthly_cap_usd", lambda *a, **k: 50.0)
    a, b = _new("A"), _new("B")
    expected = las.bulk_translate_engines([a, b])["by_drama"]
    db.update_drama(b, translation_engine="deepseek")
    captured = {}
    monkeypatch.setattr(background_jobs, "start_job",
                        lambda *args, **kw: captured.update(kw) or True)
    r = las.start_bulk_translate([a, b], expected_engines={str(k): v
                                                            for k, v in expected.items()})
    assert r["queued"] == [a]
    assert {"drama_id": b, "reason": "engine_changed"} in r["skipped"]
    assert captured["expected_engines"] == {a: "claude"}
    with pytest.raises(InvalidInputError):
        las.start_bulk_translate([a], expected_engines=["claude"])


def test_bulk_job_skips_engine_changed_later(isolated_db, monkeypatch):
    from core import Line
    a = _new("A")
    db.save_lines(a, [Line(idx=0, start=0, end=1, zh="句")])
    db.update_drama(a, translation_engine="deepseek")
    got = {}
    monkeypatch.setattr(background_jobs, "set_result", lambda jid, res: got.update(res))
    wjs.run_bulk_series_translate_job("bulk_t", [a], {"deepseek": "k"},
                                      expected_engines={a: "claude"})
    assert got["skipped_engine_changed"] == [a]
    assert got["translated"] == []


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


def test_restore_of_a_backup_older_than_the_repeat_guard_resets_the_old_silence_default(
        isolated_db, tmp_path):
    import sqlite3
    old, kept = _new("Old default"), _new("Own value")
    data = _backup_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    path = str(tmp_path / "library.db")
    with open(path, "wb") as f:
        f.write(members["library.db"])
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE dramas DROP COLUMN whisper_repeat_guard")
    conn.execute("UPDATE dramas SET hallucination_silence_sec = 2.0 WHERE id = ?", (old,))
    conn.execute("UPDATE dramas SET hallucination_silence_sec = 5.0 WHERE id = ?", (kept,))
    conn.commit()
    conn.close()
    with open(path, "rb") as f:
        members["library.db"] = f.read()
    assert _restore(_zip(members))["restored"] is True
    assert db.get_drama(old)["hallucination_silence_sec"] == 0
    assert db.get_drama(kept)["hallucination_silence_sec"] == 5.0


def test_bulk_status_translated_refuses_a_title_without_translated_lines(isolated_db):
    from core import Line
    empty = _new("Empty", "aligned")
    blank = _new("Blank", "aligned")
    db.save_lines(blank, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
    done = _new("Done", "aligned")
    db.save_lines(done, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    r = las.bulk_set_status([empty, blank, done], "translated")
    assert [(x["drama_id"], x["ok"], x.get("error")) for x in r["results"]] == [
        (empty, False, "no_translated_lines"), (blank, False, "no_translated_lines"),
        (done, True, None)]
    assert r["updated"] == 1 and r["results"][0]["message"]
    assert db.get_drama(empty)["status"] == "aligned" and db.get_drama(done)["status"] == "translated"
    assert las.bulk_set_status([empty], "aligned")["updated"] == 1


def test_export_skips_translated_titles_with_no_translated_lines(isolated_db):
    from core import Line
    good = _new("Good", "translated")
    db.save_lines(good, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    hollow = _new("Hollow", "translated")
    r = las.start_export_zip()
    assert r["drama_ids"] == [good]
    assert r["results"] == [{"drama_id": hollow, "ok": False, "error": "no_translated_lines",
                             "message": r["results"][0]["message"]}]
    _wait(r["job_id"])
    explicit = las.start_export_zip([good, hollow])
    _wait(explicit["job_id"])
    assert [(x["drama_id"], x["ok"]) for x in explicit["results"]] == [(good, True), (hollow, False)]
    with pytest.raises(InvalidInputError):
        las.start_export_zip([hollow])


def test_bulk_delete_continues_when_the_language_pack_cleanup_fails(isolated_db, monkeypatch):
    import sqlite3

    from services import language_pack_service

    a, b = _new("A"), _new("B")

    class Locked:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        def close(self):
            pass
    # Only forget_title sees the failing connection; the rest of the delete is real.
    real_db = language_pack_service.db
    monkeypatch.setattr(language_pack_service, "db", types.SimpleNamespace(
        get_conn=lambda: Locked(), get_app_setting=real_db.get_app_setting))
    language_pack_service.forget_title(a)  # must not raise
    r = las.bulk_delete([a, b], confirm=True, confirm_text="DELETE")
    assert r["deleted"] == 2
    assert db.get_drama(a) is None and db.get_drama(b) is None


def _record_swap(monkeypatch, events, fail_verify=False):
    """Records fsyncs (by file identity) and renames in order, and the live
    library.db check after the swap, noting whether the previous library
    still sat beside it at that moment."""
    real_fsync, real_rename, real_check = os.fsync, os.rename, lrs._require_quick_check
    live = os.path.join(db.LIBRARY_DIR, "library.db")

    def fsync(fd):
        st = os.fstat(fd)
        events.append(("fsync", (st.st_dev, st.st_ino)))
        real_fsync(fd)

    def rename(src, dst):
        events.append(("rename", os.path.basename(src), os.path.basename(dst)))
        real_rename(src, dst)

    def check(path, message):
        if os.path.abspath(path) == os.path.abspath(live):
            events.append(("verify", any(".pre_restore_" in n for n in _leftovers())))
            if fail_verify:
                raise ValueError(message)
        real_check(path, message)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(lrs, "_require_quick_check", check)


def test_restore_flushes_the_staged_databases_before_the_swap(isolated_db, monkeypatch):
    _new("A")
    src_store.track_series("site", "s1", "Tracked")
    data = _backup_bytes()
    events = []
    _record_swap(monkeypatch, events)
    _restore(data)
    first_rename = next(i for i, e in enumerate(events) if e[0] == "rename")
    flushed = {e[1] for e in events[:first_rename] if e[0] == "fsync"}
    # A rename keeps the file's identity, so the live files are the ones flushed.
    for name in ("library.db", "sources.db"):
        st = os.stat(os.path.join(db.LIBRARY_DIR, name))
        assert (st.st_dev, st.st_ino) in flushed, name
    # The previous library is only removed once the swapped-in one checks out.
    assert ("verify", True) in events[first_rename:]
    assert not _leftovers()


def test_a_restored_library_that_fails_its_check_is_rolled_back(isolated_db, monkeypatch):
    a = _new("Live")
    data = _backup_bytes()
    b = _new("Added later")
    _write("notes.txt", b"live file")
    events = []
    _record_swap(monkeypatch, events, fail_verify=True)
    with pytest.raises(InvalidInputError):
        _restore(data)
    assert ("verify", True) in events
    assert db.get_drama(a) is not None and db.get_drama(b) is not None
    with open(os.path.join(db.LIBRARY_DIR, "notes.txt"), "rb") as f:
        assert f.read() == b"live file"
    assert not _leftovers()


def test_a_restore_whose_rollback_fails_says_where_the_library_went(isolated_db, monkeypatch):
    _new("Live")
    data = _backup_bytes()
    events = []
    _record_swap(monkeypatch, events, fail_verify=True)
    real_rename = os.rename

    def rename(src, dst):
        if ".pre_restore_" in os.path.basename(src):
            raise OSError("in use")
        real_rename(src, dst)
    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(ServiceError) as err:
        _restore(data)
    assert "left in place" not in err.value.message
    assert "could not be put back" in err.value.message
    assert db.LIBRARY_DIR not in err.value.message
    [aside] = [n for n in _leftovers() if ".pre_restore_" in n]
    assert os.path.isfile(os.path.join(os.path.dirname(db.LIBRARY_DIR), aside, "library.db"))


def _db_only_backup():
    path = os.path.join(tempfile.mkdtemp(), "db_only.zip")
    las.write_backup_zip(path, include_media=False)
    with open(path, "rb") as f:
        return f.read()


def test_a_database_only_backup_keeps_the_media(isolated_db, monkeypatch):
    did = _new("With audio")
    audio = _write(os.path.join("dramas", str(did), "audio.wav"), b"RIFF")
    clip = _write(os.path.join("voice_bank", "clip.wav"), b"clip")
    data = _db_only_backup()
    assert not any(n.startswith("dramas/") for n in zipfile.ZipFile(io.BytesIO(data)).namelist())
    events = []
    _record_swap(monkeypatch, events)
    r = _restore(data)
    for path, body in ((audio, b"RIFF"), (clip, b"clip")):
        assert os.path.isfile(path), path
        with open(path, "rb") as f:
            assert f.read() == body
    assert r["media_kept"] is True
    assert ("verify", True) in events   # the old folder still existed at the check
    assert not _leftovers()



def test_folders_a_database_only_backup_lacks_are_named_and_kept(isolated_db):
    _new("A")
    data = _db_only_backup()
    _write(os.path.join("my_notes", "plan.txt"), b"mine")
    _write(os.path.join("logs", "app.log"), b"log")
    r = _restore(data)
    assert r["folders_left_behind"] == ["my_notes"]
    assert r["previous_library_kept"] is True
    [aside] = [n for n in _leftovers() if ".pre_restore_" in n]
    with open(os.path.join(os.path.dirname(db.LIBRARY_DIR), aside, "my_notes", "plan.txt"),
              "rb") as f:
        assert f.read() == b"mine"


def test_restore_lock_file_is_gone_after_a_restore(isolated_db):
    _new("A")
    _restore(_backup_bytes())
    assert not os.path.exists(os.path.abspath(db.LIBRARY_DIR) + ".restore-lock")

def test_a_full_backup_still_replaces_the_media(isolated_db):
    did = _new("Backed up")
    kept = _write(os.path.join("dramas", str(did), "audio.wav"), b"old audio")
    data = _backup_bytes()
    later = _write(os.path.join("dramas", str(did), "added_later.wav"), b"later")
    r = _restore(data)
    assert r["media_kept"] is False
    assert os.path.isfile(kept) and not os.path.exists(later)


def _with_db_change(data, sql):
    """The zip with `sql` run on its library.db (to stand in for another library)."""
    path = _zip_db(data)
    conn = sqlite3.connect(path)
    conn.execute(sql)
    conn.commit()
    conn.close()
    with open(path, "rb") as f:
        return _rewrite(data, replace={"library.db": f.read()})


def test_media_of_titles_not_in_an_older_backup_is_not_carried(isolated_db):
    a = _new("In the backup")
    kept = _write(os.path.join("dramas", str(a), "audio.wav"), b"a")
    data = _db_only_backup()
    b = _new("Added later")
    _write(os.path.join("dramas", str(b), "audio.wav"), b"b")
    r = _restore(data)
    assert os.path.isfile(kept)
    assert not os.path.exists(os.path.join(db.DRAMAS_DIR, str(b)))
    assert r["previous_library_kept"] is True    # b's media waits in the folder set aside
    # A new title's id starts above every media folder set aside.
    assert db.create_drama(title_en="New") > b


def test_a_backup_from_another_library_gets_none_of_this_librarys_media(isolated_db):
    a = _new("Here")
    audio = _write(os.path.join("dramas", str(a), "audio.wav"), b"ours")
    data = _with_db_change(_db_only_backup(), "UPDATE dramas SET created_at = '2001-01-01T00:00:00'")
    _restore(data)
    assert not os.path.exists(audio)


def test_a_failed_carry_is_reported(isolated_db, monkeypatch):
    _new("A")
    _write(os.path.join("backups", "keep.txt"), b"k")
    data = _db_only_backup()
    real_rename = os.rename

    def rename(src, dst):
        if os.path.basename(src) == "backups" and ".pre_restore_" in src:
            raise OSError("in use")
        real_rename(src, dst)
    monkeypatch.setattr(os, "rename", rename)
    r = _restore(data)
    assert r["previous_library_kept"] is True
    assert any(".pre_restore_" in n for n in _leftovers())


def test_extracted_media_is_flushed_before_the_old_library_is_removed(isolated_db, monkeypatch):
    did = _new("Full")
    audio = _write(os.path.join("dramas", str(did), "audio.wav"), b"RIFF")
    data = _backup_bytes()
    events = []
    _record_swap(monkeypatch, events)
    real_rmtree = shutil.rmtree

    def rmtree(path, *a, **k):
        events.append(("rmtree", os.path.basename(path)))
        real_rmtree(path, *a, **k)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    _restore(data)
    st = os.stat(audio)
    removed = next(i for i, e in enumerate(events)
                   if e[0] == "rmtree" and ".pre_restore_" in e[1])
    assert ("fsync", (st.st_dev, st.st_ino)) in events[:removed]


@pytest.mark.skipif(os.path.normcase("A") == "a", reason="needs a case-sensitive file system")
def test_a_differently_cased_media_folder_in_the_zip_does_not_drop_the_media(isolated_db):
    did = _new("A")
    audio = _write(os.path.join("dramas", str(did), "audio.wav"), b"ours")
    data = _rewrite(_db_only_backup(), extra={"Dramas/9/x.wav": b"x"})
    _restore(data)
    assert os.path.isfile(audio)


def test_restore_keeps_a_titles_release_date(isolated_db):
    dated = _new("Dated")
    undated = _new("Undated")
    db.update_drama(dated, release_date="2025-03-04")
    data = _backup_bytes()
    db.update_drama(dated, release_date="1999-01-01")
    _restore(data)
    assert db.get_drama(dated)["release_date"] == "2025-03-04"
    assert db.get_drama(undated)["release_date"] is None
