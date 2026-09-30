"""
services/ownership_service.py -- who can see a drama or series, and who
may make one private (auth slice B1; plan section B).

UI-free. A `principal` is the dict `api/auth.py` puts on
`request.state.principal` (`user_id`, `is_admin`, `is_local_owner`), or
None, which means auth is off (Streamlit, the CLI, an auth-off API) and
everything is visible. With auth on, callers (B2 onward) must always pass
the request's principal -- never None for a missing one, which would
grant full visibility.

The rule: auth off, the local owner and admins see everything. Changing
what only an admin can see (the admin override) needs `admin_override` as
well: `api.auth.listener_principal` clears it on the household (internet)
listener, where an admin still sees everything but changes only what a
member of their account could (items they own or that are shared), and
flips no one else's private flag (user decision 2026-09-30: remote admin
views, every admin write is PC-only). Otherwise a
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
  only (avoids leaks via series glossary/TM/previous_episode_summary): a
  drama's own flag is cleared whenever it gets a series (db layer), and
  `check_series_assignment`/`assign_drama_series` keep other users'
  dramas out of a private series.
"""

import contextvars
import re
import sqlite3

import db
from services.service_errors import (ConflictError, ForbiddenError, InvalidInputError,
                                     NotFoundError)

HOUSEHOLD_SHARE_KEY = "household.share_by_default"
_DRAMA_IN_SERIES_MESSAGE = "Make the whole series private instead"
_PRIVATE_SERIES_MESSAGE = "That series is private, so only its owner's dramas can go in it."
_PC_DRAMA_IN_SERIES_MESSAGE = "This series holds a drama owned at the PC; ask an admin."
_NEW_SERIES_PRIVATE_MESSAGE = (
    "A new series starts private, so it can't take someone else's drama. Create the series "
    "first and share it in Settings > Sharing, then add the drama; or pick a shared series.")
_KINDS = ("drama", "series")


def _sees_everything(principal) -> bool:
    if principal is None:            # auth off
        return True
    return bool(principal.get("is_local_owner") or principal.get("is_admin"))


def _may_override(principal) -> bool:
    """May act on everything it sees. Default-deny: an admin principal
    without `admin_override` acts as a member."""
    if principal is None or principal.get("is_local_owner"):
        return True
    return bool(principal.get("is_admin") and principal.get("admin_override"))


def _everything(principal, writing: bool) -> bool:
    return _may_override(principal) if writing else _sees_everything(principal)


def _user_id(principal):
    return None if principal is None else principal.get("user_id")


def _is_owner(principal, row) -> bool:
    uid = _user_id(principal)
    return uid is not None and row.get("owner_user_id") == uid


def _visible(principal, kind: str, row: dict, writing: bool = False) -> bool:
    """Must agree with the `visible_to` SQL in db.list_dramas/list_series.
    `writing`: may the principal change it (the admin override needs
    `_may_override`; everyone else changes what they can see)."""
    if _everything(principal, writing):
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


def can_edit(principal, kind: str, item_id: int) -> bool:
    """Visibility-based (user decision 1), except that an admin without the
    override edits only what a member could; the lines.edit permission is
    checked separately by the API layer."""
    if kind not in _KINDS:
        raise InvalidInputError("Unknown item kind.")
    row = db.get_item_ownership(kind, _item_id(item_id))
    return bool(row) and _visible(principal, kind, row, writing=True)


def can_edit_drama(principal, drama_id: int) -> bool:
    return can_edit(principal, "drama", drama_id)


def _not_found(kind: str):
    return NotFoundError("Drama not found." if kind == "drama" else "Series not found.")


_ADMIN_CHANGE_AT_PC = "An admin can change other people's private items only at the PC."


def require_visible(principal, kind: str, item_id: int) -> None:
    """Raises NotFoundError (404) for a missing or invisible item alike."""
    if not can_see(principal, kind, item_id):
        raise _not_found(kind)


def require_editable(principal, kind: str, item_id: int) -> None:
    """require_visible, then ForbiddenError (403) for an item the principal
    sees but may not change (an admin away from the PC)."""
    require_visible(principal, kind, item_id)
    if not can_edit(principal, kind, item_id):
        raise ForbiddenError(_ADMIN_CHANGE_AT_PC)


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
    """Off unless chosen: new items are private by default (user decision
    2026-09-30). A stored value, even from before that decision, is kept."""
    if principal is None or principal.get("is_local_owner") or _user_id(principal) is None:
        return bool(db.get_app_setting(HOUSEHOLD_SHARE_KEY, False))
    user = db.auth_get_user(_user_id(principal))
    if not user:
        raise NotFoundError("User not found.")
    return bool(user.get("share_by_default"))


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
    admin = _may_override(principal)
    if not (admin or _is_owner(principal, row)):
        raise ForbiddenError("Only the owner or an admin can change this.")
    # User decision 4: only whole series, or dramas with no series. A drama
    # in a series follows it, so neither direction is a real change.
    if kind == "drama" and row.get("series_id") is not None:
        raise ConflictError(_DRAMA_IN_SERIES_MESSAGE)
    # Flipping the series flips who sees the PC's dramas in it too.
    if kind == "series" and not admin and db.series_has_unowned_drama(item_id):
        raise ConflictError(_PC_DRAMA_IN_SERIES_MESSAGE)
    if db.set_item_private(kind, item_id, private):
        from services import auth_service
        auth_service.write_audit(_user_id(principal), "sharing.set_private",
                                 f"{kind} {item_id}: private={bool(private)}")
        return {"kind": kind, "id": item_id, "is_private": bool(private)}
    if kind == "drama":
        raise ConflictError(_DRAMA_IN_SERIES_MESSAGE)   # it joined a series meanwhile
    # Otherwise those dramas would vanish for the people who own them.
    raise ConflictError("Move other people's dramas out of this series first.")


SHARING_PAGE_MAX = 200


def _owner_name(row) -> str:
    if row["owner_user_id"] is None:
        return "PC owner"
    if row["owner_found"] is None:
        return "Removed user"
    name = (row["owner_display_name"] or "").strip()
    # Never show an email, even one typed in as a display name.
    return name if name and "@" not in name else f"User {row['owner_user_id']}"


def list_sharing(principal, offset: int = 0, limit: int = 100) -> dict:
    """Who can see each series and drama, for admins and the local owner
    (the route is admin-only too). Owners are named by display name, never
    by email. A drama in a series follows the series' flag."""
    if not _sees_everything(principal):
        raise ForbiddenError("Only an admin can see every item's sharing.")
    if not 0 <= offset < 2**31 or not 1 <= limit <= SHARING_PAGE_MAX:
        raise InvalidInputError(f"offset must be 0 or more and limit 1 to {SHARING_PAGE_MAX}.")
    total, rows = db.list_item_sharing(limit, offset)
    items = [{"kind": r["kind"], "id": r["id"], "title": r["title"] or "",
              "owner_name": _owner_name(r), "created_at_pc": r["owner_user_id"] is None,
              "is_private": bool(r["is_private"]),
              "series_id": r["series_id"], "series_name": r["series_name"],
              "series_is_private": None if r["series_is_private"] is None
              else bool(r["series_is_private"])}
             for r in rows]
    return {"total": total, "offset": offset, "limit": limit, "items": items}


def check_series_assignment(principal, series_id, drama_owner_user_id) -> int:
    """Guard for putting a drama into a series (create or move). A series
    the principal can't see is a 404. A private series only takes dramas
    owned by its owner or by no one (the PC owner) -- otherwise a user
    could hide someone else's drama by moving it into their own private
    series: 409. The write itself must still use db.assign_drama_series,
    which re-checks the second rule atomically. Returns the series id."""
    series_id = _item_id(series_id)
    row = db.get_item_ownership("series", series_id)
    if not row or not _visible(principal, "series", row):
        raise NotFoundError("Series not found.")
    if not _visible(principal, "series", row, writing=True):
        raise ForbiddenError(_ADMIN_CHANGE_AT_PC)
    if row["is_private"] and drama_owner_user_id is not None \
            and drama_owner_user_id != row.get("owner_user_id"):
        raise ConflictError(_PRIVATE_SERIES_MESSAGE)
    return series_id


def assign_drama_series(principal, drama_id, series_id) -> None:
    """Moves an existing drama into a series under `check_series_assignment`,
    with the private-series rule re-checked in the same write. Clears the
    drama's own private flag (user decision 4)."""
    drama = db.get_item_ownership("drama", _item_id(drama_id))
    if not drama:
        raise NotFoundError("Drama not found.")
    series_id = check_series_assignment(principal, series_id, drama.get("owner_user_id"))
    if not db.assign_drama_series(drama["id"], series_id):
        raise ConflictError(_PRIVATE_SERIES_MESSAGE)   # the series went private meanwhile


def unassign_drama_series(principal, drama_id) -> None:
    """Takes a drama the principal can see out of its series (parity P11).
    Leaving a private series keeps the drama private (db.unassign_drama_series)."""
    drama = db.get_item_ownership("drama", _item_id(drama_id))
    if not drama or not _visible(principal, "drama", drama):
        raise NotFoundError("Drama not found.")
    if not _visible(principal, "drama", drama, writing=True):
        raise ForbiddenError(_ADMIN_CHANGE_AT_PC)
    if not db.unassign_drama_series(drama["id"]):
        raise NotFoundError("Drama not found.")


def check_new_series_assignment(principal, name: str, drama_owner_user_id) -> None:
    """Pre-check for get_or_create_series_for + assign_drama_series, so a
    refused move never leaves a new, empty series behind (db has no
    delete_series): the name must be free or name a series the principal
    can see and may put this drama in, and a series created now (owned by
    the principal, private unless they share by default) must accept it."""
    name = (name or "").strip()
    if not name:
        raise InvalidInputError("A series name is required.")
    existing = db.get_series_id_by_name(name)
    if existing is not None:
        if not can_edit(principal, "series", existing):
            raise ConflictError("That series name is taken")
        check_series_assignment(principal, existing, drama_owner_user_id)
        return
    new = new_item_defaults(principal)
    if new["is_private"] and drama_owner_user_id is not None \
            and drama_owner_user_id != new["owner_user_id"]:
        raise ConflictError(_NEW_SERIES_PRIVATE_MESSAGE)


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
        if can_edit(principal, "series", existing):
            return existing
        raise ConflictError("That series name is taken")
    try:
        return db.create_series(name, **new_item_defaults(principal))
    except sqlite3.IntegrityError:
        raise ConflictError("That series name is taken") from None


# --- who is acting, and jobs (auth B2) --------------------------------------
# A per-request holder set by api.server's ActingPrincipalMiddleware and
# filled by api.auth's dependencies. The holder is a dict (not the
# principal itself) because FastAPI runs sync dependencies and handlers in
# threadpool copies of the request's context: a set() there wouldn't reach
# the handler, but a mutation of the shared holder does. Threads a job
# starts get a fresh context, so a job started by a job has no owner.
_ACTING = contextvars.ContextVar("baihe_acting_principal", default=None)


def bind_request():
    """Start a request's holder; returns the token for unbind_request."""
    return _ACTING.set({})


def unbind_request(token) -> None:
    _ACTING.reset(token)


def note_acting_principal(principal) -> None:
    holder = _ACTING.get()
    if holder is not None:
        holder["principal"] = principal


def acting_user_id():
    """The signed-in user this code runs for, or None (the local owner,
    auth off, Streamlit, the CLI, a background thread)."""
    holder = _ACTING.get()
    return _user_id(holder.get("principal")) if holder else None


def drama_id_of_job(job_id):
    """The drama a `<prefix><drama_id>` job id (background_jobs.
    DRAMA_JOB_PREFIXES) is for, else None. Only an exact prefix followed
    by digits counts."""
    import background_jobs
    job_id = str(job_id or "")
    for prefix in background_jobs.DRAMA_JOB_PREFIXES:
        rest = job_id[len(prefix):]
        if job_id.startswith(prefix) and re.fullmatch(r"[0-9]{1,18}", rest):
            return int(rest)
    return None


def sees_every_job(principal) -> bool:
    """Admins, the local owner and auth off (None) see every job."""
    return _sees_everything(principal)


def can_see_job(principal, job_id, owner_user_id, writing: bool = False) -> bool:
    """Admins, the local owner and auth off see every job. A drama's job
    (`<prefix><drama_id>`) is visible to whoever can see that drama --
    its starter included, so a drama that went private stops showing its
    jobs to them (review L-1). Any other job is visible only to the user
    who started it: another user's or the PC's Discover/Sources/Live/
    library job is hidden. `writing` (cancel, stop): an admin without the
    override may act only on the jobs a member could see."""
    if _everything(principal, writing):
        return True
    drama_id = drama_id_of_job(job_id)
    if drama_id is not None:
        return (can_edit_drama if writing else can_see_drama)(principal, drama_id)
    uid = _user_id(principal)
    return uid is not None and owner_user_id == uid


def require_job_visible(principal, job_id, owner_user_id) -> None:
    if not can_see_job(principal, job_id, owner_user_id):
        raise NotFoundError("No such job.")


def require_job_changeable(principal, job_id, owner_user_id) -> None:
    """For cancel/stop after the visibility check: 403 for a job the
    principal sees but may not act on (an admin away from the PC)."""
    if not can_see_job(principal, job_id, owner_user_id, writing=True):
        raise ForbiddenError("An admin can stop other people's jobs only at the PC.")
