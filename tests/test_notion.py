"""
Tests for the Notion export (roadmap item 112, services/notion_service.py and
api/routers/notion_routes.py). Fully mocked: an in-memory fake of the Notion
API behind a fake requests.Session; no network and no real token.
"""
import json
import os
import time
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
requests = pytest.importorskip("requests")

from fastapi.testclient import TestClient

import background_jobs
import db
from api.api_config import ApiSettings
from api.server import create_app
from services import notion_service as ns
from services import settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError)

TOKEN = "ntn_" + "A1b2C3d4" * 6
DB_HEX = "0123456789abcdef0123456789abcdef"
DB_ID = ns._dashed(DB_HEX)
PAGE_HEX = "fedcba9876543210fedcba9876543210"
PAGE_ID = ns._dashed(PAGE_HEX)


class Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = json.dumps(data).encode() if data is not None else b""

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        pass


def _rich(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


class FakeNotion:
    """Just enough of api.notion.com for the export, with Notion's limits."""

    def __init__(self):
        self.calls = []
        self.pages, self.blocks, self.children = {}, {}, {}
        self.databases = {DB_ID: {"object": "database", "id": DB_ID, "title": _rich("Dramas"),
                                  "properties": {
                                      "Name": {"type": "title"},
                                      "Lines": {"type": "number"},
                                      "Original title": {"type": "rich_text"},
                                      "Source language": {"type": "select"},
                                      "Exported": {"type": "date"},
                                      "Translated": {"type": "rich_text"},  # wrong type: skipped
                                      "Rating": {"type": "number"},  # not Baihe's
                                  }}}
        self.pages[PAGE_ID] = {"object": "page", "id": PAGE_ID, "archived": False,
                               "parent": {"type": "workspace"},
                               "properties": {"title": {"type": "title", "title": _rich("Notes")}}}
        self.children[PAGE_ID] = []
        self.fail = {}  # (method, path prefix) -> list of statuses to answer first

    # -- helpers
    def page_blocks(self, page_id):
        return [self.blocks[b] for b in self.children.get(page_id, [])]

    def headings(self, page_id):
        return [b for b in self.page_blocks(page_id) if ns._is_baihe_heading(b)]

    def _add_block(self, parent, block):
        assert len(json.dumps(block)) < 500_000
        bid = str(uuid.uuid4())
        kids = (block.get(block["type"]) or {}).pop("children", None)
        stored = dict(block, id=bid, has_children=bool(kids))
        for item in (stored.get(stored["type"]) or {}).get("rich_text", []):
            assert len(item["text"]["content"]) <= 2000
            item["plain_text"] = item["text"]["content"]
        self.blocks[bid] = stored
        self.children.setdefault(parent, []).append(bid)
        self.children.setdefault(bid, [])
        if kids:
            assert len(kids) <= 100
            for kid in kids:
                self._add_block(bid, kid)
        return stored

    # -- the API
    def handle(self, method, path, params, body):
        for (m, prefix), statuses in list(self.fail.items()):
            if m == method and path.startswith(prefix) and statuses:
                status = statuses.pop(0)
                if isinstance(status, tuple):
                    return Resp(status[0], status[1] if len(status) > 1 else None,
                                status[2] if len(status) > 2 else None)
                return Resp(status, {"object": "error", "message": "nope"})
        parts = path.strip("/").split("/")
        if path == "/users/me" and method == "GET":
            return Resp(200, {"object": "user", "type": "bot", "name": "Baihe export"})
        if parts[0] == "databases" and method == "GET":
            data = self.databases.get(parts[1])
            return Resp(200, data) if data else Resp(404, {"message": "not found"})
        if parts[0] == "pages":
            if method == "POST" and len(parts) == 1:
                pid = str(uuid.uuid4())
                parent = body["parent"]
                ptype = "database_id" if "database_id" in parent else "page_id"
                self.pages[pid] = {"object": "page", "id": pid, "archived": False,
                                   "parent": {"type": ptype, ptype: parent[ptype]},
                                   "properties": body["properties"]}
                self.children[pid] = []
                return Resp(200, self.pages[pid])
            page = self.pages.get(parts[1])
            if page is None:
                return Resp(404, {"message": "not found"})
            if method == "GET":
                return Resp(200, page)
            if method == "PATCH":
                page["properties"].update(body["properties"])
                return Resp(200, page)
        if parts[0] == "blocks":
            bid = parts[1]
            if bid not in self.children:
                return Resp(404, {"message": "not found"})
            if method == "DELETE":
                self.blocks[bid]["archived"] = True
                for kids in self.children.values():
                    if bid in kids:
                        kids.remove(bid)
                return Resp(200, self.blocks[bid])
            if parts[2:] == ["children"] and method == "GET":
                ids = self.children[bid]
                start = int(params.get("start_cursor") or 0)
                size = int(params.get("page_size") or 100)
                chunk = ids[start:start + size]
                more = start + size < len(ids)
                return Resp(200, {"results": [self.blocks[i] for i in chunk], "has_more": more,
                                  "next_cursor": str(start + size) if more else None})
            if parts[2:] == ["children"] and method == "PATCH":
                assert len(body["children"]) <= 100
                made = [self._add_block(bid, b) for b in body["children"]]
                return Resp(200, {"results": made})
        return Resp(400, {"message": f"unhandled {method} {path}"})


class FakeSession:
    server = None

    def __init__(self):
        pass

    def request(self, method, url, params=None, data=None, headers=None, timeout=None,
                allow_redirects=True, stream=False):
        assert timeout and allow_redirects is False and stream
        assert url.startswith(ns.API_BASE + "/")
        body = json.loads(data) if data else None
        FakeSession.server.calls.append({"method": method, "url": url, "params": dict(params or {}),
                                         "headers": dict(headers or {}), "data": data})
        return FakeSession.server.handle(method, url[len(ns.API_BASE):], params or {}, body)

    def close(self):
        pass


@pytest.fixture
def notion(isolated_db, monkeypatch, tmp_path):
    env = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "_default_env_path", lambda: str(env))
    server = FakeNotion()
    FakeSession.server = server
    monkeypatch.setattr(requests, "Session", FakeSession)
    sleeps = []
    monkeypatch.setattr(ns, "_sleep", sleeps.append)
    server.sleeps = sleeps
    ns.set_token(TOKEN)
    ns.set_config(target_type="database", target_id=DB_HEX)
    return server


def _lines(n=3):
    from core import Line
    return [Line(idx=i, start=float(i * 2), end=float(i * 2 + 1), zh=f"第{i}句", en=f"Line {i}",
                 speaker="Mo Ran" if i % 2 == 0 else None) for i in range(n)]


@pytest.fixture
def drama(notion):
    d = db.create_drama(title_en="Erha", title_zh="二哈", source_language="zh")
    db.save_lines(d, _lines())
    return d


def _export(drama_id, field="en"):
    ns._run_export(f"notion_export_{drama_id}", drama_id, field)


def _texts(server, block_id):
    out = []
    for bid in server.children.get(block_id, []):
        b = server.blocks[bid]
        out.append(ns._plain((b.get(b["type"]) or {}).get("rich_text")))
    return out


# --- settings -------------------------------------------------------------------

def test_config_never_returns_token(notion):
    cfg = ns.get_config()
    assert cfg == {"target_type": "database", "target_id": DB_ID, "token_configured": True}
    assert TOKEN not in json.dumps(cfg)


def test_token_is_validated(notion):
    for bad in ("", "abc", "sk-" + "a" * 40, "ntn_ with space", TOKEN + '"'):
        with pytest.raises(InvalidInputError):
            ns.set_token(bad)
    assert ns.clear_token()["token_configured"] is False


@pytest.mark.parametrize("value,expected", [
    (DB_HEX, DB_ID), (DB_ID, DB_ID), (DB_HEX.upper(), DB_ID),
    (f"https://www.notion.so/me/Dramas-{DB_HEX}?v=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", DB_ID),
    (f"https://notion.so/{DB_HEX}", DB_ID),
    (f"https://me.notion.site/Notes-{PAGE_HEX}", PAGE_ID),
])
def test_target_accepts_ids_and_links(value, expected):
    assert ns.parse_notion_id(value) == expected


@pytest.mark.parametrize("value", [
    "", "not an id", f"http://www.notion.so/{DB_HEX}", f"https://evil.example/{DB_HEX}",
    f"https://notion.so.evil.example/{DB_HEX}", "https://www.notion.so/me/no-id-here",
])
def test_target_refuses_other_links(value):
    with pytest.raises(InvalidInputError):
        ns.parse_notion_id(value)


# --- HTTP -------------------------------------------------------------------------

def test_token_only_in_the_bearer_header(notion, drama):
    ns.test_connection()
    _export(drama)
    assert notion.calls
    for call in notion.calls:
        assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert call["headers"]["Notion-Version"] == ns.NOTION_VERSION
        assert call["url"].startswith("https://api.notion.com/v1/")
        assert TOKEN not in call["url"]
        assert TOKEN not in json.dumps(call["params"])
        assert TOKEN.encode() not in (call["data"] or b"")


def test_test_connection_reports_bot_and_target(notion):
    assert ns.test_connection() == {"ok": True, "bot_name": "Baihe export",
                                    "target_title": "Dramas", "target_type": "database"}
    ns.set_config(target_type="page", target_id=PAGE_ID)
    assert ns.test_connection()["target_title"] == "Notes"


def test_unshared_target_and_bad_token_are_fixed_text(notion):
    ns.set_config(target_id="11111111111111111111111111111111")
    with pytest.raises(DependencyUnavailableError) as e:
        ns.test_connection()
    assert "Connections" in e.value.message
    notion.fail[("GET", "/users/me")] = [401]
    with pytest.raises(DependencyUnavailableError) as e:
        ns.test_connection()
    assert "refused the token" in e.value.message and TOKEN not in e.value.message


def test_redirect_is_not_followed(notion):
    notion.fail[("GET", "/users/me")] = [(302, None, {"Location": "https://evil.example/"})]
    with pytest.raises(DependencyUnavailableError) as e:
        ns.test_connection()
    assert e.value.message == ns._UNREACHABLE
    assert all("evil" not in c["url"] for c in notion.calls)


def test_network_error_is_fixed_text(notion, monkeypatch):
    def boom(self, *a, **kw):
        raise requests.ConnectionError(f"failed with Authorization: Bearer {TOKEN}")
    monkeypatch.setattr(FakeSession, "request", boom)
    with pytest.raises(DependencyUnavailableError) as e:
        ns.test_connection()
    assert e.value.message == ns._UNREACHABLE


def test_notion_validation_message_is_redacted(notion, drama):
    notion.fail[("POST", "/pages")] = [(400, {"message": f"bad value {TOKEN} secret_" + "x" * 30})]
    with pytest.raises(DependencyUnavailableError) as e:
        _export(drama)
    assert "Notion refused the export" in e.value.message
    assert TOKEN not in e.value.message and "secret_x" not in e.value.message


def test_redact_removes_the_token_by_value():
    token = "abcdefghij_custom_value_1234567890"
    assert token not in ns.redact(f"oops {token} here", token)


def test_rate_limit_is_retried_after_retry_after(notion, drama):
    notion.fail[("GET", "/databases/")] = [(429, {"message": "slow down"}, {"Retry-After": "3"}),
                                           503]
    _export(drama)
    assert 3.0 in notion.sleeps and 2.0 in notion.sleeps  # Retry-After, then back-off 2**1
    assert [c["url"] for c in notion.calls].count(f"{ns.API_BASE}/databases/{DB_ID}") == 3


def test_rate_limit_gives_up_eventually(notion, drama):
    notion.fail[("GET", "/databases/")] = [429] * (ns.MAX_RETRIES + 1)
    with pytest.raises(DependencyUnavailableError) as e:
        _export(drama)
    assert e.value.message == ns._BUSY
    assert all(s <= ns.MAX_RETRY_WAIT for s in notion.sleeps)


def test_test_connection_has_a_small_retry_budget(notion):
    notion.fail[("GET", "/users/me")] = [(429, {}, {"Retry-After": "600"})] * 3
    with pytest.raises(DependencyUnavailableError) as e:
        ns.test_connection()
    assert e.value.message == ns._BUSY
    assert len([c for c in notion.calls if c["url"].endswith("/users/me")]) == ns.TEST_RETRIES + 1
    assert max(notion.sleeps) <= ns.TEST_RETRY_WAIT


def test_writes_are_retried_only_when_notion_did_nothing(notion, drama):
    notion.fail[("POST", "/pages")] = [503]
    with pytest.raises(DependencyUnavailableError):
        _export(drama)
    assert [c["method"] for c in notion.calls].count("POST") == 1
    notion.fail[("POST", "/pages")] = [429, 409]
    _export(drama)
    assert [c["method"] for c in notion.calls].count("POST") == 4
    assert len([p for p in notion.pages.values()
                if p["parent"].get("database_id") == DB_ID]) == 1


def test_bad_retry_after_values_fall_back(notion):
    for raw in ("nan", "inf", "-5", "soon"):
        notion.fail[("GET", "/users/me")] = [(429, {}, {"Retry-After": raw})]
        assert ns.test_connection()["ok"] is True
    assert all(0.5 <= s <= ns.TEST_RETRY_WAIT for s in notion.sleeps if s > ns.MIN_INTERVAL)


def test_cancel_is_honoured_before_a_retry_wait(notion, drama, monkeypatch):
    notion.fail[("GET", "/databases/")] = [(429, {}, {"Retry-After": "30"})] * 3
    monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda job_id: True)
    with pytest.raises(background_jobs.JobCancelled):
        _export(drama)
    assert 30.0 not in notion.sleeps


def test_requests_are_spaced(monkeypatch):
    waits = []
    monkeypatch.setattr(ns, "_sleep", waits.append)
    ns._last_request[0] = time.monotonic()
    ns._throttle()
    assert waits and 0 < waits[0] <= ns.MIN_INTERVAL


def test_static_timeout():
    from tests.test_static_analysis import PROJECT_ROOT, _find_requests_calls_missing_timeout
    path = os.path.join(PROJECT_ROOT, "services", "notion_service.py")
    assert _find_requests_calls_missing_timeout(path, session_verbs=True) == []


# --- export -------------------------------------------------------------------------

def test_first_export_creates_a_database_page(notion, drama):
    _export(drama)
    page_id = db.get_drama(drama)["notion_page_id"]
    page = notion.pages[page_id]
    assert page["parent"]["database_id"] == DB_ID
    props = page["properties"]
    assert props["Name"]["title"][0]["text"]["content"] == "Erha"
    assert props["Lines"] == {"number": 3}
    assert props["Original title"]["rich_text"][0]["text"]["content"] == "二哈"
    assert props["Source language"] == {"select": {"name": "zh"}}
    assert "start" in props["Exported"]["date"]
    assert "Translated" not in props and "Rating" not in props  # wrong type / not Baihe's
    [heading] = notion.headings(page_id)
    texts = _texts(notion, heading["id"])
    assert "00:00:00  Mo Ran: Line 0" in texts and "00:00:02  Line 1" in texts
    assert any(t.startswith("Original title: 二哈") for t in texts)


def test_bilingual_and_original_text(notion, drama):
    _export(drama, "bilingual")
    page_id = db.get_drama(drama)["notion_page_id"]
    assert "00:00:02  Line 1\n第1句" in _texts(notion, notion.headings(page_id)[0]["id"])
    _export(drama, "zh")
    assert "00:00:02  第1句" in _texts(notion, notion.headings(page_id)[0]["id"])


def test_reexport_updates_the_same_page_in_place(notion, drama):
    _export(drama)
    page_id = db.get_drama(drama)["notion_page_id"]
    # the user's own note on the page, and an edited property Baihe doesn't own
    notion._add_block(page_id, {"type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "text": {"content": "my own note"}}]}})
    notion.pages[page_id]["properties"]["Rating"] = {"number": 5}
    old_heading = notion.headings(page_id)[0]["id"]
    lines = db.load_line_objects(drama)
    lines[0].en = "Edited line"
    db.save_lines(drama, lines)
    before = len(notion.pages)
    notion.calls.clear()
    _export(drama)
    assert len(notion.pages) == before
    assert not [c for c in notion.calls if c["method"] == "POST"]  # no new page
    assert db.get_drama(drama)["notion_page_id"] == page_id
    [heading] = notion.headings(page_id)
    assert heading["id"] != old_heading and notion.blocks[old_heading].get("archived")
    assert "00:00:00  Mo Ran: Edited line" in _texts(notion, heading["id"])
    assert "my own note" in _texts(notion, page_id)
    assert notion.pages[page_id]["properties"]["Rating"] == {"number": 5}


def test_export_does_not_bump_updated_at(notion, drama):
    before = db.get_drama(drama)["updated_at"]
    _export(drama)
    assert db.get_drama(drama)["updated_at"] == before


@pytest.mark.parametrize("change", ["deleted", "trashed", "moved"])
def test_gone_or_moved_page_gets_a_new_one(notion, drama, change):
    _export(drama)
    old = db.get_drama(drama)["notion_page_id"]
    if change == "deleted":
        del notion.pages[old]
    elif change == "trashed":
        notion.pages[old]["in_trash"] = True
    else:
        ns.set_config(target_type="page", target_id=PAGE_HEX)
    _export(drama)
    new = db.get_drama(drama)["notion_page_id"]
    assert new != old
    if change == "moved":
        assert notion.pages[new]["parent"] == {"type": "page_id", "page_id": PAGE_ID}
        assert set(notion.pages[new]["properties"]) == {"title"}
        assert len(notion.headings(old)) == 1  # the old page is left alone


def test_long_transcript_is_chunked(notion, drama):
    db.save_lines(drama, _lines(250))
    _export(drama)
    page_id = db.get_drama(drama)["notion_page_id"]
    [heading] = notion.headings(page_id)
    assert len(notion.children[heading["id"]]) == 250 + 4  # 2 details, stamp, divider
    appends = [c for c in notion.calls if c["method"] == "PATCH" and c["url"].endswith("/children")]
    assert len(appends) == 3
    for c in appends:
        body = json.loads(c["data"])
        kids = body["children"]
        if kids[0]["type"] == "heading_2":
            kids = kids[0]["heading_2"]["children"]
        assert len(kids) <= ns.MAX_BLOCKS_PER_CALL


def test_chunks_respect_the_payload_cap(monkeypatch):
    monkeypatch.setattr(ns, "MAX_PAYLOAD_BYTES", 1000)
    blocks = [ns._paragraph(ns._rt("x" * 300)) for _ in range(10)]
    chunks = ns._chunks(blocks)
    assert len(chunks) > 1 and sum(len(c) for c in chunks) == 10
    assert all(len(json.dumps(c)) <= 1000 + 400 for c in chunks)


def test_long_text_is_split_into_notion_sized_pieces():
    items = ns._rt("字" * 4500)
    assert [len(i["text"]["content"]) for i in items] == [2000, 2000, 500]


def test_failed_append_keeps_the_old_transcript(notion, drama):
    _export(drama)
    page_id = db.get_drama(drama)["notion_page_id"]
    old_heading = notion.headings(page_id)[0]["id"]
    db.save_lines(drama, _lines(150))
    real = notion.handle
    state = {"appends": 0}

    def flaky(method, path, params, body):
        if method == "PATCH" and path.endswith("/children") and path != f"/blocks/{page_id}/children":
            state["appends"] += 1
            return Resp(500, {"message": "boom"})
        return real(method, path, params, body)
    notion.handle = flaky
    with pytest.raises(DependencyUnavailableError):
        _export(drama)
    assert [h["id"] for h in notion.headings(page_id)] == [old_heading]
    assert state["appends"] == 1  # a write is never retried on a 5xx (it may have landed)


def test_cancel_removes_the_half_written_block(notion, drama, monkeypatch):
    _export(drama)
    page_id = db.get_drama(drama)["notion_page_id"]
    old_heading = notion.headings(page_id)[0]["id"]
    db.save_lines(drama, _lines(150))
    checks = {"n": 0}

    def cancel_second(job_id):
        checks["n"] += 1
        return checks["n"] >= 2
    monkeypatch.setattr(background_jobs, "is_cancel_requested", cancel_second)
    with pytest.raises(background_jobs.JobCancelled):
        _export(drama)
    assert [h["id"] for h in notion.headings(page_id)] == [old_heading]


def test_leftover_transcripts_are_removed_next_time(notion, drama):
    _export(drama)
    page_id = db.get_drama(drama)["notion_page_id"]
    notion._add_block(page_id, {"type": "heading_2", "heading_2": {
        "rich_text": [{"type": "text", "text": {"content": ns.HEADING_TEXT}}],
        "is_toggleable": True}})
    assert len(notion.headings(page_id)) == 2
    _export(drama)
    assert len(notion.headings(page_id)) == 1


def test_start_export_checks(notion, drama, monkeypatch):
    with pytest.raises(InvalidInputError):
        ns.start_export(drama, field="exe")
    empty = db.create_drama(title_en="Empty")
    with pytest.raises(InvalidInputError):
        ns.start_export(empty)
    monkeypatch.setattr(ns.drama_service, "job_running_for_drama", lambda d: True)
    with pytest.raises(ConflictError):
        ns.start_export(drama)
    ns.clear_token()
    with pytest.raises(InvalidInputError):
        ns.start_export(drama)


def test_export_status_builds_a_notion_link(notion, drama):
    assert ns.export_status(drama) == {"drama_id": drama, "page_id": None, "page_url": None}
    _export(drama)
    status = ns.export_status(drama)
    assert status["page_url"] == "https://www.notion.so/" + status["page_id"].replace("-", "")
    db.set_drama_notion_page_id(drama, "../../evil")
    assert ns.export_status(drama)["page_url"] is None


def _wait(job_id):
    for _ in range(400):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_job_error_text_is_fixed_and_token_free(notion, drama):
    notion.fail[("GET", "/databases/")] = [401]
    started = ns.start_export(drama)
    job = _wait(started["job_id"])
    assert job["status"] == "error"
    assert "refused the token" in job["error"] and TOKEN not in job["error"]


# --- routes ------------------------------------------------------------------------

@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def test_routes_round_trip(client, notion, drama):
    cfg = client.get("/api/notion/config")
    assert cfg.status_code == 200 and TOKEN not in cfg.text and cfg.json()["token_configured"]
    r = client.post("/api/notion/config", json={"target_id": f"https://www.notion.so/x-{DB_HEX}"})
    assert r.status_code == 200 and r.json()["target_id"] == DB_ID
    assert client.post("/api/notion/test", json={}).json()["target_title"] == "Dramas"
    assert client.get(f"/api/notion/dramas/{drama}").json()["page_url"] is None
    r = client.post(f"/api/notion/dramas/{drama}/export", json={"field": "en"})
    assert r.status_code == 200 and r.json() == {"job_id": f"notion_export_{drama}"}
    assert _wait(r.json()["job_id"])["status"] == "done"
    status = client.get(f"/api/notion/dramas/{drama}").json()
    assert status["page_url"].startswith("https://www.notion.so/")


def test_token_routes_use_the_key_write_gate(notion):
    closed = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                        headers={"X-Baihe-Local": "1"})
    assert closed.post("/api/notion/token", json={"value": TOKEN, "confirm": True}).status_code == 403
    assert closed.post("/api/notion/token/clear", json={"confirm": True}).status_code == 403
    opened = TestClient(create_app(ApiSettings(allow_key_writes=True)),
                        raise_server_exceptions=False, headers={"X-Baihe-Local": "1"})
    assert opened.post("/api/notion/token", json={"value": TOKEN}).status_code == 422  # no confirm
    r = opened.post("/api/notion/token", json={"value": TOKEN, "confirm": True})
    assert r.status_code == 200 and TOKEN not in r.text and r.json()["token_configured"] is True
    bad = opened.post("/api/notion/token", json={"value": "nope", "confirm": True})
    assert bad.status_code == 422 and "nope" not in bad.text
    r = opened.post("/api/notion/token/clear", json={"confirm": True})
    assert r.json()["token_configured"] is False


def test_routes_validation(client, notion, drama):
    assert client.post("/api/notion/config", json={"token": TOKEN}).status_code == 422
    assert client.post("/api/notion/config", json={"target_type": "wiki"}).status_code == 422
    assert client.post("/api/notion/config", json={"target_id": "nope"}).status_code == 422
    assert client.post("/api/notion/dramas/99999/export", json={}).status_code == 404
    assert client.post(f"/api/notion/dramas/{drama}/export",
                       json={"field": "exe"}).status_code == 422


def test_every_notion_route_is_pc_only(isolated_db, notion, drama):
    from api.auth import iter_route_declarations
    app = create_app(ApiSettings())
    notion_routes = [(path, decls) for _r, path, _m, decls in iter_route_declarations(app)
                     if path.startswith("/api/notion")]
    assert len(notion_routes) == 7
    assert all(decls == [("local_only", None)] for _p, decls in notion_routes)
    # With sign-in on, a remote device is refused before anything runs.
    remote = TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False)),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    assert remote.get("/api/notion/config").status_code in (401, 403)
    r = remote.post(f"/api/notion/dramas/{drama}/export", json={"field": "en"},
                    headers={"X-Baihe-Local": "1"})
    assert r.status_code in (401, 403)
    assert background_jobs.get_status(f"notion_export_{drama}") is None
