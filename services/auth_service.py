"""
services/auth_service.py -- users, allowlist, permissions, server-side
sessions and the audit log (Step 133; see docs/remote-access-decision.md).

UI-free: plain dicts in and out, errors from `service_errors`. Nothing
here knows about HTTP; `api/auth.py` is the FastAPI layer on top.

Security rules kept here:
- Session ids and CSRF tokens come from `secrets.token_urlsafe(32)`; only
  their SHA-256 hash is stored, the raw value is returned once by
  `create_session` and never again. Hashes are compared with
  `hmac.compare_digest`. No function returns a hash.
- Permissions are deny-by-default: only names in `PERMISSIONS` exist, and
  an unknown name is never granted.
- The allowlist is keyed by email for administration only. Matching a
  future Google login to a user is by `google_sub`, never by email alone
  (`bind_google_sub` refuses to rebind an already-bound user).
- Audit details go through `translate_engines.redact_secrets` plus a
  scrub of long token-like runs, and are length-capped.
"""

import hashlib
import hmac
import ipaddress
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict, deque

import db
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     RateLimitedError)

# One catalogue, one place. Household defaults are granted to a newly
# allowlisted user; the opt-in list exists but is granted only explicitly.
HOUSEHOLD_DEFAULT_PERMISSIONS = (
    "library.read", "lines.read", "lines.edit", "jobs.start", "jobs.cancel", "review.use",
)
OPT_IN_PERMISSIONS = (
    "media.import_url", "sources.import", "engines.paid", "extension.send", "media.stream",
)
ADMIN_PERMISSIONS = (   # the `admin.*` family; only is_admin users hold these
    "admin.library", "admin.settings", "admin.diagnostics", "admin.users",
)
PERMISSIONS = HOUSEHOLD_DEFAULT_PERMISSIONS + OPT_IN_PERMISSIONS + ADMIN_PERMISSIONS

IDLE_TIMEOUT_SECONDS = 14 * 24 * 3600
ABSOLUTE_TIMEOUT_SECONDS = 30 * 24 * 3600
_TOUCH_INTERVAL_SECONDS = 60
_MAX_EMAIL = 254
_MAX_TOKEN_LEN = 200
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TOKENISH_RE = re.compile(r"[A-Za-z0-9_-]{32,}")
_MAX_AUDIT_DETAIL = 500


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _norm_email(email) -> str:
    email = (email or "").strip().lower()
    if not email or len(email) > _MAX_EMAIL or not _EMAIL_RE.match(email):
        raise InvalidInputError("A valid email address is required.")
    return email


def _public_user(row: dict) -> dict:
    return {"id": row["id"], "email": row["email"], "display_name": row["display_name"] or "",
            "is_admin": bool(row["is_admin"]), "is_active": bool(row["is_active"]),
            "has_google_binding": bool(row["google_sub"]), "created_at": row["created_at"]}


def _require_user(user_id: int) -> dict:
    row = db.auth_get_user(user_id)
    if not row:
        raise NotFoundError("User not found.")
    return row


# --- audit ---------------------------------------------------------------

def write_audit(user_id, action: str, detail: str = ""):
    from translate_engines import redact_secrets
    text = redact_secrets(str(detail or ""))
    text = _TOKENISH_RE.sub("[REDACTED]", text)[:_MAX_AUDIT_DETAIL]
    db.auth_insert_audit(user_id, str(action)[:80], text)


def list_audit(limit: int = 100) -> list:
    return db.auth_list_audit(max(1, min(int(limit), 500)))


# --- allowlist / users -----------------------------------------------------

def add_user(email: str, display_name: str = "", actor_id=None) -> dict:
    """Allowlists an email with the household default permissions."""
    email = _norm_email(email)
    if db.auth_get_user_by_email(email):
        raise ConflictError("That user already exists.")
    try:
        uid = db.auth_create_user(email, display_name)
    except sqlite3.IntegrityError:   # a concurrent add won the race
        raise ConflictError("That user already exists.")
    for p in HOUSEHOLD_DEFAULT_PERMISSIONS:
        db.auth_grant_permission(uid, p)
    write_audit(actor_id, "user.add", f"user {uid}")
    return get_user(uid)


def get_user(user_id: int) -> dict:
    out = _public_user(_require_user(user_id))
    out["permissions"] = effective_permissions(user_id)
    return out


def list_users() -> list:
    return [dict(_public_user(r), permissions=effective_permissions(r["id"]))
            for r in db.auth_list_users()]


def deactivate_user(user_id: int, actor_id=None) -> dict:
    _require_user(user_id)
    db.auth_update_user(user_id, is_active=0)
    db.auth_delete_user_sessions(user_id)
    write_audit(actor_id, "user.deactivate", f"user {user_id}")
    return get_user(user_id)


def activate_user(user_id: int, actor_id=None) -> dict:
    _require_user(user_id)
    db.auth_update_user(user_id, is_active=1)
    write_audit(actor_id, "user.activate", f"user {user_id}")
    return get_user(user_id)


def grant_admin_local(email: str) -> dict:
    """CLI recovery path: creates or activates the user as admin. Admin
    rights come from the is_admin flag alone (effective_permissions), so
    only the household-default rows are stored: a later demotion
    (is_admin=0) leaves no opt-in or admin rights behind. Callers must be
    local (the CLI touches the DB directly)."""
    email = _norm_email(email)
    row = db.auth_get_user_by_email(email)
    uid = row["id"] if row else db.auth_create_user(email)
    db.auth_update_user(uid, is_admin=1, is_active=1)
    for p in HOUSEHOLD_DEFAULT_PERMISSIONS:
        db.auth_grant_permission(uid, p)
    write_audit(None, "user.grant_admin_local", f"user {uid}")
    return get_user(uid)


def find_user_by_google_sub(google_sub: str):
    """The only lookup future OIDC login may use to identify a user."""
    if not google_sub:
        return None
    row = db.auth_get_user_by_sub(google_sub)
    return _public_user(row) if row else None


def find_user_by_email(email: str):
    """Allowlist lookup by email, for local administration (the CLI) and
    the first-login binding in `oidc_service` (a verified email may bind
    an allowlisted user that has no Google binding yet). Never the way a
    returning user is identified: that is `find_user_by_google_sub`.
    None for an unknown or malformed address."""
    try:
        email = _norm_email(email)
    except InvalidInputError:
        return None
    row = db.auth_get_user_by_email(email)
    return _public_user(row) if row else None


def bind_google_sub(user_id: int, google_sub: str):
    """Binds an allowlisted user to a Google `sub` once. Never rebinds."""
    row = _require_user(user_id)
    if not google_sub:
        raise InvalidInputError("A Google subject is required.")
    if row["google_sub"]:
        raise ConflictError("That user is already bound.")
    try:
        db.auth_update_user(user_id, google_sub=google_sub)
    except Exception:   # UNIQUE violation: that sub belongs to another user
        raise ConflictError("That Google account is already bound.")
    write_audit(user_id, "user.bind_google", f"user {user_id}")


# --- permissions -------------------------------------------------------------

def effective_permissions(user_id: int) -> list:
    """Read fresh from the DB every time, so a change applies to the very
    next request. Inactive or unknown users hold nothing."""
    row = db.auth_get_user(user_id)
    if not row or not row["is_active"]:
        return []
    if row["is_admin"]:
        return sorted(PERMISSIONS)
    return [p for p in db.auth_get_permissions(user_id)
            if p in PERMISSIONS and p not in ADMIN_PERMISSIONS]


def grant_permission(user_id: int, permission: str, actor_id=None) -> dict:
    _require_user(user_id)
    if permission not in PERMISSIONS:
        raise InvalidInputError("Unknown permission.")
    if permission in ADMIN_PERMISSIONS:
        raise InvalidInputError("Admin permissions come only from the admin flag.")
    db.auth_grant_permission(user_id, permission)
    write_audit(actor_id, "permission.grant", f"user {user_id}: {permission}")
    return get_user(user_id)


def revoke_permission(user_id: int, permission: str, actor_id=None) -> dict:
    _require_user(user_id)
    db.auth_revoke_permission(user_id, permission)
    write_audit(actor_id, "permission.revoke", f"user {user_id}: {permission}")
    return get_user(user_id)


# --- sessions --------------------------------------------------------------

def _parse_ip(ip):
    """An ip_address, IPv4-mapped IPv6 unwrapped to IPv4; None if unparseable."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _ip_prefix(ip: str) -> str:
    """Coarse address only, for storage and audit: IPv4 /24 as its first three
    octets ("203.0.113"), IPv6 /48 as a network ("2001:db8:1::/48"). IPv4-mapped
    IPv6 counts as IPv4. Anything unparseable is stored as ""."""
    addr = _parse_ip(ip)
    if addr is None:
        return ""
    if addr.version == 4:
        return ".".join(str(addr).split(".")[:3])
    return str(ipaddress.ip_network(f"{addr}/48", strict=False))


def rate_limit_key(ip: str) -> str:
    """The bucket a client address is rate-limited in: an IPv4 address on its
    own, an IPv6 address by its /64 (one host usually holds a whole /64, so
    per-address buckets would be unlimited). IPv4-mapped IPv6 counts as IPv4."""
    addr = _parse_ip(ip)
    if addr is None:
        return (ip or "").strip()[:64] or "unknown"
    if addr.version == 4:
        return str(addr)
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


def create_session(user_id: int, user_agent: str = "", ip: str = "", now: float = None) -> dict:
    """Returns the raw session token and CSRF token exactly once."""
    row = _require_user(user_id)
    if not row["is_active"]:
        raise NotFoundError("User not found.")
    now = time.time() if now is None else now
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    sid = db.auth_insert_session(
        _hash(token), user_id, now, now + ABSOLUTE_TIMEOUT_SECONDS,
        (user_agent or "")[:60], _ip_prefix(ip), _hash(csrf))
    write_audit(user_id, "session.create", f"session {sid}")
    return {"session_token": token, "csrf_token": csrf, "session_id": sid,
            "expires_at": now + ABSOLUTE_TIMEOUT_SECONDS}


def _lookup(token, now: float):
    """(session row, user row) for a live session, else None. The stored
    hash is re-compared with compare_digest after the indexed lookup."""
    if not token or not isinstance(token, str) or len(token) > _MAX_TOKEN_LEN:
        return None
    digest = _hash(token)
    sess = db.auth_get_session_by_hash(digest)
    if not sess or not hmac.compare_digest(sess["id_hash"], digest):
        return None
    if now >= sess["expires_at"] or now - sess["last_seen_at"] >= IDLE_TIMEOUT_SECONDS:
        db.auth_delete_session(sess["id"])
        return None
    user = db.auth_get_user(sess["user_id"])
    if not user or not user["is_active"]:
        return None
    return sess, user


def resolve_session(token, now: float = None):
    """Live session -> {session_id, user_id, email, is_admin, permissions};
    otherwise None (expired, revoked, unknown, or inactive user)."""
    now = time.time() if now is None else now
    found = _lookup(token, now)
    if not found:
        return None
    sess, user = found
    if now - sess["last_seen_at"] >= _TOUCH_INTERVAL_SECONDS:
        db.auth_touch_session(sess["id"], now)
    return {"session_id": sess["id"], "user_id": user["id"], "email": user["email"],
            "is_admin": bool(user["is_admin"]),
            "permissions": effective_permissions(user["id"])}


def verify_csrf(session_token, csrf_token, now: float = None) -> bool:
    """Timing-safe check of a CSRF token against the session's stored hash."""
    if not csrf_token or not isinstance(csrf_token, str) or len(csrf_token) > _MAX_TOKEN_LEN:
        return False
    found = _lookup(session_token, time.time() if now is None else now)
    if not found:
        return False
    return hmac.compare_digest(found[0]["csrf_hash"], _hash(csrf_token))


def revoke_session(session_id: int, user_id: int = None) -> bool:
    """`user_id` scopes the revoke to that user's own session."""
    ok = db.auth_delete_session(session_id, user_id)
    if ok:
        write_audit(user_id, "session.revoke", f"session {session_id}")
    return ok


def revoke_all_for_user(user_id: int, actor_id=None) -> int:
    n = db.auth_delete_user_sessions(user_id)
    write_audit(actor_id, "session.revoke_all", f"user {user_id}: {n}")
    return n


def list_sessions(user_id: int) -> list:
    """No raw tokens and no hashes."""
    return db.auth_list_sessions(user_id)


# --- rate limiting -----------------------------------------------------------

class SlidingWindowRateLimiter:
    """In-memory sliding window: at most `max_events` per `window_seconds`
    per key. `hit(key)` records an attempt and raises RateLimitedError (429)
    once the limit is reached. Process-local by design (one API process).
    For login-type routes (step 134). Memory is bounded: at most `max_keys`
    keys are tracked, least recently hit evicted first, O(1) per hit."""

    def __init__(self, max_events: int, window_seconds: float, clock=time.monotonic,
                 max_keys: int = 10000):
        self.max_events, self.window, self._clock = max_events, window_seconds, clock
        self.max_keys = max_keys
        self._events = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str):
        now = self._clock()
        with self._lock:
            q = self._events.get(key)
            if q is None:
                q = self._events[key] = deque()
                while len(self._events) > self.max_keys:
                    self._events.popitem(last=False)
            else:
                self._events.move_to_end(key)
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.max_events:
                raise RateLimitedError("Too many attempts. Try again later.")
            q.append(now)
