"""
Route batch 2A: /api/library/admin/* over services/library_admin_service.py.
FastAPI TestClient against an isolated library. Job starters for translate
are faked; export/backup jobs run for real on the temp library (no network,
no models).
"""

import io
import os
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.routers import library_admin_routes
from api.server import create_app
from services import auth_service
from services import library_admin_service as las
from services import workspace_job_service as wjs

BASE = "/api/library/admin"
REMOTE = "https://baihe.example.com"


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _new(title="T", status="aligned", **fields):
    did = db.create_drama(title_en=title, source_language="zh", **fields)
    db.update_drama(did, status=status)
    return did


def _clean(resp):
    """No library path in the body or any header."""
    assert db.LIBRARY_DIR not in resp.text
    for k, v in resp.headers.items():
        assert db.LIBRARY_DIR not in v, k
    return resp


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


def _put_job(job_id, status="running"):
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {
            "status": status, "progress": 0.0, "message": "", "result": None,
            "error": None, "started_at": time.time(), "finished_at": None}


def _wait(job_id, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _fake_running(monkeypatch, drama_id):
    monkeypatch.setattr(las.drama_service, "job_running_for_drama",
                        lambda did: did == drama_id)


# ---- bulk status / tags ----------------------------------------------------

class TestBulkStatusTags:
    def test_status_per_id_results(self, client):
        a, b = _new("A"), _new("B")
        r = _clean(client.post(f"{BASE}/bulk/status",
                               json={"drama_ids": [a, 999, b, a], "status": "translated"}))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["updated"] == 2
        assert [x["drama_id"] for x in body["results"]] == [a, 999, b]
        assert body["results"][1]["error"] == "not_found"
        assert db.get_drama(a)["status"] == db.get_drama(b)["status"] == "translated"

    @pytest.mark.parametrize("body", [
        {"drama_ids": [1], "status": "done"},
        {"drama_ids": [1], "status": "Translated"},
        {"drama_ids": [], "status": "aligned"},
        {"drama_ids": list(range(1, 502)), "status": "aligned"},
        {"drama_ids": ["1"], "status": "aligned"},
        {"drama_ids": [0], "status": "aligned"},
        {"drama_ids": [1], "status": "aligned", "extra": 1},
    ])
    def test_status_validation_422(self, client, body):
        r = client.post(f"{BASE}/bulk/status", json=body)
        assert r.status_code == 422
        assert _error(r)["code"] == "validation_error"

    def test_tags_add_and_remove(self, client):
        a = _new("A")
        r = client.post(f"{BASE}/bulk/tags",
                        json={"drama_ids": [a, 999], "tag": "Favorite", "present": True})
        assert r.status_code == 200, r.text
        assert r.json()["updated"] == 1
        assert db.has_custom_tag(db.get_drama(a), "Favorite")
        r = client.post(f"{BASE}/bulk/tags",
                        json={"drama_ids": [a], "tag": "Favorite", "present": False})
        assert r.status_code == 200
        assert not db.has_custom_tag(db.get_drama(a), "Favorite")

    @pytest.mark.parametrize("body", [
        {"drama_ids": [1], "tag": "Evil", "present": True},
        {"drama_ids": [1], "tag": "favorite", "present": True},
        {"drama_ids": [1], "tag": "Favorite", "present": "true"},
        {"drama_ids": [1], "tag": "Favorite"},
    ])
    def test_tags_validation_422(self, client, body):
        assert client.post(f"{BASE}/bulk/tags", json=body).status_code == 422


# ---- bulk delete -------------------------------------------------------------

class TestBulkDelete:
    @pytest.mark.parametrize("confirm,text", [
        (False, "DELETE"), (True, "delete"), ("true", "DELETE"), (True, ""),
        (True, "RESTORE"), (None, "DELETE")])
    def test_wrong_confirm_422_nothing_changes(self, client, confirm, text):
        a = _new("A")
        body = {"drama_ids": [a], "confirm_text": text}
        if confirm is not None:
            body["confirm"] = confirm
        r = client.post(f"{BASE}/bulk/delete", json=body)
        assert r.status_code == 422
        assert db.get_drama(a) is not None

    def test_per_id_results(self, client, monkeypatch):
        a, b = _new("A"), _new("B")
        _fake_running(monkeypatch, b)
        r = _clean(client.post(f"{BASE}/bulk/delete", json={
            "drama_ids": [a, b, 999], "confirm": True, "confirm_text": "DELETE"}))
        assert r.status_code == 200, r.text
        by_id = {x["drama_id"]: x for x in r.json()["results"]}
        assert by_id[a]["ok"] is True
        assert by_id[b]["error"] == "job_running"
        assert by_id[999]["error"] == "not_found"
        assert r.json()["deleted"] == 1
        assert db.get_drama(a) is None and db.get_drama(b) is not None

    def test_refused_while_backup_runs(self, client):
        a = _new("A")
        _put_job(las.BACKUP_JOB_ID)
        r = client.post(f"{BASE}/bulk/delete", json={
            "drama_ids": [a], "confirm": True, "confirm_text": "DELETE"})
        assert r.status_code == 409
        assert db.get_drama(a) is not None


# ---- bulk translate ----------------------------------------------------------

class TestBulkTranslate:
    def test_starts_with_expected_engines(self, client, monkeypatch):
        a = _new("A", translation_engine="ollama")
        b = _new("B", translation_engine="deepseek")
        c = _new("C", status="translated")
        captured = {}

        def fake_start(job_id, target, *args, **kwargs):
            captured.update(job_id=job_id, args=args, kwargs=kwargs)
            return True
        monkeypatch.setattr(background_jobs, "start_job", fake_start)
        r = _clean(client.post(f"{BASE}/bulk/translate", json={"drama_ids": [a, b, c]}))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["job_id"] == las.BULK_TRANSLATE_JOB_ID
        assert body["queued"] == [a, b]
        assert body["skipped"] == [{"drama_id": c, "reason": "not_aligned"}]
        assert captured["kwargs"]["expected_engines"] == {a: "ollama", b: "deepseek"}
        assert captured["kwargs"]["default_locale"] == "en-US"
        # keys are resolved server-side and never echoed
        assert "api_key" not in r.text

    def test_route_always_passes_expected_engines(self, client, monkeypatch):
        a = _new("A")
        seen = {}

        def fake(drama_ids, default_locale="en-US", expected_engines=None):
            seen["expected"] = expected_engines
            return {"job_id": "x", "queued": [a], "skipped": []}
        monkeypatch.setattr(las, "start_bulk_translate", fake)
        r = client.post(f"{BASE}/bulk/translate",
                        json={"drama_ids": [a], "default_locale": "fr-FR"})
        assert r.status_code == 200
        assert seen["expected"] == {a: "claude"}

    def test_duplicate_409(self, client, monkeypatch):
        a = _new("A")
        _put_job(las.BULK_TRANSLATE_JOB_ID)
        r = client.post(f"{BASE}/bulk/translate", json={"drama_ids": [a]})
        assert r.status_code == 409

    def test_nothing_to_translate_422(self, client):
        a = _new("A", status="translated")
        r = client.post(f"{BASE}/bulk/translate", json={"drama_ids": [a, 999]})
        assert r.status_code == 422

    @pytest.mark.parametrize("locale", ["english", "en_US", "e1-US"])
    def test_bad_locale_422(self, client, locale):
        a = _new("A")
        r = client.post(f"{BASE}/bulk/translate",
                        json={"drama_ids": [a], "default_locale": locale})
        assert r.status_code == 422


# ---- export / backup / artifacts -------------------------------------------

class TestExportBackupArtifacts:
    @pytest.mark.parametrize("kind", ["backup", "export", "database"])
    def test_no_artifact_404(self, client, kind):
        for path in (f"{BASE}/artifacts/{kind}/info", f"{BASE}/artifacts/{kind}"):
            r = _clean(client.get(path))
            assert r.status_code == 404
            assert _error(r)["message"] == "No artifact available."

    @pytest.mark.parametrize("kind", ["Backup", "../backup", "zip", "exports"])
    def test_unknown_kind_422_or_404(self, client, kind):
        r = client.get(f"{BASE}/artifacts/{kind}/info")
        assert r.status_code in (404, 422)

    def _download(self, client, kind, media_type):
        info = _clean(client.get(f"{BASE}/artifacts/{kind}/info"))
        assert info.status_code == 200, info.text
        meta = info.json()
        assert set(meta) == {"kind", "name", "size"} and meta["kind"] == kind
        r = _clean(client.get(f"{BASE}/artifacts/{kind}"))
        assert r.status_code == 200
        assert r.headers["content-disposition"] == f'attachment; filename="{meta["name"]}"'
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-type"] == media_type
        assert len(r.content) == meta["size"]
        return r.content

    def test_export_job_info_download(self, client):
        from core import Line
        a = _new("Exported", "translated")
        db.save_lines(a, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
        _new("Skip", "aligned")
        r = _clean(client.post(f"{BASE}/export"))
        assert r.status_code == 200, r.text
        assert r.json()["drama_ids"] == [a]
        st = _wait(r.json()["job_id"])
        assert st["status"] == "done", st.get("error")
        data = self._download(client, "export", "application/zip")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert any(n.endswith("english.srt") for n in zf.namelist())

    def test_export_explicit_ids_and_duplicate(self, client):
        a = _new("A", "translated")
        b = _new("B", "aligned")
        _put_job(las.EXPORT_JOB_ID)
        assert client.post(f"{BASE}/export", json={"drama_ids": [a]}).status_code == 409
        background_jobs.clear_all_jobs()
        r = client.post(f"{BASE}/export", json={"drama_ids": [a, b, 999]})
        assert r.status_code == 200, r.text
        errors = {x["drama_id"]: x.get("error") for x in r.json()["results"]}
        assert errors == {a: None, b: "not_translated", 999: "not_found"}
        _wait(las.EXPORT_JOB_ID)
        r = client.post(f"{BASE}/export", json={"drama_ids": [b]})
        assert r.status_code == 422

    def test_backup_job_info_download(self, client):
        _new("Backed up")
        r = _clean(client.post(f"{BASE}/backup", json={}))
        assert r.status_code == 200 and r.json() == {"job_id": las.BACKUP_JOB_ID}
        st = _wait(las.BACKUP_JOB_ID)
        assert st["status"] == "done", st.get("error")
        data = self._download(client, "backup", "application/zip")
        las.validate_backup_zip(data)

    def test_database_backup_job_info_download(self, client):
        _new("A")
        r = client.post(f"{BASE}/backup", json={"database_only": True})
        assert r.status_code == 200 and r.json() == {"job_id": las.DATABASE_BACKUP_JOB_ID}
        st = _wait(las.DATABASE_BACKUP_JOB_ID)
        assert st["status"] == "done", st.get("error")
        data = self._download(client, "database", "application/octet-stream")
        assert data.startswith(b"SQLite format 3")
        # a full backup has not been made
        assert client.get(f"{BASE}/artifacts/backup/info").status_code == 404

    def test_backup_duplicate_409_and_strict_flag(self, client):
        _put_job(las.BACKUP_JOB_ID)
        assert client.post(f"{BASE}/backup").status_code == 409
        assert client.post(f"{BASE}/backup",
                           json={"database_only": "true"}).status_code == 422


# ---- restore -----------------------------------------------------------------

def _restore(client, data, confirm="true", text="RESTORE", **kw):
    return client.post(f"{BASE}/restore", files={"file": ("b.zip", data, "application/zip")},
                       data={"confirm": confirm, "confirm_text": text}, **kw)


@pytest.fixture
def no_swap(monkeypatch):
    calls = []
    monkeypatch.setattr(wjs, "restore_library_backup", lambda *a, **k: calls.append(a))
    return calls


class TestRestore:
    @pytest.mark.parametrize("confirm,text", [
        ("true", "restore"), ("True", "RESTORE"), ("1", "RESTORE"), ("false", "RESTORE"),
        ("", "RESTORE"), ("true", ""), ("true", "DELETE")])
    def test_wrong_confirm_refused_before_read(self, client, monkeypatch, no_swap,
                                               confirm, text):
        read = []
        monkeypatch.setattr(library_admin_routes, "_read_capped",
                            lambda f: read.append(1) or b"")
        r = _restore(client, _zip({"library.db": b"x"}), confirm, text)
        assert r.status_code == 422
        assert read == [] and no_swap == []

    def test_409_while_job_runs(self, client, no_swap):
        _put_job("some_other_job")
        r = _restore(client, _zip({"library.db": b"x"}))
        assert r.status_code == 409
        assert no_swap == []

    @pytest.mark.parametrize("payload", [b"not a zip", b"", "no_db"])
    def test_bad_zip_422_swap_never_called(self, client, no_swap, payload):
        if payload == "no_db":
            payload = _zip({"readme.txt": b"hi"})
        r = _clean(_restore(client, payload))
        assert r.status_code == 422
        assert no_swap == []

    def test_oversize_422(self, client, monkeypatch, no_swap):
        monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "0.001")   # ~1 KiB
        validated = []
        monkeypatch.setattr(las, "validate_backup_zip", lambda b: validated.append(1))
        r = _restore(client, b"0" * 4096)
        assert r.status_code == 422
        assert _error(r)["message"] == "The uploaded file is too large."
        assert validated == [] and no_swap == []

    def test_real_round_trip(self, client):
        a = _new("Kept")
        assert client.post(f"{BASE}/backup").status_code == 200
        assert _wait(las.BACKUP_JOB_ID)["status"] == "done"
        data = client.get(f"{BASE}/artifacts/backup").content
        _new("Added after backup")
        background_jobs.clear_all_jobs()
        r = _clean(_restore(client, data))
        assert r.status_code == 200, r.text
        assert r.json() == {"restored": True, "sessions_revoked": 0}
        assert [d["id"] for d in db.list_dramas()] == [a]


# ---- storage -----------------------------------------------------------------

def _clips(did, size=200):
    clips = os.path.join(db.drama_dir(did), "dub_clips")
    os.makedirs(clips, exist_ok=True)
    with open(os.path.join(clips, "c.wav"), "wb") as f:
        f.write(b"0" * size)
    return clips


class TestStorage:
    def test_scan_is_dry_run(self, client):
        a, b = _new("A"), _new("B")
        _clips(a), _clips(b)
        r = _clean(client.get(f"{BASE}/storage"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["preset"] == "balanced" and body["would_free_bytes"] == 400
        assert os.path.isdir(os.path.join(db.drama_dir(a), "dub_clips"))
        assert client.get(f"{BASE}/storage?preset=archival").json()["would_free_bytes"] == 0

    def test_bad_preset_422(self, client):
        assert client.get(f"{BASE}/storage?preset=nope").status_code == 422
        assert client.post(f"{BASE}/storage/clean", json={
            "preset": "nope", "confirm": True, "confirm_text": "CLEAN"}).status_code == 422

    @pytest.mark.parametrize("confirm,text", [(True, "clean"), ("true", "CLEAN"),
                                              (False, "CLEAN"), (True, "")])
    def test_wrong_confirm_nothing_removed(self, client, confirm, text):
        a = _new("A")
        clips = _clips(a)
        r = client.post(f"{BASE}/storage/clean",
                        json={"preset": "balanced", "confirm": confirm, "confirm_text": text})
        assert r.status_code == 422
        assert os.path.exists(os.path.join(clips, "c.wav"))

    def test_clean_skips_running(self, client, monkeypatch):
        a, b = _new("A"), _new("B")
        _clips(a), _clips(b)
        _fake_running(monkeypatch, b)
        r = _clean(client.post(f"{BASE}/storage/clean", json={
            "preset": "balanced", "confirm": True, "confirm_text": "CLEAN"}))
        assert r.status_code == 200, r.text
        assert r.json()["freed_bytes"] == 200
        by_id = {x["drama_id"]: x for x in r.json()["results"]}
        assert by_id[b]["error"] == "job_running" and by_id[a]["freed_bytes"] == 200
        assert not os.path.isdir(os.path.join(db.drama_dir(a), "dub_clips"))
        assert os.path.isdir(os.path.join(db.drama_dir(b), "dub_clips"))


# ---- auth on -----------------------------------------------------------------

def _remote_app():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                      raise_server_exceptions=False)


def _local_app():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False)


def _headers(session):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
            api_auth.CSRF_HEADER: session["csrf_token"]}


def _household(*extra, email="kid@example.com"):
    u = auth_service.add_user(email)
    for p in extra:
        auth_service.grant_permission(u["id"], p)
    return _headers(auth_service.create_session(u["id"], "pytest", "203.0.113.9"))


def _admin():
    u = auth_service.grant_admin_local("admin@example.com")
    return _headers(auth_service.create_session(u["id"], "pytest", "203.0.113.9"))


_LOCAL_ONLY = [
    ("post", "/bulk/delete", {"json": {"drama_ids": [1], "confirm": True,
                                        "confirm_text": "DELETE"}}),
    ("post", "/export", {}),
    ("post", "/backup", {"json": {"database_only": True}}),
    ("get", "/artifacts/backup", {}),
    ("post", "/restore", {"files": {"file": ("b.zip", b"PK", "application/zip")},
                          "data": {"confirm": "true", "confirm_text": "RESTORE"}}),
    ("post", "/storage/clean", {"json": {"preset": "balanced", "confirm": True,
                                          "confirm_text": "CLEAN"}}),
]
_ADMIN_LIBRARY = [
    ("post", "/bulk/status", {"json": {"drama_ids": [1], "status": "aligned"}}),
    ("post", "/bulk/tags", {"json": {"drama_ids": [1], "tag": "Favorite", "present": True}}),
    ("get", "/artifacts/backup/info", {}),
    ("get", "/storage", {}),
]


class TestAuthOn:
    def test_no_session_401(self, isolated_db):
        c = _remote_app()
        for method, path, kw in _ADMIN_LIBRARY + [
                ("post", "/bulk/translate", {"json": {"drama_ids": [1]}})]:
            assert getattr(c, method)(BASE + path, **kw).status_code == 401, path

    @pytest.mark.parametrize("method,path,kw", _LOCAL_ONLY)
    def test_remote_admin_refused_local_only(self, isolated_db, method, path, kw):
        c = _remote_app()
        a = _new("A")
        h = _admin()
        r = getattr(c, method)(BASE + path, headers=h, **kw)
        assert r.status_code == 403, path
        assert db.get_drama(a) is not None
        assert background_jobs.list_all_jobs() == {}

    @pytest.mark.parametrize("method,path,kw", _ADMIN_LIBRARY)
    def test_household_refused_admin_library(self, isolated_db, method, path, kw):
        c = _remote_app()
        a = _new("A")
        r = getattr(c, method)(BASE + path, headers=_household(), **kw)
        assert r.status_code == 403, path
        assert db.get_drama(a)["status"] == "aligned"

    def test_remote_admin_allowed_admin_library(self, isolated_db):
        c = _remote_app()
        a = _new("A")
        h = _admin()
        r = _clean(c.post(f"{BASE}/bulk/status", headers=h,
                          json={"drama_ids": [a], "status": "dubbed"}))
        assert r.status_code == 200, r.text
        assert db.get_drama(a)["status"] == "dubbed"
        assert _clean(c.get(f"{BASE}/storage", headers=h)).status_code == 200
        assert c.get(f"{BASE}/artifacts/database/info", headers=h).status_code == 404

    def test_household_no_paid_engine_403_on_claude(self, isolated_db, monkeypatch):
        started = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda *a, **k: started.append(a) or True)
        c = _remote_app()
        a = _new("A")                                   # default engine: claude
        b = _new("B", translation_engine="ollama")
        h = _household()
        assert c.post(f"{BASE}/bulk/translate", headers=h,
                      json={"drama_ids": [a]}).status_code == 403
        assert c.post(f"{BASE}/bulk/translate", headers=h,
                      json={"drama_ids": [a, b]}).status_code == 403
        assert started == []
        r = c.post(f"{BASE}/bulk/translate", headers=h, json={"drama_ids": [b]})
        assert r.status_code == 200, r.text
        assert r.json()["queued"] == [b]
        assert len(started) == 1

    def test_household_with_paid_engine_allowed(self, isolated_db, monkeypatch):
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
        c = _remote_app()
        a = _new("A")
        r = c.post(f"{BASE}/bulk/translate", headers=_household("engines.paid"),
                   json={"drama_ids": [a]})
        assert r.status_code == 200, r.text

    def test_household_bare_403_on_translate(self, isolated_db):
        c = _remote_app()
        u = auth_service.add_user("bare@example.com")
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            auth_service.revoke_permission(u["id"], p)
        h = _headers(auth_service.create_session(u["id"]))
        a = _new("A", translation_engine="ollama")
        assert c.post(f"{BASE}/bulk/translate", headers=h,
                      json={"drama_ids": [a]}).status_code == 403

    def test_local_owner_allowed_local_only(self, isolated_db):
        c = _local_app()
        a = _new("A")
        _clips(a)
        r = c.post(f"{BASE}/storage/clean", json={
            "preset": "balanced", "confirm": True, "confirm_text": "CLEAN"})
        assert r.status_code == 200, r.text

    def test_remote_multipart_restore_refused_before_body_read(self, isolated_db, monkeypatch,
                                                               no_swap):
        c = _remote_app()
        h = _admin()
        read = []
        import starlette.formparsers as fp
        orig = fp.MultiPartParser.parse

        async def spy(self):
            read.append(1)
            return await orig(self)
        monkeypatch.setattr(fp.MultiPartParser, "parse", spy)
        files = {"file": ("b.zip", b"0" * 4096, "application/zip")}
        data = {"confirm": "true", "confirm_text": "RESTORE"}
        assert c.post(f"{BASE}/restore", files=files, data=data).status_code == 403
        assert c.post(f"{BASE}/restore", files=files, data=data,
                      headers=h).status_code == 403
        assert read == [] and no_swap == []
