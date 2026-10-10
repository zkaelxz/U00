"""Deliver a maintenance-assistant proposed fix as a GitHub PR
(services/assistant_github_service.py, api/routers/assistant_github_routes.py).
GitHub is a fake that records every request: no network, no real token."""
import base64
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import action_tiers
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from lib import http
from services import assistant_github_service as gh, auth_service, settings_service

TOKEN = "ghp_" + "Z" * 36
LOCAL = "http://127.0.0.1:8600"
REMOTE = "https://baihe.example.com"
BASE_FILE = ("def plan(lines):\n    for line in lines:\n        if not line.speaker:\n"
             "            continue\n        yield line\n")
PATCH = ("--- a/services/dub_service.py\n+++ b/services/dub_service.py\n"
         "@@ -2,3 +2,3 @@\n     for line in lines:\n-        if not line.speaker:\n"
         "+        if not line.speaker and not default_voice:\n             continue\n")


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    headers = {}

    def iter_content(self, size):
        yield json.dumps(self._body).encode()

    def close(self):
        pass


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.files = {"services/dub_service.py": BASE_FILE}
        self.trees = []

    def request(self, method, url, ip="unset", headers=None, json=None, params=None, timeout=None):
        assert timeout and ip is None  # ip None: guard=None, a fixed vendor URL
        assert url.startswith("https://api.github.com/") and TOKEN not in url
        path = url[len("https://api.github.com"):]
        self.calls.append((method, path, json, headers))
        if method == "GET" and path == "/repos/me/app":
            return Resp(200, {"default_branch": "main", "permissions": {"push": True}})
        if method == "GET" and path.startswith("/repos/me/app/branches/"):
            return Resp(200, {})
        if method == "GET" and path == "/repos/me/app/git/ref/heads/baihe-subtitler":
            return Resp(200, {"object": {"sha": "base111"}})
        if method == "GET" and path == "/repos/me/app/git/commits/base111":
            return Resp(200, {"tree": {"sha": "tree111"}})
        if method == "GET" and path == "/repos/me/app/git/trees/tree111":
            return Resp(200, {"tree": [{"path": p, "mode": "100644", "type": "blob"}
                                       for p in self.files]})
        if method == "GET" and path.startswith("/repos/me/app/contents/"):
            p = path[len("/repos/me/app/contents/"):]
            if p not in self.files:
                return Resp(404, {"message": "Not Found"})
            return Resp(200, {"type": "file", "encoding": "base64",
                              "content": base64.b64encode(self.files[p].encode()).decode()})
        if method == "POST" and path == "/repos/me/app/git/trees":
            self.trees.append(json)
            return Resp(201, {"sha": "tree222"})
        if method == "POST" and path == "/repos/me/app/git/commits":
            return Resp(201, {"sha": "commit222"})
        if method == "POST" and path == "/repos/me/app/git/refs":
            return Resp(201, {})
        if method == "POST" and path == "/repos/me/app/pulls":
            return Resp(201, {"number": 7, "html_url": "https://github.com/me/app/pull/7"})
        return Resp(500, {"message": f"unexpected {method} {path} token={TOKEN}"})


def _serve(monkeypatch, fn):
    """Route lib.http's connection step to `fn(method, url, ip=, headers=, timeout=, **requests_kwargs)`,
    passing on exactly what lib.http handed pinned_get."""
    monkeypatch.setattr(http, "pinned_get", lambda url, ip, headers, timeout=None, method="GET", **kw:
                        fn(method, url, ip=ip, headers=headers, timeout=timeout, **kw))


@pytest.fixture
def env(isolated_db, tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "default_env_path", lambda: str(path))
    monkeypatch.delenv("BAIHE_GITHUB_TOKEN", raising=False)
    return path


@pytest.fixture
def fake(monkeypatch):
    f = FakeGitHub()
    _serve(monkeypatch, f.request)
    return f


@pytest.fixture
def ready(env, fake):
    gh.set_token(TOKEN)
    gh.set_settings({"enabled": True, "repo": "me/app"})
    return fake


# --- inert until enabled with a token (exit 2) ------------------------------------

def test_off_by_default_and_no_calls_without_enable_or_token(env, fake):
    st = gh.get_status()
    assert st["enabled"] is False and st["token_configured"] is False
    assert st["base_branch"] == "baihe-subtitler"
    sha = gh.patch_sha256(PATCH)
    for fn in (gh.test_connection, lambda: gh.preview(PATCH, "Fix"),
               lambda: gh.deliver(PATCH, "Fix", sha256=sha, confirm=True)):
        with pytest.raises(gh.ConflictError):
            fn()
    gh.set_settings({"enabled": True, "repo": "me/app"})  # enabled, but no token
    with pytest.raises(gh.ConflictError):
        gh.test_connection()
    gh.set_settings({"enabled": False})
    gh.set_token(TOKEN)  # token, but disabled
    with pytest.raises(gh.ConflictError):
        gh.deliver(PATCH, "Fix", sha256=sha, confirm=True)
    assert fake.calls == []


# --- a proposed fix becomes a new branch + draft PR, never a push to base (exit 1) --

def test_deliver_creates_a_new_branch_and_a_draft_pr(ready):
    prev = gh.preview(PATCH, "Dub: keep lines with a default voice")
    assert ready.calls == []  # preview makes no network call
    assert prev["files"] == [{"path": "services/dub_service.py", "change": "modify"}]
    assert prev["patch"] == PATCH and prev["base_branch"] == "baihe-subtitler"
    out = gh.deliver(PATCH, prev["title"], "Found by the assistant.", sha256=prev["sha256"],
                     confirm=True)
    assert out["pr_url"] == "https://github.com/me/app/pull/7"
    assert out["branch"].startswith("baihe-assistant/dub-keep-lines-with-a-default-voice-")
    methods = [(m, p) for m, p, _j, _h in ready.calls]
    # The only writes: a tree, a commit, ONE new ref, the PR. No ref is ever updated.
    assert [(m, p) for m, p in methods if m != "GET"] == [
        ("POST", "/repos/me/app/git/trees"), ("POST", "/repos/me/app/git/commits"),
        ("POST", "/repos/me/app/git/refs"), ("POST", "/repos/me/app/pulls")]
    assert not any(m in ("PATCH", "PUT", "DELETE") for m, _p in methods)
    ref = next(j for m, p, j, _h in ready.calls if p.endswith("/git/refs"))
    assert ref["ref"] == f"refs/heads/{out['branch']}" and "baihe-subtitler" not in ref["ref"]
    pr = next(j for m, p, j, _h in ready.calls if p.endswith("/pulls"))
    assert pr["draft"] is True and pr["base"] == "baihe-subtitler" and pr["head"] == out["branch"]
    # The committed content is exactly the base file with the patch applied.
    assert ready.trees[0]["tree"][0]["content"] == BASE_FILE.replace(
        "if not line.speaker:", "if not line.speaker and not default_voice:")
    # The token only ever travels in the Authorization header.
    assert all(h["Authorization"] == f"Bearer {TOKEN}" for *_x, h in ready.calls)
    assert all(TOKEN not in json.dumps(j or {}) for _m, _p, j, _h in ready.calls)


def test_deliver_needs_confirm_and_the_previewed_sha(ready):
    sha = gh.patch_sha256(PATCH)
    with pytest.raises(gh.InvalidInputError):
        gh.deliver(PATCH, "Fix", sha256=sha, confirm=False)
    changed = PATCH.replace("default_voice", "other_voice")
    with pytest.raises(gh.ConflictError):
        gh.deliver(changed, "Fix", sha256=sha, confirm=True)
    assert ready.calls == []


def test_delivery_is_a_red_tier_action():
    assert action_tiers.classify_action("publish_to_shared_target") is action_tiers.ActionTier.RED
    assert action_tiers.classify_action(
        "modify_code", touches_paths=["services/assistant_github_service.py"]) \
        is action_tiers.ActionTier.RED


def test_a_stale_patch_is_refused_before_any_write(ready):
    ready.files["services/dub_service.py"] = "something else entirely\n"
    with pytest.raises(gh.ConflictError):
        gh.deliver(PATCH, "Fix", sha256=gh.patch_sha256(PATCH), confirm=True)
    assert all(m == "GET" for m, *_ in ready.calls)


def test_redirect_is_not_followed(ready, monkeypatch):
    calls = []

    class Redirect(Resp):
        headers = {"Location": "https://evil.example/"}

    def redirect(method, url, **kw):
        calls.append(url)
        return Redirect(302, {})
    _serve(monkeypatch, redirect)
    with pytest.raises(gh.ServiceError) as e:
        gh.test_connection()
    assert "GitHub said 302" in e.value.message
    assert len(calls) == 1 and "evil.example" not in calls[0]


def test_errors_never_carry_the_token(ready, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"connection reset for Authorization: Bearer {TOKEN}")
    _serve(monkeypatch, boom)
    with pytest.raises(gh.ServiceError) as e:
        gh.test_connection()
    assert TOKEN not in e.value.message


def test_github_error_body_is_scrubbed(ready):
    ready.files = {}
    ready_calls_before = len(ready.calls)
    with pytest.raises(gh.ServiceError) as e:
        gh._call(TOKEN, "GET", "/repos/me/app/nope")
    assert TOKEN not in e.value.message and len(ready.calls) == ready_calls_before + 1


def test_only_repo_paths_are_called(ready):
    with pytest.raises(gh.ServiceError):
        gh._call(TOKEN, "GET", "https://evil.example/x")
    with pytest.raises(gh.ServiceError):
        gh._call(TOKEN, "DELETE", "/orgs/me")
    assert ready.calls == []


def test_settings_are_validated(env):
    with pytest.raises(gh.InvalidInputError):
        gh.set_settings({"base_branch": "baihe-assistant/x"})
    with pytest.raises(gh.InvalidInputError):
        gh.set_settings({"repo": "not a repo"})
    with pytest.raises(gh.InvalidInputError):
        gh.set_settings({"enabled": "yes"})
    assert gh.set_settings({"base_branch": "main"})["base_branch"] == "main"


# --- the patch applier ------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "--- a/../etc/passwd\n+++ b/../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n",
    "--- a/.git/config\n+++ b/.git/config\n@@ -1 +1 @@\n-a\n+b\n",
    "--- /etc/passwd\n+++ /etc/passwd\n@@ -1 +1 @@\n-a\n+b\n",
    "--- a/x.py\n+++ b/y.py\n@@ -1 +1 @@\n-a\n+b\n",
    "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n-a\n+b\n",
    "just text",
])
def test_bad_patches_are_refused(bad):
    with pytest.raises(gh.InvalidInputError):
        gh.parse_patch(bad)


def test_apply_add_delete_offset_and_no_newline():
    add = gh.parse_patch("--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n")[0]
    assert gh.change_kind(add) == "add" and gh.apply_file_patch(None, add) == "a\nb\n"
    dele = gh.parse_patch("--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n")[0]
    assert gh.apply_file_patch("a\nb\n", dele) is None
    moved = gh.parse_patch("--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n x\n-y\n+Y\n")[0]
    assert gh.apply_file_patch("new top\nx\ny\n", moved) == "new top\nx\nY\n"  # found by content
    noeol = gh.parse_patch("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n"
                           "+b\n\\ No newline at end of file\n")[0]
    assert gh.apply_file_patch("a", noeol) == "b"
    with pytest.raises(gh.ConflictError):
        gh.apply_file_patch("q\nq\n", gh.parse_patch(
            "--- a/x.py\n+++ b/x.py\n@@ -5 +5 @@\n-z\n+Z\n")[0])


def test_adding_a_file_that_exists_is_refused(ready):
    patch = "--- /dev/null\n+++ b/services/dub_service.py\n@@ -0,0 +1 @@\n+x\n"
    with pytest.raises(gh.ConflictError):
        gh.deliver(patch, "Fix", sha256=gh.patch_sha256(patch), confirm=True)
    assert all(m == "GET" for m, *_ in ready.calls)


# --- API ----------------------------------------------------------------------------

def _client(**kw):
    return TestClient(create_app(ApiSettings(allow_key_writes=True, **kw)), base_url=LOCAL,
                      client=("127.0.0.1", 50000), raise_server_exceptions=False)


def test_api_flow_and_token_never_returned(env, fake):
    c = _client()
    r = c.post("/api/assistant/github/token", json={"value": TOKEN, "confirm": True})
    assert r.status_code == 200 and r.json() == {"token_configured": True}
    assert TOKEN not in r.text
    assert c.post("/api/assistant/github/settings",
                  json={"enabled": True, "repo": "me/app"}).status_code == 200
    st = c.get("/api/assistant/github")
    assert st.json()["token_configured"] is True and TOKEN not in st.text
    prev = c.post("/api/assistant/github/preview", json={"patch": PATCH, "title": "Fix"}).json()
    r = c.post("/api/assistant/github/deliver", json={
        "patch": PATCH, "title": "Fix", "body": "", "sha256": prev["sha256"], "confirm": True})
    assert r.status_code == 200 and r.json()["pr_number"] == 7 and TOKEN not in r.text
    assert c.post("/api/assistant/github/token/clear",
                  json={"confirm": True}).json() == {"token_configured": False}


def test_token_write_needs_the_key_write_gate(env):
    c = TestClient(create_app(ApiSettings(allow_key_writes=False)), base_url=LOCAL,
                   client=("127.0.0.1", 50000), raise_server_exceptions=False)
    r = c.post("/api/assistant/github/token", json={"value": TOKEN, "confirm": True})
    assert r.status_code == 403 and not env.exists()


def test_every_github_route_is_pc_only(env, fake):
    app = create_app(ApiSettings(auth_mode="on", allow_key_writes=True))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"], "pytest", "203.0.113.9")
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    assert remote.get("/api/assistant/github", headers=h).status_code == 403
    for path, body in (("/api/assistant/github/settings", {"enabled": True}),
                       ("/api/assistant/github/token", {"value": TOKEN, "confirm": True}),
                       ("/api/assistant/github/token/clear", {"confirm": True}),
                       ("/api/assistant/github/test", {}),
                       ("/api/assistant/github/preview", {"patch": PATCH, "title": "x"}),
                       ("/api/assistant/github/deliver", {"patch": PATCH, "title": "x",
                                                          "sha256": "0" * 64, "confirm": True})):
        assert remote.post(path, json=body, headers=h).status_code == 403, path
    assert fake.calls == [] and not env.exists()


# --- security review fixes ------------------------------------------------------------

@pytest.mark.parametrize("path", [".github/workflows/tests.yml", ".GitHub/x.yml", "a/.GIT/config"])
def test_ci_workflows_and_git_internals_are_refused(path):
    with pytest.raises(gh.InvalidInputError):
        gh.parse_patch(f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n")



@pytest.mark.parametrize("path", [
    ".claude/settings.json", ".claude/hooks/session-start.sh", "docs/.Claude/x.md",
    "CLAUDE.md", "claude.md", "services/CLAUDE.md", "CLAUDE.local.md",
    "run_tests.py", "RUN_TESTS.PY", "tests/conftest.py", "a/b/ConfTest.py",
    "requirements.txt", "requirements-core.txt", "Requirements-Optional.txt",
    "constraints.txt", "pytest.ini", "setup.cfg", "pyproject.toml", "start.bat",
    "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts",
    "frontend/vitest.config.mts", "frontend/Playwright.config.ts", "frontend/eslint.config.js",
    ".github/dependabot.yml",
    "frontend/.oxlintrc.json", "frontend/tsconfig.json", "frontend/tsconfig.app.json",
    "frontend/tsconfig.node.json", "frontend/TSConfig.build.json",
    "frontend/scripts/check.mjs", "frontend/scripts/sub/x.js",
    ".npmrc", "frontend/.npmrc", "check_setup.py", "Check_Setup.py",
    "constraints-dev.txt", "Constraints.txt", "sitecustomize.py", "a/b/usercustomize.py",
    "pytest.ini", ".pytest.ini", "services/pytest.ini", "frontend/tox.ini", "sub/setup.cfg",
    "sub/pyproject.toml", ".mcp.json", "postcss.config.js", "frontend/postcss.config.cjs",
    ".postcssrc", "frontend/.postcssrc.json", "tests/CLAUDE.local.md",
])
def test_paths_that_run_before_review_are_refused(path):
    for patch in (f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n",
                  f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+b\n"):
        with pytest.raises(gh.InvalidInputError, match="runs before anyone reviews"):
            gh.parse_patch(patch)


@pytest.mark.parametrize("path", ["tests/test_x.py", "docs/claude-notes.md", "services/run_tests.py",
                                  "frontend/src/package.json", "frontend/src/vite.config.ts",
                                  "docs/requirements.txt", "frontend/src/App.tsx",
                                  "frontend/src/tsconfig.json", "frontend/src/scripts/x.js",
                                  "services/check_setup.py", "frontend/scripts.ts", "docs/constraints-notes.md",
                                  "services/.mcp.json"])
def test_ordinary_paths_with_similar_names_are_allowed(path):
    assert gh.parse_patch(f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-a\n+b\n")[0]["new"] == path

@pytest.mark.parametrize("ch", ["\u202e", "\u202a", "\u2066", "\u2069", "\u200b", "\u200d",
                                "\u2060", "\ufeff"])
def test_hidden_and_bidi_characters_in_a_hunk_are_refused(ch):
    for kind in ("+", "-", " "):
        with pytest.raises(gh.InvalidInputError, match="control character"):
            gh.parse_patch(f"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n{kind}a{ch}b\n"
                           + ("+c\n" if kind == "-" else "-c\n" if kind == "+" else ""))


@pytest.mark.parametrize("extra", ["Binary files a/x.png and b/x.png differ",
                                   "old mode 100644", "rename from x.py", "GIT binary patch",
                                   "some stray commentary"])
def test_unsupported_or_hidden_lines_are_refused(extra):
    patch = f"--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n{extra}\n"
    with pytest.raises(gh.InvalidInputError):
        gh.parse_patch(patch)


def test_plain_git_diff_headers_are_fine():
    patch = ("diff --git a/x.py b/x.py\nindex 111..222 100644\n--- a/x.py\n+++ b/x.py\n"
             "@@ -1 +1 @@\n-a\n+b\n")
    assert gh.parse_patch(patch)[0]["new"] == "x.py"


def test_context_free_inserts_and_out_of_order_hunks_are_refused():
    with pytest.raises(gh.ConflictError):
        gh.apply_file_patch("a\nb\n", gh.parse_patch("--- a/x.py\n+++ b/x.py\n@@ -9,0 +10 @@\n+z\n")[0])
    two = gh.parse_patch("--- a/x.py\n+++ b/x.py\n@@ -3 +3 @@\n-c\n+C\n@@ -1 +1 @@\n-a\n+A\n")[0]
    with pytest.raises(gh.ConflictError):  # the second hunk may not match before the first
        gh.apply_file_patch("a\nb\nc\n", two)


def test_a_hunk_far_from_its_header_line_is_refused():
    body = [f"l{i}" for i in range(60)]
    text = "\n".join(body) + "\n"
    # Header says line 1, the only match is at line 51: too far to trust.
    far = gh.parse_patch("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-l50\n+L50\n")[0]
    with pytest.raises(gh.ConflictError, match="50 lines away"):
        gh.apply_file_patch(text, far)
    # Within 20 lines (header line 31, match at line 51) it still applies.
    near = gh.parse_patch("--- a/x.py\n+++ b/x.py\n@@ -31 +31 @@\n-l50\n+L50\n")[0]
    assert gh.apply_file_patch(text, near) == text.replace("l50\n", "L50\n")


def test_confirmation_is_bound_to_repo_and_base(ready):
    prev = gh.preview(PATCH, "Fix")
    gh.set_settings({"base_branch": "main"})
    with pytest.raises(gh.ConflictError):
        gh.deliver(PATCH, "Fix", sha256=prev["sha256"], confirm=True)
    assert ready.calls == []


def test_dot_repo_names_are_refused(env):
    for bad in ("me/..", "me/."):
        with pytest.raises(gh.InvalidInputError):
            gh.set_settings({"repo": bad})


def test_add_under_an_existing_file_is_refused(ready):
    patch = "--- /dev/null\n+++ b/services/dub_service.py/x.py\n@@ -0,0 +1 @@\n+x\n"
    with pytest.raises(gh.ConflictError):
        gh.deliver(patch, "Fix", sha256=gh.patch_sha256(patch, "me/app", "baihe-subtitler"),
                   confirm=True)
    assert all(m == "GET" for m, *_ in ready.calls)


def test_failed_pr_names_the_branch_it_left(ready, monkeypatch):
    real = ready.request

    def no_draft(method, url, **kw):
        if url.endswith("/pulls"):
            return Resp(422, {"message": "Draft pull requests are not supported"})
        return real(method, url, **kw)

    _serve(monkeypatch, no_draft)
    prev = gh.preview(PATCH, "Fix")
    with pytest.raises(gh.ServiceError) as e:
        gh.deliver(PATCH, "Fix", sha256=prev["sha256"], confirm=True)
    assert "baihe-assistant/fix-" in e.value.message and "Draft pull requests" in e.value.message
    assert not any(m in ("PATCH", "PUT", "DELETE") for m, *_ in ready.calls)


def test_an_oversized_github_response_is_refused(monkeypatch):
    from services.service_errors import ServiceError

    class Endless:
        status_code = 200
        headers = {}
        closed = False

        def iter_content(self, size):
            while True:
                yield b"x" * size

        def close(self):
            Endless.closed = True
    monkeypatch.setattr(gh, "MAX_RESPONSE_BYTES", 1000)
    _serve(monkeypatch, lambda *a, **k: Endless())
    with pytest.raises(ServiceError, match="more data than expected"):
        gh._call("tok", "GET", "/user")
    assert Endless.closed
