""""Fill from link" (services/link_details_service.py, api/routers/
link_details_routes.py, New title with a link in drama_routes). yt_dlp is a
fake module in sys.modules and the child runs in-process, DNS is patched and
the egress proxy is a stand-in: no network, no real yt-dlp. The real child
process (time limit, output cap) is covered at the end with a fake yt_dlp on
PYTHONPATH."""
import contextlib
import importlib.machinery
import os
import sys
import threading
import time
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from lib import url_guard
from services import auth_service, egress_proxy, ytdlp_child
from services import link_details_service as svc
from services.service_errors import NotFoundError

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
VIDEO = f"https://www.youtube.com/watch?v=abcdefghijk&sig={SECRET}"
LOCAL_HEADERS = {"X-Baihe-Local": "1"}


class FakeYDL:
    info = {}
    calls = []
    opts = None
    gate = None

    def __init__(self, opts):
        FakeYDL.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        FakeYDL.calls.append((url, download))
        if FakeYDL.gate is not None:
            FakeYDL.gate.wait(5)
        if isinstance(FakeYDL.info, Exception):
            raise FakeYDL.info
        return dict(FakeYDL.info)


INFO = {"id": "abcdefghijk", "title": "配信 #12", "channel": "Some Channel",
        "upload_date": "20260301", "duration": 3600.0, "live_status": "was_live",
        "description": "long text https://secret.example/x", "thumbnail": "https://i.example/t.jpg",
        "url": "https://manifest.example/secret.m3u8", "formats": [{"url": "https://f.example"}]}


@pytest.fixture
def env(isolated_db, monkeypatch):
    fake = types.ModuleType("yt_dlp")
    fake.__spec__ = importlib.machinery.ModuleSpec("yt_dlp", None)
    fake.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])

    @contextlib.contextmanager
    def no_proxy():
        yield types.SimpleNamespace(url="http://u:p@127.0.0.1:1")
    monkeypatch.setattr(egress_proxy, "GuardedProxy", no_proxy)
    specs = []

    def fake_probe(tmp_dir, spec, timeout, cancel, max_output_chars, mode="live_status"):
        specs.append(dict(spec, mode=mode))
        events = []
        ytdlp_child.run_probe_worker(dict(spec, mode=mode), events.append)
        for event in events:
            if cancel():
                yield {"returncode": None, "timed_out": False, "cancelled": True}
                return
            if ytdlp_child._valid_event(event):
                yield {"event": event}
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(ytdlp_child, "run_live_probe", fake_probe)
    monkeypatch.setattr(svc, "SCAN_SPACING_SECONDS", (0.0, 0.0))
    FakeYDL.info, FakeYDL.calls, FakeYDL.gate = dict(INFO), [], None
    yield types.SimpleNamespace(specs=specs)
    if FakeYDL.gate is not None:
        FakeYDL.gate.set()
    _wait(svc.SCAN_JOB_ID)
    background_jobs.clear_job(svc.SCAN_JOB_ID)


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False,
                      headers=LOCAL_HEADERS)


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _probe(client, url=VIDEO):
    return client.post("/api/media-link/probe", json={"url": url})


# --- the child's fields ----------------------------------------------------

def test_link_fields_are_shape_checked_and_carry_no_description_or_url():
    fields = ytdlp_child.link_fields(INFO)
    assert fields == {"status": "not_live", "title": "配信 #12", "channel": "Some Channel",
                      "release_timestamp": None, "upload_date": "20260301", "duration": 3600.0,
                      "has_thumbnail": True, "is_playlist": False}
    assert ytdlp_child._valid_event({"link": fields})
    odd = ytdlp_child.link_fields({"upload_date": "2026-03-01", "duration": float("inf"),
                                   "title": "x" * 5000, "release_timestamp": True})
    assert (odd["upload_date"], odd["duration"], odd["release_timestamp"]) == ("", None, None)
    assert len(odd["title"]) == 200


@pytest.mark.parametrize("bad", [
    {"description": "x"}, {"title": 5}, {"channel": "x" * 201}, {"duration": "1"},
    {"has_thumbnail": "yes"},
])
def test_a_malformed_link_event_is_dropped(bad):
    link = dict(ytdlp_child.link_fields(INFO), **bad)
    assert not ytdlp_child._valid_event({"link": link})


# --- the probe route -------------------------------------------------------

def test_video_link_probe_reads_metadata_only_and_returns_no_link(client, env):
    r = _probe(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["kind"], body["title"], body["channel"], body["release_date"], body["is_live"]) \
        == ("video", "配信 #12", "Some Channel", "2026-03-01", False)
    assert body["series_suggestion"] == "Some Channel"
    assert body["display_url"] == "https://www.youtube.com/watch"
    for leaked in (SECRET, "abcdefghijk", "secret", "manifest", "i.example", "127.0.0.1"):
        assert leaked not in r.text
    assert FakeYDL.calls == [(VIDEO, False)]
    assert FakeYDL.opts["skip_download"] is True
    assert not [k for k in FakeYDL.opts if "cookie" in k.lower()]
    spec = env.specs[-1]
    assert spec["mode"] == "link_info" and "cookies_browser" not in spec
    assert spec["proxy"].startswith("http://")


def test_a_live_stream_gives_its_start_date(client, env):
    import datetime
    start = datetime.datetime(2026, 10, 1, 12, 0).timestamp()
    FakeYDL.info = dict(INFO, live_status="is_live", release_timestamp=start, upload_date=None)
    body = _probe(client).json()
    assert body["is_live"] is True and body["release_date"] == "2026-10-01"


def test_no_date_is_never_invented(client, env):
    FakeYDL.info = {"title": "t"}
    assert _probe(client).json()["release_date"] == ""


@pytest.mark.parametrize("url", ["ftp://www.youtube.com/watch?v=x", "https://u:p@youtube.com/watch",
                                 "https://www.youtube.com/watch?v=" + "a" * 2000, "not a link"])
def test_bad_links_are_refused_before_any_read(client, env, url):
    r = _probe(client, url)
    assert r.status_code == 422 and SECRET not in r.text
    assert FakeYDL.calls == []


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "::ffff:192.168.1.5", "169.254.169.254"])
def test_a_private_address_is_refused(client, env, monkeypatch, address):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", (address, port))])
    r = _probe(client)
    assert r.status_code == 422 and "youtube" not in r.text
    assert FakeYDL.calls == []


def test_any_other_page_is_not_fetched(client, env, monkeypatch):
    from lib import http
    monkeypatch.setattr(http, "get", lambda *a, **k: pytest.fail("fetched"))
    r = _probe(client, "https://blog.example/post/1?token=abc")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "page" and body["title"] == "" and "AI" in body["note"]
    assert body["display_url"] == "https://blog.example/post/1"
    assert FakeYDL.calls == []


def test_a_failed_read_shows_fixed_text_only(client, env):
    FakeYDL.info = RuntimeError(f"ERROR: private video {VIDEO}")
    r = _probe(client)
    assert r.status_code == 503 and SECRET not in r.text and "youtube" not in r.text


def test_missing_ytdlp_is_503(client, env, monkeypatch):
    monkeypatch.delitem(sys.modules, "yt_dlp")
    monkeypatch.setattr("builtins.__import__", _no_ytdlp_import(__import__))
    r = _probe(client)
    assert r.status_code == 503 and "yt-dlp" in r.json()["error"]["message"]


def _no_ytdlp_import(real):
    def fake(name, *a, **k):
        if name == "yt_dlp":
            raise ImportError(name)
        return real(name, *a, **k)
    return fake


class _FakeAdapter:
    name = "fake_novel"
    display_name = "Fake Novels"
    content_types = ["novel"]
    fetched = []

    def __init__(self, **kw):
        self.kw = kw

    def supports(self, method):
        return method == "get_series"

    def parse_url(self, url):
        return ("series", "s1")

    def get_series(self, series_id):
        _FakeAdapter.fetched.append(series_id)
        from sources.models import SeriesInfo
        return SeriesInfo(source="fake_novel", series_id=series_id, title="小说名",
                          authors=["作者甲", "乙"], description="简介 " * 2000)


def test_a_sources_link_uses_the_adapters_own_data(client, env, monkeypatch):
    from sources import registry
    monkeypatch.setattr(registry, "adapter_class_for_url", lambda url: _FakeAdapter)
    monkeypatch.setattr(registry, "find_for_url", lambda url, **kw: _FakeAdapter(**kw))
    _FakeAdapter.fetched = []
    body = _probe(client, "https://novels.example/book/1").json()
    assert (body["kind"], body["title"], body["channel"], body["platform"]) == \
        ("source", "小说名", "作者甲", "Fake Novels")
    assert body["release_date"] == "" and "no release date" in body["note"]
    assert len(body["summary"]) <= svc.SUMMARY_MAX
    assert _FakeAdapter.fetched == ["s1"] and FakeYDL.calls == []


# --- PC only ---------------------------------------------------------------

def test_every_route_is_refused_from_another_device(env):
    did = db.create_drama(title_en="x", source_url="https://www.youtube.com/watch")
    remote = TestClient(create_app(ApiSettings(auth_mode="on")),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    for method, path, body in (
            ("post", "/api/media-link/probe", {"url": VIDEO}),
            ("post", f"/api/dramas/{did}/link-details/probe", None),
            ("post", f"/api/dramas/{did}/link-details/apply", {"author": "a"}),
            ("post", "/api/library/link-scan", None),
            ("get", "/api/library/link-scan", None)):
        r = getattr(remote, method)(path, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path
    assert FakeYDL.calls == [] and db.get_drama(did)["author"] in (None, "")


# --- one title: refresh and apply ------------------------------------------

def test_refresh_shows_what_is_there_beside_the_link(client, env):
    did = db.create_drama(title_en="Mine", author="Typed", source_url="https://www.youtube.com/watch",
                          source_url_full=VIDEO)
    r = client.post(f"/api/dramas/{did}/link-details/probe")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == {"title_zh": "", "author": "Typed", "release_date": "", "series": ""}
    assert body["suggested"]["author"] == "Some Channel"
    assert FakeYDL.calls == [(VIDEO, False)]
    assert SECRET not in r.text
    assert db.get_drama(did)["author"] == "Typed"   # read-only


def test_refresh_needs_a_link(client, env):
    did = db.create_drama(title_en="Mine")
    assert client.post(f"/api/dramas/{did}/link-details/probe").status_code == 422


def test_apply_never_overwrites_typed_text(client, env):
    did = db.create_drama(title_en="Mine", author="Typed")
    r = client.post(f"/api/dramas/{did}/link-details/apply",
                    json={"author": "Some Channel", "release_date": "2026-03-01",
                          "title_zh": "配信 #12"})
    assert r.status_code == 200, r.text
    assert r.json() == {"applied": ["title_zh", "release_date"], "kept": ["author"]}
    d = db.get_drama(did)
    assert (d["author"], d["release_date"], d["title_zh"], d["title_en"]) == \
        ("Typed", "2026-03-01", "配信 #12", "Mine")


def test_apply_writes_only_what_was_ticked_and_checks_the_date(client, env):
    did = db.create_drama(title_en="Mine")
    assert client.post(f"/api/dramas/{did}/link-details/apply",
                       json={"release_date": "2026-02-30"}).status_code == 422
    assert client.post(f"/api/dramas/{did}/link-details/apply",
                       json={"summary": "x"}).status_code == 422
    assert client.post(f"/api/dramas/{did}/link-details/apply", json={"author": "A"}).json() \
        == {"applied": ["author"], "kept": []}
    d = db.get_drama(did)
    assert d["author"] == "A" and not d["release_date"] and d["series_id"] is None


def test_a_series_is_made_only_when_ticked_and_only_for_a_title_in_none(client, env):
    did = db.create_drama(title_en="Mine")
    assert client.post(f"/api/dramas/{did}/link-details/apply",
                       json={"series": "Some Channel"}).json()["applied"] == ["series"]
    sid = db.get_drama(did)["series_id"]
    assert sid is not None and db.get_series_id_by_name("Some Channel") == sid
    other = db.get_or_create_series("Other")
    did2 = db.create_drama(title_en="Two", series_id=other)
    assert client.post(f"/api/dramas/{did2}/link-details/apply",
                       json={"series": "Some Channel"}).json() == {"applied": [], "kept": ["series"]}
    assert db.get_drama(did2)["series_id"] == other


def test_a_new_series_takes_the_titles_own_owner_and_privacy(client, env):
    did = db.create_drama(title_en="Private", is_private=1)
    assert client.post(f"/api/dramas/{did}/link-details/apply",
                       json={"series": "Fresh"}).json()["applied"] == ["series"]
    series = db.get_item_ownership("series", db.get_drama(did)["series_id"])
    assert (series["owner_user_id"], series["is_private"]) == (None, 1)


def test_the_owners_own_series_with_the_same_privacy_is_joined(client, env):
    sid = db.create_series("Some Channel")
    did = db.create_drama(title_en="Mine")
    assert client.post(f"/api/dramas/{did}/link-details/apply",
                       json={"series": "Some Channel"}).json()["applied"] == ["series"]
    assert db.get_drama(did)["series_id"] == sid


@pytest.mark.parametrize("series_kw, drama_private", [
    ({"owner": True, "is_private": True}, False),    # a member's private series
    ({"owner": True, "is_private": False}, True),    # a member's shared series, private title
    ({"owner": False, "is_private": False}, True),   # the owner's shared series, private title
])
def test_a_site_named_series_never_joins_someone_elses_or_a_differently_shared_one(
        client, env, series_kw, drama_private):
    member = db.auth_create_user("member@example.com")
    sid = db.create_series("Some Channel", owner_user_id=member if series_kw["owner"] else None,
                           is_private=series_kw["is_private"])
    did = db.create_drama(title_en="Mine", author="", is_private=int(drama_private))
    r = client.post(f"/api/dramas/{did}/link-details/apply",
                    json={"series": "Some Channel", "author": "A"})
    assert r.status_code == 409, r.text
    d = db.get_drama(did)
    assert d["series_id"] is None and d["is_private"] == int(drama_private) and not d["author"]
    assert db.list_dramas_by_series(sid) == []


def test_a_series_chosen_meanwhile_is_not_replaced(client, env, monkeypatch):
    did = db.create_drama(title_en="Mine")
    chosen = db.get_or_create_series("Chosen elsewhere")
    real = svc._series_for

    def racing(drama_id, name):
        series_id = real(drama_id, name)
        db.update_drama(drama_id, series_id=chosen)   # another tab saves a series now
        return series_id
    monkeypatch.setattr(svc, "_series_for", racing)
    r = client.post(f"/api/dramas/{did}/link-details/apply", json={"series": "Some Channel"})
    assert r.json() == {"applied": [], "kept": ["series"]}
    assert db.get_drama(did)["series_id"] == chosen


def test_the_pc_owner_cannot_fill_another_users_private_title(client, env):
    owner = db.auth_create_user("owner@example.com")
    did = db.create_drama(title_en="Private", owner_user_id=owner, is_private=1,
                          source_url="https://www.youtube.com/watch")
    assert client.post(f"/api/dramas/{did}/link-details/apply", json={"author": "x"}).status_code == 404
    assert client.post(f"/api/dramas/{did}/link-details/probe").status_code == 404
    assert FakeYDL.calls == [] and not db.get_drama(did)["author"]


def test_another_users_private_title_is_not_found_for_a_member(env):
    owner = db.auth_create_user("owner@example.com")
    member = db.auth_create_user("member@example.com")
    did = db.create_drama(title_en="Private", owner_user_id=owner, is_private=1,
                          source_url="https://www.youtube.com/watch")
    principal = {"user_id": member, "is_admin": False, "is_local_owner": False,
                 "permissions": ["admin.library"]}
    with pytest.raises(NotFoundError):
        svc.apply(did, {"author": "x"}, principal=principal)
    with pytest.raises(NotFoundError):
        svc.probe_title(did, principal=principal)
    assert FakeYDL.calls == [] and not db.get_drama(did)["author"]


# --- after a URL download --------------------------------------------------

def test_fill_after_download_fills_blanks_only(env):
    did = db.create_drama(title_en="Mine", author="Typed")
    filled = svc.fill_after_download(did, ytdlp_child.link_fields(INFO))
    assert filled == ["release_date"]
    d = db.get_drama(did)
    assert (d["author"], d["release_date"]) == ("Typed", "2026-03-01")
    assert svc.fill_after_download(did, None) == []


# --- New title from a link -------------------------------------------------

def _create(client, **body):
    return client.post("/api/dramas", json={"source_language": "ja", **body})


def test_a_new_title_from_a_link_takes_the_links_title(client, env):
    r = _create(client, source_url=VIDEO)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title_zh"] == "配信 #12" and body["title_from_link"] == "link"
    assert body["source_url"] == "https://www.youtube.com/watch" and SECRET not in r.text
    d = db.get_drama(body["id"])
    assert d["source_url_full"] == VIDEO and not d["title_en"]
    assert not d["author"] and d["series_id"] is None   # the channel is only suggested


def test_a_link_that_cannot_be_read_names_the_title_after_its_host(client, env):
    FakeYDL.info = RuntimeError("nope")
    body = _create(client, source_url=VIDEO).json()
    assert body["title_zh"] == "youtube.com" and body["title_from_link"] == "placeholder"


def test_a_typed_name_wins_and_the_link_is_not_read(client, env):
    body = _create(client, title_en="Typed", source_url=VIDEO).json()
    assert body["title_en"] == "Typed" and not body["title_zh"]
    assert body["title_from_link"] is None and FakeYDL.calls == []


def test_a_new_title_needs_a_name_or_a_public_link(client, env, monkeypatch):
    assert _create(client).status_code == 422
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("10.0.0.1", port))])
    r = _create(client, source_url=VIDEO)
    assert r.status_code == 422 and db.list_dramas() == []


def test_from_another_device_the_link_is_never_read(env):
    remote = TestClient(create_app(ApiSettings(auth_mode="on")),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    r = remote.post("/api/dramas", json={"source_language": "ja", "source_url": VIDEO}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["title_from_link"] == "placeholder" and FakeYDL.calls == []


# --- the scan --------------------------------------------------------------

def _scan(client):
    r = client.post("/api/library/link-scan")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_scan_previews_and_writes_nothing(client, env):
    full = db.create_drama(title_zh="有", title_en="Full", author="A", release_date="2020-01-01",
                           series_id=db.get_or_create_series("S"),
                           source_url="https://www.youtube.com/watch")
    blank = db.create_drama(title_en="Blank", source_url="https://www.youtube.com/watch",
                            source_url_full=VIDEO)
    no_link = db.create_drama(title_en="No link")
    page = db.create_drama(title_en="Page", source_url="https://blog.example/p")
    owner = db.auth_create_user("owner@example.com")
    db.create_drama(title_en="Someone's private", owner_user_id=owner, is_private=1,
                    source_url="https://www.youtube.com/watch")
    assert _scan(client) == {"job_id": "link_scan", "total": 2}
    st = _wait("link_scan")
    assert st["status"] == "done", st
    r = client.get("/api/library/link-scan")
    body = r.json()
    rows = {row["drama_id"]: row for row in body["titles"]}
    assert set(rows) == {blank, page} and full not in rows and no_link not in rows
    assert rows[blank]["suggested"]["author"] == "Some Channel"
    assert rows[blank]["blank"] == ["title_zh", "author", "release_date", "series"]
    assert rows[page]["suggested"] is None and rows[page]["note"]
    assert SECRET not in r.text and "abcdefghijk" not in r.text
    assert FakeYDL.calls == [(VIDEO, False)]
    d = db.get_drama(blank)
    assert not d["author"] and not d["release_date"] and not d["title_zh"]


def test_the_scan_is_cancellable_and_refuses_a_second_start(client, env):
    for n in range(4):
        db.create_drama(title_en=f"T{n}", source_url="https://www.youtube.com/watch")
    FakeYDL.gate = threading.Event()
    _scan(client)
    assert client.post("/api/library/link-scan").status_code == 409
    assert client.post("/api/jobs/link_scan/cancel").status_code == 200
    FakeYDL.gate.set()
    st = _wait("link_scan")
    assert st["status"] == "cancelled", st
    assert len(FakeYDL.calls) <= svc.MAX_PARALLEL_PROBES


def test_a_new_scan_never_clears_the_previous_job_entry(client, env, monkeypatch):
    db.create_drama(title_en="T", source_url="https://www.youtube.com/watch")
    _scan(client)
    assert _wait("link_scan")["status"] == "done"
    real_clear, cleared = background_jobs.clear_job, []
    monkeypatch.setattr(background_jobs, "clear_job", cleared.append)
    assert _scan(client)["total"] == 1
    assert _wait("link_scan")["status"] == "done"
    monkeypatch.setattr(background_jobs, "clear_job", real_clear)
    assert cleared == []   # clearing would drop a force-stopped, still-running scan's cancel flag


def test_the_scan_runs_three_at_a_time_with_a_pause_between_starts(env, monkeypatch):
    for n in range(5):
        db.create_drama(title_en=f"T{n}", source_url="https://www.youtube.com/watch")
    monkeypatch.setattr(svc, "SCAN_SPACING_SECONDS", (10.0, 20.0))
    pauses, live, peak = [], [0], [0]
    lock = threading.Lock()
    monkeypatch.setattr(svc, "_wait", lambda job_id, seconds: pauses.append(seconds))
    real_row = svc._scan_row

    def counting_row(*a):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        try:
            return real_row(*a)
        finally:
            with lock:
                live[0] -= 1
    monkeypatch.setattr(svc, "_scan_row", counting_row)
    svc.start_scan()
    assert _wait("link_scan")["status"] == "done"
    assert len(pauses) == 4 and all(10.0 <= p <= 20.0 for p in pauses)
    assert peak[0] <= svc.MAX_PARALLEL_PROBES


# --- the real child process ------------------------------------------------

FAKE_YT_DLP = '''
import os, sys, time

class YoutubeDL:
    def __init__(self, opts):
        self.opts = opts
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def extract_info(self, url, download=True):
        mode = os.environ["FAKE_YTDLP_MODE"]
        if mode == "ok":
            return {"title": "T", "channel": "C", "upload_date": "20250101",
                    "description": "D", "thumbnail": "https://t.example/x.jpg"}
        if mode == "hang":
            time.sleep(120)
        for _ in range(10000):
            sys.stdout.write("x" * 100 + "\\n")
        sys.stdout.flush()
        time.sleep(120)
'''


@pytest.fixture
def real_child(tmp_path, monkeypatch, isolated_db):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "yt_dlp.py").write_text(FAKE_YT_DLP, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        [str(fake)] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])

    def run(mode):
        monkeypatch.setenv("FAKE_YTDLP_MODE", mode)
        return svc.read_link(VIDEO)
    return run


def test_the_real_child_reads_the_fields(real_child):
    view = real_child("ok")
    assert (view["title"], view["channel"], view["release_date"]) == ("T", "C", "2025-01-01")
    assert "D" not in view.values()


def test_a_hung_read_is_killed_at_the_time_limit(real_child, monkeypatch):
    from services.service_errors import DependencyUnavailableError
    monkeypatch.setattr(svc, "PROBE_TIMEOUT_SECONDS", 2)
    start = time.monotonic()
    with pytest.raises(DependencyUnavailableError, match="too long"):
        real_child("hang")
    assert time.monotonic() - start < 15


def test_output_past_the_cap_ends_the_read(real_child, monkeypatch):
    from services.service_errors import DependencyUnavailableError
    monkeypatch.setattr(svc, "PROBE_OUTPUT_CHARS", 50_000)
    start = time.monotonic()
    with pytest.raises(DependencyUnavailableError):
        real_child("spam")
    assert time.monotonic() - start < 15


def test_a_sources_read_that_fails_shows_fixed_text(client, env, monkeypatch):
    from sources import registry
    from sources.models import SourceError

    class Broken(_FakeAdapter):
        def get_series(self, series_id):
            raise SourceError(f"blocked at https://novels.example/book/1?sig={SECRET}")
    monkeypatch.setattr(registry, "adapter_class_for_url", lambda url: Broken)
    monkeypatch.setattr(registry, "find_for_url", lambda url, **kw: Broken(**kw))
    r = _probe(client, "https://novels.example/book/1")
    assert r.status_code == 503 and SECRET not in r.text and "novels.example" not in r.text
