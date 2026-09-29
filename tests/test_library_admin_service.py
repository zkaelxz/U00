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
    monkeypatch.setattr(wjs, "restore_library_backup", lambda b, d: called.append(d))
    r = las.restore_backup(_zip({"library.db": b"x", "dramas/1/a.txt": b"x"}),
                           confirm=True, confirm_text="RESTORE")
    assert r == {"restored": True}
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
