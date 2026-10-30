"""
services/member_key_service.py -- a signed-in member's own Ollama API key.

Ollama's hosted ("cloud") models normally go through the Ollama app on the
PC, signed in to the owner's account, so every member's cloud run spends the
owner's free cap. A member who saves their own key here has their cloud
runs sent to ollama.com with it instead (engine_backends/local.py), and may
pick cloud models without `engines.paid` (api.auth.require_cloud_model_allowed),
because the quota is theirs. A member without a key keeps today's rule.
UI-free; the table is `member_keys.py`.

Security rules kept here:
- The key is stored encrypted (Fernet) with a server secret kept in the data
  folder's .env (SECRET_ENV), made on the first save, so a copied
  library.db alone does not reveal it. A lost or changed secret, or a
  missing `cryptography`, leaves saved keys unreadable: the card reads
  "paste it again", and that member's cloud runs are refused rather than
  moved onto the PC's sign-in. Never a crash. The plaintext carries its
  row's user id, so a ciphertext moved to another row doesn't decrypt.
- Admins keep the PC's own sign-in and can't save a key; a key saved before
  a promotion is ignored while the account is an admin.
- Write-only: nothing returns the key; status gives its last four characters.
- A member reads, saves and removes only their own (the user id comes from
  their session). An admin sees only whether a member has one, and may
  remove it; that removal is audited. Deactivating an account removes it.
- A job reads its starter's key once, when it starts (for_job), so removing the key mid-run never moves that run onto the
  owner's account.
"""

import threading
import time
from typing import Optional

import member_keys
from services import auth_service, settings_service
from services.service_errors import InvalidInputError, NotFoundError

PROVIDER = "ollama"
SECRET_ENV = "BAIHE_MEMBER_KEY_SECRET"
# Shorter than this and the last four characters shown would be much of it.
_MIN_KEY_LENGTH = 16
_SECRET_LOCK = threading.Lock()
_SIGNED_IN_ONLY = "Only a signed-in household member can save their own Ollama key."
_NEEDS_CRYPTOGRAPHY = ("Saving your own key needs the cryptography package, which this "
                       "install is missing. Ask the owner to repair the install.")
_SECRET_DAMAGED = (f"The key-encryption secret ({SECRET_ENV}) in the main PC's .env is damaged, "
                   "so keys can't be saved. Ask the owner to remove that line; saved keys then "
                   "need pasting again.")
_ADMINS_USE_PC = ("Admins' cloud runs use the main PC's own Ollama sign-in, so an admin "
                  "account doesn't save a key here.")


def _secret_on_disk(candidate: str) -> str:
    """The secret in .env, inserting `candidate` only when no line sets it.
    The check runs inside the locked rewrite, on the lines just read, so a
    failed read raises instead of looking like "no secret", and a secret
    another process wrote meanwhile is kept: replacing it would leave every
    key sealed with it unreadable."""
    found = []

    def transform(lines):
        for line in lines:
            if settings_service._line_var(line) == SECRET_ENV:
                found.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
                return lines
        out = list(lines)
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{SECRET_ENV}={candidate}\n")
        return out

    settings_service._rewrite_env(settings_service.default_env_path(), transform)
    return found[0] if found else candidate


def _cipher(create: bool):
    """(cipher, problem): the cipher for the server secret, or None and the
    reason it can't be had (None when there is simply no secret yet and not
    `create`)."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None, _NEEDS_CRYPTOGRAPHY
    secret = settings_service.resolve_env_names((SECRET_ENV,))
    if not secret and create:
        with _SECRET_LOCK:
            secret = _secret_on_disk(Fernet.generate_key().decode("ascii"))
    if not secret:
        return None, None
    try:
        return Fernet(secret.encode("ascii")), None
    except (ValueError, UnicodeEncodeError):
        return None, _SECRET_DAMAGED


def _bound(user_id: int, key: str) -> bytes:
    # The row's owner is sealed in with the key, so a ciphertext copied onto
    # another member's row does not decrypt as theirs.
    return f"{user_id}:{PROVIDER}:{key}".encode("utf-8")


def _decrypt(row) -> Optional[str]:
    if not row:
        return None
    cipher, _problem = _cipher(create=False)
    if cipher is None:
        return None
    from cryptography.fernet import InvalidToken
    try:
        plain = cipher.decrypt(row["ciphertext"].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None
    owner, _, key = plain.partition(f":{PROVIDER}:")
    return key if owner == str(row["user_id"]) and key else None


def _require_member(user_id) -> int:
    if user_id is None:
        raise InvalidInputError(_SIGNED_IN_ONLY)
    return int(user_id)


def status(user_id) -> dict:
    """What the member's own card shows. `unreadable`: a key is saved but
    can't be decrypted any more, so it counts as none until pasted again."""
    row = member_keys.get(_require_member(user_id), PROVIDER)
    if not row:
        return {"has_key": False, "last4": None, "added_at": None, "unreadable": False}
    readable = _decrypt(row) is not None
    return {"has_key": readable, "last4": row["last4"], "added_at": row["added_at"],
            "unreadable": not readable}


def save(user_id, value) -> dict:
    user_id = _require_member(user_id)
    key = settings_service._validate_key_value(value)
    if len(key) < _MIN_KEY_LENGTH:
        raise InvalidInputError("That is too short to be an Ollama API key.")
    import db
    if (db.auth_get_user(user_id) or {}).get("is_admin"):
        raise InvalidInputError(_ADMINS_USE_PC)
    cipher, problem = _cipher(create=True)
    if cipher is None:
        raise InvalidInputError(problem or _NEEDS_CRYPTOGRAPHY)
    member_keys.put(user_id, PROVIDER, cipher.encrypt(_bound(user_id, key)).decode("ascii"),
                    key[-4:], time.time())
    from services import auth_service   # it imports this module
    auth_service.write_audit(user_id, "member_key.save", "ollama")
    return status(user_id)


def remove(user_id, actor_id=None, member_only: bool = False) -> bool:
    """Removes the key; True when there was one. `actor_id` is who did it:
    the member, an admin, or None when no person did (the owner at the PC
    with sign-in off, or a deactivation without an acting user).
    `member_only`: keeps an admin's key, checked in the delete itself."""
    user_id = _require_member(user_id)
    removed = member_keys.delete(user_id, PROVIDER, member_only=member_only)
    if removed:
        from services import auth_service   # it imports this module
        auth_service.write_audit(actor_id, "member_key.remove", f"ollama, user {user_id}")
    return removed


def admin_remove(user_id: int, actor_id=None, at_pc: bool = False) -> dict:
    """An admin removing a member's key; 404 for an unknown user, 403 for an
    admin account's key away from the PC, like every other admin-target write."""
    import db
    row = db.auth_get_user(user_id)
    if not row:
        raise NotFoundError("User not found.")
    auth_service._require_pc_for_admin(row, at_pc)
    removed = remove(user_id, actor_id=actor_id, member_only=not at_pc)
    # Promoted after the check above: refused like any admin target, never "no key".
    if not removed and not at_pc and (db.auth_get_user(user_id) or {}).get("is_admin"):
        auth_service._require_pc_for_admin({"is_admin": True}, at_pc)
    return {"user_id": user_id, "removed": removed}


def _member_row(user_id):
    """The user's saved key row, or None. An admin's is ignored: a member
    promoted after saving one (`python -m api grant-admin`) keeps the row,
    but admins' cloud runs use the PC's own sign-in."""
    if user_id is None:
        return None
    import db
    user = db.auth_get_user(int(user_id))
    if not user or user["is_admin"]:
        return None
    return member_keys.get(int(user_id), PROVIDER)


def has_usable_key(user_id) -> bool:
    return _decrypt(_member_row(user_id)) is not None


def has_unreadable_key(user_id) -> bool:
    """A key is saved but can't be decrypted: that member's cloud runs are
    refused, never moved onto the PC's sign-in."""
    row = _member_row(user_id)
    return bool(row) and _decrypt(row) is None


def cloud_key_for_user(user_id) -> Optional[str]:
    """What a cloud call for this user sends: their key, UNREADABLE_MEMBER_KEY
    when one is saved but can't be read, or None (none saved: the PC's
    sign-in, as before)."""
    row = _member_row(user_id)
    if not row:
        return None
    from engine_backends.local import UNREADABLE_MEMBER_KEY
    return _decrypt(row) or UNREADABLE_MEMBER_KEY


def user_ids_with_key() -> set:
    """Users with a saved key, readable or not: the admin list's flag."""
    return member_keys.user_ids_with(PROVIDER)


def acting_key() -> Optional[str]:
    """cloud_key_for_user for the member the current API request (or queue
    start) runs for; None for the owner, with auth off, or without a key."""
    from services import ownership_service
    return cloud_key_for_user(ownership_service.acting_user_id())


def for_job(target):
    """`target` as a job runs it, carrying the key of whoever starts it: the
    member of this request, or the key the starting job itself carries. A
    partial, so a process job's target still pickles."""
    from engine_backends.local import carry_member_cloud_key, member_cloud_key
    return carry_member_cloud_key(target, member_cloud_key())
