"""What the scheduled chapter check does with a tracked series whose drama
link went stale after it was made.

A tracked series' drama link is checked (ownership_service.can_edit_drama)
when it is set (sources_tracking_service.set_tracked_drama,
sources_registry_service.set_tracked), which also records who set it
(`linked_by_user_id`). The chapter check runs with no request principal;
with `auto_queue_new_chapters` on it imports into `row["drama_id"]` only
while the link owner, as an ordinary member, can still edit that drama.
These tests cover what happens when, between link time and the next cycle,
(A) the drama's owner makes it private (or the link owner is deactivated),
or (B) the drama is deleted. Fake adapters only, no network."""

import os
import time

import pytest

import background_jobs
import db
from services import auth_service, drama_service, sources_registry_service
from services import ownership_service as own
from services import sources_tracking_service as tracking
from sources import chapter_check, pipeline, registry, store
from sources.base import SourceAdapter
from sources.http import PacingPolicy
from sources.models import ChapterInfo, PageRef, SourceError
from tests.sources_helpers import ScriptedTransport, png

HOST = "https://fake.invalid"
SERIES = "s1"


def _chapters(name):
    return [ChapterInfo(name, SERIES, cid, f"Chapter {cid}", f"{HOST}/c/{cid}")
            for cid in ("c1", "c2", "c3")]


def _make(name, comic=False):
    """A fake novel (or comic) adapter: three chapters, no network. `calls`
    records every chapter it fetched."""
    calls = []

    class Fake(SourceAdapter):
        pass

    Fake.name = name
    Fake.display_name = name.title()

    def __init__(self, client=None, **kw):
        kw.setdefault("transport", ScriptedTransport({}))
        kw["policy"] = PacingPolicy(min_delay=0.0, max_delay=0.0, session_break_min_requests=0)
        SourceAdapter.__init__(self, client, **kw)

    def get_chapters(self, series_id):
        return _chapters(name)

    def get_chapter_text(self, ch):
        calls.append(ch.chapter_id)
        return f"imported text of {ch.chapter_id} " * 10

    def get_pages(self, ch):
        calls.append(ch.chapter_id)
        return [PageRef(name, ch.chapter_id, i, f"{HOST}/p/{i}") for i in range(2)]

    def download_page(self, page):
        return png(20, 30, seed=page.index), ".png"

    Fake.__init__ = __init__
    Fake.get_chapters = get_chapters
    if comic:
        Fake.get_pages = get_pages
        Fake.download_page = download_page
    else:
        Fake.get_chapter_text = get_chapter_text
    Fake.calls = calls
    return Fake


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if not st or st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def _p(uid):
    return {"user_id": uid, "is_admin": False, "is_local_owner": False,
            "admin_override": False}


@pytest.fixture
def env(isolated_db, monkeypatch):
    classes = {}
    monkeypatch.setattr(registry, "adapter_classes", lambda: dict(classes))
    store.set_setting("auto_queue_new_chapters", True)
    yield classes
    for jid in list(background_jobs.list_all_jobs()):
        if str(jid).startswith(("sources_", "sourceimport_")):
            _wait(jid)
            background_jobs.clear_job(jid)


def _track(source, drama_id=None):
    """Tracks the series with only c1 known, so a check finds c2 and c3."""
    store.track_series(source, SERIES, "Series T", "", drama_id,
                       known_chapters=[c for c in _chapters(source) if c.chapter_id == "c1"])


def _row(source):
    return next(r for r in store.list_tracked_series()
                if r["source"] == source and r["series_id"] == SERIES)


def _drama_folder(did):
    # Built by hand: db.drama_dir() would create the folder being checked for.
    return os.path.join(db.DRAMAS_DIR, str(did))


def _raw_path(did):
    return os.path.join(_drama_folder(did), pipeline.RAW_NOVEL_FILENAME)


def _cycle_then_wait(did):
    summary = chapter_check.run_check_cycle()
    st = _wait(pipeline.import_job_id(did))
    return summary, st


# ---------------------------------------------------------------------------
# (A) B links a tracked series to A's shared drama; A then makes it private
# ---------------------------------------------------------------------------

@pytest.fixture
def made_private(env):
    """A owns a shared novel drama; B (who may edit it while it is shared)
    links a tracked series to it; A then makes the drama private."""
    env["alpha"] = _make("alpha")
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    did = db.create_drama(title_en="A's novel", media_type="novel",
                          content_mode="novel_narration", owner_user_id=a, is_private=0)
    _track("alpha")
    assert own.can_edit_drama(_p(b), did)
    tracking.set_tracked_drama("alpha", SERIES, did, principal=_p(b))
    assert _row("alpha")["drama_id"] == did
    own.set_private(_p(a), "drama", did, True)
    assert not own.can_edit_drama(_p(b), did)   # B could not make this link now
    return did


def _denied(did, env, name="alpha"):
    summary = chapter_check.run_check_cycle()
    _wait(pipeline.import_job_id(did))
    assert summary["new"] == 2 and summary["queued"] == []
    assert summary["errors"] == {"Series T": chapter_check.LINK_OWNER_DENIED}
    assert env[name].calls == []
    assert not os.path.exists(_raw_path(did))
    assert store.imported_chapter_ids(name, SERIES, did) == set()
    row = _row(name)
    assert row["drama_id"] == did          # the link is kept, not cleared
    assert row["last_check_error"] == "The link owner can no longer edit the drama."


def _imports(did, env, name="alpha"):
    summary, st = _cycle_then_wait(did)
    assert summary["queued"] == ["Series T"] and summary["errors"] == {}
    assert st["status"] == "done"
    assert env[name].calls == ["c2", "c3"]
    with open(_raw_path(did), encoding="utf-8") as f:
        text = f.read()
    assert "imported text of c2" in text and "imported text of c3" in text
    assert store.imported_chapter_ids(name, SERIES, did) == {"c2", "c3"}
    assert not _row(name)["last_check_error"]


def test_desired_private_drama_gets_no_auto_import_from_another_users_link(made_private, env):
    did = made_private
    assert db.auth_get_user_by_email("b@example.com")["id"] == _row("alpha")["linked_by_user_id"]
    _denied(did, env)
    assert db.get_item_ownership("drama", did)["is_private"] == 1


@pytest.fixture
def shared_link(env):
    """A owns a shared novel drama that B links a tracked series to."""
    env["alpha"] = _make("alpha")
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    did = db.create_drama(title_en="A's novel", media_type="novel",
                          content_mode="novel_narration", owner_user_id=a, is_private=0)
    _track("alpha")
    tracking.set_tracked_drama("alpha", SERIES, did, principal=_p(b))
    return a, b, did


def test_link_owner_still_imports_while_the_drama_stays_shared(shared_link, env):
    _a, b, did = shared_link
    assert _row("alpha")["linked_by_user_id"] == b
    _imports(did, env)


def test_deactivated_link_owner_gets_no_import(shared_link, env):
    _a, b, did = shared_link
    auth_service.deactivate_user(b)
    _denied(did, env)


def test_missing_link_owner_gets_no_import(shared_link, env):
    _a, _b, did = shared_link
    store.set_tracked_drama("alpha", SERIES, None)
    store.set_tracked_drama("alpha", SERIES, did, linked_by_user_id=987654)
    assert db.auth_get_user(987654) is None
    _denied(did, env)


def test_admin_link_is_checked_as_a_member(env):
    """An admin at the PC may link another user's private drama (the admin
    override); the scheduled check acts for them only as a member."""
    env["alpha"] = _make("alpha")
    a = db.auth_create_user("a@example.com")
    admin = db.auth_create_user("admin@example.com", is_admin=True)
    did = db.create_drama(title_en="A's novel", media_type="novel",
                          content_mode="novel_narration", owner_user_id=a, is_private=1)
    _track("alpha")
    pc_admin = dict(_p(admin), is_admin=True, admin_override=True)
    tracking.set_tracked_drama("alpha", SERIES, did, principal=pc_admin)
    assert _row("alpha")["linked_by_user_id"] == admin
    _denied(did, env)


def test_null_link_owner_keeps_importing(env):
    """Auth off / the PC owner made the link: no owner is stored and the
    cycle imports as before, even into a private drama."""
    env["alpha"] = _make("alpha")
    a = db.auth_create_user("a@example.com")
    did = db.create_drama(title_en="A's novel", media_type="novel",
                          content_mode="novel_narration", owner_user_id=a, is_private=1)
    _track("alpha")
    tracking.set_tracked_drama("alpha", SERIES, did)
    assert _row("alpha")["linked_by_user_id"] is None
    _imports(did, env)


def test_owner_linking_their_own_drama_then_making_it_private_still_imports(env):
    env["alpha"] = _make("alpha")
    a = db.auth_create_user("a@example.com")
    did = db.create_drama(title_en="A's novel", media_type="novel",
                          content_mode="novel_narration", owner_user_id=a, is_private=0)
    _track("alpha")
    tracking.set_tracked_drama("alpha", SERIES, did, principal=_p(a))
    own.set_private(_p(a), "drama", did, True)
    _imports(did, env)


def test_relinking_the_same_drama_keeps_the_link_owner(shared_link, env):
    a, b, did = shared_link
    tracking.set_tracked_drama("alpha", SERIES, did, principal=_p(a))
    assert _row("alpha")["linked_by_user_id"] == b
    store.track_series("alpha", SERIES, "Series T", "", did, linked_by_user_id=a)
    assert _row("alpha")["linked_by_user_id"] == b
    other = db.create_drama(title_en="A's other", media_type="novel",
                            content_mode="novel_narration", owner_user_id=a, is_private=0)
    tracking.set_tracked_drama("alpha", SERIES, other, principal=_p(a))
    assert _row("alpha")["linked_by_user_id"] == a
    store.track_series("alpha", SERIES, "Series T", "", did, linked_by_user_id=b)
    assert _row("alpha")["linked_by_user_id"] == b


def test_link_owner_is_not_in_the_tracked_list(shared_link):
    _a, b, _did = shared_link
    for principal in (None, _p(b)):
        rows = sources_registry_service.list_tracked(principal)
        assert rows and all("linked_by_user_id" not in r for r in rows)


def test_old_sources_db_gains_the_link_owner_column(isolated_db):
    import sqlite3
    path = store.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tracked_series (source TEXT NOT NULL, series_id TEXT NOT "
                     "NULL, title TEXT NOT NULL, url TEXT, drama_id INTEGER, last_checked "
                     "REAL, last_check_error TEXT, PRIMARY KEY (source, series_id))")
        conn.execute("INSERT INTO tracked_series(source, series_id, title) VALUES('x', 's', 'T')")
    [row] = store.list_tracked_series()
    assert row["title"] == "T" and row["linked_by_user_id"] is None


# ---------------------------------------------------------------------------
# (B) the linked drama is deleted
# ---------------------------------------------------------------------------

def _link_then_delete(env, comic):
    name = "comic" if comic else "alpha"
    env[name] = _make(name, comic=comic)
    if comic:
        did = db.create_drama(title_en="D", media_type="manhua")
    else:
        did = db.create_drama(title_en="D", media_type="novel", content_mode="novel_narration")
    _track(name)
    tracking.set_tracked_drama(name, SERIES, did)
    db.drama_dir(did)       # the folder a real drama has, so the delete removes one
    drama_service.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert db.get_drama(did) is None
    assert not os.path.exists(_drama_folder(did))
    assert _row(name)["drama_id"] == did     # deleting the drama leaves the link
    return name, did


@pytest.mark.parametrize("comic", [False, True], ids=["novel", "comic"])
def test_desired_deleted_drama_gets_no_import_and_row_reports_it(env, comic):
    name, did = _link_then_delete(env, comic=comic)
    summary = chapter_check.run_check_cycle()
    _wait(pipeline.import_job_id(did))
    assert summary["queued"] == []
    assert env[name].calls == []
    assert not os.path.exists(_drama_folder(did))
    row = _row(name)
    assert row["drama_id"] == did          # the link is kept, not cleared
    assert row["last_check_error"] == "The linked drama was deleted."
    assert summary["errors"] == {"Series T": "The linked drama was deleted."}
    assert store.imported_chapter_ids(name, SERIES, did) == set()


def test_start_import_refuses_missing_drama(env):
    name, did = _link_then_delete(env, comic=False)
    assert pipeline.start_import(name, SERIES, _chapters(name), did) is False
    assert not os.path.exists(_drama_folder(did))


def test_run_import_job_refuses_missing_drama(env):
    name, did = _link_then_delete(env, comic=False)
    with pytest.raises(SourceError):
        pipeline.run_import_job(pipeline.import_job_id(did), name, _chapters(name), did)
    assert not os.path.exists(_drama_folder(did))
