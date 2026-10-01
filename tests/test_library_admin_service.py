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
from services.service_errors import ConflictError, InvalidInputError, NotFoundError


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
    wjs.restore_library_backup(data, db.LIBRARY_DIR)
    with pytest.raises(NotFoundError):
        las.admin_artifact_path("export")
    with open(os.path.join(db.LIBRARY_DIR, page_server.TOKEN_FILENAME), "rb") as f:
        assert f.read() == b"current-token"
    # Canonical removal: a planted plain FILE named "backups" is removed too.
    lib2 = str(tmp_path / "lib2")
    os.makedirs(os.path.join(lib2, "backups"))
    with open(os.path.join(lib2, "backups", "keep.zip"), "wb") as f:
        f.write(b"k")
    wjs.restore_library_backup(_rewrite(data, extra={"backups": b"a file"}), lib2)
    assert os.path.isfile(os.path.join(lib2, "backups", "keep.zip"))


def test_sidecar_members_not_extracted(isolated_db):
    _new("A")
    data = _rewrite(_backup_bytes(), extra={"library.db-wal": b"junk", "sources.db-shm": b"j",
                                            "library.db-journal": b"j"})
    wjs.restore_library_backup(data, db.LIBRARY_DIR)
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
    orig = las._any_job_running
    calls = []

    def running():
        calls.append(1)
        if len(calls) == 2:   # the before_swap re-check: a sign-in happens now
            auth_service.add_user("new@example.com")
        return orig()
    monkeypatch.setattr(las, "_any_job_running", running)
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
    assert wjs._schema_sql_allowed(sql) is ok


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
    assert wjs._schema_sql_allowed(sql) is False


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
