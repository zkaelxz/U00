"""Tests for services/library_admin_service.py (migration E0 remainder)."""
import io
import json
import os
import time
import types
import zipfile

import pytest

import background_jobs
from jobs import job_launch, job_runner
import db
from services import library_admin_service as las
from services import library_backup_restore_service as lbr
from services import library_backup_service as lbs
from services import library_restore_service as lrs
from services import workspace_job_service as wjs
from lib.errors import (ConflictError, InvalidInputError, NotFoundError,
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
            lbr.restore_backup(good, confirm=confirm, confirm_text=text)
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

def test_bulk_set_series_existing_new_and_remove(isolated_db):
    a, b = _new("A"), _new("B")
    sid = db.get_or_create_series("Saga")
    r = las.bulk_set_series([a, 999999, b], series_id=sid)
    assert r["updated"] == 2
    assert r["results"][1]["error"] == "not_found"
    assert db.get_drama(a)["series_id"] == db.get_drama(b)["series_id"] == sid

    # One series is created for the whole pick, not one per title.
    las.bulk_set_series([a, b], new_series_name=" Fresh ")
    ids = {db.get_drama(a)["series_id"], db.get_drama(b)["series_id"]}
    assert len(ids) == 1 and ids != {sid}
    assert sorted(x["name"] for x in db.list_series()) == ["Fresh", "Saga"]

    las.bulk_set_series([a, b], series_id=0)
    assert db.get_drama(a)["series_id"] is None and db.get_drama(b)["series_id"] is None


def test_bulk_set_series_rejects_bad_input_and_unknown_series(isolated_db):
    a = _new("A")
    with pytest.raises(InvalidInputError):
        las.bulk_set_series([a])
    with pytest.raises(InvalidInputError):
        las.bulk_set_series([a], series_id=1, new_series_name="X")
    with pytest.raises(InvalidInputError):
        las.bulk_set_series([a], new_series_name="  ")
    r = las.bulk_set_series([a], series_id=424242)
    assert r["updated"] == 0 and r["results"][0]["error"] == "not_found"


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
    assert job_launch.start_job(f"translate_{a}", gate.wait, 5)
    try:
        r = las.bulk_delete([a], confirm=True, confirm_text="DELETE")
        assert r["results"][0]["error"] == "job_running"
        assert db.get_drama(a) is not None
    finally:
        gate.set()
        wait_for_job(f"translate_{a}")


def test_bulk_translate_uses_existing_job(isolated_db, monkeypatch):
    monkeypatch.setattr(las.settings_service, "get_monthly_cap_usd", lambda *a, **k: 50.0)
    a, b = _new("A", "aligned"), _new("B", "translated")
    captured = {}

    def fake_start(job_id, target, *args, **kwargs):
        captured.update(job_id=job_id, target=target, args=args, kwargs=kwargs)
        return True
    monkeypatch.setattr(job_launch, "start_job", fake_start)
    r = las.start_bulk_translate([a, b])
    assert r == {"job_id": "bulk_series_translate", "queued": [a],
                 "skipped": [{"drama_id": b, "reason": "not_aligned"}]}
    assert captured["target"] is wjs.run_bulk_series_translate_job
    assert captured["args"][1] == [a]
    with pytest.raises(InvalidInputError):
        las.start_bulk_translate([b])


# ---- export / backup jobs -------------------------------------------------


def test_export_zip_job_and_artifact(isolated_db):
    from core import Line
    a = _new("Exported", "translated")
    db.save_lines(a, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    _new("Skip", "aligned")
    r = lbs.start_export_zip()
    assert r["drama_ids"] == [a]
    st = wait_for_job(r["job_id"])
    assert st["status"] == "done", st.get("error")
    _no_path(st["result"], db.LIBRARY_DIR)
    art = las.latest_admin_artifact("export")
    assert set(art) == {"kind", "name", "size"} and art["size"] > 0
    _no_path(art, db.LIBRARY_DIR)
    with zipfile.ZipFile(las.admin_artifact_path("export")["path"]) as zf:
        assert any(n.endswith("(en).srt") for n in zf.namelist())


def test_export_zip_writes_srts_in_time_order_with_a_bom(isolated_db):
    from core import Line
    a = _new("Retimed", "translated")
    # Stored order is not time order: the second row was retimed to start first.
    db.save_lines(a, [Line(idx=0, start=5.0, end=6.0, zh="后来", en="Later"),
                      Line(idx=1, start=1.0, end=2.0, zh="先前", en="Earlier")])
    st = wait_for_job(lbs.start_export_zip([a])["job_id"])
    assert st["status"] == "done", st.get("error")
    with zipfile.ZipFile(las.admin_artifact_path("export")["path"]) as zf:
        srts = {n: zf.read(n) for n in zf.namelist() if n.endswith(".srt")}
    assert len(srts) == 3 and sum(raw.startswith(b"\xef\xbb\xbf") for raw in srts.values()) == 2
    for name, raw in srts.items():
        text = raw.decode("utf-8-sig")
        # Chinese text gets a BOM (some TVs garble it without one); the
        # all-ASCII English file stays as it was.
        assert raw.startswith(b"\xef\xbb\xbf") is (not text.isascii()), name
        assert text.startswith("1\n00:00:01,000 --> 00:00:02,000")
        first = text.index("先前") if "先前" in text else text.index("Earlier")
        later = text.index("后来") if "后来" in text else text.index("Later")
        assert first < later


def test_backup_job_then_valid_for_restore(isolated_db):
    _new("Backed up")
    r = lbs.start_backup()
    st = wait_for_job(r["job_id"])
    assert st["status"] == "done", st.get("error")
    _no_path(st["result"], db.LIBRARY_DIR)
    with open(las.admin_artifact_path("backup")["path"], "rb") as f:
        data = f.read()
    lbr.validate_backup_zip(data)
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
    monkeypatch.setattr(lbs, "_BACKUP_PROGRESS_INTERVAL", 0)
    calls = []
    plain, with_cb = str(tmp_path / "a.zip"), str(tmp_path / "b.zip")
    lbs.write_backup_zip(plain)
    lbs.write_backup_zip(with_cb, progress=lambda f, m: calls.append((f, m)),
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
    st = wait_for_job(lbs.start_backup()["job_id"])
    assert st["status"] == "done", st.get("error")
    assert st["progress"] == 1.0 and st["message"] == "Backup ready."


def test_backup_job_cancel_leaves_no_partial_or_zip(isolated_db, monkeypatch):
    _media()
    monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: True)
    with pytest.raises(background_jobs.JobCancelled):
        lbs._backup_job(las.BACKUP_JOB_ID)
    folder = las._artifact_dir("backup", create=True)
    assert os.listdir(folder) == []


def test_backup_job_cancel_marks_job_cancelled(isolated_db, monkeypatch):
    _media()
    monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: True)
    job_id = lbs.start_backup()["job_id"]
    end = time.time() + 10
    st = None
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.05)
    assert st["status"] == "cancelled"
    assert las.latest_admin_artifact("backup") is None
    assert os.listdir(las._artifact_dir("backup", create=True)) == []


# ---- restore --------------------------------------------------------------

def test_restore_refused_while_job_running(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a: called.append(a))
    import threading
    gate = threading.Event()
    assert job_launch.start_job("some_job", gate.wait, 5)
    try:
        with pytest.raises(ConflictError):
            lbr.restore_backup(_zip({"library.db": b"x"}), confirm=True, confirm_text="RESTORE")
    finally:
        gate.set()
        wait_for_job("some_job")
    assert not called


def test_restore_refused_for_other_process_job_record(isolated_db, monkeypatch):
    monkeypatch.setattr(db, "list_job_records", lambda: [
        {"job_id": "transcribe_1", "status": "running", "updated_at": time.time()}])
    with pytest.raises(ConflictError):
        lbr.restore_backup(_zip({"library.db": b"x"}), confirm=True, confirm_text="RESTORE")


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
        lbr.restore_backup(payload, confirm=True, confirm_text="RESTORE")
    assert not called
    assert db.get_drama(did) is not None
    assert db.LIBRARY_DIR not in str(ei.value)


def test_oversized_zip_rejected_before_change(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a: called.append(a))
    data = _zip({"library.db": b"x" * 100, "big.bin": b"y" * 1000})
    monkeypatch.setattr(lrs, "MAX_RESTORE_MEMBER_BYTES", 500)
    with pytest.raises(InvalidInputError):
        lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(lrs, "MAX_RESTORE_MEMBER_BYTES", 10 ** 9)
    monkeypatch.setattr(lrs, "MAX_RESTORE_TOTAL_BYTES", 900)
    with pytest.raises(InvalidInputError):
        lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(lrs, "MAX_RESTORE_TOTAL_BYTES", 10 ** 9)
    monkeypatch.setattr(lrs, "MAX_RESTORE_MEMBERS", 1)
    with pytest.raises(InvalidInputError):
        lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert not called


def test_valid_restore_calls_existing_swap(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(lrs, "restore_library_backup",
                        lambda b, d, before_swap=None: called.append(d))
    r = lbr.restore_backup(_zip({"library.db": b"x", "dramas/1/a.txt": b"x"}),
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
        lbr.restore_backup(_zip({"library.db": b"x"}), confirm=True, confirm_text="RESTORE")
        assert echo.ENGINE_ID in engine_registry.ENGINES
        monkeypatch.setattr(lrs, "restore_library_backup", lambda *a, **k: db.set_app_setting(
            assistant._SETTINGS_PREFIX + "developer_mode", False))
        lbr.restore_backup(_zip({"library.db": b"x"}), confirm=True, confirm_text="RESTORE")
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
from tests.job_helpers import wait_for_job


def _backup_bytes():
    st = wait_for_job(lbs.start_backup()["job_id"])
    assert st["status"] == "done", st.get("error")
    with open(las.admin_artifact_path("backup")["path"], "rb") as f:
        return f.read()


def _restore(data):
    return lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")


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
    assert not job_launch.exclusive_active()


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
    record = db.get_job_record(las.BACKUP_JOB_ID)
    # Reads as "interrupted", and ends when the backup last wrote, not at restore time.
    assert record["detail_state"] == "interrupted"
    assert record["finished_at"] == record["updated_at"]


def test_a_restored_queue_start_time_is_dropped(isolated_db):
    # The backup's start time belongs to the moment it was taken: restored
    # within a day it would still lie ahead and start the queue on its own.
    from services import queue_service
    _new("A")
    queue_service.set_schedule(None, "02:00")
    data = _backup_bytes()
    queue_service.set_schedule(None, None)
    _restore(data)
    assert queue_service.get_queue(None)["schedule"] is None


def test_a_queue_running_when_the_backup_was_taken_restores_stopped(isolated_db):
    # The backup holds this same server's document: without settling it on
    # the next read, a queue that was running would start its next item.
    from services import queue_service
    _new("A")
    queue_service.start(None)
    assert queue_service.get_queue(None)["state"] == "running"
    data = _backup_bytes()
    queue_service.pause(None)
    _restore(data)
    assert queue_service.get_queue(None)["state"] == "idle"


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
        lbs.write_backup_zip(dest, include_media=include_media)
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
    st = wait_for_job(lbs.start_backup()["job_id"])
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


def test_restore_without_sources_db_drops_title_links_that_name_the_old_library(isolated_db):
    _new("A")
    with zipfile.ZipFile(io.BytesIO(_backup_bytes())) as src:
        data = _zip({n: src.read(n) for n in src.namelist() if n != "sources.db"})
    src_store.track_series("site", "s1", "Followed")
    src_store.set_tracked_drama("site", "s1", 1)
    src_store.record_imported("site", "s1", "c1", 1)
    _restore(data)
    assert [r["drama_id"] for r in src_store.list_tracked_series()] == [None]
    assert src_store.imported_chapter_ids("site", "s1", 1) == set()


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
    from services import auto_backup_settings as abt_
    _new("A")
    db.set_app_setting(abt_.SETTINGS_KEY, {"enabled": True, "folder": "elsewhere"})
    db.set_app_setting(abt_.STATE_KEY, {"last_success_at": "from the backup"})
    data = _backup_bytes()
    db.set_app_setting(abt_.SETTINGS_KEY, {"enabled": False, "folder": ""})
    db.set_app_setting(abt_.STATE_KEY, {"last_success_at": "live"})
    _restore(data)
    assert db.get_app_setting(abt_.SETTINGS_KEY) == {"enabled": False, "folder": ""}
    assert db.get_app_setting(abt_.STATE_KEY) == {"last_success_at": "live"}


def test_restore_rechecks_jobs_before_swap(isolated_db, monkeypatch):
    a = _new("A")
    data = _backup_bytes()
    b = _new("B")
    answers = iter([False, True])
    monkeypatch.setattr(lbr, "any_job_running", lambda: next(answers))
    with pytest.raises(ConflictError):
        _restore(data)
    assert db.get_drama(a) and db.get_drama(b)
    assert not _leftovers()
    assert not job_launch.exclusive_active()


def test_restore_waits_for_finished_job_threads_before_swap(isolated_db, monkeypatch):
    """A finished job's thread still writes timing/notification/GPU-lock
    rows; the library must not be swapped under it (as reset_library)."""
    _new("A")
    data = _backup_bytes()
    b = _new("B")
    calls = []
    monkeypatch.setattr(job_runner, "wait_for_job_threads",
                        lambda timeout: calls.append(timeout) or False)
    with pytest.raises(ConflictError):
        _restore(data)
    assert calls
    assert db.get_drama(b)
    assert not _leftovers()
    assert not job_launch.exclusive_active()


def test_no_job_or_delete_during_restore(isolated_db):
    a = _new("A")
    assert job_launch.acquire_exclusive("test")
    try:
        assert job_launch.start_job("x", lambda: None) is False
        with pytest.raises(ConflictError):
            lbs.start_backup()
        with pytest.raises(ConflictError):
            las.bulk_delete([a], confirm=True, confirm_text="DELETE")
        with pytest.raises(ConflictError):
            las.storage_cleanup("balanced", confirm=True, confirm_text="CLEAN")
        assert not job_launch.acquire_exclusive("again")
    finally:
        job_launch.release_exclusive()
    assert db.get_drama(a)
    assert job_launch.start_job("x", lambda: None)
    wait_for_job("x")


@pytest.mark.parametrize("job_id", [las.BACKUP_JOB_ID, las.EXPORT_JOB_ID])
def test_delete_and_cleanup_refused_while_archiving(isolated_db, job_id):
    a = _new("A")
    gate = threading.Event()
    assert job_launch.start_job(job_id, gate.wait, 5)
    try:
        with pytest.raises(ConflictError):
            las.bulk_delete([a], confirm=True, confirm_text="DELETE")
        with pytest.raises(ConflictError):
            las.storage_cleanup("balanced", confirm=True, confirm_text="CLEAN")
    finally:
        gate.set()
        wait_for_job(job_id)
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
    monkeypatch.setattr(lbr, "RESTORE_MAX_MEMBERS", 1)
    with pytest.raises(InvalidInputError):
        lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(lbr, "RESTORE_MAX_MEMBERS", 100)
    monkeypatch.setattr(lbr, "_RESTORE_MIN_TOTAL_BYTES", 1)
    monkeypatch.setattr(lbr, "_RESTORE_EXPANSION_FACTOR", 1)   # cap = upload size
    monkeypatch.setattr(lbr, "_library_size", lambda: 0)
    with pytest.raises(InvalidInputError):
        lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
    monkeypatch.setattr(lbr, "_library_size", lambda: 10 ** 6)  # 2x library allows it
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(las.shutil, "disk_usage", lambda p: usage(1, 1, 10))
    with pytest.raises(InvalidInputError) as ei:
        lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert "disk" in str(ei.value)
    assert not called
    monkeypatch.setattr(las.shutil, "disk_usage", lambda p: usage(1, 1, 10 ** 12))
    lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert len(called) == 1
    # The library's parent folder is checked; free space that can't be read
    # doesn't block the restore.
    asked = []

    def unreadable(path):
        asked.append(path)
        raise OSError("not reported")
    monkeypatch.setattr(las.shutil, "disk_usage", unreadable)
    lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
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
        lbr.validate_backup_zip(buf.getvalue())


def test_export_explicit_ids_one_result_each(isolated_db):
    a = _new("A", status="translated", translated=True)
    b = _new("B", status="aligned")
    r = lbs.start_export_zip([a, b, 999])
    wait_for_job(r["job_id"])
    assert r["drama_ids"] == [a]
    assert r["results"] == [{"drama_id": a, "ok": True},
                            {"drama_id": b, "ok": False, "error": "not_translated"},
                            {"drama_id": 999, "ok": False, "error": "not_found"}]


def test_artifact_write_failure_has_no_path(isolated_db, monkeypatch):
    def boom(kind, create):
        raise OSError(f"{db.LIBRARY_DIR}/backups: denied")
    monkeypatch.setattr(lbs, "_artifact_dir", boom)
    t = _new("A", status="translated", translated=True)
    for start in (lbs.start_backup, lbs.start_database_backup,
                  lambda: lbs.start_export_zip([t])):
        st = wait_for_job(start()["job_id"])
        assert st["status"] == "error"
        assert db.LIBRARY_DIR not in (st.get("error") or "")


def test_database_backup(isolated_db):
    _new("A")
    uid = auth_service.add_user("a@example.com")["id"]
    auth_service.create_session(uid)
    st = wait_for_job(lbs.start_database_backup()["job_id"])
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
    monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: True)
    r = las.start_bulk_translate([a, b])
    assert r["queued"] == [a]
    assert {"drama_id": b, "reason": "job_running"} in r["skipped"]


def test_bulk_translate_refusal_names_each_titles_own_reason(isolated_db, monkeypatch):
    monkeypatch.setattr(las.settings_service, "get_monthly_cap_usd", lambda *a, **k: 50.0)
    a, b = _new("Running one"), _new("Done one", status="translated")
    _fake_running(monkeypatch, a)
    with pytest.raises(InvalidInputError) as caught:
        las.start_bulk_translate([a, b, 999999])
    assert caught.value.message == ("Nothing was started. Skipped: Running one (a job is running), "
                                    "Done one (not aligned), title #999999 (not found).")
    # None aligned at all: the status sentence as before.
    with pytest.raises(InvalidInputError, match=r"status 'aligned'"):
        las.start_bulk_translate([b])


def test_bulk_translate_expected_engines(isolated_db, monkeypatch):
    monkeypatch.setattr(las.settings_service, "get_monthly_cap_usd", lambda *a, **k: 50.0)
    a, b = _new("A"), _new("B")
    expected = las.bulk_translate_engines([a, b])["by_drama"]
    db.update_drama(b, translation_engine="deepseek")
    captured = {}
    monkeypatch.setattr(job_launch, "start_job",
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
    r = lbs.start_export_zip()
    assert r["drama_ids"] == [good]
    assert r["results"] == [{"drama_id": hollow, "ok": False, "error": "no_translated_lines",
                             "message": r["results"][0]["message"]}]
    wait_for_job(r["job_id"])
    explicit = lbs.start_export_zip([good, hollow])
    wait_for_job(explicit["job_id"])
    assert [(x["drama_id"], x["ok"]) for x in explicit["results"]] == [(good, True), (hollow, False)]
    with pytest.raises(InvalidInputError):
        lbs.start_export_zip([hollow])


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
    lbs.write_backup_zip(path, include_media=False)
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


def test_restore_disk_check_counts_the_database_rebuild(isolated_db, monkeypatch):
    """The rebuild writes a scratch copy and a fresh file of each database
    beside the extracted ones, so those need room too."""
    called, asked = [], []
    monkeypatch.setattr(lrs, "restore_library_backup", lambda *a, **k: called.append(a))
    monkeypatch.setattr(lbr, "has_disk_room", lambda folder, need: asked.append(need) or False)
    data = _zip({"library.db": b"x" * 1000, "sources.db": b"s" * 500, "a.bin": b"a" * 100})
    with pytest.raises(InvalidInputError) as ei:
        lbr.restore_backup(data, confirm=True, confirm_text="RESTORE")
    assert "disk" in str(ei.value) and not called
    assert asked == [1600 + 2 * 1500]


def test_disk_full_while_rebuilding_is_not_called_a_bad_backup(isolated_db, monkeypatch):
    a = _new("A")
    data = _backup_bytes()

    def fill_up(path):
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA max_page_count = 1")
            conn.execute("CREATE TABLE IF NOT EXISTS filler (x)")
            conn.execute("CREATE TABLE IF NOT EXISTS filler2 (x)")
        finally:
            conn.close()
    monkeypatch.setattr(db, "migrate_database_file", fill_up)
    with pytest.raises(InvalidInputError) as ei:
        _restore(data)
    assert str(ei.value) == lrs.RESTORE_DISK_FULL
    assert db.get_drama(a)


def test_disk_full_check_works_without_sqlite_full_constants(monkeypatch):
    """sqlite3.SQLITE_FULL and Error.sqlite_errorcode are Python 3.11+; on
    3.10 a bad library.db must still be reported as one, not as a crash."""
    monkeypatch.delattr(sqlite3, "SQLITE_FULL", raising=False)
    try:
        raise ValueError(lrs.BAD_LIBRARY_DB) from sqlite3.DatabaseError("file is not a database")
    except ValueError as e:
        bad = e
    assert lrs._ran_out_of_disk(bad) is False
    full = sqlite3.OperationalError("database or disk is full")
    full.sqlite_errorcode = 13
    wrapped = ValueError(lrs.BAD_LIBRARY_DB)
    wrapped.__context__ = full
    assert lrs._ran_out_of_disk(wrapped) is True


def test_a_file_removed_mid_backup_is_skipped_and_reported(isolated_db, tmp_path, monkeypatch):
    """A file listed for the backup can vanish before it is copied (a cleanup,
    a job tidying its temp files): the backup skips it and says so."""
    _media(3)
    real = lbs._backup_files

    def listed_then_removed(*a):
        files = real(*a)
        os.remove(files[0][0])
        return files

    monkeypatch.setattr(lbs, "_backup_files", listed_then_removed)
    dest = str(tmp_path / "b.zip")
    skipped = lbs.write_backup_zip(dest)
    assert len(skipped) == 1
    with zipfile.ZipFile(dest) as zf:
        assert len([n for n in zf.namelist() if n.endswith("audio.bin")]) == 2
        assert "library.db" in zf.namelist()


def test_backup_job_reports_skipped_files(isolated_db, monkeypatch):
    _media(2)
    real = lbs._backup_files

    def listed_then_removed(*a):
        files = real(*a)
        os.remove(files[0][0])
        return files

    monkeypatch.setattr(lbs, "_backup_files", listed_then_removed)
    st = wait_for_job(lbs.start_backup()["job_id"])
    assert st["status"] == "done", st.get("error")
    assert st["result"]["skipped_count"] == 1
    assert "1 file" in st["message"]


@pytest.mark.parametrize("job_id", [las.BACKUP_JOB_ID, las.AUTO_BACKUP_JOB_ID])
def test_single_delete_refused_while_backing_up(isolated_db, job_id):
    from services import drama_service
    a = _new("A")
    gate = threading.Event()
    assert job_launch.start_job(job_id, gate.wait, 5)
    try:
        with pytest.raises(ConflictError, match="Try again when the backup finishes"):
            drama_service.delete_drama(a, confirm=True, confirm_text="DELETE")
    finally:
        gate.set()
        wait_for_job(job_id)
    assert db.get_drama(a)
    drama_service.delete_drama(a, confirm=True, confirm_text="DELETE")
    assert db.get_drama(a) is None


def _start_during(monkeypatch, module, name, job_id):
    """Replaces module.name with a wrapper that tries to start job_id while
    the real call runs, and records whether the start was refused."""
    seen = []
    real = getattr(module, name)

    def wrapper(*a, **k):
        try:
            seen.append(job_launch.start_job(job_id, lambda: None))
        except ConflictError as e:
            seen.append(str(e))
        return real(*a, **k)

    monkeypatch.setattr(module, name, wrapper)
    return seen


def test_no_job_starts_on_a_title_while_it_is_deleted(isolated_db, monkeypatch):
    from services import drama_service
    a, b = _new("A"), _new("B")
    seen = _start_during(monkeypatch, drama_service, "hard_delete_drama", f"transcribe_{a}")
    other = _start_during(monkeypatch, drama_service, "hard_delete_drama", f"transcribe_{b}")
    drama_service.delete_drama(a, confirm=True, confirm_text="DELETE")
    assert db.get_drama(a) is None
    assert other == [True]   # another title's job still starts
    assert len(seen) == 1 and "being deleted" in seen[0]
    wait_for_job(f"transcribe_{b}")
    background_jobs.clear_job(f"transcribe_{b}")
    assert background_jobs.get_status(f"transcribe_{a}") is None


def test_no_job_starts_on_a_title_while_bulk_delete_removes_it(isolated_db, monkeypatch):
    from services import drama_service
    a = _new("A")
    seen = _start_during(monkeypatch, drama_service, "hard_delete_drama", f"transcribe_{a}")
    assert las.bulk_delete([a], confirm=True, confirm_text="DELETE")["deleted"] == 1
    assert len(seen) == 1 and "being deleted" in seen[0]
    assert background_jobs.get_status(f"transcribe_{a}") is None


def test_no_job_starts_on_a_title_while_its_storage_is_cleaned(isolated_db, monkeypatch):
    from services import keep_original_service
    a = _new("A")
    seen = _start_during(monkeypatch, keep_original_service, "clean_title_storage",
                         f"transcribe_{a}")
    las.storage_cleanup("balanced", confirm=True, confirm_text="CLEAN")
    assert len(seen) == 1 and "being deleted" in seen[0]
    # The hold ends with the cleanup: the title's jobs start again.
    assert job_launch.start_job(f"transcribe_{a}", lambda: None)
    wait_for_job(f"transcribe_{a}")
    background_jobs.clear_job(f"transcribe_{a}")


def test_a_busy_title_is_never_held_so_its_chained_jobs_still_start(isolated_db, monkeypatch):
    """A running job's own follow-up start (transcribe chains diarize) must not
    meet a hold that a refused delete or a skipped cleanup took on its title."""
    from services import drama_service
    a, b = _new("A"), _new("B")
    gate = threading.Event()
    assert job_launch.start_job(f"transcribe_{a}", gate.wait, 5)
    chained = []
    real_check = drama_service.job_running_for_drama

    def check(did, *args, **kwargs):
        if did == a:   # the chained start lands while the title is checked
            try:
                chained.append(job_launch.start_job(f"diarize_{a}", lambda: None))
            except ConflictError as e:
                chained.append(str(e))
        return real_check(did, *args, **kwargs)

    monkeypatch.setattr(drama_service, "job_running_for_drama", check)
    try:
        with pytest.raises(ConflictError, match="still running"):
            drama_service.delete_drama(a, confirm=True, confirm_text="DELETE")
        result = las.storage_cleanup("balanced", confirm=True, confirm_text="CLEAN")
        assert {r["drama_id"]: r["ok"] for r in result["results"]} == {a: False, b: True}
        assert all(c is True for c in chained)
        assert job_launch._held_titles == {}
    finally:
        gate.set()
        for job_id in (f"transcribe_{a}", f"diarize_{a}"):
            if background_jobs.get_status(job_id):
                wait_for_job(job_id)
                background_jobs.clear_job(job_id)
    assert db.get_drama(a) and db.get_drama(b)


def test_a_backup_from_a_newer_baihe_is_refused_with_that_reason(isolated_db, monkeypatch):
    a = _new("Keep me")
    data = _backup_bytes()
    path = _zip_db(data)
    try:
        conn = sqlite3.connect(path)
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                     (db.APP_VERSION_SETTING, json.dumps("9.0.0")))
        conn.commit()
        conn.close()
        with open(path, "rb") as f:
            data = _rewrite(data, replace={"library.db": f.read()})
    finally:
        os.remove(path)
    monkeypatch.setattr(db, "_running_app_version", lambda: "0.1.0")
    with pytest.raises(InvalidInputError) as ei:
        _restore(data)
    assert ei.value.message == lrs.BACKUP_FROM_NEWER_APP
    assert db.get_drama(a)
    assert not _leftovers()
