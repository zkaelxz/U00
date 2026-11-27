"""Preferences a signed-in account keeps on the server so they follow the person to any device."""

import db
from lib.errors import InvalidInputError

KEY = "account_prefs.{}"
# One allowed value list per preference. A new preference is added here and nowhere else, so a
# client can never store a key or value the app doesn't understand.
CHOICES = {"theme": ("system", "light", "dark", "sepia", "oled"), "live_focus": (True, False)}


def get(user_id: int) -> dict:
    """The account's saved preferences; a stored value that is no longer allowed is left out."""
    stored = db.get_app_setting(KEY.format(user_id))
    if not isinstance(stored, dict):
        return {}
    return {k: v for k, v in stored.items() if k in CHOICES and v in CHOICES[k]}


def update(user_id: int, changes: dict) -> dict:
    """Saves the preferences in `changes` (each must be allowed) and returns the whole set.
    Done in one read-modify-write so two devices saving at once don't undo each other's key."""
    for key, value in changes.items():
        if key not in CHOICES or value not in CHOICES[key]:
            raise InvalidInputError(f"'{key}' can't be set to that.")

    def merge(row):
        merged = dict(row) if isinstance(row, dict) else {}
        merged.update(changes)
        return merged
    db.update_app_setting(KEY.format(user_id), merge, default={})
    return get(user_id)
