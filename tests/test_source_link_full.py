"""The raw source link (dramas.source_url_full): kept beside the cleaned one
when cleaning changes it, returned only by the PC-only
GET /api/dramas/{id}/source-link, and absent from every other response,
the backup manifest and support reports. Mocked: no network."""
import json
import re
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import diagnostics_report
from api.api_config import ApiSettings
from api.auth import iter_route_declarations
from api.server import create_app
from services import auth_service, drama_service, source_link_service

MARKER = "keepme7Q2"
RAW = f"https://video.example/watch?v={MARKER}&list=PL1#t=30"
CLEAN = "https://video.example/watch"


def _app(**kw):
    return create_app(ApiSettings(serve_frontend=False, **kw))


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _pinned_title():
    did = db.create_drama(title_en="Linked", media_type="video_drama")
    assert drama_service.set_source_url_once(did, RAW)
    return did


# ----- storing ---------------------------------------------------------------

@pytest.mark.parametrize("raw,clean,full", [
    (RAW, CLEAN, RAW),
    ("https://novel.example/book/7", "https://novel.example/book/7", ""),
    ("https://user:pw@novel.example/b", "https://novel.example/b", "https://user:pw@novel.example/b"),
    ("not a url", "", ""),
])
def test_split_keeps_the_raw_link_only_when_cleaning_changed_it(raw, clean, full):
    assert source_link_service.split_source_url(raw) == (clean, full)


def test_set_once_stores_both_forms_and_never_overwrites(isolated_db):
    did = _pinned_title()
    d = db.get_drama(did)
    assert (d["source_url"], d["source_url_full"]) == (CLEAN, RAW)
    assert drama_service.set_source_url_once(did, "https://other.example/x?y=1") is False
    d = db.get_drama(did)
    assert (d["source_url"], d["source_url_full"]) == (CLEAN, RAW)


def test_a_clean_link_leaves_the_full_column_empty(isolated_db):
    did = db.create_drama(title_en="N")
    assert drama_service.set_source_url_once(did, "https://novel.example/book/7")
    assert db.get_drama(did)["source_url_full"] == ""


def test_owner_edit_stores_the_clean_form_and_keeps_the_raw_one(isolated_db):
    did = db.create_drama(title_en="N")
    drama_service.update_drama_metadata(did, source_url=RAW)
    d = db.get_drama(did)
    assert (d["source_url"], d["source_url_full"]) == (CLEAN, RAW)
    drama_service.update_drama_metadata(did, source_url="https://novel.example/b")
    d = db.get_drama(did)
    assert (d["source_url"], d["source_url_full"]) == ("https://novel.example/b", "")
    drama_service.update_drama_metadata(did, source_url="")
    d = db.get_drama(did)
    assert (d["source_url"], d["source_url_full"]) == ("", "")


# ----- the PC-only route -----------------------------------------------------

def test_source_link_route_returns_the_raw_link_on_the_pc(isolated_db):
    did = _pinned_title()
    r = _local(_app(auth_mode="off")).get(f"/api/dramas/{did}/source-link")
    assert r.status_code == 200 and r.json() == {"url": RAW}


def test_source_link_route_falls_back_to_the_clean_link(isolated_db):
    did = db.create_drama(title_en="N")
    c = _local(_app(auth_mode="off"))
    assert c.get(f"/api/dramas/{did}/source-link").json() == {"url": ""}
    drama_service.set_source_url_once(did, "https://novel.example/b")
    assert c.get(f"/api/dramas/{did}/source-link").json() == {"url": "https://novel.example/b"}
    assert c.get("/api/dramas/999999/source-link").status_code == 404


def test_legacy_typed_link_is_the_original(isolated_db):
    did = db.create_drama(title_en="Old", source_url=RAW)   # stored raw before the split
    assert _local(_app(auth_mode="off")).get(
        f"/api/dramas/{did}/source-link").json() == {"url": RAW}


def test_remote_principal_gets_403_even_as_admin(isolated_db):
    did = _pinned_title()
    c = TestClient(_app(auth_mode="on"), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"], "pytest", "203.0.113.9")
    from api import auth as api_auth
    r = c.get(f"/api/dramas/{did}/source-link",
              headers={"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}"})
    assert r.status_code == 403 and MARKER not in r.text


def test_household_listener_gets_403(isolated_db):
    did = _pinned_title()
    app = create_app(ApiSettings(household_port=8610, google_client_id="cid",
                                 google_client_secret="s", public_url="https://baihe.example.com"),
                     listener="household")
    c = TestClient(app, base_url="https://baihe.example.com", client=("203.0.113.9", 5000),
                   raise_server_exceptions=False)
    r = c.get(f"/api/dramas/{did}/source-link")
    assert r.status_code in (401, 403) and MARKER not in r.text


# ----- nowhere else ----------------------------------------------------------

_LISTS = ["/api/library/dramas", "/api/library/recent", "/api/library/continue",
          "/api/library/series", "/api/library/stats", "/api/library/costs",
          "/api/library/filter-options", "/api/library/search?q=Linked"]


def test_no_other_api_response_for_the_title_carries_the_full_link(isolated_db):
    """Every GET route that takes only a drama id, plus the Library lists,
    called as the PC owner (who sees the most): only source-link may hold
    the raw link's query."""
    did = _pinned_title()
    db.save_lines(did, [])
    app = _app(auth_mode="off")
    c = _local(app)
    paths = list(_LISTS)
    for _r, path, methods, _decls in iter_route_declarations(app):
        params = set(re.findall(r"{(\w+)(?::\w+)?}", path))
        if "GET" in methods and params == {"drama_id"}:
            paths.append(path.replace("{drama_id}", str(did)))
    assert f"/api/library/dramas/{did}" in paths and len(paths) > 40
    seen = []
    for path in paths:
        r = c.get(path)
        if path == f"/api/dramas/{did}/source-link":
            assert MARKER in r.text
            continue
        assert MARKER not in r.text, path
        seen.append(r.status_code)
    assert 200 in seen
    # The POST routes that return the title after a write.
    for path, body in [(f"/api/dramas/{did}/metadata", {"title_en": "Linked"}),
                       (f"/api/metadata/dramas/{did}/autofill/apply", {"title_en": "Linked"})]:
        r = c.post(path, json=body)
        assert r.status_code == 200 and MARKER not in r.text, (path, r.text[:200])
    detail = c.get(f"/api/library/dramas/{did}").json()
    assert detail["source_url"] == CLEAN and "source_url_full" not in detail


def test_backup_manifest_never_carries_the_link(isolated_db, tmp_path):
    from services import auto_backup_service
    _pinned_title()
    snap = tmp_path / "snap.db"
    import sqlite3
    with sqlite3.connect(db.DB_PATH) as src, sqlite3.connect(snap) as dst:
        src.backup(dst)
    body = auto_backup_service._manifest_bytes(str(snap), False, ("lib", 1)).decode()
    assert MARKER not in body and "video.example" not in body
    assert json.loads(body)["dramas"][0]["title"] == "Linked"


def _run_export(did):
    from core import Line
    from services import library_admin_service as las
    db.update_drama(did, status="translated")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    job = las.start_export_zip([did])["job_id"]
    end = time.time() + 10
    while (background_jobs.get_status(job) or {}).get("status") not in ("done", "error"):
        assert time.time() < end, "export did not finish"
        time.sleep(0.05)
    assert background_jobs.get_status(job)["status"] == "done"
    return zipfile.ZipFile(las.admin_artifact_path("export")["path"])


def test_export_zip_never_carries_the_link(isolated_db):
    did = _pinned_title()
    db.update_drama(did, keep_original=1)    # originals included, still no link
    with _run_export(did) as zf:
        assert any(n.endswith("english.srt") for n in zf.namelist())
        for name in zf.namelist():
            assert MARKER not in name and MARKER.encode() not in zf.read(name)


@pytest.mark.parametrize("text", [
    f"failed on {RAW}",
    "opened https://bob:hunter2@video.example/watch?v=keepme7Q2 then stopped",
])
def test_support_report_strips_query_and_userinfo(text):
    out = diagnostics_report.redact_for_support(text)
    assert MARKER not in out and "hunter2" not in out and "bob" not in out
    assert "?" not in out and "#" not in out


def test_research_apply_returns_the_detail_shape_without_the_full_link(isolated_db, monkeypatch):
    from services import metadata_research_service as mrs
    did = _pinned_title()
    monkeypatch.setattr(mrs, "apply_research", lambda drama_id, *a, **k: {
        "drama_id": drama_id, "replaced": [], "saved_alternates": [], "confirmed": [], "kept": [],
        "drama": drama_service.library_service.get_library_drama(drama_id)})
    r = _local(_app(auth_mode="off")).post(f"/api/metadata/dramas/{did}/research/apply",
                                           json={"research_id": "a" * 64, "choices": {}})
    assert r.status_code == 200, r.text
    assert MARKER not in r.text and r.json()["drama"]["source_url"] == CLEAN


def test_library_service_never_hands_out_the_full_link(isolated_db):
    did = _pinned_title()
    assert "source_url_full" not in drama_service.library_service.get_library_drama(did)
