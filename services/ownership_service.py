"""
services/ownership_service.py -- who can see a drama or series, and who
may make one private (auth slice B1; plan section B).

UI-free. A `principal` is the dict `api/auth.py` puts on
`request.state.principal` (`user_id`, `is_admin`, `is_local_owner`), or
None, which means auth is off (Streamlit, the CLI, an auth-off API) and
everything is visible. With auth on, callers (B2 onward) must always pass
the request's principal -- never None for a missing one, which would
grant full visibility.

The rule: auth off, the local owner and admins see everything. Otherwise a
series is visible to its owner, or when it is not private. A drama is
visible to the owner of its series; else, if its series is private, to no
one else (owning the drama doesn't override a private series); else to the
drama's owner, or when the drama is not private. Existing rows carry `owner_user_id = NULL, is_private = 0`
("the PC owner / admins, shared").

A denied read is always `NotFoundError` (404), never 403, so a private
item's existence isn't revealed.

User decisions applied (2026-09-29):
- Editing: anyone who can see an item (and holds `lines.edit`) may edit
  it; private items are owner/admin only. `can_edit_drama` is therefore
  visibility-based; the permission check stays in `api/auth.py`.
- Series-name collision: refused. `get_or_create_series_for` raises
  ConflictError when the name belongs to a series the caller can't see
  (this reveals that the name exists -- accepted).
- The private flag applies to whole series, and to dramas with no series
  only (avoids leaks via series glossary/TM/previous_episode_summary).
"""

import sqlite3

import db
from services.service_errors import (ConflictError, ForbiddenError, InvalidInputError,
                                     NotFoundError)

HOUSEHOLD_SHARE_KEY = "household.share_by_default"
_KINDS = ("drama", "series")


def _sees_everything(principal) -> bool:
    if principal is None:            # auth off
        return True
    return bool(principal.get("is_local_owner") or principal.get("is_admin"))


def _user_id(principal):
    return None if principal is None else principal.get("user_id")


def _is_owner(principal, row) -> bool:
    uid = _user_id(principal)
    return uid is not None and row.get("owner_user_id") == uid


def _visible(principal, kind: str, row: dict) -> bool:
    """Must agree with the `visible_to` SQL in db.list_dramas/list_series."""
    if _sees_everything(principal):
        return True
    if kind == "drama":
        uid = _user_id(principal)
        if row.get("series_id") is not None and uid is not None \
                and row.get("series_owner_user_id") == uid:
            return True
        if row.get("series_is_private"):
            return False
    return _is_owner(principal, row) or not row["is_private"]


def _item_id(item_id) -> int:
    """A malformed or out-of-range id (SQLite integers are 64-bit) is a 404,
    like any other id that matches nothing."""
    try:
        value = int(item_id)
    except (TypeError, ValueError, OverflowError):
        raise NotFoundError("Not found.") from None
    if not -2**63 <= value < 2**63:
        raise NotFoundError("Not found.")
    return value


def can_see(principal, kind: str, item_id: int) -> bool:
    if kind not in _KINDS:
        raise InvalidInputError("Unknown item kind.")
    row = db.get_item_ownership(kind, _item_id(item_id))
    return bool(row) and _visible(principal, kind, row)


def can_see_drama(principal, drama_id: int) -> bool:
    return can_see(principal, "drama", drama_id)


def can_see_series(principal, series_id: int) -> bool:
    return can_see(principal, "series", series_id)


def can_edit_drama(principal, drama_id: int) -> bool:
    """Visibility-based (user decision 1); the lines.edit permission is
    checked separately by the API layer."""
    return can_see_drama(principal, drama_id)


def require_visible(principal, kind: str, item_id: int) -> None:
    """Raises NotFoundError (404) for a missing or invisible item alike."""
    if not can_see(principal, kind, item_id):
        raise NotFoundError("Drama not found." if kind == "drama" else "Series not found.")


def visible_to_filter(principal):
    """The `visible_to` argument for db.list_dramas / db.list_series:
    None when the principal sees everything, else their user id (-1 for a
    principal with no id, so only shared items match)."""
    if _sees_everything(principal):
        return None
    uid = _user_id(principal)
    return -1 if uid is None else uid


def filter_visible_drama_ids(principal, drama_ids) -> list:
    """The ids (same order, duplicates dropped) the principal may see.
    Each id is checked individually -- never matched by position."""
    out, seen = [], set()
    for did in drama_ids:
        did = _item_id(did)
        if did in seen:
            continue
        seen.add(did)
        if can_see_drama(principal, did):
            out.append(did)
    return out


def get_share_by_default(principal) -> bool:
    if principal is None or principal.get("is_local_owner") or _user_id(principal) is None:
        return bool(db.get_app_setting(HOUSEHOLD_SHARE_KEY, True))
    user = db.auth_get_user(_user_id(principal))
    if not user:
        raise NotFoundError("User not found.")
    val = user.get("share_by_default")
    return True if val is None else bool(val)


def set_share_by_default(principal, share: bool) -> bool:
    share = bool(share)
    if principal is None or principal.get("is_local_owner") or _user_id(principal) is None:
        db.set_app_setting(HOUSEHOLD_SHARE_KEY, share)
    elif not db.auth_update_user(_user_id(principal), share_by_default=int(share)):
        raise NotFoundError("User not found.")
    return share


def new_item_defaults(principal) -> dict:
    """Fields to stamp on a new drama/series: its creator (None for the
    local owner / auth off) and `is_private = not share_by_default`."""
    return {"owner_user_id": _user_id(principal),
            "is_private": int(not get_share_by_default(principal))}


def set_private(principal, kind: str, item_id: int, private: bool) -> dict:
    """Only the item's owner or an admin/local owner may change it. An item
    the principal can't see is a 404; a visible one they don't own is 403."""
    if kind not in _KINDS:
        raise InvalidInputError("Unknown item kind.")
    item_id = _item_id(item_id)
    row = db.get_item_ownership(kind, item_id)
    if not row or not _visible(principal, kind, row):
        raise NotFoundError("Drama not found." if kind == "drama" else "Series not found.")
    if not (_sees_everything(principal) or _is_owner(principal, row)):
        raise ForbiddenError("Only the owner or an admin can change this.")
    if kind == "drama" and private and row.get("series_id"):
        # User decision 3: only whole series, or dramas with no series.
        raise InvalidInputError("Make the series private instead.")
    if kind == "series" and private and db.series_has_dramas_owned_by_others(item_id):
        # Otherwise those dramas would vanish for the people who own them.
        raise ConflictError("Move other people's dramas out of this series first.")
    db.set_item_private(kind, item_id, private)
    return {"kind": kind, "id": item_id, "is_private": bool(private)}


def get_or_create_series_for(principal, name: str) -> int:
    """Visibility-aware `db.get_or_create_series`: reuses an existing series
    only if the principal can see it; a name taken by a series they can't
    see is refused (user decision 2) rather than joined. A new series is
    stamped with `new_item_defaults`. The create is insert-only, so a race
    with another user creating the same name is refused too, never handed
    their series id."""
    name = (name or "").strip()
    if not name:
        raise InvalidInputError("A series name is required.")
    existing = db.get_series_id_by_name(name)
    if existing is not None:
        if can_see_series(principal, existing):
            return existing
        raise ConflictError("That series name is taken")
    try:
        return db.create_series(name, **new_item_defaults(principal))
    except sqlite3.IntegrityError:
        raise ConflictError("That series name is taken") from None
