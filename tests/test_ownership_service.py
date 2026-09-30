"""Auth slice B1: ownership columns, migration and visibility rule."""

import contextlib
import sqlite3

import pytest

import db
from services import ownership_service as own
from services.service_errors import (ConflictError, ForbiddenError, InvalidInputError,
                                     NotFoundError)

LOCAL = {"user_id": None, "is_admin": True, "is_local_owner": True}


def _p(uid, admin=False, override=None):
    """Like auth_service.resolve_session: an admin holds the override unless
    `override=False` (api.auth.listener_principal on the household listener)."""
    return {"user_id": uid, "is_admin": admin, "is_local_owner": False,
            "admin_override": admin if override is None else override}


@pytest.fixture
def people(isolated_db):
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    adm = db.auth_create_user("admin@example.com", is_admin=True)
    return {"a": _p(a), "b": _p(b), "admin": _p(adm, admin=True), "a_id": a, "b_id": b}


def _cols(table):
    with contextlib.closing(db.get_conn()) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_adds_columns_and_is_idempotent(isolated_db):
    legacy_drama = db.create_drama(title_zh="旧")
    legacy_series = db.get_or_create_series("Old")
    db.init_db()
    db.init_db()
    assert {"owner_user_id", "is_private"} <= _cols("dramas")
    assert {"owner_user_id", "is_private"} <= _cols("series")
    assert "share_by_default" in _cols("users")
    assert "user_id" in _cols("translate_history")
    assert "owner_user_id" in _cols("job_records")
    d = db.get_item_ownership("drama", legacy_drama)
    s = db.get_item_ownership("series", legacy_series)
    assert (d["owner_user_id"], d["is_private"]) == (None, 0)
    assert (s["owner_user_id"], s["is_private"]) == (None, 0)


def test_migration_upgrades_a_pre_b1_database(isolated_db):
    # Simulate an old DB: drop to a table lacking the new columns.
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DROP TABLE series")
        conn.execute("CREATE TABLE series (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "name TEXT UNIQUE, created_at TEXT, instructions TEXT)")
        conn.execute("INSERT INTO series (name) VALUES ('legacy')")
        conn.commit()
    db.init_db()
    row = db.list_series()[0]
    assert row["owner_user_id"] is None and row["is_private"] == 0
    assert own.can_see_series(_p(999), row["id"])


def test_visibility_rule_every_branch(people):
    a, b, admin = people["a"], people["b"], people["admin"]
    shared = db.create_drama(title_zh="s", owner_user_id=people["a_id"], is_private=0)
    private = db.create_drama(title_zh="p", owner_user_id=people["a_id"], is_private=1)
    legacy = db.create_drama(title_zh="l")
    for principal in (None, LOCAL, admin, a):
        assert own.can_see_drama(principal, private)
    assert not own.can_see_drama(b, private)
    assert own.can_see_drama(b, shared) and own.can_see_drama(b, legacy)
    with pytest.raises(NotFoundError):
        own.require_visible(b, "drama", private)
    with pytest.raises(NotFoundError):
        own.require_visible(admin, "drama", 99999)
    own.require_visible(a, "drama", private)
    assert own.filter_visible_drama_ids(b, [private, shared, shared, legacy]) == [shared, legacy]
    assert {d["id"] for d in db.list_dramas(visible_to=own.visible_to_filter(b))} == {shared, legacy}
    assert own.visible_to_filter(admin) is None and own.visible_to_filter(None) is None
    assert len(db.list_dramas()) == 3
    assert own.can_edit_drama(b, shared) and not own.can_edit_drama(b, private)


def test_private_series_hides_its_dramas(people):
    a, b = people["a"], people["b"]
    sid = db.get_or_create_series("Mine", owner_user_id=people["a_id"], is_private=True)
    did = db.create_drama(title_zh="ep1", series_id=sid, owner_user_id=people["a_id"])
    assert not own.can_see_series(b, sid)
    assert not own.can_see_drama(b, did)
    assert own.can_see_drama(a, did) and own.can_see_drama(people["admin"], did)
    assert sid not in {s["id"] for s in db.list_series(visible_to=people["b_id"])}
    assert did not in {d["id"] for d in db.list_dramas(visible_to=people["b_id"])}
    assert sid in {s["id"] for s in db.list_series(visible_to=people["a_id"])}


def test_set_private_permissions(people):
    a, b, admin = people["a"], people["b"], people["admin"]
    did = db.create_drama(title_zh="x", owner_user_id=people["a_id"])
    with pytest.raises(ForbiddenError):
        own.set_private(b, "drama", did, True)
    own.set_private(a, "drama", did, True)
    assert not own.can_see_drama(b, did)
    with pytest.raises(NotFoundError):
        own.set_private(b, "drama", did, False)
    own.set_private(admin, "drama", did, False)
    assert own.can_see_drama(b, did)
    own.set_private(LOCAL, "drama", did, True)
    assert db.get_item_ownership("drama", did)["is_private"] == 1
    with pytest.raises(InvalidInputError):
        own.set_private(a, "bogus", did, True)


def test_admin_without_override_sees_everything_but_changes_as_a_member(people):
    """An admin on the household listener: views stay, overrides don't."""
    remote = _p(db.auth_get_user_by_email("admin@example.com")["id"], admin=True,
                override=False)
    theirs = db.create_drama(title_zh="p", owner_user_id=people["a_id"])
    own.set_private(people["a"], "drama", theirs, True)
    mine = db.create_drama(title_zh="m", owner_user_id=remote["user_id"])
    shared = db.create_drama(title_zh="s", owner_user_id=people["a_id"])
    pc_private = db.create_drama(title_zh="pc")
    own.set_private(LOCAL, "drama", pc_private, True)
    for did in (theirs, mine, shared, pc_private):
        assert own.can_see_drama(remote, did)
    assert own.visible_to_filter(remote) is None
    assert own.can_edit_drama(remote, mine) and own.can_edit_drama(remote, shared)
    assert not own.can_edit_drama(remote, theirs) and not own.can_edit_drama(remote, pc_private)
    with pytest.raises(ForbiddenError):
        own.require_editable(remote, "drama", theirs)
    own.require_editable(remote, "drama", shared)
    with pytest.raises(ForbiddenError):
        own.set_private(remote, "drama", theirs, False)
    with pytest.raises(ForbiddenError):
        own.set_private(remote, "drama", shared, True)
    assert db.get_item_ownership("drama", theirs)["is_private"] == 1
    own.set_private(remote, "drama", mine, True)
    # A series holding a PC drama: the admin-only flip is refused too.
    sid = db.get_or_create_series("Mixed", owner_user_id=remote["user_id"])
    db.create_drama(title_zh="pc2", series_id=sid)
    with pytest.raises(ConflictError):
        own.set_private(remote, "series", sid, True)
    # Jobs: every job is visible; only a member's jobs can be stopped.
    assert own.can_see_job(remote, "other-pc-job", None)
    assert not own.can_see_job(remote, "other-pc-job", None, writing=True)
    assert own.can_see_job(remote, "other-mine", remote["user_id"], writing=True)
    # With the override (the PC, or the single-port setup) nothing changes.
    assert own.can_edit_drama(people["admin"], theirs)
    assert own.can_see_job(people["admin"], "other-pc-job", None, writing=True)
    # Default-deny: an admin principal without the flag acts as a member.
    bare = {"user_id": remote["user_id"], "is_admin": True, "is_local_owner": False}
    assert own.can_see_drama(bare, theirs) and not own.can_edit_drama(bare, theirs)


def test_set_private_refused_for_drama_in_series(people):
    a = people["a"]
    sid = db.get_or_create_series("S", owner_user_id=people["a_id"])
    did = db.create_drama(title_zh="e", series_id=sid, owner_user_id=people["a_id"])
    with pytest.raises(ConflictError, match="Make the whole series private instead"):
        own.set_private(a, "drama", did, True)
    assert db.get_item_ownership("drama", did)["is_private"] == 0
    with pytest.raises(ConflictError, match="Make the whole series private instead"):
        own.set_private(a, "drama", did, False)  # it follows the series either way
    own.set_private(a, "series", sid, True)
    assert not own.can_see_drama(people["b"], did)


def test_share_by_default_and_new_item_defaults(people):
    # New items are private unless the creator chooses to share by default.
    a = people["a"]
    assert own.get_share_by_default(a) is False
    assert own.new_item_defaults(a) == {"owner_user_id": people["a_id"], "is_private": 1}
    own.set_share_by_default(a, True)
    assert own.get_share_by_default(a) is True
    assert own.new_item_defaults(a)["is_private"] == 0
    assert own.get_share_by_default(people["b"]) is False
    assert own.get_share_by_default(LOCAL) is False
    assert own.new_item_defaults(None) == {"owner_user_id": None, "is_private": 1}
    own.set_share_by_default(LOCAL, True)
    assert db.get_app_setting(own.HOUSEHOLD_SHARE_KEY) is True
    assert own.new_item_defaults(None) == {"owner_user_id": None, "is_private": 0}
    own.set_share_by_default(LOCAL, False)
    assert db.get_app_setting(own.HOUSEHOLD_SHARE_KEY) is False


def _old_share_column():
    """A database from before the private default: the column has DEFAULT 1,
    so every existing user reads 1 without having chosen it."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("ALTER TABLE users DROP COLUMN share_by_default")
        conn.execute("ALTER TABLE users ADD COLUMN share_by_default INTEGER DEFAULT 1")
        conn.commit()


def test_share_by_default_off_for_everyone_on_an_older_database(people):
    """Existing accounts are switched off once, a new user starts off, and
    existing items are untouched."""
    shared = db.create_drama(title_zh="s", owner_user_id=people["a_id"], is_private=0)
    _old_share_column()
    assert db.auth_get_user(people["a_id"])["share_by_default"] == 1
    db.init_db()
    assert own.get_share_by_default(people["a"]) is False
    assert own.get_share_by_default(people["b"]) is False
    c = db.auth_create_user("c@example.com")
    assert db.auth_get_user(c)["share_by_default"] == 0
    assert own.get_share_by_default(_p(c)) is False
    assert db.get_item_ownership("drama", shared)["is_private"] == 0


def test_share_off_migration_runs_once(people):
    _old_share_column()
    db.init_db()
    own.set_share_by_default(people["a"], True)     # a choice made afterwards
    db.init_db()
    db.init_db()
    assert own.get_share_by_default(people["a"]) is True
    assert own.get_share_by_default(people["b"]) is False
    assert db.get_app_setting("migrations.share_by_default_off") is True


def test_share_off_migration_skips_a_new_database(people):
    own.set_share_by_default(people["a"], True)
    db.init_db()
    assert own.get_share_by_default(people["a"]) is True
    assert db.get_app_setting("migrations.share_by_default_off") is None


def test_series_with_a_pc_drama_only_admins_flip(people):
    a = people["a"]
    sid = db.get_or_create_series("Mixed", owner_user_id=people["a_id"])
    db.create_drama(title_zh="pc", series_id=sid)
    for private in (True, False):
        with pytest.raises(ConflictError, match="owned at the PC; ask an admin"):
            own.set_private(a, "series", sid, private)
    assert db.get_item_ownership("series", sid)["is_private"] == 0
    own.set_private(people["admin"], "series", sid, True)
    own.set_private(LOCAL, "series", sid, False)


def test_every_flip_is_audited_by_id(people):
    a, b = people["a"], people["b"]
    did = db.create_drama(title_zh="Secret title", owner_user_id=people["a_id"])
    sid = db.get_or_create_series("S", owner_user_id=people["a_id"])
    ep = db.create_drama(title_zh="ep", series_id=sid, owner_user_id=people["a_id"])
    own.set_private(a, "drama", did, True)
    own.set_private(LOCAL, "series", sid, True)
    for who, kind, item in ((b, "drama", did), (b, "drama", 10**6), (a, "drama", ep)):
        with pytest.raises((ForbiddenError, NotFoundError, ConflictError)):
            own.set_private(who, kind, item, False)
    rows = [r for r in db.auth_list_audit(50) if r["action"] == "sharing.set_private"]
    assert [(r["user_id"], r["detail_redacted"]) for r in rows] == [
        (None, f"series {sid}: private=True"), (people["a_id"], f"drama {did}: private=True")]
    assert "Secret" not in str(rows)


def test_new_series_refusal_says_what_to_do(people):
    b_drama = db.create_drama(title_zh="b", owner_user_id=people["b_id"])
    with pytest.raises(ConflictError, match="share it in Settings > Sharing"):
        own.check_new_series_assignment(people["admin"], "Fresh", people["b_id"])
    assert db.get_drama(b_drama)["series_id"] is None


def test_series_name_collision_refused(people):
    a, b = people["a"], people["b"]
    own.set_share_by_default(a, False)
    sid = own.get_or_create_series_for(a, "Secret")
    assert db.get_item_ownership("series", sid)["owner_user_id"] == people["a_id"]
    assert own.get_or_create_series_for(a, "Secret") == sid
    with pytest.raises(ConflictError, match="That series name is taken"):
        own.get_or_create_series_for(b, "Secret")
    assert own.get_or_create_series_for(people["admin"], "Secret") == sid
    shared = db.get_or_create_series("Open")
    assert own.get_or_create_series_for(b, "Open") == shared


def test_owner_fields_on_history_and_job_records(people):
    hid = db.save_translate_history("zh", "en", "x", "你好", "hi", user_id=people["a_id"])
    assert [r for r in db.list_translate_history() if r["id"] == hid][0]["user_id"] == people["a_id"]
    db.save_job_record("j1", "queued", owner_user_id=people["a_id"])
    db.save_job_record("j1", "running")   # later writes in the same run keep it
    db.save_job_record("j1", "done")
    assert _job_owner("j1") == people["a_id"]


def _job_owner(job_id):
    with contextlib.closing(db.get_conn()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT owner_user_id FROM job_records WHERE job_id=?",
                           (job_id,)).fetchone()
    return row["owner_user_id"]


def test_reused_job_id_takes_the_new_runs_owner(people):
    """background_jobs.start_job reuses ids like translate_<drama>: a new run
    after a finished one belongs to whoever started it, not the first owner."""
    db.save_job_record("translate_1", "running", owner_user_id=people["a_id"])
    db.save_job_record("translate_1", "done")
    db.save_job_record("translate_1", "queued", owner_user_id=people["b_id"])
    assert _job_owner("translate_1") == people["b_id"]
    db.save_job_record("translate_1", "running")
    assert _job_owner("translate_1") == people["b_id"]
    db.save_job_record("translate_1", "error")
    db.save_job_record("translate_1", "running")   # a new run by auth-off / local owner
    assert _job_owner("translate_1") is None


def _visible_sql(uid):
    return {d["id"] for d in db.list_dramas(visible_to=uid)}


def _visible_py(principal, ids):
    return {i for i in ids if own.can_see_drama(principal, i)}


def test_mixed_owner_series_python_and_sql_agree(people):
    a, b = people["a"], people["b"]
    sid = db.get_or_create_series("A's", owner_user_id=people["a_id"])
    a_ep = db.create_drama(title_zh="a1", series_id=sid, owner_user_id=people["a_id"])
    # A drama in a series can't carry its own private flag (decision 4).
    b_ep = db.create_drama(title_zh="b1", series_id=sid, owner_user_id=people["b_id"],
                           is_private=1)
    assert db.get_item_ownership("drama", b_ep)["is_private"] == 0
    b_solo = db.create_drama(title_zh="b2", owner_user_id=people["b_id"], is_private=1)
    ids = [a_ep, b_ep, b_solo]
    # The series owner sees every drama in their series, even another user's.
    assert _visible_py(a, ids) == _visible_sql(people["a_id"]) == {a_ep, b_ep}
    assert _visible_py(b, ids) == _visible_sql(people["b_id"]) == {a_ep, b_ep, b_solo}
    # Refused while B's drama is inside; B keeps seeing it.
    with pytest.raises(ConflictError, match="Move other people's dramas"):
        own.set_private(a, "series", sid, True)
    with pytest.raises(ConflictError):
        own.set_private(people["admin"], "series", sid, True)
    assert db.get_item_ownership("series", sid)["is_private"] == 0
    # Legacy-owned (NULL) dramas don't block an admin; the series owner
    # must ask one, since the flip would hide the PC's drama too.
    db.create_drama(title_zh="legacy", series_id=sid)
    db.update_drama(b_ep, series_id=None)
    with pytest.raises(ConflictError, match="owned at the PC"):
        own.set_private(a, "series", sid, True)
    own.set_private(people["admin"], "series", sid, True)
    ids = [d["id"] for d in db.list_dramas()]
    assert _visible_py(a, ids) == _visible_sql(people["a_id"])
    assert _visible_py(b, ids) == _visible_sql(people["b_id"]) == {b_ep, b_solo}


def test_drama_ownership_does_not_override_a_private_series(people):
    """If B's drama ends up in A's private series anyway (a raw db write;
    the services refuse it), B loses it too -- the series' privacy wins,
    in both paths."""
    sid = db.get_or_create_series("Hidden", owner_user_id=people["a_id"], is_private=True)
    b_ep = db.create_drama(title_zh="b", series_id=sid, owner_user_id=people["b_id"])
    assert not own.can_see_drama(people["b"], b_ep)
    assert b_ep not in _visible_sql(people["b_id"])
    assert own.can_see_drama(people["a"], b_ep) and b_ep in _visible_sql(people["a_id"])
    assert own.can_see_drama(people["admin"], b_ep)


def test_series_owned_by_local_owner_does_not_match_userless_principal(people):
    sid = db.get_or_create_series("PC", is_private=True)
    did = db.create_drama(title_zh="x", series_id=sid)
    nobody = {"user_id": None, "is_admin": False, "is_local_owner": False}
    assert not own.can_see_drama(nobody, did)
    assert did not in _visible_sql(own.visible_to_filter(nobody))


def test_series_name_race_never_returns_another_users_series(people, monkeypatch):
    theirs = db.get_or_create_series("Raced", owner_user_id=people["a_id"], is_private=True)
    # Simulate the race: the name lookup ran before A's insert landed.
    monkeypatch.setattr(db, "get_series_id_by_name", lambda name: None)
    with pytest.raises(ConflictError, match="That series name is taken"):
        own.get_or_create_series_for(people["b"], "Raced")
    assert db.get_item_ownership("series", theirs)["owner_user_id"] == people["a_id"]


@pytest.mark.parametrize("bad", ["abc", "", None, 2**70, -2**70, float("inf")])
def test_malformed_ids_are_not_found(people, bad):
    with pytest.raises(NotFoundError):
        own.can_see_drama(people["b"], bad)
    with pytest.raises(NotFoundError):
        own.set_private(people["admin"], "series", bad, True)
    with pytest.raises(NotFoundError):
        own.filter_visible_drama_ids(people["b"], [bad])


# --- LOW-1: series assignment guard, LOW-2: no private flag inside a series ---

def test_series_assignment_guard(people):
    a, b, admin = people["a"], people["b"], people["admin"]
    secret = db.get_or_create_series("A secret", owner_user_id=people["a_id"], is_private=True)
    shared = db.get_or_create_series("A shared", owner_user_id=people["a_id"])
    # Invisible series: 404 for B, whatever the drama.
    with pytest.raises(NotFoundError):
        own.check_series_assignment(b, secret, people["b_id"])
    with pytest.raises(NotFoundError):
        own.check_series_assignment(b, 10**6, None)
    with pytest.raises(NotFoundError):
        own.check_series_assignment(b, "x", None)
    # Visible private series: only its owner's or PC-owned (NULL) dramas.
    assert own.check_series_assignment(a, secret, people["a_id"]) == secret
    assert own.check_series_assignment(a, secret, None) == secret
    for who in (a, admin, LOCAL, None):
        with pytest.raises(ConflictError, match="only its owner's dramas"):
            own.check_series_assignment(who, secret, people["b_id"])
    assert own.check_series_assignment(b, shared, people["b_id"]) == shared


def test_move_into_private_series_refused_service_and_sql_agree(people):
    """The LOW-1 attack: A moves B's shared drama into A's private series."""
    a, b = people["a"], people["b"]
    secret = db.get_or_create_series("A secret", owner_user_id=people["a_id"], is_private=True)
    b_drama = db.create_drama(title_zh="b", owner_user_id=people["b_id"])
    with pytest.raises(ConflictError):
        own.assign_drama_series(a, b_drama, secret)
    assert not db.assign_drama_series(b_drama, secret)          # the SQL guard alone
    assert db.get_drama(b_drama)["series_id"] is None
    assert own.can_see_drama(b, b_drama) and b_drama in _visible_sql(people["b_id"])
    # A's own and PC-owned dramas may go in.
    a_drama = db.create_drama(title_zh="a", owner_user_id=people["a_id"])
    pc_drama = db.create_drama(title_zh="pc")
    own.assign_drama_series(a, a_drama, secret)
    assert db.assign_drama_series(pc_drama, secret)
    with pytest.raises(NotFoundError):
        own.assign_drama_series(a, 10**6, secret)


def test_empty_series_made_private_then_move_refused(people):
    a = people["a"]
    sid = own.get_or_create_series_for(a, "Mine")
    own.set_private(a, "series", sid, True)
    b_drama = db.create_drama(title_zh="b", owner_user_id=people["b_id"])
    with pytest.raises(ConflictError):
        own.assign_drama_series(a, b_drama, sid)


def test_private_switch_is_one_conditional_write(people):
    """db.set_item_private itself refuses (0 rows) what the service refuses."""
    sid = db.get_or_create_series("S", owner_user_id=people["a_id"])
    b_ep = db.create_drama(title_zh="b", series_id=sid, owner_user_id=people["b_id"])
    assert not db.set_item_private("series", sid, True)
    assert not db.set_item_private("drama", b_ep, True)
    assert db.get_item_ownership("series", sid)["is_private"] == 0
    db.update_drama(b_ep, owner_user_id=None)                # PC-owned: no longer blocks
    assert db.set_item_private("series", sid, True)
    assert db.set_item_private("series", sid, False)
    solo = db.create_drama(title_zh="solo", owner_user_id=people["b_id"])
    assert db.set_item_private("drama", solo, True)
    assert not db.set_item_private("drama", 10**6, False)


def test_drama_private_flag_cleared_when_it_gets_a_series(people):
    b = people["b"]
    sid = db.get_or_create_series("Open", owner_user_id=people["b_id"])
    solo = db.create_drama(title_zh="x", owner_user_id=people["b_id"])
    own.set_private(b, "drama", solo, True)
    own.assign_drama_series(b, solo, sid)
    assert db.get_item_ownership("drama", solo)["is_private"] == 0
    other = db.create_drama(title_zh="y", owner_user_id=people["b_id"], is_private=1)
    db.update_drama(other, series_id=sid)                     # raw path too
    assert db.get_item_ownership("drama", other)["is_private"] == 0
    db.update_drama(other, series_id=None, is_private=1)      # no series: allowed
    assert db.get_item_ownership("drama", other)["is_private"] == 1
    assert own.can_see_drama(b, other) and not own.can_see_drama(people["a"], other)
    assert other not in _visible_sql(people["a_id"])
