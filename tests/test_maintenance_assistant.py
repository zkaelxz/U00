"""Step 42: the in-app AI maintenance assistant, read-only v1
(services/maintenance_assistant_service.py, api/routers/assistant_routes.py).
The engine is a scripted fake: no network, no real model, no real key."""
import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import action_tiers
import applog
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, maintenance_assistant_service as svc

LOCAL = "http://127.0.0.1:8600"
REMOTE = "https://baihe.example.com"
FAKE_KEY = "sk-ant-api03-" + "A" * 40


def _client(**kw):
    return TestClient(create_app(ApiSettings(**kw)), base_url=LOCAL,
                      client=("127.0.0.1", 50000), raise_server_exceptions=False)


class ScriptedChat:
    """Replays canned model replies; records every message list it saw."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, system_prompt, messages, engine):
        self.seen.append((system_prompt, [dict(m) for m in messages]))
        return self.replies.pop(0)


@pytest.fixture
def dev_mode(isolated_db):
    svc.set_settings({"developer_mode": True})


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(svc, "build_engine", lambda name=None, model=None: (object(), "claude", None))


# --- read-only is structural --------------------------------------------------

WRITE_SHAPED = ("write", "edit", "apply", "patch", "commit", "push", "checkout", "delete",
                "remove", "reset", "install", "set_", "save", "create", "move", "rename")


def test_no_write_tool_exists():
    for name in svc.READ_ONLY_TOOLS:
        assert not any(w in name for w in WRITE_SHAPED), name
    assert svc.list_tools()["write_tools"] == []


def test_every_tool_is_green_tier():
    assert set(svc.TOOL_ACTIONS) == set(svc.READ_ONLY_TOOLS)
    for name, action in svc.TOOL_ACTIONS.items():
        assert action_tiers.classify_action(action) is action_tiers.ActionTier.GREEN, name
    assert all(t["tier"] == "green" for t in svc.list_tools()["tools"])


@pytest.mark.parametrize("name", ["write_file", "apply_patch", "git_commit", "git_push",
                                  "delete_drama", None, 42])
def test_unknown_tool_is_refused_and_runs_nothing(name):
    out = svc.run_tool(name, {"path": "x.py", "content": "boom"})
    assert out["ok"] is False and "No such tool" in out["output"]


def test_git_runs_only_read_commands(monkeypatch):
    seen = []

    class P:
        returncode, stdout, stderr = 0, "ok", ""

    monkeypatch.setattr(svc.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or P())
    for tool in ("git_status", "git_log", "git_diff"):
        assert svc.run_tool(tool, {})["ok"]
    subcommands = {c[c.index("--no-optional-locks") + 1] for c in seen}
    assert subcommands == {"status", "log", "diff"}
    assert all("shell" not in c for c in seen)


# --- diagnosis grounded in real log data (exit condition 1) -------------------

def test_ask_is_grounded_in_the_real_log(dev_mode, fake_engine, monkeypatch):
    monkeypatch.setattr(applog, "tail", lambda n: [
        "2026-09-29 INFO transcribe started drama 3",
        f"2026-09-29 ERROR bilibili download failed: HTTP 412 key={FAKE_KEY}",
    ])
    chat = ScriptedChat(
        'Let me look.\nTOOL: {"id": "t1", "name": "inspect_logs", "args": {"keyword": "bilibili"}}',
        "The Bilibili download failed with HTTP 412 (see the log).\n"
        "BACKLOG: bug | Bilibili downloader gets HTTP 412",
    )
    out = svc.ask("Why did the Bilibili downloader stop working?", chat=chat)
    # The tool result the model saw was the real (redacted) log line, keyed by its id.
    tool_msg = chat.seen[1][1][-1]["content"]
    assert tool_msg.startswith("RESULT t1 (inspect_logs, ok)")
    assert "HTTP 412" in tool_msg and FAKE_KEY not in tool_msg
    assert "transcribe started" not in tool_msg  # keyword filter applied
    assert out["answer"].startswith("The Bilibili download failed")
    assert "BACKLOG:" not in out["answer"]
    assert out["suggested_backlog"] == [{"kind": "bug", "text": "Bilibili downloader gets HTTP 412"}]
    assert out["tool_calls"] == [{"id": "t1", "name": "inspect_logs", "args": {"keyword": "bilibili"},
                                  "ok": True, "summary": out["tool_calls"][0]["summary"]}]
    assert out["proposed_patches"] == []


def test_model_asking_for_a_write_tool_gets_refused_and_nothing_changes(dev_mode, fake_engine,
                                                                        tmp_path, monkeypatch):
    before = {p: os.path.getmtime(p) for p in (svc.__file__,)}
    chat = ScriptedChat(
        'TOOL: {"id": "w1", "name": "write_file", "args": {"path": "db.py", "content": ""}}',
        "I can't edit files; here is a patch instead.\n```diff\n--- a/db.py\n+++ b/db.py\n"
        "@@ -1,1 +1,1 @@\n-old\n+new\n```",
    )
    out = svc.ask("Fix db.py", chat=chat)
    assert "No such tool" in chat.seen[1][1][-1]["content"]
    assert out["tool_calls"][0]["ok"] is False
    assert out["proposed_patches"][0]["files"] == ["db.py"]
    assert "+new" in out["proposed_patches"][0]["patch"]
    assert {p: os.path.getmtime(p) for p in before} == before


def test_tool_rounds_are_bounded(dev_mode, fake_engine):
    loop = 'TOOL: {"id": "t", "name": "git_status", "args": {}}'
    chat = ScriptedChat(*([loop] * svc.MAX_ROUNDS))
    out = svc.ask("loop forever", chat=chat)
    assert len(chat.seen) == svc.MAX_ROUNDS
    assert "ran out of tool rounds" in out["answer"]


def test_ask_refused_when_developer_mode_off(isolated_db, fake_engine):
    with pytest.raises(svc.ConflictError):
        svc.ask("hi", chat=ScriptedChat("x"))


# --- path safety ------------------------------------------------------------

@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "C:/Windows/win.ini", ".env",
                                  ".git/config", "library/library.db", "node_modules/x/index.js",
                                  "foo/../../x", "secrets.json", "api_server.log"])
def test_read_file_refuses_outside_or_secret_paths(path):
    out = svc.run_tool("read_file", {"path": path})
    assert out["ok"] is False
    assert "passwd" not in out["output"]


def test_read_and_search_real_code():
    out = svc.run_tool("read_file", {"path": "services/maintenance_assistant_service.py",
                                     "start": 1, "end": 3})
    assert out["ok"] and "1: " in out["output"]
    out = svc.run_tool("search_code", {"query": "READ_ONLY_TOOLS = {", "path": "services"})
    assert out["ok"] and "services/maintenance_assistant_service.py:" in out["output"]
    listing = svc.run_tool("list_files", {"path": ""})["output"]
    assert "services/" in listing and ".git/" not in listing and "library/" not in listing


def test_run_tests_only_takes_one_test_file(monkeypatch):
    for bad in ("tests", "tests/../db.py", "db.py", "tests/test_x.py -k x", "--help"):
        assert svc.run_tool("run_tests", {"path": bad})["ok"] is False


# --- backlog persists (exit condition 3) -------------------------------------

def test_backlog_add_list_delete_clear(isolated_db):
    a = svc.add_backlog_item("bug", "Stage stepper stuck on Diarize")
    b = svc.add_backlog_item("feature", f"Track releases token={FAKE_KEY}")
    items = svc.list_backlog()["items"]
    assert [i["id"] for i in items] == [b["id"], a["id"]]
    assert items[1]["text"] == "Stage stepper stuck on Diarize" and items[1]["kind"] == "bug"
    assert FAKE_KEY not in items[0]["text"]
    with pytest.raises(svc.InvalidInputError):
        svc.delete_backlog_item(a["id"])
    assert svc.delete_backlog_item(a["id"], confirm=True) == {"deleted": True}
    with pytest.raises(svc.NotFoundError):
        svc.delete_backlog_item(a["id"], confirm=True)
    assert svc.clear_backlog(confirm=True) == {"deleted": 1}
    assert svc.list_backlog() == {"items": []}


# --- settings ------------------------------------------------------------------

def test_developer_mode_off_by_default_and_validated(isolated_db):
    s = svc.get_settings()
    assert s["developer_mode"] is False and "claude" in s["engine_choices"]
    assert "deepl" not in s["engine_choices"]
    with pytest.raises(svc.InvalidInputError):
        svc.set_settings({"developer_mode": "yes"})
    with pytest.raises(svc.InvalidInputError):
        svc.set_settings({"engine": "deepl"})
    with pytest.raises(svc.InvalidInputError):
        svc.set_settings({"developer_mode": True, "bogus": 1})
    assert svc.get_settings()["developer_mode"] is False  # a bad batch changes nothing
    assert svc.set_settings({"developer_mode": True, "engine": "ollama"})["engine"] == "ollama"


# --- changelog -------------------------------------------------------------------

def test_changelog_feeds_the_commit_range_to_the_model(dev_mode, fake_engine, monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "_git", lambda *a: calls.append(a) or
                        "abc123 2026-09-29 Fix stage stepper\n\n---\ndef456 2026-09-28 Add X\n\n---\n")
    chat = ScriptedChat("## Fixed\n- The stage stepper no longer sticks.")
    out = svc.changelog("v1.0", "HEAD", chat=chat)
    assert out["commit_count"] == 2 and "stepper" in out["changelog"]
    assert "v1.0..HEAD" in calls[0]
    for bad in ("--output=/tmp/x", "a..b", "x y", ""):
        with pytest.raises(svc.InvalidInputError):
            svc.changelog(bad)


# --- API -------------------------------------------------------------------------

def test_api_flow(isolated_db, fake_engine, monkeypatch):
    c = _client()
    assert c.get("/api/assistant/settings").json()["developer_mode"] is False
    r = c.post("/api/assistant/ask", json={"question": "hi"})
    assert r.status_code == 409
    assert c.post("/api/assistant/settings", json={"developer_mode": True}).status_code == 200
    monkeypatch.setattr(svc, "_chat", ScriptedChat("All good."))
    r = c.post("/api/assistant/ask", json={"question": "hi", "chat_history": []})
    assert r.status_code == 200 and r.json()["answer"] == "All good."
    r = c.post("/api/assistant/backlog", json={"kind": "note", "text": "later"})
    item = r.json()
    assert c.get("/api/assistant/backlog").json()["items"] == [item]
    assert c.post(f"/api/assistant/backlog/{item['id']}/delete",
                  json={"confirm": True}).json() == {"deleted": True}
    assert c.get("/api/assistant/tools").json()["write_tools"] == []


def test_api_is_pc_only_even_for_admin(isolated_db):
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"], "pytest", "203.0.113.9")
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}
    for method, path, body in (("get", "/api/assistant/settings", None),
                               ("get", "/api/assistant/backlog", None),
                               ("get", "/api/assistant/tools", None),
                               ("post", "/api/assistant/ask", {"question": "x"}),
                               ("post", "/api/assistant/settings", {"developer_mode": True})):
        kw = {"headers": h} | ({"json": body} if body is not None else {})
        assert getattr(remote, method)(path, **kw).status_code == 403, path
    assert svc.get_settings()["developer_mode"] is False


def test_api_answer_never_carries_a_key(dev_mode, fake_engine, monkeypatch):
    monkeypatch.setattr(svc, "_chat", ScriptedChat(f"Your key is {FAKE_KEY}"))
    r = _client().post("/api/assistant/ask", json={"question": "what's my key?"})
    assert r.status_code == 200 and FAKE_KEY not in r.text
    assert json.loads(r.text)["answer"]
