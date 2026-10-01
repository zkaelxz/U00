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

    def fake_git(*a):
        calls.append(a)
        if a[0] == "rev-list":
            return "3\n"
        return "abc123 2026-09-29 Fix stage stepper\ndef456 2026-09-28 Add X\n"

    monkeypatch.setattr(svc, "_git", fake_git)
    chat = ScriptedChat("## Fixed\n- The stage stepper no longer sticks.")
    out = svc.changelog("v1.0", "HEAD", chat=chat)
    assert out["commit_count"] == 3 and "stepper" in out["changelog"]
    assert out["truncated"] is True  # 3 in range, 2 reached the model
    assert all("v1.0..HEAD" in c for c in calls)
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


def test_changelog_refused_when_developer_mode_off(isolated_db):
    with pytest.raises(svc.ConflictError):
        svc.changelog("HEAD~1")


# --- review fixes: paths and patches survive redaction ----------------------------

def test_deep_paths_and_patches_are_not_mangled(dev_mode, fake_engine):
    out = svc.run_tool("search_code", {"query": "READ_ONLY_TOOLS = {", "path": ""})
    assert "services/maintenance_assistant_service.py:" in out["output"]
    out = svc.run_tool("read_file", {"path": "api/routers/assistant_routes.py", "start": 1,
                                     "end": 400})
    assert out["ok"] and '"/api/assistant"' in out["output"]
    patch = ("```diff\n--- a/api/routers/assistant_routes.py\n+++ b/api/routers/assistant_routes.py\n"
             "@@ -1,1 +1,1 @@\n-x = \"/api/assistant/ask\"\n+x = \"/api/assistant/ask2\"\n```")
    res = svc.ask("fix", chat=ScriptedChat(patch))
    assert res["proposed_patches"][0]["files"] == ["api/routers/assistant_routes.py"]
    assert '"/api/assistant/ask2"' in res["proposed_patches"][0]["patch"]


def test_absolute_repo_prefix_and_github_token_are_removed():
    root = os.path.realpath(svc.repo_root())
    text = svc._redact(f"at {root}/services/x.py token ghp_{'a' * 36} and {FAKE_KEY}")
    assert root not in text and "services/x.py" in text
    assert "ghp_" not in text and FAKE_KEY not in text


def test_untracked_files_are_not_readable(monkeypatch):
    monkeypatch.setattr(svc, "_tracked_files", lambda: {"db.py"})
    assert svc.run_tool("read_file", {"path": "db.py", "end": 2})["ok"] is True
    out = svc.run_tool("read_file", {"path": "qa.py", "end": 2})
    assert out["ok"] is False and "outside" in out["output"]
    assert "qa.py" not in svc.run_tool("list_files", {"path": ""})["output"]


@pytest.mark.parametrize("path", [":(glob)**/[.]env", "C:/x", "\\\\server\\x"])
def test_git_diff_path_refuses_magic_and_absolute(path):
    assert svc.run_tool("git_diff", {"path": path})["ok"] is False


def test_tool_ids_are_unique_across_the_question(dev_mode, fake_engine):
    chat = ScriptedChat(
        'TOOL: {"id": "t1", "name": "git_status", "args": {}}\n'
        'TOOL: {"id": "t1", "name": "list_files", "args": {}}',
        'TOOL: {"id": "t1", "name": "list_files", "args": {}}',
        "done",
    )
    out = svc.ask("x", chat=chat)
    ids = [c["id"] for c in out["tool_calls"]]
    assert ids == ["t1", "t1-2", "t1-3"]
    assert "RESULT t1-2 (list_files" in chat.seen[1][1][-1]["content"]


def test_only_one_test_run_per_question(dev_mode, fake_engine, monkeypatch):
    ran = []
    monkeypatch.setitem(svc.READ_ONLY_TOOLS, "run_tests",
                        (lambda a: ran.append(a) or "ok",) + svc.READ_ONLY_TOOLS["run_tests"][1:])
    call = 'TOOL: {"id": "r", "name": "run_tests", "args": {"path": "tests/test_db.py"}}'
    out = svc.ask("x", chat=ScriptedChat(call + "\n" + call, "done"))
    assert len(ran) == 1 and [c["ok"] for c in out["tool_calls"]] == [True, False]


def test_test_run_env_has_no_keys_and_uses_the_guard(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BAIHE_GITHUB_TOKEN", "x")
    env = svc._test_env()
    assert "ANTHROPIC_API_KEY" not in env and "BAIHE_GITHUB_TOKEN" not in env
    assert env["BAIHE_ASSISTANT_TEST_RUN"] == "1" and "PATH" in env
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "1 passed", ""

    monkeypatch.setattr(svc, "_tracked_files", lambda: None)
    monkeypatch.setattr(svc.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd, **kw) or P())
    assert svc.run_tool("run_tests", {"path": "tests/test_maintenance_assistant.py"})["ok"]
    assert "services.assistant_pytest_guard" in seen["cmd"]
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_pytest_guard_refuses_outside_a_test_run(monkeypatch):
    import importlib
    import sys
    monkeypatch.delenv("BAIHE_ASSISTANT_TEST_RUN", raising=False)
    sys.modules.pop("services.assistant_pytest_guard", None)
    with pytest.raises(RuntimeError):
        importlib.import_module("services.assistant_pytest_guard")


def test_assistant_code_is_a_protected_path():
    for path in ("services/maintenance_assistant_service.py", "api/routers/assistant_routes.py"):
        assert action_tiers.classify_action("modify_code", touches_paths=[path]) \
            is action_tiers.ActionTier.RED


def test_saved_model_applies_to_the_default_engine_and_resets_on_engine_change(isolated_db):
    svc.set_settings({"model": "claude-x"})
    assert svc.get_settings()["model"] == "claude-x"
    svc.set_settings({"engine": "ollama"})
    assert svc.get_settings()["model"] is None


def test_empty_history_turns_are_dropped():
    assert svc._check_history([{"role": "assistant", "content": "  "},
                               {"role": "user", "content": "hi"}]) == [{"role": "user", "content": "hi"}]


def test_non_finite_args_are_not_echoed():
    assert svc._shown_args({"n": float("nan"), "path": "db.py"}) == {"path": "db.py"}


def test_second_ask_while_one_runs_is_refused(dev_mode, fake_engine):
    assert svc._ASK_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(svc.ConflictError):
            svc.ask("x", chat=ScriptedChat("y"))
    finally:
        svc._ASK_LOCK.release()


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git not installed")
def test_real_git_read_tools_smoke():
    if not os.path.isdir(os.path.join(svc.repo_root(), ".git")):
        pytest.skip("not a git checkout")
    for tool, args in (("git_status", {}), ("git_log", {"n": 2}),
                       ("git_diff", {"ref": "HEAD", "stat": True}),
                       ("git_diff", {"ref": "HEAD", "path": "db.py"})):
        out = svc.run_tool(tool, args)
        assert out["ok"], (tool, out["output"])


# --- lead review: cloud consent and git_diff ---------------------------------------

@pytest.fixture
def real_build(monkeypatch):
    """The real build_engine, with the engine constructor and cap check faked."""
    from services import line_ai_service, reader_service
    built = []
    monkeypatch.setattr(reader_service, "_llm_engine", lambda name, model: built.append(name) or object())
    monkeypatch.setattr(line_ai_service, "refuse_if_over_monthly_cap", lambda *a: None)
    return built


def test_default_engine_is_local(dev_mode, real_build):
    out = svc.ask("x", chat=ScriptedChat("fine"))
    assert out["engine"] == "ollama" and real_build == ["ollama"]
    s = svc.get_settings()
    assert s["default_engine"] == "ollama" and "ollama" in s["local_engines"]
    assert all(s["cloud_consent"][e] is False for e in ("claude", "deepseek", "gemini"))


def test_cloud_engine_needs_saved_consent_for_ask_and_changelog(dev_mode, real_build, monkeypatch):
    with pytest.raises(svc.ConflictError) as e:
        svc.ask("x", engine_name="claude", chat=ScriptedChat("fine"))
    assert e.value.details == {"reason": "cloud_consent_required", "engine": "claude"}
    monkeypatch.setattr(svc, "_git", lambda *a: "1" if a[0] == "rev-list" else "abc 2026 x\n")
    with pytest.raises(svc.ConflictError):
        svc.changelog("HEAD~1", engine_name="gemini", chat=ScriptedChat("notes"))
    assert real_build == []  # refused before the engine is even built
    svc.set_settings({"cloud_consent": {"claude": True}})
    assert svc.ask("x", engine_name="claude", chat=ScriptedChat("fine"))["engine"] == "claude"
    with pytest.raises(svc.ConflictError):  # consent is per provider
        svc.ask("x", engine_name="gemini", chat=ScriptedChat("fine"))
    svc.set_settings({"cloud_consent": {"claude": False}})
    with pytest.raises(svc.ConflictError):
        svc.ask("x", engine_name="claude", chat=ScriptedChat("fine"))


def test_cloud_consent_is_validated(isolated_db):
    for bad in ({"deepl": True}, {"claude": "yes"}, ["claude"]):
        with pytest.raises(svc.InvalidInputError):
            svc.set_settings({"cloud_consent": bad})


def test_api_consent_409_then_pass(isolated_db, real_build, monkeypatch):
    c = _client()
    c.post("/api/assistant/settings", json={"developer_mode": True})
    monkeypatch.setattr(svc, "_chat", ScriptedChat("fine"))
    r = c.post("/api/assistant/ask", json={"question": "x", "engine": "claude"})
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "cloud_consent_required"
    r = c.post("/api/assistant/settings", json={"cloud_consent": {"claude": True}})
    assert r.json()["cloud_consent"]["claude"] is True
    assert c.post("/api/assistant/ask", json={"question": "x", "engine": "claude"}).status_code == 200


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git not installed")
def test_git_diff_without_a_path_shows_a_modified_tracked_file(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "mod.py").write_text("x = 1\n")
    run("add", "mod.py")
    run("commit", "-q", "-m", "init")
    (repo / "mod.py").write_text("x = 2\n")
    monkeypatch.setattr(svc, "repo_root", lambda: str(repo))
    out = svc.run_tool("git_diff", {})
    assert out["ok"] and "mod.py" in out["output"] and "+x = 2" in out["output"]
    assert "mod.py" in svc.run_tool("git_diff", {"stat": True})["output"]



@pytest.mark.parametrize("url,local", [
    (None, True), ("http://localhost:11434", True), ("http://127.0.0.1:11434", True),
    ("http://[::1]:11434", True), ("192.168.1.5:11434", False),
    ("http://ollama.example.com", False), ("http://127.0.0.1.evil.com", False)])
def test_ollama_is_local_only_on_loopback(isolated_db, monkeypatch, url, local):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: url)
    assert svc.cloud_consent_given("ollama") is local
    if not local:
        with pytest.raises(svc.ConflictError):
            svc.require_cloud_consent("ollama")


def test_ollama_consent_can_be_saved_for_a_remote_server(isolated_db, monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: "http://10.0.0.2:11434")
    monkeypatch.setattr(svc, "_get", lambda k, d=None: {"ollama": True} if k == "cloud_consent" else d)
    assert svc.cloud_consent_given("ollama") is True


def test_cloud_consent_must_be_a_real_boolean(isolated_db):
    c = _client()
    c.post("/api/assistant/settings", json={"developer_mode": True})
    for bad in ("true", 1, "yes"):
        r = c.post("/api/assistant/settings", json={"cloud_consent": {"claude": bad}})
        assert r.status_code == 422
    assert c.post("/api/assistant/settings", json={"cloud_consent": {"claude": True}}).status_code == 200


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git not installed")
def test_git_diff_without_a_path_hides_denied_files(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "mod.py").write_text("x = 1\n")
    (repo / "secrets.py").write_text("k = 1\n")
    run("add", ".")
    run("commit", "-q", "-m", "init")
    (repo / "secrets.py").write_text("k = 'hunter2'\n")
    monkeypatch.setattr(svc, "repo_root", lambda: str(repo))
    assert svc.run_tool("git_diff", {})["output"] == "No differences."
    (repo / "mod.py").write_text("x = 2\n")
    out = svc.run_tool("git_diff", {})["output"]
    assert "+x = 2" in out and "hunter2" not in out and "secrets.py" not in out
    assert "secrets.py" not in svc.run_tool("git_diff", {"stat": True})["output"]


@pytest.mark.parametrize("tok", [
    "ghp_" + "a" * 36, "gho_" + "B1" * 15, "ghu_" + "c" * 25, "ghs_" + "d" * 30,
    "ghr_" + "e" * 30, "github_pat_" + "11AB_" * 8])
def test_redact_secrets_strips_github_tokens(tok):
    from translate_engines import redact_secrets
    out = redact_secrets(f"clone https://x:{tok}@github.com failed")
    assert tok not in out and "[REDACTED]" in out
    assert redact_secrets("ghp_short and github_pat_ok") == "ghp_short and github_pat_ok"


def test_remote_ollama_consent_is_saved_and_shown(isolated_db, monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: "http://10.0.0.2:11434")
    s = svc.get_settings()
    assert "ollama" not in s["local_engines"] and s["cloud_consent"]["ollama"] is False
    with pytest.raises(svc.ConflictError):
        svc.require_cloud_consent("ollama")
    s = svc.set_settings({"cloud_consent": {"ollama": True}})
    assert s["cloud_consent"]["ollama"] is True
    svc.require_cloud_consent("ollama")
    # Back on this PC, Ollama is local again and needs no consent entry.
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: None)
    s = svc.get_settings()
    assert "ollama" in s["local_engines"] and "ollama" not in s["cloud_consent"]


# --- tiered escalation: user-triggered, consented, redacted --------------------------

@pytest.fixture
def keys(monkeypatch):
    """Which engines have a key; Ollama and the offline engine always do."""
    have = {"ollama", "fake"}
    monkeypatch.setattr(svc, "_key_set", lambda name: name in have)
    return have


def _ladder():
    return [t["engine"] for t in svc.tier_ladder()]


def test_default_ladder_order_skips_engines_without_keys(isolated_db, keys):
    assert _ladder() == ["ollama"]
    keys.update({"claude", "deepseek"})
    assert _ladder() == ["ollama", "claude"]
    keys.add("gemini")
    assert _ladder() == ["ollama", "gemini", "claude"]
    svc.set_settings({"engine": "deepseek"})  # the saved hosted engine is the last tier
    assert _ladder() == ["ollama", "gemini", "deepseek"]
    tiers = svc.get_settings()["tiers"]
    assert [t["tier"] for t in tiers] == [1, 2, 3]
    assert tiers[0]["local"] is True and tiers[1]["local"] is False and tiers[1]["consent"] is False


def test_saved_ladder_order_is_kept_and_skips_missing_keys(isolated_db, keys):
    keys.add("claude")
    s = svc.set_settings({"tiers": ["claude", "gemini", "ollama"]})
    assert s["tier_order"] == ["claude", "gemini", "ollama"]
    assert [t["engine"] for t in s["tiers"]] == ["claude", "ollama"]  # no Gemini key: skipped
    assert s["engine_keys"]["gemini"] is False and s["engine_keys"]["claude"] is True
    assert svc.set_settings({"tiers": None})["tier_order"] is None
    assert _ladder() == ["ollama", "claude"]


@pytest.mark.parametrize("bad", [["ollama", "ollama"], ["ollama", "gemini", "claude", "deepseek"],
                                 ["deepl"], [None], "ollama", [1]])
def test_tier_order_is_validated(isolated_db, bad):
    with pytest.raises(svc.InvalidInputError):
        svc.set_settings({"tiers": bad})


def test_settings_carry_key_booleans_never_key_values(isolated_db, monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: FAKE_KEY if k == "claude" else None)
    r = _client().get("/api/assistant/settings")
    assert r.status_code == 200 and FAKE_KEY not in r.text
    assert r.json()["engine_keys"]["claude"] is True
    assert [t["engine"] for t in r.json()["tiers"]] == ["ollama", "claude"]


def test_escalating_to_a_cloud_tier_needs_the_consent_flag(dev_mode, real_build, keys):
    keys.add("gemini")
    svc.set_settings({"cloud_consent": {"gemini": True}})
    chat = ScriptedChat("fine")
    with pytest.raises(svc.ConflictError) as e:
        svc.ask("x", engine_name="gemini", escalate=True, chat=chat)
    assert e.value.details == {"reason": "escalation_consent_required", "engine": "gemini"}
    assert real_build == [] and chat.seen == []  # nothing was built or sent
    out = svc.ask("x", engine_name="gemini", escalate=True, consent=True, chat=chat)
    assert out["engine"] == "gemini" and out["tier"] == 2 and out["local"] is False
    assert out["next_engine"] is None


def test_escalation_still_needs_the_saved_provider_consent(dev_mode, real_build, keys):
    keys.add("gemini")
    with pytest.raises(svc.ConflictError) as e:
        svc.ask("x", engine_name="gemini", escalate=True, consent=True, chat=ScriptedChat("y"))
    assert e.value.details["reason"] == "cloud_consent_required" and real_build == []


def test_escalation_only_to_a_tier_of_the_ladder(dev_mode, real_build, keys):
    svc.set_settings({"cloud_consent": {"claude": True}})
    for kw in ({"engine_name": "claude"}, {}):  # claude has no key here: not a tier
        with pytest.raises(svc.InvalidInputError):
            svc.ask("x", escalate=True, consent=True, chat=ScriptedChat("y"), **kw)
    with pytest.raises(svc.InvalidInputError):  # evidence only travels with an escalation
        svc.ask("x", evidence="RESULT t1", chat=ScriptedChat("y"))
    assert real_build == []


def test_answer_names_its_tier_and_hands_on_redacted_evidence(dev_mode, real_build, keys,
                                                              monkeypatch):
    keys.add("gemini")
    monkeypatch.setattr(svc.diagnostics_gaps_service, "get_log_tail",
                        lambda n, kw: [f"ERROR boom key={FAKE_KEY}"])
    chat = ScriptedChat('TOOL: {"id": "t1", "name": "inspect_logs", "args": {}}', "It's the key.")
    out = svc.ask("why?", chat=chat)
    assert (out["engine"], out["tier"], out["local"], out["next_engine"]) == ("ollama", 1, True, "gemini")
    assert "RESULT t1 (inspect_logs, ok)" in out["evidence"] and "ERROR boom" in out["evidence"]
    assert FAKE_KEY not in json.dumps(out)
    assert len(out["evidence"]) < svc.MAX_EVIDENCE_CHARS


def test_escalation_sends_the_redacted_thread_and_evidence(dev_mode, real_build, keys):
    keys.add("gemini")
    svc.set_settings({"cloud_consent": {"gemini": True}})
    root = os.path.realpath(svc.repo_root())
    history = [{"role": "user", "content": f"it failed at {root}/db.py with {FAKE_KEY}"},
               {"role": "assistant", "content": "Look at db.py."}]
    chat = ScriptedChat("Deeper answer.")
    out = svc.ask(f"still broken {FAKE_KEY}", history, engine_name="gemini", escalate=True,
                  consent=True, evidence=f"RESULT t1 (inspect_logs, ok):\nkey {FAKE_KEY}", chat=chat)
    sent = json.dumps(chat.seen)
    assert FAKE_KEY not in sent and root not in sent and "db.py" in sent
    last = chat.seen[0][1][-1]["content"]
    assert last.startswith("still broken") and "RESULT t1 (inspect_logs, ok)" in last
    assert len(chat.seen[0][1]) == 3  # two thread turns + the question
    assert out["tier"] == 2 and "RESULT t1" in out["evidence"]


def test_thread_and_evidence_are_bounded(dev_mode, keys):
    too_many = [{"role": "user", "content": "x"}] * (svc.MAX_CHAT_TURNS + 1)
    with pytest.raises(svc.InvalidInputError):
        svc.ask("x", too_many, chat=ScriptedChat("y"))
    with pytest.raises(svc.InvalidInputError):
        svc.ask("x", escalate=True, engine_name="ollama", evidence="e" * (svc.MAX_EVIDENCE_CHARS + 1),
                chat=ScriptedChat("y"))
    r = _client().post("/api/assistant/ask", json={"question": "x", "escalate": True,
                                                   "evidence": "e" * (svc.MAX_EVIDENCE_CHARS + 1)})
    assert r.status_code == 422


@pytest.mark.parametrize("raised, reason", [
    (RuntimeError(f"429 Client Error: Too Many Requests key={FAKE_KEY}"), "rate_limited"),
    (RuntimeError("HTTPConnectionPool: Max retries exceeded (Connection refused)"), "unreachable"),
    (RuntimeError(f"bad gateway {FAKE_KEY}"), "failed"),
])
def test_a_failed_tier_offers_the_next_without_calling_it(dev_mode, real_build, keys, monkeypatch,
                                                          raised, reason):
    import qa
    keys.add("gemini")
    engines = []

    def boom(system_prompt, messages, engine, max_tokens=0):
        engines.append(engine)
        raise raised

    monkeypatch.setattr(qa, "_dispatch_chat", boom)
    with pytest.raises(svc.ServiceError) as e:
        svc.ask("x")
    assert e.value.details == {"reason": reason, "engine": "ollama", "tier": 1,
                               "next_engine": "gemini"}
    assert FAKE_KEY not in e.value.message
    assert len(engines) == 1 and real_build == ["ollama"]  # Gemini was never built or called


def test_a_tier_with_no_key_is_reported_with_the_next_tier(dev_mode, keys, monkeypatch):
    from services import line_ai_service, translate_service
    monkeypatch.setattr(line_ai_service, "refuse_if_over_monthly_cap", lambda *a: None)
    keys.add("claude")
    svc.set_settings({"tiers": ["claude", "ollama"], "cloud_consent": {"claude": True}})
    # The key disappears between loading the page and asking.
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, *a: None)
    with pytest.raises(svc.DependencyUnavailableError) as e:
        svc.ask("x", engine_name="claude", chat=ScriptedChat("y"))
    assert e.value.details["reason"] == "unavailable" and e.value.details["next_engine"] == "ollama"


def test_a_used_up_spending_cap_is_reported_and_nothing_is_sent(dev_mode, keys, monkeypatch):
    from services import line_ai_service, reader_service

    def capped(*a):
        raise svc.UnsupportedOperationError("This month's spending cap ($5.00) is already used up.")

    monkeypatch.setattr(reader_service, "_llm_engine", lambda name, model: object())
    monkeypatch.setattr(line_ai_service, "refuse_if_over_monthly_cap", capped)
    chat = ScriptedChat("y")
    with pytest.raises(svc.UnsupportedOperationError) as e:
        svc.ask("x", chat=chat)
    assert e.value.details["reason"] == "spend_cap" and chat.seen == []


def test_api_escalation_consent_and_errors_carry_no_key(isolated_db, real_build, keys, monkeypatch):
    import qa
    keys.add("gemini")
    c = _client()
    c.post("/api/assistant/settings", json={"developer_mode": True, "cloud_consent": {"gemini": True}})
    real_chat = svc._chat
    monkeypatch.setattr(svc, "_chat", ScriptedChat("fine"))
    body = {"question": "x", "engine": "gemini", "escalate": True}
    r = c.post("/api/assistant/ask", json=body)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["reason"] == "escalation_consent_required"
    assert c.post("/api/assistant/ask", json=body | {"consent": "yes"}).status_code == 422
    r = c.post("/api/assistant/ask", json=body | {"consent": True})
    assert r.status_code == 200 and r.json()["tier"] == 2 and r.json()["engine"] == "gemini"
    monkeypatch.setattr(svc, "_chat", real_chat)

    def rate_limited(*a, **k):
        raise RuntimeError(f"429 Too Many Requests {FAKE_KEY}")

    monkeypatch.setattr(qa, "_dispatch_chat", rate_limited)
    r = c.post("/api/assistant/ask", json={"question": "x"})
    assert r.status_code == 500 and FAKE_KEY not in r.text
    assert r.json()["error"]["details"] == {"reason": "rate_limited", "engine": "ollama", "tier": 1,
                                            "next_engine": "gemini"}


def test_escalated_tier_is_still_read_only(dev_mode, real_build, keys):
    keys.add("gemini")
    svc.set_settings({"cloud_consent": {"gemini": True}})
    chat = ScriptedChat('TOOL: {"id": "w", "name": "write_file", "args": {"path": "db.py"}}', "ok")
    out = svc.ask("fix it", engine_name="gemini", escalate=True, consent=True, chat=chat)
    assert out["tool_calls"][0]["ok"] is False
    assert "No such tool" in chat.seen[1][1][-1]["content"]
    assert svc.list_tools()["write_tools"] == []


def test_developer_report_is_redacted_and_local(dev_mode, monkeypatch):
    root = os.path.realpath(svc.repo_root())
    monkeypatch.setattr(svc.diagnostics_gaps_service, "build_support_report",
                        lambda: "Python 3.x\nRecent errors: none")
    history = [{"role": "user", "content": f"crash in {root}/api/x.py key {FAKE_KEY}"},
               {"role": "assistant", "content": f"token ghp_{'a' * 36} looks wrong"}]
    out = svc.developer_report(history, question="and now?", evidence=f"RESULT t1:\n{FAKE_KEY}")
    text = out["report"]
    assert FAKE_KEY not in text and root not in text and "ghp_" not in text
    assert "api/x.py" in text and "You (not answered yet): and now?" in text
    assert "RESULT t1" in text and "Recent errors: none" in text and "not sent anywhere" in text


def test_developer_report_api_needs_developer_mode_and_the_pc(isolated_db, monkeypatch):
    monkeypatch.setattr(svc.diagnostics_gaps_service, "build_support_report", lambda: "ok")
    c = _client()
    assert c.post("/api/assistant/report", json={"chat_history": []}).status_code == 409
    c.post("/api/assistant/settings", json={"developer_mode": True})
    r = c.post("/api/assistant/report", json={"chat_history": [{"role": "user", "content": FAKE_KEY}]})
    assert r.status_code == 200 and FAKE_KEY not in r.text
    assert "== Support report ==" in r.json()["report"]
    remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                        raise_server_exceptions=False)
    assert remote.post("/api/assistant/report", json={}).status_code in (401, 403)


def test_source_failures_tool_is_redacted_and_bounded(isolated_db, monkeypatch):
    from sources import health, registry
    monkeypatch.setattr(registry, "adapter_classes", lambda: {"alpha": object})
    health.record_failure("alpha", "LAYOUT_CHANGED",
                          "https://a.invalid/x?key=sk-abcdefghijklmnopqrstuvwxyz0123456789",
                          now=1_700_000_000.0, base_backoff=10)
    health.record_failure("https://pasted.example", "SERVER_ERROR", "x", base_backoff=10)
    out = svc.run_tool("source_failures", {})
    assert out["ok"] is True
    assert out["output"] == "alpha: layout_changed x1, last seen 2023-11-14 22:13 UTC"
    health.reset("alpha")
    health.record_success("alpha", 0.1)
    assert svc.run_tool("source_failures", {})["output"] == "No recent source failures."
