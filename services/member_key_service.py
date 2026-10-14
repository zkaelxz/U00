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
  missing `cryptography`, leaves saved keys unreadable: they read as
  "paste it again" and count as no key, never as an error.
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
from services import settings_service
from services.service_errors import InvalidInputError, NotFoundError

PROVIDER = "ollama"
SECRET_ENV = "BAIHE_MEMBER_KEY_SECRET"
# Shorter than this and the last four characters shown would be much of it.
_MIN_KEY_LENGTH = 16
_SECRET_LOCK = threading.Lock()
_SIGNED_IN_ONLY = "Only a signed-in household member can save their own Ollama key."
_NEEDS_CRYPTOGRAPHY = ("Saving your own key needs the cryptography package, which this "
                       "install is missing. Ask the owner to repair the install.")


def _fernet(create: bool):
    """The cipher for the server secret, or None (no secret yet and not
    `create`, a malformed secret, or `cryptography` missing)."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    secret = settings_service.resolve_env_names((SECRET_ENV,))
    if not secret and create:
        with _SECRET_LOCK:
            # Two first saves at once must not each write a secret: the
            # loser's key would be sealed with one that no longer exists.
            secret = settings_service.resolve_env_names((SECRET_ENV,))
            if not secret:
                secret = Fernet.generate_key().decode("ascii")
                settings_service.write_env_var(SECRET_ENV, secret)
    if not secret:
        return None
    try:
        return Fernet(secret.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


def _decrypt(row) -> Optional[str]:
    if not row:
        return None
    cipher = _fernet(create=False)
    if cipher is None:
        return None
    from cryptography.fernet import InvalidToken
    try:
        return cipher.decrypt(row["ciphertext"].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None


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
    cipher = _fernet(create=True)
    if cipher is None:
        raise InvalidInputError(_NEEDS_CRYPTOGRAPHY)
    member_keys.put(user_id, PROVIDER, cipher.encrypt(key.encode("utf-8")).decode("ascii"),
                    key[-4:], time.time())
    from services import auth_service   # it imports this module
    auth_service.write_audit(user_id, "member_key.save", "ollama")
    return status(user_id)


def remove(user_id, actor_id=None) -> bool:
    """Removes the key; True when there was one. `actor_id` is who did it
    (the member themselves, or an admin)."""
    user_id = _require_member(user_id)
    removed = member_keys.delete(user_id, PROVIDER)
    if removed:
        from services import auth_service   # it imports this module
        actor = user_id if actor_id is None else actor_id
        auth_service.write_audit(actor, "member_key.remove", f"ollama, user {user_id}")
    return removed


def admin_remove(user_id: int, actor_id=None) -> dict:
    """An admin removing a member's key; 404 for an unknown user."""
    import db
    if not db.auth_get_user(user_id):
        raise NotFoundError("User not found.")
    return {"user_id": user_id, "removed": remove(user_id, actor_id=actor_id)}


def key_for_user(user_id) -> Optional[str]:
    """Server-side only: the member's key in plain text, or None (no key, an
    unreadable one, or no user)."""
    if user_id is None:
        return None
    return _decrypt(member_keys.get(int(user_id), PROVIDER))


def has_usable_key(user_id) -> bool:
    return key_for_user(user_id) is not None


def user_ids_with_key() -> set:
    """Users with a saved key, readable or not: the admin list's flag."""
    return member_keys.user_ids_with(PROVIDER)


def acting_key() -> Optional[str]:
    """The key of the member the current API request (or queue start) runs
    for, or None: the owner, auth off, or a member without one."""
    from services import ownership_service
    return key_for_user(ownership_service.acting_user_id())


def for_job(target):
    """`target` as a job runs it, carrying the key of whoever starts it: the
    member of this request, or the key the starting job itself carries. A
    partial, so a process job's target still pickles."""
    from engine_backends.local import carry_member_cloud_key, member_cloud_key
    return carry_member_cloud_key(target, member_cloud_key())
