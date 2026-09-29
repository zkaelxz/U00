"""Step 133: auth storage + service (users, permissions, sessions, audit, rate limit)."""
import contextlib
import hmac

import pytest

import db
from services import auth_service as auth
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     RateLimitedError)


@pytest.fixture
def adb(isolated_db):
    return isolated_db


def test_add_user_gets_household_defaults_only(adb):
    u = auth.add_user("Kid@Example.com", "Kid")
    assert u["email"] == "kid@example.com"
    assert set(u["permissions"]) == set(auth.HOUSEHOLD_DEFAULT_PERMISSIONS)
    assert "engines.paid" not in u["permissions"] and "admin.users" not in u["permissions"]
    with pytest.raises(ConflictError):
        auth.add_user("kid@example.com")
    with pytest.raises(InvalidInputError):
        auth.add_user("not-an-email")


def test_grant_revoke_and_unknown_permission(adb):
    u = auth.add_user("a@example.com")
    assert "media.stream" in auth.grant_permission(u["id"], "media.stream")["permissions"]
    assert "media.stream" not in auth.revoke_permission(u["id"], "media.stream")["permissions"]
    with pytest.raises(InvalidInputError):
        auth.grant_permission(u["id"], "made.up")
    with pytest.raises(InvalidInputError):
        auth.grant_permission(u["id"], "admin.users")   # admin flag only


def test_stored_admin_row_never_confers_admin_permission_without_flag(adb):
    u = auth.add_user("a@example.com")
    db.auth_grant_permission(u["id"], "admin.users")   # bypassing the service
    assert "admin.users" not in auth.effective_permissions(u["id"])


def test_grant_admin_local_creates_and_reactivates(adb):
    a = auth.grant_admin_local("Boss@Example.com")
    assert a["is_admin"] and set(a["permissions"]) == set(auth.PERMISSIONS)
    auth.deactivate_user(a["id"])
    again = auth.grant_admin_local("boss@example.com")
    assert again["id"] == a["id"] and again["is_active"]


def test_session_raw_token_returned_once_and_only_hash_stored(adb):
    u = auth.add_user("a@example.com")
    s = auth.create_session(u["id"], user_agent="UA", ip="192.168.1.77")
    with contextlib.closing(db.get_conn()) as conn:
        dump = repr([tuple(r) for r in conn.execute("SELECT * FROM auth_sessions")])
        dump += repr([tuple(r) for r in conn.execute("SELECT * FROM audit_log")])
    assert s["session_token"] not in dump and s["csrf_token"] not in dump
    listed = auth.list_sessions(u["id"])
    assert len(listed) == 1
    blob = repr(listed)
    assert s["session_token"] not in blob and "id_hash" not in blob and "csrf" not in blob
    assert listed[0]["ip_prefix"] == "192.168.1"
    assert auth.resolve_session(s["session_token"])["user_id"] == u["id"]
    assert auth.resolve_session("nope") is None and auth.resolve_session(None) is None


def test_expiry_idle_and_absolute(adb):
    u = auth.add_user("a@example.com")
    s = auth.create_session(u["id"], now=1000.0)
    assert auth.resolve_session(s["session_token"], now=1000.0 + 100)
    # idle: last seen was bumped to 1100; nothing for longer than the idle window
    assert auth.resolve_session(s["session_token"],
                                now=1100.0 + auth.IDLE_TIMEOUT_SECONDS + 1) is None
    s2 = auth.create_session(u["id"], now=5000.0)
    t = 5000.0
    while t < 5000.0 + auth.ABSOLUTE_TIMEOUT_SECONDS:   # keep it active
        t += auth.IDLE_TIMEOUT_SECONDS - 10
        if t < 5000.0 + auth.ABSOLUTE_TIMEOUT_SECONDS:
            assert auth.resolve_session(s2["session_token"], now=t)
    assert auth.resolve_session(s2["session_token"],
                                now=5000.0 + auth.ABSOLUTE_TIMEOUT_SECONDS + 1) is None


def test_revoke_and_deactivate_stop_sessions(adb):
    u = auth.add_user("a@example.com")
    s1, s2 = auth.create_session(u["id"]), auth.create_session(u["id"])
    assert auth.revoke_session(s1["session_id"], u["id"])
    assert auth.resolve_session(s1["session_token"]) is None
    assert auth.resolve_session(s2["session_token"])
    other = auth.add_user("b@example.com")
    assert not auth.revoke_session(s2["session_id"], other["id"])   # not theirs
    auth.deactivate_user(u["id"])
    assert auth.resolve_session(s2["session_token"]) is None
    assert auth.list_sessions(u["id"]) == []
    with pytest.raises(NotFoundError):
        auth.create_session(u["id"])


def test_revoke_all(adb):
    u = auth.add_user("a@example.com")
    tokens = [auth.create_session(u["id"])["session_token"] for _ in range(3)]
    assert auth.revoke_all_for_user(u["id"]) == 3
    assert all(auth.resolve_session(t) is None for t in tokens)


def test_permission_change_applies_to_live_session(adb):
    u = auth.add_user("a@example.com")
    s = auth.create_session(u["id"])
    assert "lines.edit" in auth.resolve_session(s["session_token"])["permissions"]
    auth.revoke_permission(u["id"], "lines.edit")
    assert "lines.edit" not in auth.resolve_session(s["session_token"])["permissions"]


def test_verify_csrf_uses_compare_digest(adb, monkeypatch):
    u = auth.add_user("a@example.com")
    s = auth.create_session(u["id"])
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(auth.hmac, "compare_digest",
                        lambda a, b: calls.append(1) or real(a, b))
    assert auth.verify_csrf(s["session_token"], s["csrf_token"])
    assert not auth.verify_csrf(s["session_token"], "wrong")
    assert not auth.verify_csrf(s["session_token"], "")
    assert not auth.verify_csrf("bad-session", s["csrf_token"])
    assert len(calls) >= 3   # session hash lookup + csrf compare both timing-safe


def test_google_sub_binding_never_rebinds_or_matches_email(adb):
    u = auth.add_user("a@example.com")
    assert auth.find_user_by_google_sub("sub-1") is None
    auth.bind_google_sub(u["id"], "sub-1")
    assert auth.find_user_by_google_sub("sub-1")["id"] == u["id"]
    with pytest.raises(ConflictError):
        auth.bind_google_sub(u["id"], "sub-2")
    v = auth.add_user("b@example.com")
    with pytest.raises(ConflictError):
        auth.bind_google_sub(v["id"], "sub-1")
    assert auth.find_user_by_google_sub("a@example.com") is None


def test_audit_redacts_secrets_and_token_like_runs(adb):
    auth.write_audit(None, "test", "key sk-ant-TESTSECRET1234567890abcdef and "
                     "tok abcdefghijklmnopqrstuvwxyz0123456789ABCDEF")
    text = auth.list_audit()[0]["detail_redacted"]
    assert "TESTSECRET" not in text and "abcdefghijklmnop" not in text


def test_db_update_rejects_unlisted_field(adb):
    u = auth.add_user("a@example.com")
    with pytest.raises(ValueError):
        db.auth_update_user(u["id"], **{"is_admin = 1, email": "x"})


def test_rate_limiter_sliding_window():
    now = [0.0]
    lim = auth.SlidingWindowRateLimiter(3, 60, clock=lambda: now[0])
    for _ in range(3):
        lim.hit("ip")
    with pytest.raises(RateLimitedError):
        lim.hit("ip")
    lim.hit("other")            # keys are independent
    now[0] = 61.0               # window slid past the old hits
    lim.hit("ip")
