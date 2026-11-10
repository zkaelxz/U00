"""Owns the PC window's Kae/Guest profile: a PIN, the locked state and the PIN attempt limit."""

import hashlib
import hmac
import secrets
import threading
import time

import db
from lib.errors import ForbiddenError, InvalidInputError, RateLimitedError

PIN_KEY = "pc_profile_pin"
MIN_PIN, MAX_PIN = 6, 12
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
BLOCK_KEY = "pc_profile_pin.blocked_until"

_lock = threading.Lock()
# In memory on purpose: every server start comes up on Guest, so a PC that
# was left unlocked and rebooted does not stay open for the next person.
_owner_unlocked = False
_failures = 0
# True/False once read; None until then. guest_active() runs on every list
# query, so it must not hit the database each time.
_pin_set = None


def _stored():
    return db.get_app_setting(PIN_KEY)


def pin_is_set() -> bool:
    global _pin_set
    if _pin_set is None:
        _pin_set = bool(_stored())
    return _pin_set


def guest_active() -> bool:
    """The PC window is on the Guest profile: a PIN exists and the owner has not unlocked."""
    return pin_is_set() and not _owner_unlocked


def status() -> dict:
    return {"pin_set": pin_is_set(), "guest": guest_active()}


def _hash(pin: str, salt: bytes) -> str:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=2**26).hex()


def _check_format(pin) -> str:
    if not isinstance(pin, str) or not pin.isascii() or not pin.isdigit() \
            or not MIN_PIN <= len(pin) <= MAX_PIN:
        raise InvalidInputError(f"The PIN must be {MIN_PIN} to {MAX_PIN} digits.")
    return pin


def _verify(pin) -> bool:
    stored = _stored()
    if not isinstance(stored, str) or not isinstance(pin, str):
        return False
    try:
        salt_hex, digest = stored.split("$", 1)
        return hmac.compare_digest(_hash(pin, bytes.fromhex(salt_hex)), digest)
    except ValueError:
        # A damaged stored value never matches; `python -m api clear-pc-pin`
        # is the way out when the PIN is lost or this value is unreadable.
        return False


def _throttle_check(now: float) -> None:
    # Wall-clock and stored, so restarting the app does not hand out five
    # fresh guesses.
    if now < float(db.get_app_setting(BLOCK_KEY, 0) or 0):
        raise RateLimitedError("Too many wrong PINs. Try again in a minute.")


def _record_failure(now: float) -> None:
    global _failures
    _failures += 1
    if _failures >= MAX_FAILURES:
        _failures = 0
        db.set_app_setting(BLOCK_KEY, now + LOCKOUT_SECONDS)


def unlock(pin) -> dict:
    global _owner_unlocked, _failures
    with _lock:
        now = time.time()
        _throttle_check(now)
        if not _verify(pin):
            _record_failure(now)
            raise ForbiddenError("That PIN is not right.")
        _failures = 0
        _owner_unlocked = True
    return status()


def lock() -> dict:
    global _owner_unlocked
    with _lock:
        _owner_unlocked = False
    return status()


def _audit(action: str, detail: str = "") -> None:
    from services import auth_service    # it imports ownership_service, which imports this
    auth_service.write_audit(None, action, detail)


def hide_existing_titles() -> int:
    """Marks every title and series made at the PC private, so Guest starts
    with none of them. A title inside a series follows the series' flag.
    Returns how many items changed."""
    changed = 0
    for series in db.list_series():
        if series.get("owner_user_id") is None and not series.get("is_private"):
            changed += bool(db.set_item_private("series", series["id"], True))
    for drama in db.list_dramas():
        if (drama.get("owner_user_id") is None and drama.get("series_id") is None
                and not drama.get("is_private")):
            changed += bool(db.set_item_private("drama", drama["id"], True))
    _audit("pc_profile.hide_existing", f"{changed} titles or series marked private")
    return changed


def set_pin(new_pin, current_pin=None, hide_existing=False) -> dict:
    """Sets or changes the PIN; changing needs the current one. Setting it
    leaves the owner unlocked, so it does not lock them out of the page
    they are on. `hide_existing` makes the titles made so far private in
    the same step, so the owner need not mark them one by one."""
    global _owner_unlocked, _pin_set, _failures
    new_pin = _check_format(new_pin)
    with _lock:
        if guest_active():
            raise ForbiddenError("Switch to Kae first.")
        if pin_is_set():
            now = time.time()
            _throttle_check(now)
            if not _verify(current_pin):
                _record_failure(now)
                raise ForbiddenError("The current PIN is not right.")
            _failures = 0
        salt = secrets.token_bytes(16)
        db.set_app_setting(PIN_KEY, f"{salt.hex()}${_hash(new_pin, salt)}")
        _pin_set = True
        _owner_unlocked = True
        _audit("pc_profile.set_pin")
        hidden = hide_existing_titles() if hide_existing else 0
    return {**status(), "hidden": hidden}


def clear_pin(current_pin, forgot=False) -> dict:
    """Turns profiles off, which needs the PIN (`forgot`: the command-line
    reset at the PC, which does not)."""
    global _pin_set, _owner_unlocked, _failures
    with _lock:
        now = time.time()
        _throttle_check(now)
        if not forgot and pin_is_set() and not _verify(current_pin):
            _record_failure(now)
            raise ForbiddenError("The PIN is not right.")
        _failures = 0
        db.set_app_setting(PIN_KEY, None)
        _audit("pc_profile.clear_pin")
        _pin_set = False
        _owner_unlocked = False
    return status()


def forget_cached_pin() -> None:
    """Re-reads the PIN from the database next time; after a restore swaps
    the library under a running server."""
    global _pin_set
    with _lock:
        _pin_set = None


def reset_state() -> None:
    """Forgets the in-memory state; for a test that swaps the library."""
    global _owner_unlocked, _pin_set, _failures
    with _lock:
        _owner_unlocked, _pin_set, _failures = False, None, 0
