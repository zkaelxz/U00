"""Auth slice B1: ownership columns, migration and visibility rule."""

import contextlib
import sqlite3

import pytest

import db
from services import ownership_service as own
from services.service_errors import (ConflictError, ForbiddenError, InvalidInputError,
                                     NotFoundError)

LOCAL = {"user_id": None, "is_admin": True, "is_local_owner": True}


def _p(uid, admin=False):
    return {"user_id": uid, "is_admin": admin, "is_local_owner": False}


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


def test_set_private_refused_for_drama_in_series(people):
    a = people["a"]
    sid = db.get_or_create_series("S", owner_user_id=people["a_id"])
    did = db.create_drama(title_zh="e", series_id=sid, owner_user_id=people["a_id"])
    with pytest.raises(InvalidInputError, match="Make the series private instead"):
        own.set_private(a, "drama", did, True)
    own.set_private(a, "series", sid, True)
    assert not own.can_see_drama(people["b"], did)


def test_share_by_default_and_new_item_defaults(people):
    a = people["a"]
    assert own.get_share_by_default(a) is True
    assert own.new_item_defaults(a) == {"owner_user_id": people["a_id"], "is_private": 0}
    own.set_share_by_default(a, False)
    assert own.get_share_by_default(a) is False
    assert own.new_item_defaults(a)["is_private"] == 1
    assert own.get_share_by_default(people["b"]) is True
    assert own.get_share_by_default(LOCAL) is True
    own.set_share_by_default(LOCAL, False)
    assert db.get_app_setting(own.HOUSEHOLD_SHARE_KEY) is False
    assert own.new_item_defaults(None) == {"owner_user_id": None, "is_private": 1}


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
    b_ep = db.create_drama(title_zh="b1", series_id=sid, owner_user_id=people["b_id"],
                           is_private=1)
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
    # Legacy-owned (NULL) dramas don't block it.
    db.create_drama(title_zh="legacy", series_id=sid)
    db.update_drama(b_ep, series_id=None)
    own.set_private(a, "series", sid, True)
    ids = [d["id"] for d in db.list_dramas()]
    assert _visible_py(a, ids) == _visible_sql(people["a_id"])
    assert _visible_py(b, ids) == _visible_sql(people["b_id"]) == {b_ep, b_solo}


def test_drama_ownership_does_not_override_a_private_series(people):
    """If B's drama ends up in A's private series anyway (e.g. moved in
    later), B loses it too -- the series' privacy wins, in both paths."""
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
