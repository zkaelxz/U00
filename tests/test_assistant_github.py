"""Step 72: deliver a maintenance-assistant proposed fix as a GitHub PR
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

    def json(self):
        return self._body


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.files = {"services/dub_service.py": BASE_FILE}
        self.trees = []

    def request(self, method, url, headers=None, json=None, params=None, timeout=None,
                allow_redirects=True):
        assert timeout and not allow_redirects
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


@pytest.fixture
def env(isolated_db, tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "_default_env_path", lambda: str(path))
    monkeypatch.delenv("BAIHE_GITHUB_TOKEN", raising=False)
    return path


@pytest.fixture
def fake(monkeypatch):
    f = FakeGitHub()
    monkeypatch.setattr(gh.requests, "request", f.request)
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


def test_errors_never_carry_the_token(ready, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"connection reset for Authorization: Bearer {TOKEN}")
    monkeypatch.setattr(gh.requests, "request", boom)
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
