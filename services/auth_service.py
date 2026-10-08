"""
services/auth_service.py -- users, allowlist, permissions, server-side
sessions and the audit log (see docs/remote-access-decision.md).

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
from services.service_errors import (ConflictError, ForbiddenError, InvalidInputError,
                                     NotFoundError, RateLimitedError, UnauthenticatedError)

# One catalogue, one place. Household defaults are granted to a newly
# allowlisted user; the opt-in list exists but is granted only explicitly.
HOUSEHOLD_DEFAULT_PERMISSIONS = (
    "library.read", "lines.read", "lines.edit", "jobs.start", "jobs.cancel", "review.use",
)
OPT_IN_PERMISSIONS = (
    "media.import_url", "sources.import", "engines.paid", "extension.send", "media.stream",
)
ADMIN_PERMISSIONS = (   # the `admin.*` family; only is_admin users hold these
    "admin.library", "admin.settings", "admin.diagnostics", "admin.users.read", "admin.users",
)
# Every admin permission is either view (GET routes only, kept by an admin
# session on the internet-facing household listener) or write (dropped
# there: admin changes are PC-only). A permission in neither list is
# treated as write. The three families below mix reads with writes, so they
# are write as a whole.
ADMIN_VIEW_PERMISSIONS = ("admin.users.read",)
ADMIN_WRITE_PERMISSIONS = ("admin.library", "admin.settings", "admin.diagnostics", "admin.users")
PERMISSIONS = HOUSEHOLD_DEFAULT_PERMISSIONS + OPT_IN_PERMISSIONS + ADMIN_PERMISSIONS

# library.db tables that hold who may sign in and what they may do. A
# library restore never takes them from the upload: it keeps the live
# library's rows of RESTORE_LIVE_TABLES and leaves the rest empty (every
# session is revoked), so an old or crafted backup can't bring back a
# revoked extension device token or plant one. A user backup leaves them all out.
RESTORE_LIVE_TABLES = ("users", "user_permissions", "audit_log", "extension_device_tokens")
RESTORE_KEPT_TABLES = RESTORE_LIVE_TABLES + ("auth_sessions",)

# Defaults; `configure_timeouts` (from the API settings, BAIHE_API_SESSION_*)
# may change them at startup. Both are enforced on every lookup, so a
# shorter setting also applies to sessions created before it.
DEFAULT_IDLE_TIMEOUT_DAYS = 14
DEFAULT_ABSOLUTE_TIMEOUT_DAYS = 30
IDLE_TIMEOUT_SECONDS = DEFAULT_IDLE_TIMEOUT_DAYS * 24 * 3600
ABSOLUTE_TIMEOUT_SECONDS = DEFAULT_ABSOLUTE_TIMEOUT_DAYS * 24 * 3600
_TOUCH_INTERVAL_SECONDS = 60
_MAX_EMAIL = 254
_MAX_TOKEN_LEN = 200
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TOKENISH_RE = re.compile(r"[A-Za-z0-9_-]{32,}")
_MAX_AUDIT_DETAIL = 500
AUDIT_PAGE_MAX = 200
_AUDIT_EMAIL_FIELD_RE = re.compile(r"(\bemail )(\S+)")
_ADDRESS_RE = re.compile(r"[^\s@*]+@[^\s@]+")
# A URL, a Windows drive path, a UNC path, or an absolute POSIX path of two
# or more segments ("::/48" in an IPv6 prefix is not one: the slash follows
# a colon).
_PATHISH_RE = re.compile(
    r"(?:\b[a-z][a-z0-9+.-]*://|\b[A-Za-z]:[\\/]|\\\\|(?<![\w.:/])/(?=[^\s/]+/))\S*",
    re.I)


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

def _scrub_detail(detail) -> str:
    from translate_engines import redact_secrets
    text = redact_secrets(str(detail or ""))
    return _TOKENISH_RE.sub("[REDACTED]", text)[:_MAX_AUDIT_DETAIL]


def write_audit(user_id, action: str, detail: str = ""):
    db.auth_insert_audit(user_id, str(action)[:80], _scrub_detail(detail))


def list_audit(limit: int = 100) -> list:
    return db.auth_list_audit(max(1, min(int(limit), 500)))


def mask_email(value: str) -> str:
    """"jane@gmail.com" -> "j***@gmail.com": the first character and the
    domain only, never the whole address. A one-character local part is
    hidden entirely; a value with no "@" becomes "***"."""
    local, at, domain = str(value or "").rpartition("@")
    if not at:
        return "***"
    return (local[0] if len(local) > 1 else "") + "***@" + domain


def _mask_emails(text: str) -> str:
    # The `email <value>` field of a refused sign-in (the value is whatever
    # Google sent, so it may lack an "@"), then any other address-like run.
    text = _AUDIT_EMAIL_FIELD_RE.sub(lambda m: m.group(1) + mask_email(m.group(2)), text)
    return _ADDRESS_RE.sub(lambda m: mask_email(m.group(0)), text)


def audit_page(limit: int = 50, before_id: int = None, action: str = None,
               user_id: int = None) -> dict:
    """The admin audit view: newest first, at most AUDIT_PAGE_MAX rows,
    `next_before_id` for the next (older) page or None at the end. Details
    are scrubbed again on the way out (rows written before a scrub rule
    existed), path-like text is dropped and email addresses are masked
    (mask_email; the stored row keeps the full address). Coarse IP prefixes
    that the sign-in audit stores (IPv4 /24, IPv6 /48) are kept."""
    limit = max(1, min(int(limit), AUDIT_PAGE_MAX))
    rows = db.auth_list_audit(limit + 1, before_id=before_id, action=action or None,
                              user_id=user_id)
    more = len(rows) > limit
    rows = rows[:limit]
    events = [{"id": r["id"], "ts": r["ts"], "user_id": r["user_id"],
               "action": r["action"],
               "detail": _mask_emails(_PATHISH_RE.sub("[hidden]",
                                                      _scrub_detail(r["detail_redacted"])))}
              for r in rows]
    return {"events": events, "next_before_id": rows[-1]["id"] if more and rows else None,
            "actions": db.auth_list_audit_actions()}


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


def deactivate_user(user_id: int, actor_id=None, keep_an_admin: bool = False) -> dict:
    """`keep_an_admin` refuses (409) to deactivate the last active admin.
    The CLI leaves it off: it is the local recovery path, and
    `grant-admin` brings an admin back."""
    _require_user(user_id)
    if keep_an_admin:
        if not db.auth_deactivate_user_keeping_an_admin(user_id):
            raise ConflictError(_LAST_ADMIN)
    else:
        db.auth_update_user(user_id, is_active=0)
    db.auth_delete_user_sessions(user_id)
    _revoke_device_tokens(user_id, actor_id)
    _recheck_streams(user_id)
    write_audit(actor_id, "user.deactivate", f"user {user_id}")
    return get_user(user_id)


def activate_user(user_id: int, actor_id=None) -> dict:
    _require_user(user_id)
    db.auth_update_user(user_id, is_active=1)
    write_audit(actor_id, "user.activate", f"user {user_id}")
    return get_user(user_id)


_LAST_ADMIN = ("This is the last active admin. Baihe needs at least one, so it can't be "
               "deactivated.")


def _live_session_count(user_id: int, now: float) -> int:
    return sum(1 for s in db.auth_list_sessions(user_id) if _is_live(s, now))


def admin_list_users(actor_id=None, now: float = None) -> list:
    """The admin Users view: list_users plus each user's live session count
    and whether the row is the caller. No session details (no IP prefix or
    user agent)."""
    now = time.time() if now is None else now
    return [_admin_view(u, actor_id, now) for u in list_users()]


def _admin_view(user: dict, actor_id, now: float) -> dict:
    return dict(user, active_sessions=_live_session_count(user["id"], now),
                is_self=actor_id is not None and user["id"] == actor_id)


ADMIN_AT_PC_ONLY = ("Admin accounts can only be changed at the PC. "
                    "Use: python -m api revoke-admin/deactivate/grant-admin <email>")


def _require_pc_for_admin(row, at_pc: bool):
    """Anything that changes an admin account is PC-only: a remote admin
    session could otherwise lock the owner out."""
    if row["is_admin"] and not at_pc:
        raise ForbiddenError(ADMIN_AT_PC_ONLY)


def admin_set_active(user_id: int, active: bool, actor_id=None, at_pc: bool = False) -> dict:
    """Admin activate/deactivate; returns the admin_list_users row. The
    caller can't deactivate their own account (409); an admin target needs
    `at_pc` (403); the last active admin can't be deactivated (409).
    `actor_id` None is the local owner, who has no users row and so can
    never be the target."""
    row = _require_user(user_id)
    if active:
        _require_pc_for_admin(row, at_pc)
        user = activate_user(user_id, actor_id=actor_id)
    else:
        if actor_id is not None and user_id == actor_id:
            raise ConflictError("You can't deactivate your own account.")
        _require_pc_for_admin(row, at_pc)
        user = deactivate_user(user_id, actor_id=actor_id, keep_an_admin=True)
    return _admin_view(user, actor_id, time.time())


def admin_revoke_sessions(user_id: int, actor_id=None, at_pc: bool = False) -> dict:
    """Ends every session of another user (they can sign in again). Not the
    caller's own: that would sign them out mid-action (409; use Sign out).
    An admin target needs `at_pc` (403)."""
    row = _require_user(user_id)
    if actor_id is not None and user_id == actor_id:
        raise ConflictError("To end your own sessions, use Sign out.")
    _require_pc_for_admin(row, at_pc)
    return {"user_id": user_id, "revoked": revoke_all_for_user(user_id, actor_id=actor_id)}


_LAST_ADMIN_REVOKE = ("This is the last active admin. Baihe needs at least one, so their admin "
                      "rights can't be removed.")


def revoke_admin(user_id: int, actor_id=None, at_pc: bool = False) -> dict:
    """Makes an admin a normal member; returns the admin_list_users row.
    The account stays active; with no member permissions stored it gets the
    household defaults, so it isn't left empty. Their sessions end, so the
    next request (and any open event stream) re-checks as a member.
    Checks: 404, own account (409), away from the PC (403: admin accounts
    are PC-only), not an admin (409), last active admin (409, one guarded
    UPDATE, as for deactivate). The CLI passes at_pc=True."""
    row = _require_user(user_id)
    if actor_id is not None and user_id == actor_id:
        raise ConflictError("You can't remove your own admin rights.")
    _require_pc_for_admin(row, at_pc)
    if not row["is_admin"]:
        raise ConflictError("That user isn't an admin.")
    if not db.auth_revoke_admin_keeping_an_admin(user_id):
        if not (db.auth_get_user(user_id) or {}).get("is_admin"):   # demoted meanwhile
            raise ConflictError("That user isn't an admin.")
        raise ConflictError(_LAST_ADMIN_REVOKE)
    if not [p for p in db.auth_get_permissions(user_id)
            if p in PERMISSIONS and p not in ADMIN_PERMISSIONS]:
        for p in HOUSEHOLD_DEFAULT_PERMISSIONS:
            db.auth_grant_permission(user_id, p)
    db.auth_delete_user_sessions(user_id)
    if "extension.send" not in effective_permissions(user_id):
        _revoke_device_tokens(user_id, actor_id)
    _recheck_streams(user_id)
    write_audit(actor_id, "user.revoke_admin", f"user {user_id}")
    return _admin_view(get_user(user_id), actor_id, time.time())


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


def member_principal(user_id: int):
    """A least-privilege principal for work done later on a user's behalf
    with no request (the scheduled chapter check acting on a link the user
    made). An admin gets no admin rights or override: household admin
    writes are stripped too, and the stored action may have come from
    there. None for an unknown or inactive user -- callers must deny then,
    never pass None on (None means auth off, which sees everything)."""
    row = db.auth_get_user(user_id)
    if not row or not row["is_active"]:
        return None
    return {"user_id": row["id"], "email": row["email"], "is_admin": False,
            "is_local_owner": False, "admin_override": False,
            "permissions": [p for p in db.auth_get_permissions(row["id"])
                            if p in PERMISSIONS and p not in ADMIN_PERMISSIONS]}


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
    if permission == "extension.send":
        _revoke_device_tokens(user_id, actor_id)
    write_audit(actor_id, "permission.revoke", f"user {user_id}: {permission}")
    return get_user(user_id)


# --- sessions --------------------------------------------------------------

def parse_ip(ip):
    """An ip_address, IPv4-mapped IPv6 unwrapped to IPv4; None if unparseable."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def ip_prefix(ip: str) -> str:
    """Coarse address only, for storage and audit: IPv4 /24 as its first three
    octets ("203.0.113"), IPv6 /48 as a network ("2001:db8:1::/48"). IPv4-mapped
    IPv6 counts as IPv4. Anything unparseable is stored as ""."""
    addr = parse_ip(ip)
    if addr is None:
        return ""
    if addr.version == 4:
        return ".".join(str(addr).split(".")[:3])
    return str(ipaddress.ip_network(f"{addr}/48", strict=False))


def rate_limit_key(ip: str) -> str:
    """The bucket a client address is rate-limited in: an IPv4 address on its
    own, an IPv6 address by its /64 (one host usually holds a whole /64, so
    per-address buckets would be unlimited). IPv4-mapped IPv6 counts as IPv4."""
    addr = parse_ip(ip)
    if addr is None:
        return (ip or "").strip()[:64] or "unknown"
    if addr.version == 4:
        return str(addr)
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


def configure_timeouts(idle_seconds: int, absolute_seconds: int) -> None:
    """Called by api.server.create_app with the validated settings
    (api_config.load_settings bounds them)."""
    global IDLE_TIMEOUT_SECONDS, ABSOLUTE_TIMEOUT_SECONDS
    if not 0 < idle_seconds <= absolute_seconds:
        raise ValueError("The idle timeout must be positive and at most the absolute timeout.")
    IDLE_TIMEOUT_SECONDS, ABSOLUTE_TIMEOUT_SECONDS = int(idle_seconds), int(absolute_seconds)


def _expires_at(sess: dict) -> float:
    """The stored expiry, or sooner if the absolute timeout was shortened
    after the session was created."""
    return min(sess["expires_at"], sess["created_at"] + ABSOLUTE_TIMEOUT_SECONDS)


def _is_live(sess: dict, now: float) -> bool:
    return now < _expires_at(sess) and now - sess["last_seen_at"] < IDLE_TIMEOUT_SECONDS


# (token, name), first match wins. Order matters: Edge, Opera and Samsung
# Internet also say "Chrome/", Chrome and Firefox on iOS say "Safari/", and
# an iPhone says "like Mac OS X".
_BROWSERS = (("Edg", "Edge"), ("EdgiOS", "Edge"), ("EdgA", "Edge"), ("OPR", "Opera"),
             ("SamsungBrowser", "Samsung Internet"), ("FxiOS", "Firefox"),
             ("Firefox", "Firefox"), ("CriOS", "Chrome"), ("Chrome", "Chrome"),
             ("Safari", "Safari"))
_SYSTEMS = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
            ("CrOS", "ChromeOS"), ("Windows", "Windows"), ("Macintosh", "Mac"),
            ("Linux", "Linux"))


def device_label(user_agent) -> str:
    """"Chrome on Android", "Safari on iPhone", "Browser on Windows",
    "Firefox browser" or "Unknown device", built only from the fixed names
    above, so no part of the raw user agent is ever stored or shown."""
    ua = str(user_agent or "")[:512]
    browser = next((name for token, name in _BROWSERS if f"{token}/" in ua), None)
    system = next((name for token, name in _SYSTEMS if token in ua), None)
    if system is None:
        return f"{browser} browser" if browser else "Unknown device"
    return f"{browser or 'Browser'} on {system}"


def create_session(user_id: int, user_agent: str = "", ip: str = "", now: float = None) -> dict:
    """Returns the raw session token and CSRF token exactly once. Of the
    device, only a coarse label (device_label) and IP prefix are stored."""
    row = _require_user(user_id)
    if not row["is_active"]:
        raise NotFoundError("User not found.")
    now = time.time() if now is None else now
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    sid = db.auth_insert_session(
        _hash(token), user_id, now, now + ABSOLUTE_TIMEOUT_SECONDS,
        device_label(user_agent), ip_prefix(ip), _hash(csrf))
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
    if not _is_live(sess, now):
        db.auth_delete_session(sess["id"])
        return None
    user = db.auth_get_user(sess["user_id"])
    if not user or not user["is_active"]:
        return None
    return sess, user


def resolve_session(token, now: float = None, touch: bool = True):
    """Live session -> {session_id, user_id, email, is_admin, permissions};
    otherwise None (expired, revoked, unknown, or inactive user).
    touch=False re-checks without counting as activity (the SSE stream's
    periodic re-checks must not keep an idle session alive)."""
    now = time.time() if now is None else now
    found = _lookup(token, now)
    if not found:
        return None
    sess, user = found
    if touch and now - sess["last_seen_at"] >= _TOUCH_INTERVAL_SECONDS:
        db.auth_touch_session(sess["id"], now)
    return {"session_id": sess["id"], "user_id": user["id"], "email": user["email"],
            "is_admin": bool(user["is_admin"]),
            # Acting on items only an admin can see; api.auth.listener_principal
            # clears it on the household listener.
            "admin_override": bool(user["is_admin"]),
            "permissions": effective_permissions(user["id"])}


def verify_csrf(session_token, csrf_token, now: float = None) -> bool:
    """Timing-safe check of a CSRF token against the session's stored hash."""
    if not csrf_token or not isinstance(csrf_token, str) or len(csrf_token) > _MAX_TOKEN_LEN:
        return False
    found = _lookup(session_token, time.time() if now is None else now)
    if not found:
        return False
    return hmac.compare_digest(found[0]["csrf_hash"], _hash(csrf_token))


def _revoke_device_tokens(user_id: int, actor_id=None) -> None:
    """Losing `extension.send` (or the account) also revokes the user's
    extension device tokens, so a later re-grant doesn't revive one they
    forgot about."""
    from services import device_token_service   # it imports this module
    device_token_service.revoke_all_for_user(user_id, actor_id=actor_id)


def _recheck_streams(user_id=None) -> None:
    """Open event streams (GET /api/events) re-check their session now
    rather than at their next heartbeat, so a revoked session's stream ends
    at once. None: every stream (whose session it was isn't known)."""
    try:
        from services import event_stream_service
        event_stream_service.request_recheck(user_id)
    except Exception:
        pass   # never fail a revoke; the streams still re-check on their own


def revoke_session(session_id: int, user_id: int = None) -> bool:
    """`user_id` scopes the revoke to that user's own session."""
    ok = db.auth_delete_session(session_id, user_id)
    if ok:
        _recheck_streams(user_id)
        write_audit(user_id, "session.revoke", f"session {session_id}")
    return ok


def revoke_all_for_user(user_id: int, actor_id=None) -> int:
    n = db.auth_delete_user_sessions(user_id)
    _recheck_streams(user_id)
    write_audit(actor_id, "session.revoke_all", f"user {user_id}: {n}")
    return n


def sweep_stale_sessions(now: float = None) -> int:
    """Startup clean-up: deletes expired, idle-expired and deactivated users'
    sessions (and with them their CSRF hashes), which otherwise stay in the
    table until presented. Login transactions are in memory only, so a
    restart has none left over. Returns how many sessions were removed."""
    now = time.time() if now is None else now
    n = db.auth_delete_stale_sessions(now, now - IDLE_TIMEOUT_SECONDS)
    if n:
        write_audit(None, "session.sweep", f"{n} stale")
    return n


def list_sessions(user_id: int) -> list:
    """No raw tokens and no hashes."""
    return db.auth_list_sessions(user_id)


# --- the signed-in user's own devices ------------------------------------------
# The user id always comes from the caller's session, never from the
# request, so nobody can list or end another user's sessions here; a session
# id that isn't the caller's is "not found" whether or not it exists.

_SESSION_NOT_FOUND = "That device isn't signed in."
_USE_SIGN_OUT = "This is the device you're using. Use Sign out instead."
ADMIN_DEVICES_AT_PC_ONLY = ("An admin account's devices can only be signed out at the PC. "
                            "Away from it, ask another admin to use Sign out everywhere, "
                            "or run python -m api deactivate <email> at the PC.")


def _require_pc_for_own_admin(is_admin: bool, at_pc: bool):
    """D5: anything involving an admin account is PC-only, so a stolen admin
    session away from the PC can't sign the owner's other devices out."""
    if is_admin and not at_pc:
        raise ForbiddenError(ADMIN_DEVICES_AT_PC_ONLY)


def list_own_sessions(user_id: int, current_session_id: int, now: float = None) -> list:
    """The caller's live sessions, this device first, then the most
    recently used: {id, device, created_at, last_seen_at, expires_at,
    ip_prefix, current}. No token, hash, user agent or full address."""
    now = time.time() if now is None else now
    out = [{"id": s["id"], "device": s.get("device_label") or device_label(""),
            "created_at": s["created_at"], "last_seen_at": s["last_seen_at"],
            "expires_at": _expires_at(s), "ip_prefix": s["ip_prefix"] or "",
            "current": s["id"] == current_session_id}
           for s in db.auth_list_sessions(user_id) if _is_live(s, now)]
    out.sort(key=lambda s: (not s["current"], -s["last_seen_at"], -s["id"]))
    return out


def revoke_own_session(user_id: int, session_id: int, current_session_id: int,
                       ip: str = "", *, is_admin: bool, at_pc: bool) -> dict:
    """Signs out one of the caller's other devices at once: its next
    request is a 401 and its open event streams end. 403 for an admin away
    from the PC; 404 for any id that isn't one of the caller's sessions; 409
    for the session making the request (Sign out also clears this device's
    cookies)."""
    _require_pc_for_own_admin(is_admin, at_pc)
    if session_id == current_session_id:
        raise ConflictError(_USE_SIGN_OUT)
    if not db.auth_delete_session(session_id, user_id):
        raise NotFoundError(_SESSION_NOT_FOUND)
    _recheck_streams(user_id)
    write_audit(user_id, "session.revoke", f"session {session_id} ip {ip_prefix(ip)}")
    return {"revoked": 1}


def revoke_other_sessions(user_id: int, current_session_id: int, ip: str = "", *,
                          is_admin: bool, at_pc: bool, now: float = None) -> dict:
    """Signs out every device of the caller except the one asking, and
    gives the one asking a new session token and CSRF token (returned once,
    for its cookies): a copy of this device's cookie taken earlier dies too.
    The new session keeps the old one's sign-in time and expiry."""
    _require_pc_for_own_admin(is_admin, at_pc)
    now = time.time() if now is None else now
    n = db.auth_delete_user_sessions(user_id, except_id=current_session_id)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    sid = db.auth_rotate_session(current_session_id, user_id, _hash(token), _hash(csrf),
                                 now, ip_prefix(ip))
    _recheck_streams(user_id)
    if n:
        write_audit(user_id, "session.revoke_others", f"user {user_id}: {n} ip {ip_prefix(ip)}")
    if sid is None:   # this session was revoked meanwhile (sign-out elsewhere)
        raise UnauthenticatedError("Authentication required.")
    return {"revoked": n, "session_token": token, "csrf_token": csrf, "session_id": sid}


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
