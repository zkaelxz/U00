"""The name-check-across-titles routes: permissions, the whole flow, per-person privacy. Offline."""
import itertools
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service

BASE = "/api/glossary/name-report"


def _title(name, texts, owner=None, private=0):
    did = db.create_drama(title_en=name, source_language="ko")
    if owner is not None:
        with db.get_conn() as conn:
            conn.execute("UPDATE dramas SET owner_user_id = ?, is_private = ? WHERE id = ?", (owner, private, did))
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=t, en="") for i, t in enumerate(texts)])
    return did


def _titles(owner=None, private=0):
    _title("Alpha Stream", ["Yalo said hi", "then Yalo left"], owner, private)
    _title("Beta Stream", ["Yaloch said hi", "then Yaloch left"], owner, private)


_emails = itertools.count()


def _session(*permissions):
    user = auth_service.add_user(f"kid{next(_emails)}@example.com")
    for gone in ("library.read", "lines.read", "jobs.cancel", "jobs.start", "lines.edit"):
        auth_service.revoke_permission(user["id"], gone)
    for p in permissions:
        auth_service.grant_permission(user["id"], p)
    s = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
    return user["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                        api_auth.CSRF_HEADER: s["csrf_token"]}


@pytest.fixture
def remote(isolated_db):
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
    return TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)


@pytest.fixture
def local(isolated_db):
    client = TestClient(create_app(ApiSettings(auth_mode="off", serve_frontend=False)),
                        raise_server_exceptions=False)
    client.headers["X-Baihe-Local"] = "1"    # a bodyless POST is a simple request, so auth-off wants this
    return client


def _wait(client, headers=None):
    for _ in range(100):
        body = client.get(BASE, headers=headers).json()
        if body["status"] not in ("running", "queued"):
            return body
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_every_route_needs_its_permission(remote):
    _titles()
    calls = [("get", BASE), ("post", BASE), ("post", BASE + "/cancel"),
             ("post", BASE + "/marks"), ("get", BASE + "/export.md")]
    for method, url in calls:
        assert getattr(remote, method)(url).status_code == 401
    _, nothing = _session()
    for method, url in calls:
        assert getattr(remote, method)(url, headers=nothing).status_code == 403
    _, reader = _session("lines.read")
    assert remote.get(BASE, headers=reader).status_code == 200
    assert remote.post(BASE, headers=reader).status_code == 403          # needs jobs.start too
    assert remote.post(BASE + "/marks", headers=reader, json={"key": "x", "mark": "skip"}).status_code == 403
    assert remote.post(BASE + "/cancel", headers=reader).status_code == 403
    _, library_only = _session("library.read", "jobs.start", "lines.edit")
    assert remote.post(BASE, headers=library_only).status_code == 403    # the report shows line text
    assert remote.get(BASE + "/export.md", headers=reader).status_code == 409   # nothing scanned yet


def test_scan_mark_export_flow(local):
    _titles()
    assert local.get(BASE).json() == {"job_id": "", "status": "idle", "progress": 0.0, "message": "",
                                      "result": None}
    assert local.post(BASE).status_code == 200
    body = _wait(local)
    assert body["status"] == "done"
    group = body["result"]["groups"][0]
    assert {v["text"] for v in group["variants"]} == {"Yalo", "Yaloch"} and group["titles_total"] == 2
    assert set(group) == {"key", "canonical", "total", "variants", "titles_total", "titles", "examples", "mark"}
    assert body["result"]["counts"]["unresolved"] == 1

    export = local.get(BASE + "/export.md")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/markdown")
    assert "Yaloch" in export.text and "Alpha Stream" not in export.text

    marked = local.post(BASE + "/marks", json={"key": group["key"], "mark": "wrong"})
    assert marked.json() == {"key": group["key"], "mark": "wrong"}
    assert local.get(BASE).json()["result"]["counts"] == {"unresolved": 0, "right": 0, "wrong": 1, "skip": 0}
    assert "No unresolved names" in local.get(BASE + "/export.md").text
    assert local.post(BASE + "/marks", json={"key": "nope", "mark": "wrong"}).status_code == 422
    assert local.post(BASE + "/marks", json={"key": group["key"], "mark": "maybe"}).status_code == 422
    assert local.post(BASE + "/cancel").status_code == 409                 # nothing is running


def test_a_second_start_while_one_runs_is_a_conflict(local, monkeypatch):
    import threading
    from services import glossary_name_report_service as report
    gate = threading.Event()
    real = report.scan_titles
    monkeypatch.setattr(report, "scan_titles", lambda *a, **k: (gate.wait(5), real(*a, **k))[1])
    _titles()
    try:
        assert local.post(BASE).status_code == 200
        assert local.post(BASE).status_code == 409
        assert local.post(BASE + "/cancel").status_code == 200
    finally:
        gate.set()
        background_jobs.wait_for_job_threads(10)


def test_a_member_sees_only_their_titles_and_their_own_report(remote):
    _title("Shared Stream", ["Yalo said hi", "then Yalo left"])
    _title("Ada Private", ["Yaloch said hi", "then Yaloch left"], owner=None, private=0)
    ada_id, ada = _session("lines.read", "jobs.start", "lines.edit")
    _title("Ada Own", ["Yalou said hi", "then Yalou left"], owner=ada_id, private=1)
    _title("Other Private", ["Secretname Secretname"], owner=ada_id + 100, private=1)
    ben_id, ben = _session("lines.read", "jobs.start")
    assert remote.post(BASE, headers=ada).status_code == 200
    body = _wait(remote, ada)
    text = str(body)
    assert "Other Private" not in text and "Secretname" not in text and "Ada Own" in text
    # Ben has not scanned; Ada's report is not his.
    assert remote.get(BASE, headers=ben).json()["status"] == "idle"
    assert remote.get(BASE + "/export.md", headers=ben).status_code == 409
    # And with Ada's scan finished, Ben's own scan does not show her private title.
    assert remote.post(BASE, headers=ben).status_code == 200
    assert "Ada Own" not in str(_wait(remote, ben))


def test_the_report_has_no_paths_or_urls(local):
    _titles()
    local.post(BASE)
    text = str(_wait(local))
    assert "http" not in text and "\\" not in text and ".db" not in text
