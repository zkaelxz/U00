"""
services/device_token_service.py -- per-device API tokens for the browser
extension (docs/remote-access-decision.md, Extension; docs/browser-extension.md).

A household member creates one token per computer in Settings; the
extension will send it as `Authorization: Bearer <token>` to the bridge
routes (`api.auth.require_device_token`). UI-free; the table is
`device_tokens.py`.

Security rules kept here:
- A token is `baihe_dt_` + `secrets.token_urlsafe(32)` (256 bits). It is
  returned once by `create_token` and never again; only its SHA-256 is
  stored (like session ids: a random 256-bit value needs no slow or keyed
  hash), re-compared with `hmac.compare_digest` after the indexed lookup.
  The prefix lets `translate_engines.redact_secrets` scrub a token from
  error text and logs.
- Every refusal of a presented token is the same 401 text, whether the
  token is malformed, unknown, revoked, expired or its user inactive.
  Failures are throttled per client address and per network prefix.
- A token acts as its user with member rights only: never an admin
  permission or the admin override, whatever the account, so a stolen
  token is never more than a member's extension. `extension.send` is
  re-read on every request; losing it (permission revoked, admin rights
  removed, account deactivated) also revokes the user's tokens, so a later
  re-grant doesn't silently revive a forgotten one.
- A user manages only their own tokens (the user id comes from their
  session) and may revoke them from anywhere; the owner at the PC lists and
  revokes anyone's (PC-only routes). Creating an admin account's token, or
  revoking it as someone else, is PC-only (D5).
"""

import hashlib
import hmac
import re
import secrets
import time
import unicodedata

import db
import device_tokens
from services import auth_service
from services.service_errors import (ConflictError, ForbiddenError, InvalidInputError,
                                     NotFoundError, RateLimitedError, UnauthenticatedError)

PERMISSION = "extension.send"
TOKEN_PREFIX = "baihe_dt_"
_TOKEN_RE = re.compile(r"baihe_dt_[A-Za-z0-9_-]{43}")
MAX_LABEL_CHARS = 40
MAX_LIVE_TOKENS = 10
MAX_EXPIRY_DAYS = 365
# An omitted expiry must not mean a token that lives forever; only an explicit
# None (the settings page's "Never" choice) does.
DEFAULT_EXPIRY_DAYS = 90
_TOUCH_INTERVAL_SECONDS = 60
_KEEP_ENDED_SECONDS = 30 * 24 * 3600
_GENERIC_401 = "Authentication required."
_NOT_FOUND = "That device token isn't active."
ADMIN_TOKENS_AT_PC_ONLY = ("An admin account's extension devices can only be added at "
                           "the PC. You can revoke one from anywhere.")

# Creation: a few per hour per user is plenty for setting up computers.
_create_limiter = auth_service.SlidingWindowRateLimiter(5, 3600)


class _FailureLimiter(auth_service.SlidingWindowRateLimiter):
    """Counts only failed presentations; `blocked` peeks without counting."""

    def blocked(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            q = self._events.get(key)
            return bool(q) and sum(1 for t in q if now - t < self.window) >= self.max_events

    def record(self, key: str):
        try:
            self.hit(key)
        except RateLimitedError:
            pass


# Like sign-in: per client bucket (IPv4 address, IPv6 /64), then per IPv4
# /24 or IPv6 /48, each per 10 minutes; no global cap, so no number of
# networks can lock every household member's extension out.
_fail_by_client = _FailureLimiter(20, 600)
_fail_by_prefix = _FailureLimiter(100, 600)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _status(row: dict, now: float) -> str:
    if row["revoked_at"] is not None:
        return "revoked"
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return "expired"
    return "active"


def _public(row: dict, now: float) -> dict:
    """No hash and no token, ever."""
    return {"id": row["id"], "label": row["label"], "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
            "last_used_ip_prefix": row["last_used_ip_prefix"] or "",
            "expires_at": row["expires_at"], "revoked_at": row["revoked_at"],
            "status": _status(row, now)}


def _clean_label(label) -> str:
    text = " ".join(str(label or "").split())
    if not text or len(text) > MAX_LABEL_CHARS:
        raise InvalidInputError(f"Name the device in 1 to {MAX_LABEL_CHARS} characters.")
    if any(unicodedata.category(ch)[0] == "C" for ch in text):
        raise InvalidInputError("The device name has characters that aren't allowed.")
    return text


def _require_pc_for_admin(is_admin: bool, at_pc: bool):
    if is_admin and not at_pc:
        raise ForbiddenError(ADMIN_TOKENS_AT_PC_ONLY)


# --- the signed-in user's own tokens -------------------------------------------

def create_token(principal: dict, label, expires_in_days=DEFAULT_EXPIRY_DAYS, ip: str = "",
                 at_pc: bool = False, now: float = None) -> dict:
    """{"token": <shown once>, "device_token": <public row>}. 404 without an
    account (the owner at the PC with sign-in off has none), 403 without
    `extension.send` or for an admin away from the PC, 429 past the
    creation rate, 409 at MAX_LIVE_TOKENS live tokens."""
    user_id = principal.get("user_id") if principal else None
    if user_id is None:
        raise NotFoundError("Not found.")
    if PERMISSION not in principal.get("permissions", ()):
        raise ForbiddenError("Not allowed.")
    _require_pc_for_admin(bool(principal.get("is_admin")), at_pc)
    label = _clean_label(label)
    if expires_in_days is not None and not (
            isinstance(expires_in_days, int) and 1 <= expires_in_days <= MAX_EXPIRY_DAYS):
        raise InvalidInputError(f"Expiry must be 1 to {MAX_EXPIRY_DAYS} days, or none.")
    _create_limiter.hit(f"user:{user_id}")
    now = time.time() if now is None else now
    device_tokens.prune_old(user_id, now - _KEEP_ENDED_SECONDS)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = now + expires_in_days * 86400 if expires_in_days else None
    token_id = device_tokens.insert_under_cap(user_id, label, _hash(token), now, expires_at,
                                              MAX_LIVE_TOKENS)
    if token_id is None:
        raise ConflictError(f"You already have {MAX_LIVE_TOKENS} extension devices. "
                            "Revoke one first.")
    auth_service.write_audit(user_id, "device_token.create",
                             f"token {token_id} ip {auth_service.ip_prefix(ip)}")
    return {"token": token, "device_token": _public(device_tokens.get(token_id), now)}


def list_own(user_id: int, now: float = None) -> list:
    now = time.time() if now is None else now
    return [_public(r, now) for r in device_tokens.list_for_user(user_id)]


def revoke_own(user_id: int, token_id: int, ip: str = "", now: float = None) -> dict:
    """404 for any id that isn't one of the caller's unrevoked tokens,
    whether or not it exists. Allowed from anywhere, admins included:
    revoking only removes access, and an admin with a lost laptop is away
    from the PC."""
    now = time.time() if now is None else now
    if not device_tokens.revoke(token_id, now, user_id=user_id):
        raise NotFoundError(_NOT_FOUND)
    auth_service.write_audit(user_id, "device_token.revoke",
                             f"token {token_id} ip {auth_service.ip_prefix(ip)}")
    return {"revoked": 1}


# --- admin -----------------------------------------------------------------------

def admin_list(now: float = None) -> list:
    """Every user's tokens with the owner's display name (else a masked
    email, never the address)."""
    now = time.time() if now is None else now
    return [dict(_public(r, now), user_id=r["user_id"],
                 user_name=r["user_display_name"] or auth_service.mask_email(r["user_email"]))
            for r in device_tokens.list_all()]


def admin_revoke(token_id: int, actor_id=None, *, at_pc: bool, now: float = None) -> dict:
    row = device_tokens.get(token_id)
    if row is None or row["revoked_at"] is not None:
        raise NotFoundError(_NOT_FOUND)
    owner = db.auth_get_user(row["user_id"]) or {}
    _require_pc_for_admin(bool(owner.get("is_admin")), at_pc)
    now = time.time() if now is None else now
    if not device_tokens.revoke(token_id, now):
        raise NotFoundError(_NOT_FOUND)
    auth_service.write_audit(actor_id, "device_token.admin_revoke",
                             f"token {token_id} user {row['user_id']}")
    return {"revoked": 1}


def revoke_all_for_user(user_id: int, actor_id=None, now: float = None) -> int:
    """Called when the user loses `extension.send` or is deactivated."""
    now = time.time() if now is None else now
    n = device_tokens.revoke_all_for_user(user_id, now)
    if n:
        auth_service.write_audit(actor_id, "device_token.revoke_all", f"user {user_id}: {n}")
    return n


# --- presenting a token ---------------------------------------------------------

def _failure_keys(ip: str) -> tuple:
    return ((_fail_by_client, auth_service.rate_limit_key(ip)),
            (_fail_by_prefix, auth_service.ip_prefix(ip) or "unknown"))


def _principal_for(row: dict, now: float):
    if (row["revoked_at"] is not None
            or (row["expires_at"] is not None and row["expires_at"] <= now)):
        return None
    user = db.auth_get_user(row["user_id"])
    if not user or not user["is_active"]:
        return None
    return {"user_id": user["id"], "email": user["email"], "is_admin": False,
            "is_local_owner": False, "admin_override": False, "device_token_id": row["id"],
            "permissions": [p for p in auth_service.effective_permissions(user["id"])
                            if p not in auth_service.ADMIN_PERMISSIONS]}


def authenticate(authorization_headers, ip: str = "", now: float = None) -> dict:
    """The principal for a request's `Authorization` header values (the
    header only: never a query string or cookie). Exactly one header, the
    Bearer scheme and a well-formed token, else 401. A token that verifies
    succeeds even from an address over the failure limit (a revoked laptop
    retrying must not lock out valid tokens behind the same address); an
    unverified one gets 429 while the client is over the limit, which still
    caps guessing and database lookups per address. 403 when the user lacks `extension.send`.
    Records the use at most once a minute per token."""
    keys = _failure_keys(ip)
    now = time.time() if now is None else now
    principal = row = None
    values = list(authorization_headers or ())
    if len(values) == 1:
        scheme, _, token = values[0].strip().partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and _TOKEN_RE.fullmatch(token):
            digest = _hash(token)
            row = device_tokens.get_by_hash(digest)
            if row and hmac.compare_digest(row["token_hash"], digest):
                principal = _principal_for(row, now)
    if principal is None:
        # A request with no Authorization header isn't a guess, so it neither
        # counts nor is blocked: otherwise anything behind a shared address
        # could fill the window with header-less requests.
        if not values:
            raise UnauthenticatedError(_GENERIC_401)
        if any(limiter.blocked(key) for limiter, key in keys):
            raise RateLimitedError("Too many attempts. Try again later.")
        for limiter, key in keys:
            limiter.record(key)
        raise UnauthenticatedError(_GENERIC_401)
    if PERMISSION not in principal["permissions"]:
        raise ForbiddenError("Not allowed.")
    if row["last_used_at"] is None or now - row["last_used_at"] >= _TOUCH_INTERVAL_SECONDS:
        device_tokens.touch(row["id"], now, auth_service.ip_prefix(ip))
    return principal
