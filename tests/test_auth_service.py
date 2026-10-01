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


def test_sweep_removes_only_stale_sessions(adb):
    now = 10_000_000.0
    u, gone = auth.add_user("a@example.com"), auth.add_user("b@example.com")
    live = auth.create_session(u["id"], now=now - 60)
    idle = auth.create_session(u["id"], now=now - auth.IDLE_TIMEOUT_SECONDS - 1)
    old = auth.create_session(u["id"], now=now - auth.ABSOLUTE_TIMEOUT_SECONDS)
    of_inactive = auth.create_session(gone["id"], now=now - 60)
    with contextlib.closing(db.get_conn()) as conn:   # deactivated behind the service's back
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (gone["id"],))
        # Kept busy up to now, but past its absolute expiry.
        conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?",
                     (now - 1, old["session_id"]))
        conn.commit()
    assert auth.sweep_stale_sessions(now=now) == 3
    with contextlib.closing(db.get_conn()) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM auth_sessions")]
    assert ids == [live["session_id"]]
    assert idle["session_id"] not in ids and of_inactive["session_id"] not in ids
    assert auth.resolve_session(live["session_token"], now=now)
    assert any(a["action"] == "session.sweep" for a in auth.list_audit())
    assert auth.sweep_stale_sessions(now=now) == 0


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


def test_demoted_admin_keeps_no_opt_in_or_admin_rights(adb):
    u = auth.grant_admin_local("boss@example.com")
    assert set(u["permissions"]) == set(auth.PERMISSIONS)
    db.auth_update_user(u["id"], is_admin=0)
    left = set(auth.effective_permissions(u["id"]))
    assert left == set(auth.HOUSEHOLD_DEFAULT_PERMISSIONS)
    assert not left & (set(auth.OPT_IN_PERMISSIONS) | set(auth.ADMIN_PERMISSIONS))


def test_add_user_insert_race_is_a_conflict(adb, monkeypatch):
    auth.add_user("race@example.com")
    monkeypatch.setattr(db, "auth_get_user_by_email", lambda email: None)  # lost the race
    with pytest.raises(ConflictError):
        auth.add_user("race@example.com")


def test_rate_limiter_memory_is_bounded_and_evicts_oldest():
    lim = auth.SlidingWindowRateLimiter(1, 60, clock=lambda: 0.0, max_keys=3)
    for k in ("a", "b", "c"):
        lim.hit(k)
    lim.hit("d")                      # evicts "a", the least recently hit
    assert len(lim._events) == 3 and "a" not in lim._events
    with pytest.raises(RateLimitedError):
        lim.hit("d")                  # "d" is tracked and limited
    with pytest.raises(RateLimitedError):
        lim.hit("b")                  # refreshes "b"'s recency
    lim.hit("e")                      # evicts "c", not the just-hit "b"
    assert set(lim._events) == {"b", "d", "e"}
    for i in range(1000):
        lim.hit(f"k{i}")
    assert len(lim._events) == 3


@pytest.mark.parametrize("ip, expected", [
    ("203.0.113.9", "203.0.113"),
    ("2001:db8::5", "2001:db8::/48"),
    ("2001:db8:1:2:3:4:5:6", "2001:db8:1::/48"),
    ("::ffff:203.0.113.9", "203.0.113"),
    ("::1", "::/48"),
    ("", ""), ("garbage", ""), ("1.2.3", ""),
])
def test_ip_prefix_is_coarse(ip, expected):
    assert auth._ip_prefix(ip) == expected


def test_ipv6_session_stores_only_the_48(adb):
    u = auth.add_user("a@example.com")
    auth.create_session(u["id"], ip="2001:db8:aa:bb::5")
    assert auth.list_sessions(u["id"])[0]["ip_prefix"] == "2001:db8:aa::/48"


@pytest.mark.parametrize("ip, expected", [
    ("203.0.113.9", "203.0.113.9"),
    ("::ffff:203.0.113.9", "203.0.113.9"),
    ("2001:db8:1:2::5", "2001:db8:1:2::/64"),
    ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
    ("", "unknown"),
])
def test_rate_limit_key_buckets_ipv6_by_64(ip, expected):
    assert auth.rate_limit_key(ip) == expected
