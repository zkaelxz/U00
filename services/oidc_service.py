"""
services/oidc_service.py -- Google sign-in (step 134): OpenID Connect
authorization code flow with PKCE (S256), state and nonce, and the mapping
from a verified Google identity to an allowlisted Baihe user.

UI-free: `api/routers/auth_routes.py` is the HTTP layer (cookies,
redirects). One `SignIn` object per app holds the in-memory state: pending
login transactions, the Google signing-key cache and the rate limiters.
That fits the one-process design (`auth_service.SlidingWindowRateLimiter`)
and keeps the database out of it; a restart only drops logins that are in
the middle of the Google round trip.

Security rules kept here:
- A pending transaction is single use (popped on the first callback,
  valid or not) and expires after 10 minutes. `state` and `nonce` are
  compared with `hmac.compare_digest`.
- The `id_token` is verified against Google's JWKS (RS256 only; cached,
  re-fetched on an unknown `kid` at most once a minute): `iss` is Google,
  `aud` is our client id, `exp`/`iat` hold, the nonce matches, and
  `email_verified` is literally True.
- A returning user is matched by Google `sub` only. A user with no binding
  yet is bound on first login by an allowlisted, verified email; an email
  already bound to another `sub`, an inactive user or an email not on the
  allowlist is refused. Nobody is created here.
- Every outbound HTTP call has a timeout (Authlib's httpx `OAuth2Client`
  for the code exchange, httpx for the JWKS). The client secret is only
  ever sent to Google's token endpoint; it is never logged, audited or
  returned, and every failure is reduced to a generic code.
- Authlib is imported lazily, so the app still starts without it; sign-in
  then reports 503 (`DependencyUnavailableError`).
"""

import base64
import hmac
import ipaddress
import json
import secrets
import threading
import time
import warnings
from collections import OrderedDict
from urllib.parse import urlencode, urlsplit

from services import auth_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     RateLimitedError)

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CALLBACK_PATH = "/api/auth/callback"

HTTP_TIMEOUT_SECONDS = 10
TRANSACTION_TTL_SECONDS = 600
MAX_PENDING_TRANSACTIONS = 1000
MAX_PENDING_PER_SOURCE = 20       # per rate-limit bucket (IPv4 address / IPv6 /64)
JWKS_TTL_SECONDS = 3600
JWKS_MIN_REFETCH_SECONDS = 60
CLOCK_LEEWAY_SECONDS = 60
MAX_RETURN_TO = 512
_MAX_PARAM = 4096

# (max events, window seconds). Login and callback each pass three tiers: the
# client's bucket (IPv4 address / IPv6 /64), its IPv6 /56 (one home delegation)
# and its wide net (IPv4 /24 / IPv6 /48). There is no global cap, so no number
# of networks can use up sign-in for everyone else.
LOGIN_RATE = (20, 600)            # per bucket
CALLBACK_RATE = (20, 600)         # per bucket
NET56_RATE = (40, 600)            # per IPv6 /56, login and callback each
NET_RATE = (200, 600)             # per IPv4 /24 or IPv6 /48, login and callback each
_AUDIT_RATE_LIMITED = (30, 600)   # cap on login.rate_limited audit rows

# The only values a failed sign-in redirect carries (`/?login_error=<code>`).
LOGIN_ERROR_CODES = ("denied", "not_allowed", "expired", "provider_error")


class LoginError(Exception):
    """A refused or failed sign-in. `code` is one of LOGIN_ERROR_CODES (what
    the browser sees); `action`/`reason` go to the audit log only."""

    def __init__(self, code: str, action: str, reason: str):
        super().__init__(code)
        self.code, self.action, self.reason = code, action, reason


class _JwksUnavailable(Exception):
    pass


def _jose():
    """authlib.jose (deprecated upstream in favour of joserfc, compatible
    until Authlib 2.0 -- hence `authlib<2` in requirements-core.txt)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from authlib.jose import JsonWebKey, JsonWebToken
            from authlib.jose.errors import JoseError
    except ImportError:
        raise DependencyUnavailableError(
            "Sign-in needs the authlib package. Install requirements-core.txt on the PC.")
    return JsonWebKey, JsonWebToken, JoseError


def _s256(verifier: str) -> str:
    try:
        from authlib.oauth2.rfc7636 import create_s256_code_challenge
    except ImportError:
        raise DependencyUnavailableError(
            "Sign-in needs the authlib package. Install requirements-core.txt on the PC.")
    return create_s256_code_challenge(verifier)


class GoogleProvider:
    """The real Google endpoints. Tests inject a fake with the same three
    members through `app.state.oidc_provider`. `transport` is an optional
    httpx transport (tests use `httpx.MockTransport`; no network)."""

    authorize_endpoint = GOOGLE_AUTHORIZE_URL

    def __init__(self, timeout: float = HTTP_TIMEOUT_SECONDS, transport=None):
        self.timeout = timeout
        self.transport = transport

    def exchange_code(self, *, client_id, client_secret, redirect_uri, code, code_verifier):
        """Server-to-server code exchange. Returns the token response dict."""
        from authlib.integrations.httpx_client import OAuth2Client
        extra = {"transport": self.transport} if self.transport is not None else {}
        with OAuth2Client(client_id=client_id, client_secret=client_secret,
                          redirect_uri=redirect_uri, timeout=self.timeout,
                          follow_redirects=False, **extra) as client:
            token = client.fetch_token(GOOGLE_TOKEN_URL, grant_type="authorization_code",
                                       code=code, code_verifier=code_verifier,
                                       timeout=self.timeout)
        return dict(token)

    def fetch_jwks(self) -> dict:
        import httpx
        extra = {"transport": self.transport} if self.transport is not None else {}
        with httpx.Client(timeout=self.timeout, follow_redirects=False, **extra) as client:
            resp = client.get(GOOGLE_JWKS_URL, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()


def safe_return_to(value) -> str:
    """A same-site relative path (`/...`, not `//...`, no backslash, no
    control or whitespace characters, no scheme or host); anything else is
    `/`. Stops the sign-in redirect being used as an open redirect."""
    if not isinstance(value, str) or not value or len(value) > MAX_RETURN_TO:
        return "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in value):
        return "/"
    try:
        parts = urlsplit(value)
    except ValueError:
        return "/"
    if parts.scheme or parts.netloc:
        return "/"
    return value


def config_from_settings(settings):
    """{client_id, client_secret, redirect_uri} or None when sign-in isn't
    set up. Never returned to a client."""
    if not getattr(settings, "sign_in_configured", False):
        return None
    return {"client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.public_url + CALLBACK_PATH}


def _unverified_header(token: str) -> dict:
    try:
        head = token.split(".", 1)[0]
        head += "=" * (-len(head) % 4)
        data = json.loads(base64.urlsafe_b64decode(head.encode("ascii")))
    except (ValueError, UnicodeError, AttributeError):
        raise LoginError("provider_error", "login.error", "token")
    if not isinstance(data, dict):
        raise LoginError("provider_error", "login.error", "token")
    return data


def _bounded(value) -> str:
    return value if isinstance(value, str) and len(value) <= _MAX_PARAM else ""


def _net56_key(ip: str) -> str:
    """The IPv6 /56 of a client address; "" for IPv4 or unparseable."""
    addr = auth_service.parse_ip(ip)
    if addr is None or addr.version == 4:
        return ""
    return str(ipaddress.ip_network(f"{addr}/56", strict=False))


def _net_key(ip: str) -> str:
    """The wide net of a client address (IPv4 /24, IPv6 /48); "" if unparseable."""
    return auth_service.ip_prefix(ip)


class SignIn:
    """Per-app sign-in state. `clock` is monotonic (transactions, JWKS cache,
    rate limits); `wall` is epoch seconds (token exp/iat). Both injectable."""

    def __init__(self, provider=None, clock=time.monotonic, wall=time.time):
        self.provider = provider or GoogleProvider()
        self._clock, self._wall = clock, wall
        self._pending = OrderedDict()
        self._pending_lock = threading.Lock()
        self._jwks = None
        self._jwks_at = None
        self._jwks_lock = threading.Lock()
        limiter = auth_service.SlidingWindowRateLimiter
        self._limiters = {
            kind: [(auth_service.rate_limit_key, limiter(*rate, clock=clock)),
                   (_net56_key, limiter(*NET56_RATE, clock=clock)),
                   (_net_key, limiter(*NET_RATE, clock=clock))]
            for kind, rate in (("login", LOGIN_RATE), ("callback", CALLBACK_RATE))
        }
        self._audit_limiter = limiter(*_AUDIT_RATE_LIMITED, clock=clock)

    # --- rate limiting -------------------------------------------------------

    def check_rate(self, kind: str, client_ip: str):
        """Limits "login" or "callback" per tier, finest first (see NET_RATE);
        a request refused by one tier isn't charged to the wider ones, so a
        single /64 flooding past its own limit doesn't use up its /56 or /48.
        Raises RateLimitedError (429). Rate-limited attempts are audited,
        themselves capped so a flood can't grow the audit table."""
        try:
            for key_of, tier in self._limiters[kind]:
                key = key_of(client_ip)
                if key:
                    tier.hit(key)
        except RateLimitedError:
            try:
                self._audit_limiter.hit("*")
                auth_service.write_audit(None, "login.rate_limited",
                                         f"{kind} ip {auth_service.ip_prefix(client_ip)}")
            except RateLimitedError:
                pass
            raise

    # --- pending transactions -------------------------------------------------

    def _store(self, record: dict, source: str = "", net: str = "") -> str:
        """Keeps at most MAX_PENDING_PER_SOURCE per source and
        MAX_PENDING_TRANSACTIONS overall. When full, the wide net (IPv4 /24,
        IPv6 /48) holding the most is picked, and the oldest transaction of its
        busiest source is dropped, so a flood from one source, or spread one
        per /64 across a /48, evicts its own transactions, not other people's."""
        txn_id = secrets.token_urlsafe(32)
        now = self._clock()
        with self._pending_lock:
            while self._pending:
                oldest = next(iter(self._pending.values()))
                if now - oldest["created"] < TRANSACTION_TTL_SECONDS:
                    break
                self._pending.popitem(last=False)
            if sum(rec["source"] == source
                   for rec in self._pending.values()) >= MAX_PENDING_PER_SOURCE:
                self._evict_oldest_of(source)
            elif len(self._pending) >= MAX_PENDING_TRANSACTIONS:
                self._evict_oldest_of(self._busiest_source())
            self._pending[txn_id] = dict(record, created=now, source=source,
                                         net=net or source)
        return txn_id

    def _busiest_source(self) -> str:
        nets, sources = {}, {}
        for rec in self._pending.values():
            nets[rec["net"]] = nets.get(rec["net"], 0) + 1
        net = max(nets, key=nets.get)
        for rec in self._pending.values():
            if rec["net"] == net:
                sources[rec["source"]] = sources.get(rec["source"], 0) + 1
        return max(sources, key=sources.get)

    def _evict_oldest_of(self, source: str):
        for key, rec in self._pending.items():
            if rec["source"] == source:
                del self._pending[key]
                return

    def _take(self, txn_id):
        """Single use: removed whether or not it is still valid."""
        if not isinstance(txn_id, str) or not txn_id or len(txn_id) > 200:
            return None
        with self._pending_lock:
            record = self._pending.get(txn_id)
            if record is None:
                return None
            if self._clock() - record["created"] >= TRANSACTION_TTL_SECONDS:
                del self._pending[txn_id]
                return None
            return self._pending.pop(txn_id)

    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    # --- the flow ---------------------------------------------------------------

    def begin(self, config: dict, return_to, client_ip: str = "") -> tuple:
        """-> (transaction id for the cookie, Google authorization URL)."""
        verifier = secrets.token_urlsafe(64)   # 86 chars, within RFC 7636's 43-128
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        challenge = _s256(verifier)
        _jose()   # fail before redirecting if the callback couldn't verify
        txn_id = self._store({"state": state, "nonce": nonce, "verifier": verifier,
                              "return_to": safe_return_to(return_to)},
                             source=auth_service.rate_limit_key(client_ip),
                             net=_net_key(client_ip))
        query = urlencode({
            "response_type": "code", "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"], "scope": "openid email profile",
            "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256", "prompt": "select_account",
        })
        return txn_id, f"{self.provider.authorize_endpoint}?{query}"

    def complete(self, config: dict, txn_id, *, state, code, error, presented_session=None,
                 user_agent: str = "", client_ip: str = "") -> dict:
        """Finishes a sign-in. Returns {session_token, csrf_token, return_to,
        user_id}; raises LoginError (already audited) on any refusal. On
        success the session the client presented, if any, is revoked and a
        fresh one is created (never adopted)."""
        ip = auth_service.ip_prefix(client_ip)
        email = None
        try:
            record = self._take(txn_id)
            state = _bounded(state)
            if record is None or not state or not hmac.compare_digest(record["state"], state):
                raise LoginError("expired", "login.error", "state")
            if error:
                raise LoginError("denied", "login.error", "provider_denied")
            code = _bounded(code)
            if not code:
                raise LoginError("provider_error", "login.error", "token")
            try:
                tokens = self.provider.exchange_code(
                    client_id=config["client_id"], client_secret=config["client_secret"],
                    redirect_uri=config["redirect_uri"], code=code,
                    code_verifier=record["verifier"])
            except Exception:
                raise LoginError("provider_error", "login.error", "token")
            id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
            if not isinstance(id_token, str) or not id_token:
                raise LoginError("provider_error", "login.error", "token")
            claims = self.verify_id_token(id_token, config["client_id"], record["nonce"])
            email = claims.get("email") if isinstance(claims.get("email"), str) else None
            if claims.get("email_verified") is not True:
                raise LoginError("not_allowed", "login.denied", "unverified")
            user = resolve_user(claims["sub"], email)
        except LoginError as exc:
            detail = f"{exc.reason} ip {ip}"
            if email and exc.action == "login.denied":
                detail += f" email {email[:254]}"   # so the admin can see who tried
            auth_service.write_audit(None, exc.action, detail)
            raise
        if presented_session:
            old = auth_service.resolve_session(presented_session)
            if old:
                auth_service.revoke_session(old["session_id"], old["user_id"])
        session = auth_service.create_session(user["id"], user_agent, client_ip)
        auth_service.write_audit(user["id"], "login.success", f"user {user['id']} ip {ip}")
        return {"session_token": session["session_token"], "csrf_token": session["csrf_token"],
                "return_to": record["return_to"], "user_id": user["id"]}

    # --- id_token verification ------------------------------------------------

    def _keys_for(self, kid):
        """The cached JWKS, re-fetched when stale or when `kid` is unknown --
        but never more than once per JWKS_MIN_REFETCH_SECONDS, so forged
        tokens with random kids can't turn into a stream of fetches."""
        with self._jwks_lock:
            now = self._clock()
            cached = self._jwks
            fresh = cached is not None and now - self._jwks_at < JWKS_TTL_SECONDS
            if fresh and any(k.get("kid") == kid for k in cached["keys"]):
                return cached
            if cached is not None and now - self._jwks_at < JWKS_MIN_REFETCH_SECONDS:
                return cached
            try:
                jwks = self.provider.fetch_jwks()
            except Exception:
                jwks = None
            if not (isinstance(jwks, dict) and isinstance(jwks.get("keys"), list)
                    and all(isinstance(k, dict) for k in jwks["keys"])):
                if cached is not None:
                    return cached
                raise _JwksUnavailable()
            self._jwks, self._jwks_at = jwks, now
            return jwks

    def verify_id_token(self, id_token: str, client_id: str, nonce: str) -> dict:
        JsonWebKey, JsonWebToken, JoseError = _jose()
        header = _unverified_header(id_token)
        if header.get("alg") != "RS256":
            raise LoginError("provider_error", "login.error", "token")
        try:
            jwks = self._keys_for(header.get("kid"))
        except _JwksUnavailable:
            raise LoginError("provider_error", "login.error", "jwks")
        try:
            key_set = JsonWebKey.import_key_set(jwks)
            claims = JsonWebToken(["RS256"]).decode(id_token, key_set, claims_options={
                "iss": {"essential": True, "values": list(GOOGLE_ISSUERS)},
                "aud": {"essential": True, "value": client_id},
                "exp": {"essential": True},
                "iat": {"essential": True},
                "sub": {"essential": True},
            })
            claims.validate(now=int(self._wall()), leeway=CLOCK_LEEWAY_SECONDS)
        except (JoseError, ValueError, TypeError, KeyError):
            raise LoginError("provider_error", "login.error", "token")
        claims = dict(claims)
        # Belt and braces over Authlib's own checks.
        aud = claims.get("aud")
        aud_ok = aud == client_id or (isinstance(aud, list) and client_id in aud
                                      and claims.get("azp") == client_id)
        sub = claims.get("sub")
        token_nonce = claims.get("nonce")
        if (claims.get("iss") not in GOOGLE_ISSUERS or not aud_ok
                or not isinstance(sub, str) or not sub or len(sub) > 255
                or not isinstance(token_nonce, str)
                or not hmac.compare_digest(token_nonce.encode(), nonce.encode())):
            raise LoginError("provider_error", "login.error", "token")
        return claims


def resolve_user(google_sub: str, email) -> dict:
    """Allowlisted, active user for a verified Google identity, or LoginError.
    1. by `sub` (a returning user; their email may have changed since);
    2. else an allowlisted email with no binding yet: bind this `sub` to it;
    3. else refuse (not on the allowlist, bound to another `sub`, inactive)."""
    user = auth_service.find_user_by_google_sub(google_sub)
    if user is not None:
        if not user["is_active"]:
            raise LoginError("not_allowed", "login.denied", "inactive")
        return user
    candidate = auth_service.find_user_by_email(email) if email else None
    if candidate is None:
        raise LoginError("not_allowed", "login.denied", "not_allowlisted")
    if candidate["has_google_binding"]:
        raise LoginError("not_allowed", "login.denied", "bound_other")
    if not candidate["is_active"]:
        raise LoginError("not_allowed", "login.denied", "inactive")
    try:
        auth_service.bind_google_sub(candidate["id"], google_sub)
    except ConflictError:
        raise LoginError("not_allowed", "login.denied", "bound_other")
    return auth_service.get_user(candidate["id"])


def sign_out(principal: dict):
    """Revokes the caller's own session and audits it."""
    auth_service.revoke_session(principal["session_id"], principal["user_id"])
    auth_service.write_audit(principal["user_id"], "logout", f"session {principal['session_id']}")
