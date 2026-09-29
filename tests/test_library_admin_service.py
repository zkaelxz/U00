"""Tests for services/library_admin_service.py (migration E0 remainder)."""
import io
import json
import os
import time
import zipfile

import pytest

import background_jobs
import db
from services import library_admin_service as las
from services import workspace_job_service as wjs
from services.service_errors import ConflictError, InvalidInputError


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _new(title="T", status="aligned"):
    did = db.create_drama(title_en=title, source_language="zh")
    db.update_drama(did, status=status)
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
    monkeypatch.setattr(wjs, "restore_library_backup", lambda *a: called.append(a))
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
    a, b = _new("A"), _new("B")
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


# ---- restore --------------------------------------------------------------

def test_restore_refused_while_job_running(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(wjs, "restore_library_backup", lambda *a: called.append(a))
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
    monkeypatch.setattr(wjs, "restore_library_backup", lambda *a: called.append(a))
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
    monkeypatch.setattr(wjs, "restore_library_backup", lambda *a: called.append(a))
    data = _zip({"library.db": b"x" * 100, "big.bin": b"y" * 1000})
    monkeypatch.setattr(wjs, "_MAX_RESTORE_MEMBER_BYTES", 500)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(wjs, "_MAX_RESTORE_MEMBER_BYTES", 10 ** 9)
    monkeypatch.setattr(wjs, "_MAX_RESTORE_TOTAL_BYTES", 900)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(wjs, "_MAX_RESTORE_TOTAL_BYTES", 10 ** 9)
    monkeypatch.setattr(wjs, "_MAX_RESTORE_MEMBERS", 1)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert not called


def test_valid_restore_calls_existing_swap(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(wjs, "restore_library_backup",
                        lambda b, d, before_swap=None: called.append(d))
    r = las.restore_backup(_zip({"library.db": b"x", "dramas/1/a.txt": b"x"}),
                           confirm=True, confirm_text="RESTORE")
    assert r["restored"] is True
    assert called == [db.LIBRARY_DIR]


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
from services import auth_service  # noqa: E402
from services.service_errors import NotFoundError  # noqa: E402
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


def test_restore_rechecks_jobs_before_swap(isolated_db, monkeypatch):
    a = _new("A")
    data = _backup_bytes()
    b = _new("B")
    answers = iter([False, True])
    monkeypatch.setattr(las, "_any_job_running", lambda: next(answers))
    with pytest.raises(ConflictError):
        _restore(data)
    assert db.get_drama(a) and db.get_drama(b)
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
    monkeypatch.setattr(wjs, "restore_library_backup", lambda *a, **k: called.append(a))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("library.db", b"x" * 100)
        zf.writestr("big.bin", b"y" * 5000)   # compresses far below 5000 bytes
    data = buf.getvalue()
    monkeypatch.setattr(las, "_RESTORE_MAX_MEMBERS", 1)
    with pytest.raises(InvalidInputError):
        las.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(las, "_RESTORE_MAX_MEMBERS", 100)
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


def test_encrypted_member_is_invalid_input(isolated_db):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("library.db", b"x")
        zf.writestr("secret.bin", b"y")
        zf.getinfo("secret.bin").flag_bits |= 0x1   # central directory: encrypted
    with pytest.raises(InvalidInputError):
        las.validate_backup_zip(buf.getvalue())


def test_export_explicit_ids_one_result_each(isolated_db):
    a = _new("A", status="translated")
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
    t = _new("A", status="translated")
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
    a = _new("A")
    b = _new("B")
    c = _new("C", status="translated")
    db.update_drama(b, translation_engine="deepseek")
    assert las.bulk_translate_engines([a, b, c]) == ["claude", "deepseek"]
    _fake_running(monkeypatch, b)
    assert las.bulk_translate_engines([a, b, c]) == ["claude"]
    monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
    r = las.start_bulk_translate([a, b])
    assert r["queued"] == [a]
    assert {"drama_id": b, "reason": "job_running"} in r["skipped"]
