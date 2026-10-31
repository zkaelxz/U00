"""A signed-in member's own Ollama key (services/member_key_service.py,
api/routers/member_key_routes.py, engine_backends/local.py). Mocked HTTP
only: no network, no real key."""
import json
import sqlite3
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from fastapi.testclient import TestClient

import db
import member_keys
import translate_engines as te
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from engine_backends import local
from services import auth_service, library_backup_service, member_key_service, settings_service
from services import ownership_service
from services.service_errors import InvalidInputError

KEY = "0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOpQrStUvWx"
OTHER_KEY = "ollama_zyxwvutsrqponmlkjihgfedcba98"
CLOUD = "gemma4:31b-cloud"
REMOTE = "https://baihe.example.com"


@pytest.fixture
def env(isolated_db, tmp_path, monkeypatch):
    """A private .env, so the server secret never lands in the checkout's."""
    path = str(tmp_path / ".env")
    monkeypatch.setattr(settings_service, "default_env_path", lambda: path)
    monkeypatch.delenv(member_key_service.SECRET_ENV, raising=False)
    return path


def _member(email):
    u = auth_service.add_user(email)
    s = auth_service.create_session(u["id"], "pytest", "198.51.100.4")
    return u, {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
               api_auth.CSRF_HEADER: s["csrf_token"]}


def _admin(email="admin@example.com"):
    u = auth_service.grant_admin_local(email)
    s = auth_service.create_session(u["id"], "pytest", "127.0.0.1")
    return u, {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
               api_auth.CSRF_HEADER: s["csrf_token"]}


def _remote():
    return TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False),
                                 frontend_dist=None),
                      base_url=REMOTE, client=("203.0.113.9", 5000),
                      raise_server_exceptions=False)


def _local():
    return TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False),
                                 frontend_dist=None),
                      base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


# --- storage ---------------------------------------------------------------------

def test_key_is_stored_encrypted_and_only_its_last_four_are_shown(env):
    u = auth_service.add_user("kid@example.com")
    out = member_key_service.save(u["id"], f"  {KEY}  ")
    assert out == {"has_key": True, "last4": KEY[-4:], "added_at": out["added_at"],
                   "unreadable": False}
    row = member_keys.get(u["id"], "ollama")
    assert KEY not in row["ciphertext"]
    assert member_key_service.cloud_key_for_user(u["id"]) == KEY
    with open(db.DB_PATH, "rb") as f:
        assert KEY.encode() not in f.read()
    assert member_key_service.SECRET_ENV in open(env).read()


def test_a_lost_or_changed_secret_reads_as_no_key_never_an_error(env):
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    settings_service.write_env_var(member_key_service.SECRET_ENV, "not-a-fernet-key")
    assert member_key_service.status(u["id"])["unreadable"] is True
    assert member_key_service.has_usable_key(u["id"]) is False
    settings_service.remove_env_vars([member_key_service.SECRET_ENV])
    from cryptography.fernet import Fernet
    settings_service.write_env_var(member_key_service.SECRET_ENV, Fernet.generate_key().decode())
    status = member_key_service.status(u["id"])
    assert status["unreadable"] is True and status["has_key"] is False
    assert member_key_service.cloud_key_for_user(u["id"]) == local.UNREADABLE_MEMBER_KEY
    # Pasting it again works under the new secret.
    assert member_key_service.save(u["id"], KEY)["has_key"] is True


def test_a_secret_already_on_disk_is_never_replaced(env, monkeypatch):
    """A .env read that fails, or another process that wrote the secret
    meanwhile, must not mean "no secret yet": replacing it would make every
    saved key unreadable."""
    from cryptography.fernet import Fernet
    first = Fernet.generate_key().decode()
    with open(env, "w") as f:
        f.write(f"OTHER=1\n{member_key_service.SECRET_ENV}={first}\n")
    monkeypatch.setattr(settings_service, "resolve_env_names", lambda names, env_path=None: None)
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    text = open(env).read()
    assert text.count(member_key_service.SECRET_ENV) == 1 and first in text and "OTHER=1" in text
    monkeypatch.undo()
    monkeypatch.setattr(settings_service, "default_env_path", lambda: env)
    assert member_key_service.cloud_key_for_user(u["id"]) == KEY


def test_an_unreadable_env_file_refuses_the_save_instead_of_making_a_new_secret(env, monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_env_names", lambda names, env_path=None: None)

    def broken(path, transform):
        raise OSError("read failed")
    monkeypatch.setattr(settings_service, "_rewrite_env", broken)
    u = auth_service.add_user("kid@example.com")
    with pytest.raises(OSError):
        member_key_service.save(u["id"], KEY)
    assert member_keys.get(u["id"], "ollama") is None


def test_a_damaged_secret_is_not_reported_as_a_missing_package(env):
    settings_service.write_env_var(member_key_service.SECRET_ENV, "not-a-fernet-key")
    u = auth_service.add_user("kid@example.com")
    with pytest.raises(InvalidInputError) as info:
        member_key_service.save(u["id"], KEY)
    assert "damaged" in str(info.value) and "cryptography" not in str(info.value)


def test_a_key_copied_onto_another_members_row_does_not_decrypt(env):
    a = auth_service.add_user("a@example.com")
    b = auth_service.add_user("b@example.com")
    member_key_service.save(a["id"], KEY)
    row = member_keys.get(a["id"], "ollama")
    member_keys.put(b["id"], "ollama", row["ciphertext"], row["last4"], row["added_at"])
    assert member_key_service.cloud_key_for_user(a["id"]) == KEY
    assert member_key_service.cloud_key_for_user(b["id"]) == local.UNREADABLE_MEMBER_KEY
    assert member_key_service.status(b["id"])["unreadable"] is True


def test_admins_keep_the_pcs_sign_in_and_cannot_save_a_key(env):
    admin, _ = _admin()
    with pytest.raises(InvalidInputError) as info:
        member_key_service.save(admin["id"], KEY)
    assert KEY not in str(info.value)
    assert member_keys.get(admin["id"], "ollama") is None


def test_short_or_malformed_keys_are_refused_without_echoing_them(env):
    u = auth_service.add_user("kid@example.com")
    for bad in ("abc123def", "has space in the middle of it", "x" * 600):
        with pytest.raises(InvalidInputError) as info:
            member_key_service.save(u["id"], bad)
        assert bad not in str(info.value)
    with pytest.raises(InvalidInputError):
        member_key_service.save(u["id"], "")
    assert member_keys.get(u["id"], "ollama") is None


def test_the_owner_without_an_account_cannot_save_one(env):
    with pytest.raises(InvalidInputError):
        member_key_service.save(None, KEY)


def test_deactivating_an_account_removes_its_key(env):
    admin, _ = _admin()
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    auth_service.deactivate_user(u["id"], actor_id=admin["id"])
    assert member_keys.get(u["id"], "ollama") is None


def test_a_member_promoted_to_admin_runs_on_the_pcs_sign_in_again(env):
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    auth_service.grant_admin_local("kid@example.com")
    assert member_key_service.cloud_key_for_user(u["id"]) is None
    assert not member_key_service.has_usable_key(u["id"])
    assert not member_key_service.has_unreadable_key(u["id"])
    # Kept, not deleted: a later demotion brings the member's own key back.
    auth_service.grant_admin_local("other-admin@example.com")
    auth_service.revoke_admin(u["id"], at_pc=True)
    assert member_key_service.cloud_key_for_user(u["id"]) == KEY


def test_backups_leave_keys_out_and_a_restore_keeps_the_live_ones(env, tmp_path):
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    dest = str(tmp_path / "snap.db")
    library_backup_service._sanitized_snapshot(dest)
    conn = sqlite3.connect(dest)
    try:
        assert conn.execute("SELECT COUNT(*) FROM member_api_keys").fetchone()[0] == 0
    finally:
        conn.close()
    assert "member_api_keys" in auth_service.RESTORE_LIVE_TABLES
    from services import library_user_backup_service as lub
    assert lub.USER_BACKUP_TABLES["member_api_keys"][0] == "empty"


def test_redaction_scrubs_both_likely_key_shapes():
    text = te.redact_secrets(f"failed with {KEY} and {OTHER_KEY}; see ollama_url")
    assert KEY not in text and OTHER_KEY not in text and "ollama_url" in text


# --- routes ----------------------------------------------------------------------

def test_routes_never_return_the_key_and_are_not_stored_by_browsers(env):
    c = _remote()
    _u, h = _member("kid@example.com")
    assert c.get("/api/auth/ollama-key", headers=h).json()["has_key"] is False
    r = c.post("/api/auth/ollama-key", json={"key": KEY}, headers=h)
    assert r.status_code == 200 and KEY not in r.text
    assert r.json()["last4"] == KEY[-4:] and r.headers["Cache-Control"] == "no-store"
    assert r.headers["pragma"] == "no-cache"
    r = c.get("/api/auth/ollama-key", headers=h)
    assert r.json()["has_key"] is True and KEY not in r.text
    r = c.delete("/api/auth/ollama-key", headers=h)
    assert r.status_code == 200 and r.json()["has_key"] is False
    assert all(KEY not in a["detail_redacted"] for a in auth_service.list_audit(500))


def test_a_member_only_touches_their_own_key(env):
    c = _remote()
    a, ha = _member("a@example.com")
    b, hb = _member("b@example.com")
    assert c.post("/api/auth/ollama-key", json={"key": KEY}, headers=ha).status_code == 200
    assert c.get("/api/auth/ollama-key", headers=hb).json()["has_key"] is False
    assert c.post("/api/auth/ollama-key", json={"key": OTHER_KEY}, headers=hb).status_code == 200
    assert c.delete("/api/auth/ollama-key", headers=hb).status_code == 200
    assert member_key_service.cloud_key_for_user(a["id"]) == KEY
    assert member_key_service.cloud_key_for_user(b["id"]) is None
    # A member can't reach the admin removal of someone else's key.
    assert c.delete(f"/api/admin/users/{a['id']}/ollama-key", headers=hb).status_code == 403
    assert member_key_service.cloud_key_for_user(a["id"]) == KEY


def test_me_says_whether_the_caller_has_a_usable_key(env):
    c = _remote()
    u, h = _member("kid@example.com")
    assert c.get("/api/auth/me", headers=h).json()["own_ollama_key"] is False
    member_key_service.save(u["id"], KEY)
    me = c.get("/api/auth/me", headers=h)
    assert me.json()["own_ollama_key"] is True and KEY not in me.text


def test_a_member_who_lost_jobs_start_can_still_remove_their_key(env):
    c = _remote()
    u, h = _member("kid@example.com")
    member_key_service.save(u["id"], KEY)
    auth_service.revoke_permission(u["id"], "jobs.start")
    assert c.post("/api/auth/ollama-key", json={"key": KEY}, headers=h).status_code == 403
    assert c.delete("/api/auth/ollama-key", headers=h).status_code == 200
    assert member_keys.get(u["id"], "ollama") is None
    removal = [a for a in auth_service.list_audit(500) if a["action"] == "member_key.remove"]
    assert removal[0]["user_id"] == u["id"]


def test_a_removal_no_person_made_is_not_audited_as_the_members(env):
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    assert member_key_service.remove(u["id"]) is True
    removal = [a for a in auth_service.list_audit(500) if a["action"] == "member_key.remove"]
    assert removal[0]["user_id"] is None


def test_routes_need_sign_in_and_csrf(env):
    c = _remote()
    _u, h = _member("kid@example.com")
    assert c.get("/api/auth/ollama-key").status_code == 401
    no_csrf = {k: v for k, v in h.items() if k != api_auth.CSRF_HEADER}
    assert c.post("/api/auth/ollama-key", json={"key": KEY}, headers=no_csrf).status_code == 403


def test_with_sign_in_off_the_routes_are_404(env):
    c = TestClient(create_app(ApiSettings(auth_mode="off", serve_frontend=False),
                              frontend_dist=None), base_url="http://127.0.0.1:8600",
                   client=("127.0.0.1", 5000), raise_server_exceptions=False)
    assert c.get("/api/auth/ollama-key").status_code == 404
    assert c.post("/api/auth/ollama-key", json={"key": KEY},
                 headers={"X-Baihe-Local": "1"}).status_code == 404


def test_admin_sees_only_a_flag_and_can_remove_but_not_read(env):
    admin, ha = _admin()
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    c = _local()
    r = c.get("/api/admin/users", headers=ha)
    assert KEY not in r.text and KEY[-4:] not in r.text
    row = next(x for x in r.json()["users"] if x["id"] == u["id"])
    assert row["has_own_ollama_key"] is True
    r = c.delete(f"/api/admin/users/{u['id']}/ollama-key", headers=ha)
    assert r.status_code == 200 and r.json() == {"user_id": u["id"], "removed": True}
    assert member_key_service.cloud_key_for_user(u["id"]) is None
    removal = [a for a in auth_service.list_audit(500) if a["action"] == "member_key.remove"]
    assert removal and removal[0]["user_id"] == admin["id"]
    assert c.delete("/api/admin/users/999999/ollama-key", headers=ha).status_code == 404


def test_removing_an_admin_accounts_key_is_pc_only(env):
    """Like every other write whose target is an admin account: a remote
    admin session could otherwise act on the owner's own account."""
    _admin_a, ha = _admin()
    b = auth_service.add_user("b@example.com")
    member_key_service.save(b["id"], KEY)
    auth_service.grant_admin_local("b@example.com")
    m = auth_service.add_user("kid@example.com")
    member_key_service.save(m["id"], OTHER_KEY)
    remote = _remote()
    r = remote.delete(f"/api/admin/users/{b['id']}/ollama-key", headers=ha)
    assert r.status_code == 403
    assert b["id"] in member_key_service.user_ids_with_key()
    assert remote.delete(f"/api/admin/users/{m['id']}/ollama-key", headers=ha).status_code == 200
    assert _local().delete(f"/api/admin/users/{b['id']}/ollama-key",
                           headers=ha).json()["removed"] is True

# --- where a cloud run goes ------------------------------------------------------

class _Resp:
    def __init__(self, body=None, status=200, raw=b""):
        self.status_code, self.ok, self.headers = status, status < 400, {}
        self._raw = raw if body is None else json.dumps(body).encode()

    def iter_content(self, size):
        yield self._raw

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.HTTPError(f"{self.status_code}")
            err.response = self
            raise err


def _capture(monkeypatch, resp=None):
    seen = []

    def post(url, json=None, timeout=None, stream=None, headers=None, allow_redirects=True):
        seen.append({"url": url, "model": json["model"], "timeout": timeout,
                     "headers": headers, "allow_redirects": allow_redirects})
        return resp or _Resp({"message": {"content": '{"1": "Hi."}'}})
    monkeypatch.setattr("requests.post", post)
    return seen


def test_with_a_member_key_a_cloud_tag_goes_to_ollama_com_with_it(monkeypatch):
    seen = _capture(monkeypatch)
    local.carry_member_cloud_key(lambda: te.OllamaEngine(model=CLOUD).translate_batch(["你好"], {}),
                                 KEY)()
    assert seen[0]["url"] == "https://ollama.com/api/chat"
    assert seen[0]["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert seen[0]["model"] == "gemma4:31b" and seen[0]["timeout"]
    assert seen[0]["allow_redirects"] is False


def test_without_a_member_key_a_cloud_tag_still_uses_the_pcs_sign_in(monkeypatch):
    seen = _capture(monkeypatch)
    local.carry_member_cloud_key(lambda: te.OllamaEngine(model=CLOUD).translate_batch(["你好"], {}),
                                 None)()
    assert seen[0]["url"].startswith("http://localhost:11434") and seen[0]["headers"] is None


def test_a_member_key_never_reaches_a_local_model(monkeypatch):
    seen = _capture(monkeypatch)
    local.carry_member_cloud_key(
        lambda: te.OllamaEngine(model="gemma4:12b").translate_batch(["你好"], {}), KEY)()
    assert seen[0]["url"].startswith("http://localhost:11434") and seen[0]["headers"] is None


def test_direct_model_names_drop_the_app_cloud_suffix():
    assert local.direct_cloud_model("gemma4:31b-cloud") == "gemma4:31b"
    assert local.direct_cloud_model("gemma4:cloud") == "gemma4"


def test_over_the_cap_names_the_members_own_quota(monkeypatch):
    _capture(monkeypatch, _Resp(status=429, raw=b"weekly limit"))
    with pytest.raises(te.OllamaCloudLimitError) as info:
        local.carry_member_cloud_key(te._ollama_chat, KEY)("http://localhost:11434",
                                                           {"model": CLOUD})
    assert str(info.value).startswith("Your Ollama account")
    assert KEY not in str(info.value)


def test_a_refused_member_key_says_so_and_does_not_fall_back(monkeypatch):
    seen = _capture(monkeypatch, _Resp(status=401))
    with pytest.raises(te.OllamaUnavailableError) as info:
        local.carry_member_cloud_key(te._ollama_chat, KEY)("http://localhost:11434",
                                                           {"model": CLOUD})
    assert info.value.reason == "ollama_cloud_key_refused"
    assert [s["url"] for s in seen] == ["https://ollama.com/api/chat"]


def test_an_unfollowed_redirect_is_not_a_reply(monkeypatch):
    _capture(monkeypatch, _Resp(status=302))
    with pytest.raises(te.OllamaUnavailableError) as info:
        local.carry_member_cloud_key(te._ollama_chat, KEY)("http://localhost:11434",
                                                           {"model": CLOUD})
    assert info.value.reason == "ollama_cloud_unreachable"


def test_qa_uses_the_member_key_for_a_cloud_tag(monkeypatch):
    import qa
    seen = {}

    def post_json(url, payload, timeout=None, headers=None, label=None):
        seen.update(url=url, headers=headers, model=payload["model"])
        return {"message": {"content": "answer"}}, {}
    monkeypatch.setattr(qa, "post_json", post_json)
    local.carry_member_cloud_key(qa.ask_about_drama, KEY)(
        "who?", [], {}, te.OllamaEngine(model=CLOUD))
    assert seen == {"url": "https://ollama.com/api/chat",
                    "headers": {"Authorization": f"Bearer {KEY}"}, "model": "gemma4:31b"}


def test_an_unreadable_saved_key_stops_a_cloud_call_instead_of_using_the_pc(monkeypatch):
    seen = _capture(monkeypatch)
    with pytest.raises(te.OllamaUnavailableError) as info:
        local.carry_member_cloud_key(te._ollama_chat, local.UNREADABLE_MEMBER_KEY)(
            "http://localhost:11434", {"model": CLOUD})
    assert info.value.reason == "ollama_cloud_key_unreadable" and seen == []
    # A local model is unaffected.
    local.carry_member_cloud_key(te._ollama_chat, local.UNREADABLE_MEMBER_KEY)(
        "http://localhost:11434", {"model": "gemma4:12b"})
    assert seen[0]["url"].startswith("http://localhost:11434") and seen[0]["headers"] is None


def test_a_job_of_a_member_whose_key_became_unreadable_carries_that(env):
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    settings_service.write_env_var(member_key_service.SECRET_ENV, "not-a-fernet-key")
    assert _key_seen_by_a_job(u["id"]) == local.UNREADABLE_MEMBER_KEY
    import qa
    with pytest.raises(te.OllamaUnavailableError):
        local.carry_member_cloud_key(qa.ask_about_drama, local.UNREADABLE_MEMBER_KEY)(
            "who?", [], {}, te.OllamaEngine(model=CLOUD))


def test_qa_errors_on_the_own_key_path_name_the_members_account(monkeypatch):
    import qa
    seen = {}

    def post_json(url, payload, timeout=None, headers=None, label=None):
        seen["label"] = label
        return {"message": {"content": "answer"}}, {}
    monkeypatch.setattr(qa, "post_json", post_json)
    local.carry_member_cloud_key(qa.ask_about_drama, KEY)("who?", [], {}, te.OllamaEngine(model=CLOUD))
    assert seen["label"].startswith("Your Ollama account")
    local.carry_member_cloud_key(qa.ask_about_drama, None)("who?", [], {}, te.OllamaEngine(model=CLOUD))
    assert seen["label"] == "Ollama"


# --- jobs carry their starter's key ------------------------------------------------

def _start_as(user_id, target):
    """Starts a job as if from a request by `user_id` (None: the owner)."""
    from jobs import job_launch
    token = ownership_service.bind_request()
    try:
        ownership_service.note_acting_principal(None if user_id is None else {"user_id": user_id})
        return job_launch.start_job(f"member-key-test-{user_id}", target)
    finally:
        ownership_service.unbind_request(token)


def _key_seen_by_a_job(user_id, before_run=None):
    seen, done = {}, threading.Event()
    gate = threading.Event()

    def target():
        gate.wait(5)
        seen["key"] = local.member_cloud_key()
        done.set()
    assert _start_as(user_id, target)
    if before_run:
        before_run()
    gate.set()
    assert done.wait(5)
    return seen["key"]


def test_a_members_job_carries_their_key_and_the_owners_carries_none(env):
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    assert _key_seen_by_a_job(u["id"]) == KEY
    assert _key_seen_by_a_job(None) is None


def test_removing_the_key_mid_run_does_not_move_the_run_to_the_owners_account(env):
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    assert _key_seen_by_a_job(u["id"], lambda: member_key_service.remove(u["id"])) == KEY


def test_a_request_without_a_job_uses_the_signed_in_members_key(env):
    u = auth_service.add_user("kid@example.com")
    member_key_service.save(u["id"], KEY)
    token = ownership_service.bind_request()
    try:
        ownership_service.note_acting_principal({"user_id": u["id"]})
        assert local.member_cloud_key() == KEY
    finally:
        ownership_service.unbind_request(token)
    assert local.member_cloud_key() is None
