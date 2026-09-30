"""What the scheduled chapter check does with a tracked series whose drama
link went stale after it was made.

A tracked series' drama link is checked (ownership_service.can_edit_drama)
only when it is set (sources_tracking_service.set_tracked_drama,
sources_registry_service.set_tracked). The chapter check then runs with no
principal and, with `auto_queue_new_chapters` on, hands `row["drama_id"]`
straight to pipeline.start_import. These tests pin down what happens when,
between link time and the next cycle, (A) the drama's owner makes it
private, or (B) the drama is deleted.

`test_current_behaviour_*` assert what the code does today; the matching
`test_desired_*` tests are strict xfails describing what it should do.
Fake adapters only, no network."""

import os
import time

import pytest

import background_jobs
import db
from services import drama_service
from services import ownership_service as own
from services import sources_tracking_service as tracking
from sources import chapter_check, pipeline, registry, store
from sources.base import SourceAdapter
from sources.http import PacingPolicy
from sources.models import ChapterInfo, PageRef
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


def test_current_behaviour_private_drama_still_receives_auto_import(made_private, env):
    """Current: the link survives set_private, and the next cycle imports
    B's series into A's now-private drama with no ownership check."""
    did = made_private
    assert not os.path.exists(_raw_path(did))
    summary, st = _cycle_then_wait(did)
    assert summary["new"] == 2 and summary["queued"] == ["Series T"]
    assert summary["errors"] == {}
    assert st["status"] == "done"
    assert [c["chapter_id"] for c in st["result"]["chapters"] if c["ok"]] == ["c2", "c3"]
    assert env["alpha"].calls == ["c2", "c3"]
    with open(_raw_path(did), encoding="utf-8") as f:
        text = f.read()
    assert "imported text of c2" in text and "imported text of c3" in text
    assert store.imported_chapter_ids("alpha", SERIES, did) == {"c2", "c3"}
    row = _row("alpha")
    assert row["drama_id"] == did and not row["last_check_error"]
    assert db.get_item_ownership("drama", did)["is_private"] == 1


@pytest.mark.xfail(strict=True, reason=(
    "Stale tracked link: sources/chapter_check.py _run_claimed_cycle auto-queues into "
    "row['drama_id'] with no principal, and tracked_series has no linked_by/owner column, "
    "so a link made while the drama was shared keeps writing after it goes private."))
def test_desired_private_drama_gets_no_auto_import_from_another_users_link(made_private, env):
    did = made_private
    summary = chapter_check.run_check_cycle()
    _wait(pipeline.import_job_id(did))
    assert summary["queued"] == []
    assert env["alpha"].calls == []
    assert not os.path.exists(_raw_path(did))


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


def test_current_behaviour_deleted_novel_drama_gets_orphan_folder_and_records(env):
    """Current: the job starts for the deleted id and succeeds: it recreates
    the deleted drama's folder (db.drama_dir makes it) with the chapter text
    in it, records the chapters as imported into the dead id, and the
    tracked row shows no error. No drama row points at the folder."""
    name, did = _link_then_delete(env, comic=False)
    summary, st = _cycle_then_wait(did)
    assert summary["queued"] == ["Series T"] and summary["errors"] == {}
    assert st["status"] == "done"
    assert all(c["ok"] for c in st["result"]["chapters"])
    assert env[name].calls == ["c2", "c3"]
    assert db.get_drama(did) is None
    assert os.path.isfile(_raw_path(did))          # orphan folder and file
    assert store.imported_chapter_ids(name, SERIES, did) == {"c2", "c3"}
    row = _row(name)
    assert row["drama_id"] == did and not row["last_check_error"]


def test_current_behaviour_deleted_comic_drama_job_errors_leaving_orphan_page(env):
    """Current: the job starts, downloads the first chapter's pages, writes
    the first page file into a recreated folder, then db.create_page fails
    the pages foreign key (sqlite3.IntegrityError, not a SourceError) and
    the whole job ends in error. The tracked row shows no error and keeps
    the link, so each later cycle with new chapters repeats this."""
    name, did = _link_then_delete(env, comic=True)
    summary, st = _cycle_then_wait(did)
    assert summary["queued"] == ["Series T"] and summary["errors"] == {}
    assert st["status"] == "error"
    assert "FOREIGN KEY" in (st.get("error") or "")
    assert env[name].calls == ["c2"]               # stopped at the first chapter
    pages_dir = os.path.join(_drama_folder(did), "pages")
    assert sorted(os.listdir(pages_dir)) == ["page_0000.png"]   # file, no row
    assert db.list_pages(did) == []
    assert store.imported_chapter_ids(name, SERIES, did) == set()
    row = _row(name)
    assert row["drama_id"] == did and not row["last_check_error"]


@pytest.mark.xfail(strict=True, reason=(
    "Stale tracked link: pipeline.start_import (sources/pipeline.py) never checks the drama "
    "exists, and deleting a drama leaves tracked_series.drama_id pointing at the dead id, "
    "so the auto-import recreates the deleted drama's folder and writes into it."))
@pytest.mark.parametrize("comic", [False, True], ids=["novel", "comic"])
def test_desired_deleted_drama_gets_no_import_and_row_reports_it(env, comic):
    name, did = _link_then_delete(env, comic=comic)
    summary = chapter_check.run_check_cycle()
    _wait(pipeline.import_job_id(did))
    assert summary["queued"] == []
    assert env[name].calls == []
    assert not os.path.exists(_drama_folder(did))
    row = _row(name)
    # Either the link is cleared or the row says why nothing was imported.
    assert row["drama_id"] is None or row["last_check_error"]
