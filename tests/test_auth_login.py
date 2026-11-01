"""
Step 134 (slice A1): Google sign-in, end to end through the real app with a
fake Google (`FakeGoogle`, injected as `app.state.oidc_provider`). No
network: the fake holds an RSA key made with `cryptography`, publishes its
JWKS and signs id_tokens with `authlib.jose`, exactly the checks the real
flow runs.

TestClient's default peer ("testclient") is a remote client; with an https
base URL the cookies are Secure, so the `__Host-` names are in play.
"""

import sqlite3
import time
import warnings
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("authlib")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings, check_bind_safety, load_settings, normalize_public_url
from api.server import create_app
from services import auth_service, oidc_service

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from authlib.jose import JsonWebKey, jwt
    from authlib.oauth2.rfc7636 import create_s256_code_challenge

CLIENT_ID = "1234-test.apps.googleusercontent.com"
SECRET = "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789"
PUBLIC = "https://baihe.example.com"
ISS = "https://accounts.google.com"


_KEYS = {}


def _rsa_jwk(kid, slot=None):
    """RSA keys are slow to make; each (kid, slot) is made once per run."""
    if (kid, slot) not in _KEYS:
        _KEYS[kid, slot] = _new_rsa_jwk(kid)
    return _KEYS[kid, slot]


def _new_rsa_jwk(kid):
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    return JsonWebKey.import_key(pem, {"kid": kid, "use": "sig", "alg": "RS256"})


class FakeGoogle:
    """Stands in for Google's authorize endpoint, token endpoint and JWKS.
    `issue(code, **claims)` registers what the token endpoint will sign for
    that code; the PKCE verifier is checked against the challenge the app
    put in the authorize URL."""

    authorize_endpoint = "https://accounts.google.test/o/oauth2/v2/auth"

    def __init__(self):
        self.key = _rsa_jwk("k1")
        self.published = [self.key]
        self.codes = {}
        self.exchanges = []
        self.jwks_fetches = 0
        self.jwks_error = None
        self.exchange_error = None

    def fetch_jwks(self):
        self.jwks_fetches += 1
        if self.jwks_error:
            raise self.jwks_error
        return {"keys": [k.as_dict(is_private=False) for k in self.published]}

    def exchange_code(self, *, client_id, client_secret, redirect_uri, code, code_verifier):
        self.exchanges.append({"client_id": client_id, "client_secret": client_secret,
                               "redirect_uri": redirect_uri, "code": code})
        if self.exchange_error:
            raise self.exchange_error
        entry = self.codes.pop(code, None)
        if entry is None or create_s256_code_challenge(code_verifier) != entry["challenge"]:
            raise RuntimeError(f"invalid_grant (secret was {client_secret})")
        return {"access_token": "ya29.fake", "id_token": entry["id_token"],
                "token_type": "Bearer", "expires_in": 3600}

    def sign(self, claims, key=None, header=None):
        key = key or self.key
        header = header or {"alg": "RS256", "kid": key.as_dict()["kid"]}
        return jwt.encode(header, claims, key).decode()

    def claims(self, the_nonce, **over):
        now = int(time.time())
        base = {"iss": ISS, "aud": CLIENT_ID, "sub": "google-sub-1", "email": "kid@example.com",
                "email_verified": True, "nonce": the_nonce, "iat": now, "exp": now + 3600,
                "name": "Kid"}
        base.update(over)
        return {k: v for k, v in base.items() if v is not _DROP}


_DROP = object()


def _settings(**kw):
    base = dict(auth_mode="on", serve_frontend=False, google_client_id=CLIENT_ID,
                google_client_secret=SECRET, public_url=PUBLIC)
    base.update(kw)
    return ApiSettings(**base)


@pytest.fixture
def fake():
    return FakeGoogle()


@pytest.fixture
def clock():
    return [1000.0]


@pytest.fixture
def app(fake, clock):
    a = create_app(_settings())
    a.state.oidc_provider = fake
    a.state.auth_clock = lambda: clock[0]
    return a


def _client(app, base=PUBLIC, peer=("203.0.113.9", 1000)):
    return TestClient(app, base_url=base, client=peer, raise_server_exceptions=False,
                      follow_redirects=False)


def _via_caddy(app, xff):
    """A client behind Caddy whose address (rightmost X-Forwarded-For) is `xff`."""
    return TestClient(app, base_url=PUBLIC, client=("127.0.0.1", 5000),
                      raise_server_exceptions=False, follow_redirects=False,
                      headers={"X-Forwarded-For": xff})


def _start(c, return_to="/library"):
    r = c.get("/api/auth/login", params={"return_to": return_to})
    assert r.status_code == 302, r.text
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    return r, q


def _sign_in(c, fake, return_to="/library", code="code-1", token=None, **over):
    """Full round trip. `token` (a callable nonce -> id_token) overrides the
    normal signed claims."""
    _r, q = _start(c, return_to)
    id_token = token(q["nonce"]) if token else fake.sign(fake.claims(q["nonce"], **over))
    fake.codes[code] = {"challenge": q["code_challenge"], "id_token": id_token}
    return c.get("/api/auth/callback", params={"state": q["state"], "code": code})


def _cookies(resp):
    return [h for k, h in resp.headers.multi_items() if k.lower() == "set-cookie"]


def _audit(action=None):
    rows = auth_service.list_audit(500)
    return [r for r in rows if action is None or r["action"] == action]


def _add(email="kid@example.com", **kw):
    return auth_service.add_user(email, **kw)


# --- the happy path -------------------------------------------------------------

class TestHappyPath:
    def test_sign_in_binds_sets_cookies_and_redirects(self, isolated_db, app, fake):
        user = _add()
        c = _client(app)
        r = _sign_in(c, fake)
        assert r.status_code == 302 and r.headers["location"] == "/library"
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["pragma"] == "no-cache"
        set_cookies = "\n".join(_cookies(r)).lower()
        assert "__host-baihe_session=" in set_cookies and "__host-baihe_csrf=" in set_cookies
        assert auth_service.get_user(user["id"])["has_google_binding"] is True
        # The session works for real routes, and /me reports the user.
        assert c.get("/api/library/dramas").status_code == 200
        me = c.get("/api/auth/me").json()
        assert me == {"auth_enabled": True, "signed_in": True, "sign_in_configured": True,
                      "zone": "internet",
                      "user": {"id": user["id"], "email": "kid@example.com", "display_name": "",
                               "is_admin": False, "is_local_owner": False},
                      "permissions": sorted(auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS),
                      "own_ollama_key": False}
        # The code exchange carried our client id, secret and redirect URI.
        ex = fake.exchanges[-1]
        assert (ex["client_id"], ex["client_secret"], ex["redirect_uri"]) == (
            CLIENT_ID, SECRET, PUBLIC + "/api/auth/callback")

    def test_authorize_url_has_pkce_state_nonce(self, isolated_db, app):
        r, q = _start(_client(app))
        assert r.headers["location"].startswith(FakeGoogle.authorize_endpoint + "?")
        assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID
        assert q["scope"] == "openid email profile" and q["prompt"] == "select_account"
        assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
        assert q["redirect_uri"] == PUBLIC + "/api/auth/callback"
        assert len(q["state"]) >= 40 and len(q["nonce"]) >= 40 and q["state"] != q["nonce"]
        assert "client_secret" not in q and SECRET not in r.headers["location"]
        oidc = [h for h in _cookies(r) if h.startswith("__Host-baihe_oidc=")]
        assert oidc and "httponly" in oidc[0].lower() and "secure" in oidc[0].lower()
        assert "samesite=lax" in oidc[0].lower() and "max-age=600" in oidc[0].lower()

    def test_returning_user_matched_by_sub_even_if_email_changed(self, isolated_db, app, fake):
        user = _add()
        c = _client(app)
        assert _sign_in(c, fake).headers["location"] == "/library"
        c2 = _client(app)
        r = _sign_in(c2, fake, code="code-2", email="renamed@gmail.com")
        assert r.headers["location"] == "/library"
        assert c2.get("/api/auth/me").json()["user"]["id"] == user["id"]

    def test_jwks_is_cached(self, isolated_db, app, fake):
        _add()
        _sign_in(_client(app), fake)
        _sign_in(_client(app), fake, code="code-2")
        assert fake.jwks_fetches == 1


# --- refusals ---------------------------------------------------------------------

def _refused(r, code):
    assert r.status_code == 302, r.text
    assert r.headers["location"] == f"/?login_error={code}"
    assert not any(h.lower().startswith(("__host-baihe_session=", "__host-baihe_csrf="))
                   and "max-age=0" not in h.lower() and 'expires=' not in h.lower()
                   for h in _cookies(r))


def _no_session_rows():
    with sqlite3.connect(db.DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


class TestStateAndTransaction:
    def test_bad_state(self, isolated_db, app, fake):
        _add()
        c = _client(app)
        _r, q = _start(c)
        fake.codes["c"] = {"challenge": q["code_challenge"],
                           "id_token": fake.sign(fake.claims(q["nonce"]))}
        _refused(c.get("/api/auth/callback", params={"state": "nope", "code": "c"}), "expired")
        assert fake.exchanges == [] and _no_session_rows()
        assert any("state" in a["detail_redacted"] for a in _audit("login.error"))

    def test_transaction_is_single_use(self, isolated_db, app, fake):
        _add()
        c = _client(app)
        _r, q = _start(c)
        fake.codes["c"] = {"challenge": q["code_challenge"],
                           "id_token": fake.sign(fake.claims(q["nonce"]))}
        # A wrong state burns the transaction; the right state after it fails too.
        c.get("/api/auth/callback", params={"state": "nope", "code": "c"})
        _refused(c.get("/api/auth/callback", params={"state": q["state"], "code": "c"}),
                 "expired")

    def test_replayed_callback_refused(self, isolated_db, app, fake):
        _add()
        c = _client(app)
        _r, q = _start(c)
        fake.codes["c"] = {"challenge": q["code_challenge"],
                           "id_token": fake.sign(fake.claims(q["nonce"]))}
        url_params = {"state": q["state"], "code": "c"}
        oidc_cookie = c.cookies.get("__Host-baihe_oidc")
        assert c.get("/api/auth/callback", params=url_params).headers["location"] == "/library"
        replay = _client(app)
        replay.cookies.set("__Host-baihe_oidc", oidc_cookie, domain="baihe.example.com")
        _refused(replay.get("/api/auth/callback", params=url_params), "expired")

    def test_missing_cookie_or_expired_transaction(self, isolated_db, app, fake, clock):
        _add()
        c = _client(app)
        _r, q = _start(c)
        other = _client(app)       # no transaction cookie at all
        _refused(other.get("/api/auth/callback", params={"state": q["state"], "code": "c"}),
                 "expired")
        c2 = _client(app)
        _r, q2 = _start(c2)
        clock[0] += oidc_service.TRANSACTION_TTL_SECONDS + 1
        _refused(c2.get("/api/auth/callback", params={"state": q2["state"], "code": "c"}),
                 "expired")

    def test_user_cancelled_at_google(self, isolated_db, app):
        c = _client(app)
        _r, q = _start(c)
        _refused(c.get("/api/auth/callback", params={"state": q["state"],
                                                     "error": "access_denied"}), "denied")

    def test_pending_transactions_are_bounded(self, isolated_db, monkeypatch):
        monkeypatch.setattr(oidc_service, "MAX_PENDING_TRANSACTIONS", 5)
        s = oidc_service.SignIn(provider=FakeGoogle(), clock=lambda: 0.0)
        cfg = {"client_id": CLIENT_ID, "redirect_uri": PUBLIC + "/api/auth/callback"}
        ids = [s.begin(cfg, "/")[0] for _ in range(8)]
        assert s.pending_count() == 5
        assert s._take(ids[0]) is None and s._take(ids[-1]) is not None

    def test_pending_cap_is_per_source(self, isolated_db, monkeypatch):
        monkeypatch.setattr(oidc_service, "MAX_PENDING_PER_SOURCE", 3)
        s = oidc_service.SignIn(provider=FakeGoogle(), clock=lambda: 0.0)
        cfg = {"client_id": CLIENT_ID, "redirect_uri": PUBLIC + "/api/auth/callback"}
        other = s.begin(cfg, "/", client_ip="203.0.113.9")[0]
        flood = [s.begin(cfg, "/", client_ip=f"2001:db8::{i:x}")[0] for i in range(8)]
        assert s.pending_count() == 4
        assert s._take(other) is not None
        assert s._take(flood[0]) is None and s._take(flood[-1]) is not None

    def test_flood_spread_across_64s_of_one_48_keeps_other_users_transaction(
            self, isolated_db):
        """A-M1: one transaction per /64 makes every /64 count 1; eviction must
        count by /48 (IPv4 /24) so the flood evicts itself, not the oldest."""
        s = oidc_service.SignIn(provider=FakeGoogle(), clock=lambda: 0.0)
        cfg = {"client_id": CLIENT_ID, "redirect_uri": PUBLIC + "/api/auth/callback"}
        victims = [s.begin(cfg, "/", client_ip=ip)[0]
                   for ip in ("203.0.113.9", "2001:db8:ffff::1")]
        flood = [s.begin(cfg, "/", client_ip=f"2001:db8:5:{i:x}::1")[0]
                 for i in range(oidc_service.MAX_PENDING_TRANSACTIONS)]
        v4_flood = [s.begin(cfg, "/", client_ip=f"198.51.100.{i}")[0] for i in range(200)]
        assert s.pending_count() == oidc_service.MAX_PENDING_TRANSACTIONS
        assert all(s._take(v) is not None for v in victims)
        assert s._take(flood[0]) is None and s._take(flood[-1]) is not None
        assert s._take(v4_flood[-1]) is not None


class TestTokenValidation:
    @pytest.mark.parametrize("over", [
        {"aud": "someone-else.apps.googleusercontent.com"},
        {"aud": [CLIENT_ID, "other"]},                     # list without azp
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 3600},
        {"iat": _DROP},
        {"sub": _DROP},
        {"nonce": "not-the-nonce"},
        {"nonce": _DROP},
    ], ids=["aud", "aud-list-no-azp", "iss", "expired", "no-iat", "no-sub", "nonce",
            "no-nonce"])
    def test_bad_claims(self, isolated_db, app, fake, over):
        _add()
        _refused(_sign_in(_client(app), fake, **over), "provider_error")
        assert _no_session_rows()
        assert any("token" in a["detail_redacted"] for a in _audit("login.error"))

    def test_accounts_google_com_issuer_without_scheme_ok(self, isolated_db, app, fake):
        _add()
        assert _sign_in(_client(app), fake, iss="accounts.google.com"
                        ).headers["location"] == "/library"

    def test_signed_by_another_key(self, isolated_db, app, fake):
        _add()
        rogue = _rsa_jwk("k1", "rogue")   # same kid, different key
        r = _sign_in(_client(app), fake,
                     token=lambda n: fake.sign(fake.claims(n), key=rogue))
        _refused(r, "provider_error")

    def test_alg_none_and_hs256_refused(self, isolated_db, app, fake):
        _add()
        import base64
        import json

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

        def none_token(n):
            return b64({"alg": "none", "kid": "k1"}) + "." + b64(fake.claims(n)) + "."
        _refused(_sign_in(_client(app), fake, token=none_token), "provider_error")

        def hs_token(n):
            return jwt.encode({"alg": "HS256", "kid": "k1"}, fake.claims(n), SECRET).decode()
        _refused(_sign_in(_client(app), fake, code="c2", token=hs_token), "provider_error")

    def test_garbage_token(self, isolated_db, app, fake):
        _add()
        _refused(_sign_in(_client(app), fake, token=lambda n: "not.a.jwt"), "provider_error")

    def test_unknown_kid_refetches_jwks_but_not_in_a_storm(self, isolated_db, app, fake,
                                                           clock):
        _add()
        _sign_in(_client(app), fake)
        assert fake.jwks_fetches == 1
        new = _rsa_jwk("k2")
        fake.published = [fake.key, new]
        # Within a minute of the last fetch an unknown kid does not refetch.
        clock[0] += 10
        r = _sign_in(_client(app), fake, code="c2",
                     token=lambda n: fake.sign(fake.claims(n), key=new))
        _refused(r, "provider_error")
        assert fake.jwks_fetches == 1
        clock[0] += oidc_service.JWKS_MIN_REFETCH_SECONDS
        r = _sign_in(_client(app), fake, code="c3",
                     token=lambda n: fake.sign(fake.claims(n), key=new))
        assert r.headers["location"] == "/library"
        assert fake.jwks_fetches == 2

    def test_jwks_unavailable(self, isolated_db, app, fake):
        _add()
        fake.jwks_error = OSError("connect timeout")
        _refused(_sign_in(_client(app), fake), "provider_error")
        assert any("jwks" in a["detail_redacted"] for a in _audit("login.error"))

    def test_code_exchange_failure(self, isolated_db, app, fake):
        _add()
        fake.exchange_error = RuntimeError(f"boom client_secret={SECRET}")
        _refused(_sign_in(_client(app), fake), "provider_error")


class TestUserResolution:
    @pytest.mark.parametrize("verified", [False, "true", 1, _DROP])
    def test_email_must_be_verified(self, isolated_db, app, fake, verified):
        user = _add()
        _refused(_sign_in(_client(app), fake, email_verified=verified), "not_allowed")
        assert auth_service.get_user(user["id"])["has_google_binding"] is False
        assert any("unverified" in a["detail_redacted"] for a in _audit("login.denied"))

    def test_not_on_allowlist(self, isolated_db, app, fake):
        _refused(_sign_in(_client(app), fake, email="stranger@gmail.com"), "not_allowed")
        assert _no_session_rows() and auth_service.list_users() == []
        denied = _audit("login.denied")
        assert denied and "not_allowlisted" in denied[0]["detail_redacted"]
        assert "stranger@gmail.com" in denied[0]["detail_redacted"]

    def test_email_bound_to_another_sub(self, isolated_db, app, fake):
        user = _add()
        auth_service.bind_google_sub(user["id"], "google-sub-ORIGINAL")
        _refused(_sign_in(_client(app), fake, sub="google-sub-ATTACKER"), "not_allowed")
        assert auth_service.find_user_by_google_sub("google-sub-ORIGINAL")["id"] == user["id"]
        assert any("bound_other" in a["detail_redacted"] for a in _audit("login.denied"))

    def test_inactive_user_by_email_and_by_sub(self, isolated_db, app, fake):
        user = _add()
        auth_service.deactivate_user(user["id"])
        _refused(_sign_in(_client(app), fake), "not_allowed")
        auth_service.activate_user(user["id"])
        assert _sign_in(_client(app), fake, code="c2").headers["location"] == "/library"
        auth_service.deactivate_user(user["id"])
        _refused(_sign_in(_client(app), fake, code="c3"), "not_allowed")
        assert sum("inactive" in a["detail_redacted"] for a in _audit("login.denied")) == 2

    def test_email_case_is_normalised(self, isolated_db, app, fake):
        _add()
        assert _sign_in(_client(app), fake, email="Kid@Example.COM"
                        ).headers["location"] == "/library"


# --- sessions, cookies, CSRF --------------------------------------------------------

class TestSessionsAndCookies:
    def test_fixation_old_session_revoked_new_token_differs(self, isolated_db, app, fake):
        user = _add()
        planted = auth_service.create_session(user["id"])
        c = _client(app)
        c.cookies.set("__Host-baihe_session", planted["session_token"],
                      domain="baihe.example.com")
        r = _sign_in(c, fake)
        assert r.headers["location"] == "/library"
        new = c.cookies.get("__Host-baihe_session")
        assert new and new != planted["session_token"]
        assert auth_service.resolve_session(planted["session_token"]) is None
        assert auth_service.resolve_session(new)["user_id"] == user["id"]

    def test_sign_in_again_rotates_and_lists_one_device(self, isolated_db, app, fake):
        """Signing in again from the same browser replaces its session (one
        device in the list, a new token); another browser adds a device.
        Only the coarse label and prefix are kept, never the user agent."""
        user = _add()
        ua = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36")
        c = _client(app)
        c.headers["User-Agent"] = ua
        _sign_in(c, fake, code="code-1")
        first = c.cookies.get("__Host-baihe_session")
        _sign_in(c, fake, code="code-2")
        second = c.cookies.get("__Host-baihe_session")
        assert first != second and auth_service.resolve_session(first) is None
        rows = c.get("/api/auth/sessions").json()["sessions"]
        assert [(r["device"], r["ip_prefix"], r["current"]) for r in rows] == [
            ("Chrome on Android", "203.0.113", True)]
        other = _client(app, peer=("198.51.100.20", 1000))
        _sign_in(other, fake, code="code-3")
        assert len(c.get("/api/auth/sessions").json()["sessions"]) == 2
        stored = db.auth_list_sessions(user["id"])
        assert all(s["user_agent_short"] == "" for s in stored)
        assert "Pixel" not in repr(stored)

    def test_secure_mode_cookie_flags(self, isolated_db, app, fake):
        _add()
        r = _sign_in(_client(app), fake)
        by_name = {h.split("=", 1)[0]: h.lower() for h in _cookies(r)}
        sess, csrf = by_name["__Host-baihe_session"], by_name["__Host-baihe_csrf"]
        for h in (sess, csrf):
            assert "secure" in h and "path=/" in h and "domain=" not in h
        assert "httponly" in sess and "samesite=lax" in sess
        assert "httponly" not in csrf and "samesite=strict" in csrf
        assert "baihe_session" not in by_name and "baihe_csrf" not in by_name
        assert 'max-age=0' in by_name["__Host-baihe_oidc"] or \
            "expires=" in by_name["__Host-baihe_oidc"]         # the transaction cookie is cleared

    def test_dev_mode_plain_names_without_secure(self, isolated_db, fake, clock):
        _add()
        app = create_app(_settings(cookie_secure=False, public_url="http://localhost:8600"))
        app.state.oidc_provider = fake
        c = _client(app, base="http://127.0.0.1:8600", peer=("127.0.0.1", 5000))
        r = _sign_in(c, fake)
        assert r.headers["location"] == "/library"
        by_name = {h.split("=", 1)[0]: h.lower() for h in _cookies(r)}
        assert set(by_name) >= {"baihe_session", "baihe_csrf"}
        assert not any(n.startswith("__Host-") for n in by_name)
        assert "secure" not in by_name["baihe_session"]
        assert fake.exchanges[-1]["redirect_uri"] == "http://localhost:8600/api/auth/callback"
        assert c.get("/api/auth/me").json()["zone"] == "pc"
        assert c.get("/api/library/dramas").status_code == 200

    def test_secure_mode_ignores_a_plain_named_cookie(self, isolated_db, app):
        user = _add()
        s = auth_service.create_session(user["id"])
        c = _client(app)
        r = c.get("/api/library/dramas", headers={"Cookie": f"baihe_session={s['session_token']}"})
        assert r.status_code == 401

    def test_csrf_cookie_then_post_without_header_is_csrf_failed(self, isolated_db, app, fake):
        _add()
        c = _client(app)
        _sign_in(c, fake)
        csrf = c.cookies.get("__Host-baihe_csrf")
        assert csrf
        r = c.post("/api/auth/logout")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "csrf_failed"
        # Same code from a permission route (the early gate and the dependency agree).
        r = c.post("/api/export/dramas/999/flag-overlaps")
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
        r = c.post("/api/export/dramas/999/flag-overlaps", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 404
        # A permission refusal keeps the plain `forbidden` code.
        r = c.get("/api/diagnostics")
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"

    def test_logout(self, isolated_db, app, fake):
        user = _add()
        c = _client(app)
        _sign_in(c, fake)
        token = c.cookies.get("__Host-baihe_session")
        r = c.post("/api/auth/logout", headers={"X-CSRF-Token": c.cookies.get("__Host-baihe_csrf")})
        assert r.status_code == 200 and r.json() == {"signed_in": False}
        cleared = {h.split("=", 1)[0] for h in _cookies(r)}
        assert {"__Host-baihe_session", "__Host-baihe_csrf"} <= cleared
        assert auth_service.resolve_session(token) is None
        assert any(a["user_id"] == user["id"] for a in _audit("logout"))
        anon = _client(app)
        assert anon.post("/api/auth/logout").status_code == 401

    def test_me_signed_out(self, isolated_db, app):
        r = _client(app).get("/api/auth/me")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert r.headers["pragma"] == "no-cache"
        assert r.json() == {"auth_enabled": True, "signed_in": False, "sign_in_configured": True,
                            "zone": "internet", "user": None, "permissions": [],
                            "own_ollama_key": False}
        local = _client(app, base="http://127.0.0.1:8600", peer=("127.0.0.1", 5000))
        assert local.get("/api/auth/me").json()["zone"] == "pc"


# --- rate limits and client address -----------------------------------------------

class TestRateLimits:
    def test_login_429_then_window_slides(self, isolated_db, app, clock):
        c = _client(app)
        codes = [c.get("/api/auth/login").status_code for _ in range(21)]
        assert codes[:20] == [302] * 20 and codes[20] == 429
        limited = c.get("/api/auth/login")
        assert limited.headers["content-type"].startswith("text/html")
        assert "10 minutes" in limited.text and limited.headers["retry-after"] == "600"
        other = _client(app, peer=("198.51.100.7", 1))
        assert other.get("/api/auth/login").status_code == 302
        clock[0] += 601
        assert c.get("/api/auth/login").status_code == 302
        assert _audit("login.rate_limited")

    def test_callback_per_ip(self, isolated_db, app):
        c = _client(app)
        codes = [c.get("/api/auth/callback").status_code for _ in range(21)]
        assert codes[:20] == [302] * 20 and codes[20] == 429

    def test_live_callbacks_from_many_64s_dont_lock_out_other_users(self, isolated_db,
                                                                     app, fake):
        """A-M1: an attacker owns the transactions it starts, so its callbacks are
        live. Ten /64s of one /56 hit the /56 cap, ten /48s hit nothing shared;
        either way other users still sign in."""
        _add()

        def flood(xff):
            c = _via_caddy(app, xff)
            codes = []
            for _ in range(20):
                r = c.get("/api/auth/login")
                if r.status_code != 302:
                    codes.append(r.status_code)
                    continue
                q = parse_qs(urlsplit(r.headers["location"]).query)
                codes.append(c.get("/api/auth/callback", params={
                    "state": q["state"][0], "code": "junk"}).status_code)
            return codes
        one_56 = [code for i in range(10) for code in flood(f"2001:db8:1:ab{i:02x}::1")]
        assert one_56.count(302) == oidc_service.NET56_RATE[0] and 429 in one_56
        spread = [code for i in range(10) for code in flood(f"2001:db8:{0x100 + i:x}::1")]
        assert spread == [302] * 200
        for i, xff in enumerate(("2001:db8:1:cd00::1", "2001:db8:99::1", "198.51.100.7")):
            r = _sign_in(_via_caddy(app, xff), fake, code=f"v{i}")
            assert r.status_code == 302 and r.headers["location"] == "/library", xff
        assert _sign_in(_client(app), fake, code="v9").headers["location"] == "/library"

    def test_48_cap_bounds_one_network(self, isolated_db, app):
        codes = [_via_caddy(app, f"2001:db8:7:{i:x}00::1").get("/api/auth/login").status_code
                 for i in range(oidc_service.NET_RATE[0] + 1)]
        assert codes[:-1] == [302] * oidc_service.NET_RATE[0] and codes[-1] == 429
        assert _via_caddy(app, "2001:db8:8::1").get("/api/auth/login").status_code == 302

    def test_junk_callbacks_from_many_addresses_dont_block_a_real_one(self, isolated_db,
                                                                       app, fake):
        _add()
        via_caddy = _client(app, peer=("127.0.0.1", 5000))
        seen = {via_caddy.get("/api/auth/callback", params={"state": "junk", "code": "x"},
                              headers={"X-Forwarded-For": f"198.{51 + i // 200}.{i % 200}.1"}
                              ).status_code for i in range(300)}
        assert seen == {302}
        r = _sign_in(_client(app), fake)
        assert r.status_code == 302 and r.headers["location"] == "/library"

    def test_ipv6_rate_limit_is_per_64(self, isolated_db, app):
        via_caddy = _client(app, peer=("127.0.0.1", 5000))
        codes = [via_caddy.get("/api/auth/login", headers={
            "X-Forwarded-For": f"2001:db8:1:2::{i:x}"}).status_code for i in range(21)]
        assert codes[:20] == [302] * 20 and codes[20] == 429
        assert via_caddy.get("/api/auth/login", headers={
            "X-Forwarded-For": "2001:db8:1:3::1"}).status_code == 302
        # IPv4-mapped IPv6 shares the IPv4 address's bucket.
        for _ in range(20):
            assert via_caddy.get("/api/auth/login", headers={
                "X-Forwarded-For": "203.0.113.77"}).status_code == 302
        assert via_caddy.get("/api/auth/login", headers={
            "X-Forwarded-For": "::ffff:203.0.113.77"}).status_code == 429

    def test_login_flood_from_one_64_keeps_other_users_transaction(self, isolated_db, app,
                                                                   fake, monkeypatch):
        monkeypatch.setattr(oidc_service, "MAX_PENDING_TRANSACTIONS", 10)
        monkeypatch.setattr(oidc_service, "MAX_PENDING_PER_SOURCE", 5)
        _add()
        victim = _client(app)
        _r, q = _start(victim)
        via_caddy = _client(app, peer=("127.0.0.1", 5000))
        for net in range(3):    # three /64s in separate /48s, flooding until rate-limited
            codes = [via_caddy.get("/api/auth/login", headers={
                "X-Forwarded-For": f"2001:db8:{net}::{i:x}"}).status_code
                for i in range(25)]
            assert codes.count(302) == 20 and codes[-1] == 429
        sign_in = app.state.sign_in
        assert sign_in.pending_count() == 10
        fake.codes["v"] = {"challenge": q["code_challenge"],
                           "id_token": fake.sign(fake.claims(q["nonce"]))}
        r = victim.get("/api/auth/callback", params={"state": q["state"], "code": "v"})
        assert r.status_code == 302 and r.headers["location"] == "/library"

    def test_rightmost_forwarded_for_when_peer_is_loopback(self, isolated_db, app):
        via_caddy = _client(app, peer=("127.0.0.1", 5000))
        caddy = {"X-Forwarded-For": "10.9.9.9, 203.0.113.50"}
        assert all(via_caddy.get("/api/auth/login", headers=caddy).status_code == 302
                   for _ in range(20))
        assert via_caddy.get("/api/auth/login", headers=caddy).status_code == 429
        # The client controls only the entries to the left; same bucket.
        spoof = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 203.0.113.50"}
        assert via_caddy.get("/api/auth/login", headers=spoof).status_code == 429
        # Another real client behind Caddy has its own bucket.
        assert via_caddy.get("/api/auth/login", headers={
            "X-Forwarded-For": "203.0.113.50, 203.0.113.51"}).status_code == 302

    def test_forwarded_for_ignored_from_a_remote_peer(self, isolated_db, app):
        c = _client(app, peer=("203.0.113.9", 1))
        for i in range(20):
            assert c.get("/api/auth/login",
                         headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 302
        assert c.get("/api/auth/login",
                     headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429

    def test_client_ip_unit(self):
        from starlette.requests import Request

        def req(peer, xff=None):
            headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
            return Request({"type": "http", "client": (peer, 1), "headers": headers})
        assert api_auth.client_ip(req("203.0.113.9", "1.1.1.1")) == "203.0.113.9"
        assert api_auth.client_ip(req("127.0.0.1", "1.1.1.1, 2.2.2.2")) == "2.2.2.2"
        assert api_auth.client_ip(req("127.0.0.1", "garbage")) == "127.0.0.1"
        assert api_auth.client_ip(req("::1")) == "::1"


# --- return_to, secrets, audit --------------------------------------------------------

class TestReturnTo:
    @pytest.mark.parametrize("value", [
        "//evil.example/x", "https://evil.example/", "/\\evil.example", "\\\\evil.example",
        "javascript:alert(1)", "evil.example", "/\t/evil.example", "/x\r\nSet-Cookie: a=b",
        " /x", "", "/" + "a" * 600, "http:/evil.example",
    ])
    def test_open_redirects_fall_back_to_root(self, isolated_db, app, fake, value):
        _add()
        assert _sign_in(_client(app), fake, return_to=value).headers["location"] == "/"

    @pytest.mark.parametrize("value", ["/", "/library", "/reader/3?line=5#top",
                                       "/%2F%2Fevil.example"])
    def test_relative_paths_kept(self, value):
        assert oidc_service.safe_return_to(value) == value


class TestSecretsNeverLeak:
    def test_secret_absent_from_every_response_log_and_audit(self, isolated_db, app, fake,
                                                             caplog):
        _add()
        c = _client(app)
        seen = []

        def grab(r):
            seen.append(r.text + repr(list(r.headers.multi_items())))
            return r
        grab(c.get("/api/auth/me"))
        grab(_sign_in(c, fake))
        grab(c.get("/api/auth/me"))
        grab(c.get("/api/meta"))
        fake.exchange_error = RuntimeError(f"upstream said client_secret={SECRET}")
        grab(_sign_in(_client(app), fake, code="c2"))
        grab(c.post("/api/auth/logout", headers={"X-CSRF-Token": c.cookies.get("__Host-baihe_csrf")}))
        blob = "\n".join(seen) + caplog.text + repr(auth_service.list_audit(500))
        for needle in (SECRET, "TopSecretClientValue"):
            assert needle not in blob
        assert CLIENT_ID not in "\n".join(seen[:1] + seen[2:4])   # /me and /meta: no client id
        assert "ya29.fake" not in blob                           # Google's access token

    def test_settings_repr_hides_secret(self):
        s = _settings()
        assert SECRET not in repr(s) and CLIENT_ID not in repr(s)

    def test_redact_secrets_knows_google_client_secrets(self):
        from engine_backends.shared import redact_secrets
        assert SECRET not in redact_secrets(f"error: {SECRET} bad")


class TestAudit:
    def test_success_bind_and_logout_rows(self, isolated_db, app, fake):
        user = _add()
        c = _client(app)
        _sign_in(c, fake)
        c.post("/api/auth/logout", headers={"X-CSRF-Token": c.cookies.get("__Host-baihe_csrf")})
        actions = [a["action"] for a in _audit() if a["user_id"] == user["id"]]
        for action in ("user.bind_google", "login.success", "session.create", "logout"):
            assert action in actions
        success = _audit("login.success")[0]["detail_redacted"]
        assert "203.0.113" in success and "203.0.113.9" not in success   # coarse address only


# --- not set up, auth off, settings, CLI ------------------------------------------------

class TestNotSetUp:
    def test_login_503_and_me_says_not_configured(self, isolated_db):
        app = create_app(_settings(google_client_secret=""))
        c = _client(app)
        r = c.get("/api/auth/login")
        assert r.status_code == 503
        assert r.json()["error"]["message"] == "Sign-in isn't set up on the PC."
        assert c.get("/api/auth/me").json()["sign_in_configured"] is False
        assert c.get("/api/auth/callback").status_code == 503


class TestAuthOff:
    def test_routes_404_and_me_is_the_owner(self, isolated_db):
        app = create_app(ApiSettings(serve_frontend=False))
        c = _client(app, base="http://127.0.0.1:8600", peer=("127.0.0.1", 5000))
        assert c.get("/api/auth/login").status_code == 404
        assert c.get("/api/auth/callback").status_code == 404
        assert c.post("/api/auth/logout", json={}).status_code == 404
        assert c.get("/api/auth/app/start").status_code == 404
        assert c.post("/api/auth/app/confirm", json={}).status_code == 404
        assert c.post("/api/auth/app/redeem", json={}).status_code == 404
        me = c.get("/api/auth/me").json()
        assert me["auth_enabled"] is False and me["signed_in"] is True
        assert me["zone"] == "pc" and me["sign_in_configured"] is False
        assert me["user"] == {"id": None, "email": None, "display_name": "PC owner",
                              "is_admin": True, "is_local_owner": True}
        assert me["permissions"] == sorted(auth_service.PERMISSIONS)

    def test_remote_still_refused_by_the_loopback_gate(self, isolated_db):
        app = create_app(ApiSettings(serve_frontend=False))
        assert _client(app).get("/api/auth/me").status_code == 403


class TestSettings:
    @pytest.mark.parametrize("value,expected", [
        ("", ""), ("https://baihe.example.com", "https://baihe.example.com"),
        ("https://baihe.example.com/", "https://baihe.example.com"),
        ("https://baihe.example.com:8443", "https://baihe.example.com:8443"),
        ("http://localhost:8600", "http://localhost:8600"),
        ("http://127.0.0.1:8600/", "http://127.0.0.1:8600"),
        ("http://[::1]:8600", "http://[::1]:8600"),
    ])
    def test_public_url_accepted(self, value, expected):
        assert normalize_public_url(value) == expected

    @pytest.mark.parametrize("value", [
        "http://baihe.example.com", "http://192.168.1.5:8600", "ftp://baihe.example.com",
        "https://user@baihe.example.com", "https://baihe.example.com/app",
        "https://baihe.example.com/?x=1", "baihe.example.com", "https://",
        "https://baihe.example.com:99999",
    ])
    def test_public_url_refused(self, value):
        with pytest.raises(ValueError) as e:
            normalize_public_url(value)
        assert value not in str(e.value) or value in ("https://",)

    def test_load_settings_reads_sign_in_values(self):
        s = load_settings({"BAIHE_GOOGLE_CLIENT_ID": " cid ", "BAIHE_GOOGLE_CLIENT_SECRET": SECRET,
                           "BAIHE_PUBLIC_URL": "https://baihe.example.com/"})
        assert (s.google_client_id, s.google_client_secret, s.public_url) == (
            "cid", SECRET, "https://baihe.example.com")
        assert s.sign_in_configured is True
        assert load_settings({}).sign_in_configured is False
        bad = load_settings({"BAIHE_GOOGLE_CLIENT_ID": "cid", "BAIHE_GOOGLE_CLIENT_SECRET": SECRET,
                             "BAIHE_PUBLIC_URL": "http://baihe.example.com"})
        assert bad.public_url == "" and "BAIHE_PUBLIC_URL" in bad.public_url_error
        assert bad.sign_in_configured is False
        assert "baihe.example.com" not in bad.public_url_error

    def test_env_file_read_only_at_real_startup(self, tmp_path, monkeypatch):
        from services import settings_service
        env = tmp_path / ".env"
        env.write_text(f'BAIHE_GOOGLE_CLIENT_SECRET="{SECRET}"\nBAIHE_PUBLIC_URL=https://a.example\n')
        monkeypatch.setattr(settings_service, "default_env_path", lambda: str(env))
        monkeypatch.delenv("BAIHE_GOOGLE_CLIENT_SECRET", raising=False)
        assert load_settings().google_client_secret == SECRET
        assert load_settings({}).google_client_secret == ""

    def test_create_app_refuses_a_bad_public_url(self):
        with pytest.raises(ValueError):
            check_bind_safety(_settings(public_url="http://baihe.example.com"))
        with pytest.raises(ValueError):
            create_app(_settings(public_url="https://baihe.example.com/sub"))


class TestRealProviderNoNetwork:
    """The real GoogleProvider through httpx.MockTransport: the code exchange
    goes through Authlib's OAuth2Client with the PKCE verifier and client
    authentication, and nothing is sent anywhere else."""

    def test_exchange_and_jwks(self):
        import httpx
        try:   # Authlib >= 1.7 prefers the httpx2 fork when it is installed
            from authlib.integrations.httpx_client._compat import httpx2 as authlib_httpx
        except ImportError:
            authlib_httpx = httpx
        seen = []

        def handler(request):
            seen.append(request)
            if str(request.url) == oidc_service.GOOGLE_TOKEN_URL:
                return authlib_httpx.Response(200, json={
                    "access_token": "a", "token_type": "Bearer", "id_token": "x.y.z",
                    "expires_in": 60})
            if str(request.url) == oidc_service.GOOGLE_JWKS_URL:
                return httpx.Response(200, json={"keys": []})
            return httpx.Response(404)
        p = oidc_service.GoogleProvider(transport=authlib_httpx.MockTransport(handler))
        token = p.exchange_code(client_id=CLIENT_ID, client_secret=SECRET,
                                redirect_uri=PUBLIC + "/api/auth/callback", code="the-code",
                                code_verifier="v" * 64)
        assert token["id_token"] == "x.y.z"
        jwks_provider = oidc_service.GoogleProvider(transport=httpx.MockTransport(handler))
        assert jwks_provider.fetch_jwks() == {"keys": []}
        post = seen[0]
        body = parse_qs(post.content.decode())
        assert post.method == "POST"
        assert body["grant_type"] == ["authorization_code"] and body["code"] == ["the-code"]
        assert body["code_verifier"] == ["v" * 64]
        assert body["redirect_uri"] == [PUBLIC + "/api/auth/callback"]
        assert "authorization" in post.headers or "client_secret" in body
        assert SECRET not in str(post.url)
        assert [str(r.url) for r in seen] == [oidc_service.GOOGLE_TOKEN_URL,
                                              oidc_service.GOOGLE_JWKS_URL]

    @staticmethod
    def _authlib_httpx():
        try:
            from authlib.integrations.httpx_client._compat import httpx2 as authlib_httpx
        except ImportError:
            import httpx as authlib_httpx
        return authlib_httpx

    def _exchange(self, provider):
        return provider.exchange_code(client_id=CLIENT_ID, client_secret=SECRET,
                                      redirect_uri=PUBLIC + "/api/auth/callback",
                                      code="the-code", code_verifier="v" * 64)

    def test_oversized_token_reply_is_refused(self):
        h = self._authlib_httpx()
        big = b'{"id_token": "' + b"x" * oidc_service.GOOGLE_RESPONSE_MAX_BYTES + b'"}'
        p = oidc_service.GoogleProvider(
            transport=h.MockTransport(lambda request: h.Response(200, content=big)))
        with pytest.raises(oidc_service.LoginError) as exc:
            self._exchange(p)
        assert exc.value.code == "provider_error" and exc.value.reason == "response_too_large"
        assert SECRET not in str(exc.value)

    def test_declared_oversized_token_reply_is_refused(self):
        h = self._authlib_httpx()
        size = str(oidc_service.GOOGLE_RESPONSE_MAX_BYTES + 1)
        p = oidc_service.GoogleProvider(transport=h.MockTransport(
            lambda request: h.Response(200, headers={"Content-Length": size}, content=b"{}")))
        with pytest.raises(oidc_service.LoginError):
            self._exchange(p)

    def test_gzipped_token_reply_is_decoded_once(self):
        import gzip
        import json as _json
        h = self._authlib_httpx()
        body = gzip.compress(_json.dumps({"access_token": "a", "token_type": "Bearer",
                                          "id_token": "x.y.z", "expires_in": 60}).encode())
        p = oidc_service.GoogleProvider(transport=h.MockTransport(lambda request: h.Response(
            200, headers={"Content-Encoding": "gzip"}, content=body)))
        assert self._exchange(p)["id_token"] == "x.y.z"

    def test_oversized_jwks_is_refused(self):
        import httpx
        big = b'{"keys": [], "pad": "' + b"x" * oidc_service.GOOGLE_RESPONSE_MAX_BYTES + b'"}'
        p = oidc_service.GoogleProvider(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=big)))
        with pytest.raises(oidc_service.LoginError):
            p.fetch_jwks()

    def test_oversized_jwks_keeps_the_cached_keys(self):
        import httpx
        big = b'{"keys": [], "pad": "' + b"x" * oidc_service.GOOGLE_RESPONSE_MAX_BYTES + b'"}'
        now = [1000.0]
        signin = oidc_service.SignIn(provider=oidc_service.GoogleProvider(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=big))),
            clock=lambda: now[0])
        signin._jwks, signin._jwks_at = {"keys": [{"kid": "old"}]}, now[0]
        now[0] += oidc_service.JWKS_TTL_SECONDS + 1
        assert signin._keys_for("old") == {"keys": [{"kid": "old"}]}


class TestCli:
    def test_add_user_deactivate_grant(self, isolated_db, capsys):
        from api.__main__ import main
        assert main(["add-user", "Kid@Example.com", "--name", "Kid"]) == 0
        u = auth_service.find_user_by_email("kid@example.com")
        assert u["display_name"] == "Kid" and u["is_active"]
        assert main(["add-user", "kid@example.com"]) == 2              # already exists
        assert main(["grant", "kid@example.com", "media.stream"]) == 0
        assert "media.stream" in auth_service.get_user(u["id"])["permissions"]
        assert main(["grant", "kid@example.com", "admin.users"]) == 2  # admin: flag only
        assert main(["grant", "kid@example.com", "nope.perm"]) == 2
        assert main(["grant", "ghost@example.com", "media.stream"]) == 2
        s = auth_service.create_session(u["id"])
        assert main(["deactivate", "kid@example.com"]) == 0
        assert auth_service.resolve_session(s["session_token"]) is None
        assert main(["deactivate", "ghost@example.com"]) == 2
        out = capsys.readouterr()
        assert s["session_token"] not in out.out + out.err


# --- phone app hand-off ----------------------------------------------------------

_VERIFIER = "v" * 20 + "erifier-for-the-phone-app-0123456789"


def _browser(app, user):
    """A browser (Chrome on the phone) already signed in as `user`."""
    c = _client(app)
    s = auth_service.create_session(user["id"])
    c.cookies.set("__Host-baihe_session", s["session_token"], domain="baihe.example.com")
    return c


def _confirm_form(page_html):
    import re
    return dict(re.findall(r"name=(challenge|ticket) value='([^']*)'", page_html))


def _hand_off(app, user, verifier=_VERIFIER):
    """Chrome confirms; returns the code from the app link."""
    c = _browser(app, user)
    challenge = oidc_service.app_challenge_for(verifier)
    page = c.get("/api/auth/app/start", params={"challenge": challenge})
    assert page.status_code == 200, page.text
    r = c.post("/api/auth/app/confirm", data=_confirm_form(page.text))
    assert r.status_code == 303, r.text
    location = r.headers["location"]
    assert location.startswith("baihe-shell://signed-in?code=")
    return parse_qs(urlsplit(location).query)["code"][0]


def _app(app):
    """The phone app's web view: its own cookie jar, no Origin on its post."""
    return _client(app)


class TestPhoneAppHandOff:
    def test_round_trip_gives_the_app_its_own_session(self, isolated_db, app):
        user = _add()
        code = _hand_off(app, user)
        web_view = _app(app)
        r = web_view.post("/api/auth/app/redeem", data={"code": code, "verifier": _VERIFIER},
                          headers={"Sec-Fetch-Site": "none"})
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert r.headers["cache-control"] == "no-store"
        set_cookies = "\n".join(_cookies(r)).lower()
        assert "__host-baihe_session=" in set_cookies and "__host-baihe_csrf=" in set_cookies
        me = web_view.get("/api/auth/me").json()
        assert me["signed_in"] and me["user"]["id"] == user["id"]
        # A separate device from the browser that confirmed it.
        assert len(auth_service.list_own_sessions(user["id"], 0)) == 2
        assert any(a["detail_redacted"].startswith("app user") for a in _audit("login.success"))

    def test_start_page_names_the_account_and_cannot_be_framed(self, isolated_db, app):
        user = _add()
        c = _browser(app, user)
        page = c.get("/api/auth/app/start",
                     params={"challenge": oidc_service.app_challenge_for(_VERIFIER)})
        assert "Sign in the Baihe Studio app?" in page.text
        assert "on this phone as kid@example.com." in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "form-action 'self' baihe-shell:" in page.headers["content-security-policy"]
        assert page.headers["cache-control"] == "no-store"

    def test_signed_out_browser_goes_through_google_first(self, isolated_db, app):
        challenge = oidc_service.app_challenge_for(_VERIFIER)
        r = _client(app).get("/api/auth/app/start", params={"challenge": challenge})
        assert r.status_code == 302
        q = parse_qs(urlsplit(r.headers["location"]).query)
        assert urlsplit(r.headers["location"]).path == "/api/auth/login"
        assert q["return_to"] == [f"/api/auth/app/start?challenge={challenge}"]
        assert oidc_service.safe_return_to(q["return_to"][0]) == q["return_to"][0]

    @pytest.mark.parametrize("challenge", ["", "short", "x" * 42 + "!", "x" * 44])
    def test_bad_challenge_shows_expired_page(self, isolated_db, app, challenge):
        c = _browser(app, _add())
        r = c.get("/api/auth/app/start", params={"challenge": challenge})
        assert r.status_code == 400
        assert "This sign-in link has expired. Start again from the app." in r.text

    def test_confirm_needs_the_ticket_from_this_session(self, isolated_db, app):
        user = _add()
        challenge = oidc_service.app_challenge_for(_VERIFIER)
        c = _browser(app, user)
        # Forged or missing ticket, as a cross-site form would send.
        for data in ({"challenge": challenge}, {"challenge": challenge, "ticket": "0" * 64}):
            r = c.post("/api/auth/app/confirm", data=data)
            assert r.status_code == 400 and "location" not in r.headers
        # A ticket from another session of the same user doesn't carry over.
        other = _browser(app, user)
        form = _confirm_form(other.get("/api/auth/app/start",
                                       params={"challenge": challenge}).text)
        assert c.post("/api/auth/app/confirm", data=form).status_code == 400
        # Nor to a different challenge.
        form = _confirm_form(c.get("/api/auth/app/start", params={"challenge": challenge}).text)
        form["challenge"] = oidc_service.app_challenge_for("w" * 50)
        assert c.post("/api/auth/app/confirm", data=form).status_code == 400

    def test_confirm_without_a_session_is_refused(self, isolated_db, app):
        form = {"challenge": oidc_service.app_challenge_for(_VERIFIER), "ticket": "0" * 64}
        assert _client(app).post("/api/auth/app/confirm", data=form).status_code == 400

    def test_code_is_single_use_even_when_the_verifier_is_wrong(self, isolated_db, app):
        user = _add()
        code = _hand_off(app, user)
        r = _app(app).post("/api/auth/app/redeem", data={"code": code, "verifier": "w" * 50})
        assert r.headers["location"] == "/?login_error=expired"
        r = _app(app).post("/api/auth/app/redeem", data={"code": code, "verifier": _VERIFIER})
        assert r.headers["location"] == "/?login_error=expired"
        assert "__host-baihe_session=" not in "\n".join(_cookies(r)).lower()
        assert len(auth_service.list_own_sessions(user["id"], 0)) == 1

    def test_code_without_its_verifier_is_useless(self, isolated_db, app):
        """Another app that catches the link has the code but not the secret."""
        user = _add()
        code = _hand_off(app, user)
        for verifier in ("", "short", oidc_service.app_challenge_for(_VERIFIER)):
            r = _app(app).post("/api/auth/app/redeem", data={"code": code, "verifier": verifier})
            assert r.headers["location"] == "/?login_error=expired"
            code = _hand_off(app, user)

    def test_code_expires(self, isolated_db, app, clock):
        code = _hand_off(app, _add())
        clock[0] += oidc_service.APP_CODE_TTL_SECONDS
        r = _app(app).post("/api/auth/app/redeem", data={"code": code, "verifier": _VERIFIER})
        assert r.headers["location"] == "/?login_error=expired"

    @pytest.mark.parametrize("headers", [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
        {"Origin": "https://baihe.example.com.evil.example"},
        {"Origin": "null"},
        {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    ])
    def test_redeem_posted_by_another_site_is_refused(self, isolated_db, app, headers):
        """Stops a page signing a browser in to the attacker's own account."""
        code = _hand_off(app, _add())
        r = _app(app).post("/api/auth/app/redeem", data={"code": code, "verifier": _VERIFIER},
                           headers=headers)
        assert r.headers["location"] == "/?login_error=expired"
        assert "__host-baihe_session=" not in "\n".join(_cookies(r)).lower()

    @pytest.mark.parametrize("headers", [
        {"Origin": PUBLIC, "Sec-Fetch-Site": "same-origin"},
        {"Origin": "null", "Sec-Fetch-Site": "none"},   # what WebView.postUrl sends
    ])
    def test_this_site_and_the_web_view_are_accepted(self, isolated_db, app, headers):
        code = _hand_off(app, _add())
        r = _app(app).post("/api/auth/app/redeem", data={"code": code, "verifier": _VERIFIER},
                           headers=headers)
        assert r.headers["location"] == "/"

    def test_redeem_revokes_the_apps_old_session(self, isolated_db, app):
        user = _add()
        old = auth_service.create_session(user["id"])
        code = _hand_off(app, user)
        web_view = _app(app)
        web_view.cookies.set("__Host-baihe_session", old["session_token"],
                             domain="baihe.example.com")
        web_view.post("/api/auth/app/redeem", data={"code": code, "verifier": _VERIFIER})
        assert auth_service.resolve_session(old["session_token"]) is None

    def test_deactivated_user_gets_no_session(self, isolated_db, app):
        user = _add()
        code = _hand_off(app, user)
        auth_service.deactivate_user(user["id"])
        r = _app(app).post("/api/auth/app/redeem", data={"code": code, "verifier": _VERIFIER})
        assert r.headers["location"] == "/?login_error=not_allowed"

    def test_codes_are_capped_per_user(self, isolated_db, app):
        user = _add()
        codes = [_hand_off(app, user) for _ in range(oidc_service.MAX_APP_CODES_PER_USER + 1)]
        r = _app(app).post("/api/auth/app/redeem", data={"code": codes[0], "verifier": _VERIFIER})
        assert r.headers["location"] == "/?login_error=expired"
        r = _app(app).post("/api/auth/app/redeem", data={"code": codes[-1], "verifier": _VERIFIER})
        assert r.headers["location"] == "/"

    def test_redeem_is_rate_limited(self, isolated_db, app):
        c = _app(app)
        for _ in range(oidc_service.APP_RATE[0]):
            c.post("/api/auth/app/redeem", data={"code": "x", "verifier": _VERIFIER})
        r = c.post("/api/auth/app/redeem", data={"code": "x", "verifier": _VERIFIER})
        assert r.status_code == 429 and "Sign-in paused" in r.text
