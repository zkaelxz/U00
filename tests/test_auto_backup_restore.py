"""Single-drama restore from the automatic snapshot: round trip, owners and media."""

import collections
import contextlib
import datetime
import json
import os
import sqlite3

import pytest

import background_jobs
from jobs import job_launch
import db
from services import auto_backup_service as abs_
from services import drama_restore_service as drs
from services import library_admin_service as las
from services import library_backup_service as lbs
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, ServiceError
from tests.auto_backup_helpers import (
    SERIES_CHILDREN, _LEGACY_BUG_REPORTS, _conn, _ddir, _delete_drama, _delete_series,
    _dump_all, _enable, _insert, _put_job, _read, _restore, _seed_world, _series_rows, _snap,
    _state, _tables,
)


# From the spec, deliberately not read from the module under test.
SKIPPED = {"usage_log", "bulk_jobs", "metadata_research_results", "speaker_merge_undos"}


LINE_JSON = {"translation_versions": "lines_json", "line_history": "snapshot_json"}


LINE_REF_TABLES = ("translation_notes", "line_emotions", "reading_history", "bug_reports")


PROFILE_TABLES = ("progress", "personal_notes", "reading_history")


def _fk_child_tables():
    """Live tables with a foreign key to dramas(id), minus the ones the
    service deliberately skips -- read from the schema, not from the module under test."""
    with contextlib.closing(_conn()) as c:
        return sorted({t for t in _tables(c)
                       for fk in c.execute(f'PRAGMA foreign_key_list("{t}")')
                       if fk["table"] == "dramas"} - SKIPPED)


def _canon(did):
    """Drama `did`'s row, every child table, its bubbles and its series,
    with every id replaced by the thing it points at (line zh, series
    character name, profile name, page filename, series name), so rows can
    be compared across a restore that gives them new ids."""
    with contextlib.closing(_conn()) as c:
        line_zh = {r["id"]: r["zh"] for r in c.execute("SELECT id, zh FROM lines WHERE drama_id = ?",
                                                        (did,))}
        sc_name = {r["id"]: r["character_name"] for r in c.execute(
            "SELECT id, character_name FROM series_characters")}
        prof = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM profiles")}
        page_fn = {r["id"]: r["filename"] for r in c.execute(
            "SELECT id, filename FROM pages WHERE drama_id = ?", (did,))}

        def line_ref(v):
            return None if v is None else ("line", line_zh.get(v, ("DANGLING", v)))

        out = {}
        for t in _fk_child_tables():
            rows = []
            for r in c.execute(f'SELECT * FROM "{t}" WHERE drama_id = ?', (did,)):
                d = dict(r)
                d.pop("id", None)
                d.pop("drama_id")
                if "line_id" in d:
                    d["line_id"] = line_ref(d["line_id"])
                if d.get("series_character_id") is not None:
                    d["series_character_id"] = ("sc", sc_name.get(d["series_character_id"],
                                                                  "DANGLING"))
                if "profile_id" in d:
                    d["profile_id"] = ("profile", prof.get(d["profile_id"], "DANGLING"))
                if t in LINE_JSON:
                    col = LINE_JSON[t]
                    items = json.loads(d[col])
                    for it in items:
                        it["id"] = line_ref(it["id"])
                    d[col] = json.dumps(items, sort_keys=True)
                rows.append(repr(sorted(d.items())))
            out[t] = sorted(rows)
        bubbles = []
        for pid, fn in page_fn.items():
            for r in c.execute("SELECT * FROM bubbles WHERE page_id = ?", (pid,)):
                d = dict(r)
                d.pop("id")
                d["page_id"] = fn
                bubbles.append(repr(sorted(d.items())))
        out["bubbles"] = sorted(bubbles)
        drama = dict(c.execute("SELECT * FROM dramas WHERE id = ?", (did,)).fetchone())
        drama.pop("id")
        drama.pop("updated_at")
        series_id = drama.pop("series_id")
        if series_id is not None:
            s = dict(c.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone())
            s.pop("id")
            out["series"] = repr(sorted(s.items()))
            for t in SERIES_CHILDREN:
                rows = []
                for r in c.execute(f'SELECT * FROM "{t}" WHERE series_id = ?', (series_id,)):
                    d = dict(r)
                    d.pop("id")
                    d.pop("series_id")
                    rows.append(repr(sorted(d.items())))
                out[t] = sorted(rows)
        else:
            out["series"] = None
        out["dramas"] = repr(sorted(drama.items()))
    return out


def test_child_tables_cover_every_fk_to_dramas(isolated_db):
    """Guard: every live table with a foreign key to dramas(id) is either
    restored (_CHILD_TABLES) or deliberately skipped (_SKIPPED_TABLES)."""
    with contextlib.closing(_conn()) as c:
        c.execute(_LEGACY_BUG_REPORTS)
        fk_tables = {t for t in _tables(c)
                     for fk in c.execute(f'PRAGMA foreign_key_list("{t}")')
                     if fk["table"] == "dramas"}
    assert fk_tables == set(drs.CHILD_TABLES) | set(drs._SKIPPED_TABLES)
    assert not set(drs.CHILD_TABLES) & set(drs._SKIPPED_TABLES)
    assert set(drs._SKIPPED_TABLES) == {"usage_log", "bulk_jobs", "metadata_research_results",
                                            "speaker_merge_undos"}


class TestRestoreBugReports:
    def _drop_table(self):
        with contextlib.closing(_conn()) as c:
            c.execute("DROP TABLE bug_reports")
            c.commit()

    def test_single_title_restore_recreates_the_table_when_the_snapshot_has_it(self, isolated_db):
        w = _seed_world()
        _snap()
        _delete_drama(w["a"])
        self._drop_table()
        _restore(w["a"])
        with contextlib.closing(_conn()) as c:
            assert c.execute("SELECT COUNT(*) FROM bug_reports WHERE drama_id = ?",
                             (w["a"],)).fetchone()[0] == 1

    def test_single_title_restore_creates_no_table_when_the_snapshot_lacks_it(self, isolated_db):
        w = _seed_world()
        self._drop_table()
        _snap()
        _delete_drama(w["a"])
        _restore(w["a"])
        with contextlib.closing(_conn()) as c:
            assert "bug_reports" not in _tables(c)


class TestRestoreRoundTrip:
    def test_full_round_trip_series_gone(self, isolated_db):
        w = _seed_world()
        a, b = w["a"], w["b"]
        expected = _canon(a)
        with contextlib.closing(_conn()) as c:
            old_line_ids = {r[0] for r in c.execute("SELECT id FROM lines WHERE drama_id = ?", (a,))}
        _snap()
        _delete_drama(a)
        _delete_series(w["series"])
        db.delete_profile(w["p2"])
        before = _dump_all()

        res = _restore(a)

        assert res["drama_id"] == a and res["restored_as_new"] is False
        assert res["title"] == "Alpha" and res["media_restored"] is False
        assert res["snapshot_kind"] == "db-only"
        assert res["skipped_tables"] == ["bulk_jobs", "metadata_research_results",
                                         "speaker_merge_undos", "usage_log"]
        # P2 is gone, so its profile rows are not restored
        for t in PROFILE_TABLES:
            expected[t] = [r for r in expected[t] if "'P2'" not in r]
        got = _canon(a)
        assert got == expected
        for t in _fk_child_tables() + ["bubbles"]:
            assert got[t], f"seed left {t} empty"
            assert res["counts"][t] == len(got[t]), t
        assert res["counts"]["series"] == 1
        for t in SERIES_CHILDREN:
            assert res["counts"][t] == len(got[t]) > 0
        # the lines really got new ids and every reference follows them
        with contextlib.closing(_conn()) as c:
            new_line_ids = {r[0] for r in c.execute("SELECT id FROM lines WHERE drama_id = ?",
                                                    (a,))}
            assert new_line_ids.isdisjoint(old_line_ids)
            for t in LINE_REF_TABLES:
                for (lid,) in c.execute(f'SELECT line_id FROM "{t}" WHERE drama_id = ?', (a,)):
                    assert lid in new_line_ids, t
            for t, col in LINE_JSON.items():
                for (js,) in c.execute(f'SELECT "{col}" FROM "{t}" WHERE drama_id = ?', (a,)):
                    assert {it["id"] for it in json.loads(js)} == new_line_ids, t
            series_id = c.execute("SELECT series_id FROM dramas WHERE id = ?", (a,)).fetchone()[0]
            assert series_id is not None and series_id != w["series"]
            new_sc = {r[0] for r in c.execute("SELECT id FROM series_characters WHERE series_id = ?",
                                              (series_id,))}
            for t in ("characters", "voice_suggestion_dismissals"):
                for (scid,) in c.execute(f'SELECT series_character_id FROM "{t}" WHERE drama_id = ? '
                                         "AND series_character_id IS NOT NULL", (a,)):
                    assert scid in new_sc, t
            # the skipped tables stay empty for the restored drama
            for t in SKIPPED:
                assert c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE drama_id = ?',
                                 (a,)).fetchone()[0] == 0, t
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        _assert_only_restored_rows_added(before, _dump_all(), a)
        assert db.get_drama(b) is not None

    def test_series_still_exists_is_linked_not_duplicated(self, isolated_db):
        w = _seed_world()
        a = w["a"]
        expected = _canon(a)
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:
            n_series = c.execute("SELECT COUNT(*) FROM series").fetchone()[0]
            n_children = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                          for t in SERIES_CHILDREN}
        res = _restore(a)
        assert "series" not in res["counts"]
        assert _canon(a) == expected
        with contextlib.closing(_conn()) as c:
            assert c.execute("SELECT COUNT(*) FROM series").fetchone()[0] == n_series
            assert {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    for t in SERIES_CHILDREN} == n_children
            assert c.execute("SELECT series_id FROM dramas WHERE id = ?",
                             (a,)).fetchone()[0] == w["series"]
            got_sc = {r[0] for r in c.execute(
                "SELECT series_character_id FROM characters WHERE drama_id = ? "
                "AND series_character_id IS NOT NULL", (a,))}
            assert got_sc == {w["sc"]["Lin"]}

    def test_id_in_use_restores_as_new_and_leaves_live_drama_alone(self, isolated_db):
        w = _seed_world()
        a = w["a"]
        expected = _canon(a)
        _snap()
        db.update_drama(a, title_en="Alpha edited", status="dubbed")
        with contextlib.closing(_conn()) as c:
            _insert(c, "lines", drama_id=a, idx=9, start=9, end=10, zh="new-live-line", en="")
            c.commit()
        live_before = _canon(a)
        before = _dump_all()

        res = _restore(a)

        assert res["restored_as_new"] is True
        new = res["drama_id"]
        assert new != a and db.get_drama(new) is not None
        today = datetime.date.today().isoformat()
        assert res["title"] == f"Alpha (restored {today})"
        assert db.get_drama(new)["title_en"] == f"Alpha (restored {today})"
        assert _canon(a) == live_before
        got = _canon(new)
        want = dict(expected)
        want["dramas"] = got["dramas"]   # title differs by design; compare the rest below
        assert got == want
        d_old = dict(eval(expected["dramas"]))   # noqa: S307 -- our own repr of a dict
        d_new = dict(eval(got["dramas"]))        # noqa: S307
        assert d_new.pop("title_en") == f"Alpha (restored {today})"
        d_old.pop("title_en")
        assert d_new == d_old
        _assert_only_restored_rows_added(before, _dump_all(), new)

    def test_title_zh_only_gets_suffix_on_title_zh(self, isolated_db):
        a = db.create_drama(title_zh="中文名")
        _snap()
        res = _restore(a)
        assert res["restored_as_new"] is True
        assert db.get_drama(res["drama_id"])["title_zh"].startswith("中文名 (restored ")

    def test_folder_exists_restores_as_new(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        os.makedirs(os.path.join(db.DRAMAS_DIR, str(a)))
        res = _restore(a)
        assert res["restored_as_new"] is True and res["drama_id"] != a
        assert db.get_drama(a) is None

    def test_restore_twice_second_is_new(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        first = _restore(a)
        second = _restore(a)
        assert first["drama_id"] == a and not first["restored_as_new"]
        assert second["drama_id"] not in (a,) and second["restored_as_new"]

    def test_unowned_user_cleared(self, isolated_db):
        from services import auth_service
        u = auth_service.add_user("kid@example.com")
        a = db.create_drama(title_en="A", owner_user_id=u["id"])
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:
            c.execute("DELETE FROM users WHERE id = ?", (u["id"],))
            c.commit()
        _restore(a)
        assert db.get_drama(a)["owner_user_id"] is None

    def test_not_in_snapshot(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        before = _dump_all()
        with pytest.raises(NotFoundError):
            _restore(a + 50)
        assert _dump_all() == before

    @pytest.mark.parametrize("did", [0, -1, "1", True, 1.0, None])
    def test_bad_id(self, isolated_db, did):
        with pytest.raises(InvalidInputError):
            drs.restore_drama(did, confirm=True, confirm_text="RESTORE")

    @pytest.mark.parametrize("confirm,text", [(False, "RESTORE"), (True, "restore"), (True, ""),
                                              (1, "RESTORE"), (True, "DELETE")])
    def test_typed_confirm_required(self, isolated_db, confirm, text):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        before = _dump_all()
        with pytest.raises(InvalidInputError):
            drs.restore_drama(a, confirm=confirm, confirm_text=text)
        assert _dump_all() == before

    def test_refused_while_backup_running(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        before = _dump_all()
        for job_id in (abs_.JOB_ID, las.BACKUP_JOB_ID, las.EXPORT_JOB_ID):
            _put_job(job_id)
            with pytest.raises(ConflictError):
                _restore(a)
            background_jobs.clear_job(job_id)
        assert _dump_all() == before

    def test_refused_during_library_restore(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        assert job_launch.acquire_exclusive("Library restore")
        try:
            with pytest.raises(ConflictError):
                _restore(a)
        finally:
            job_launch.release_exclusive()
        assert db.get_drama(a) is None

    def test_scheduled_check_busy_while_restore_holds_library(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        _enable()
        _state()
        seen = []
        real = drs._restore_from

        def spy(*args, **kw):
            seen.append(abs_.check_and_run())
            for start in (lambda: abs_.start_now(replace=True), lbs.start_backup,
                          lbs.start_database_backup):
                try:
                    start()
                    seen.append("started a backup during the restore")
                except ConflictError:
                    pass
            return real(*args, **kw)
        monkeypatch.setattr(drs, "_restore_from", spy)
        monkeypatch.setattr(abs_, "_start", lambda *a, **k: pytest.fail("backup started"))
        _restore(a)
        assert seen == ["busy"]

    def test_failed_insert_rolls_back(self, isolated_db, monkeypatch):
        w = _seed_world()
        a = w["a"]
        _snap(include_media=True)
        _delete_drama(a)
        _delete_series(w["series"])
        before = _dump_all()
        real = drs._insert

        def flaky(dst, table, row, cols):
            if table == "wiki_entries":
                raise sqlite3.IntegrityError(f"boom {db.LIBRARY_DIR}")
            return real(dst, table, row, cols)
        monkeypatch.setattr(drs, "_insert", flaky)
        with pytest.raises(ServiceError) as e:
            _restore(a)
        assert db.LIBRARY_DIR not in str(e.value)
        assert _dump_all() == before
        assert not [n for n in os.listdir(db.DRAMAS_DIR)
                    if n.startswith((".restoring", db.MEDIA_STAGING_PREFIX))] \
            if os.path.isdir(db.DRAMAS_DIR) else True


def _assert_only_restored_rows_added(before, after, live_id):
    """Every row that existed before is still there, unchanged; the new
    rows belong to the restored drama (or its recreated series, bubbles on
    its pages, its audit entry)."""
    with contextlib.closing(_conn()) as c:
        pages = {r[0] for r in c.execute("SELECT id FROM pages WHERE drama_id = ?", (live_id,))}
        sid = c.execute("SELECT series_id FROM dramas WHERE id = ?", (live_id,)).fetchone()[0]
        cols = {t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')] for t in after}
    for t, rows in before.items():
        missing = rows - after.get(t, collections.Counter())
        assert not missing, f"{t}: rows changed or removed: {list(missing)[:3]}"
    for t, rows in after.items():
        added = rows - before.get(t, collections.Counter())
        for rep in added:
            row = dict(zip(cols[t], eval(rep)))   # noqa: S307 -- repr of our own tuple
            if "drama_id" in row and t != "usage_log":
                assert row["drama_id"] == live_id, (t, row)
            elif t == "bubbles":
                assert row["page_id"] in pages, row
            elif t == "series":
                assert row["id"] == sid, row
            elif t in SERIES_CHILDREN:
                assert row["series_id"] == sid, (t, row)
            elif t == "dramas":
                assert row["id"] == live_id, row
            elif t == "audit_log":
                pass
            else:
                pytest.fail(f"unexpected new row in {t}: {row}")


def _set_live_users(accounts):
    """Replaces the live accounts with {id: email}, as if people were removed
    and added since the copy was made."""
    with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
        c.execute("DELETE FROM user_permissions")
        c.execute("DELETE FROM users")
        for uid, email in accounts.items():
            c.execute("INSERT INTO users (id, email) VALUES (?, ?)", (uid, email))
        c.commit()


def _audit_details(action="library.restore_drama"):
    with contextlib.closing(_conn()) as c:
        return [r[0] for r in c.execute(
            "SELECT detail_redacted FROM audit_log WHERE action = ? ORDER BY id", (action,))]


class TestRestoreOwnersByEmail:
    """A copy's account ids mean the accounts of the library when it was
    made: a title's owner is matched to a live account by email, never by
    id, or goes to the PC owner."""

    def _two_users(self):
        from services import auth_service
        a = auth_service.add_user("a@example.com")["id"]
        b = auth_service.add_user("b@example.com")["id"]
        return a, b

    def test_clashing_id_goes_to_same_email(self, isolated_db):
        a, b = self._two_users()
        did = db.create_drama(title_en="A's", owner_user_id=a)
        _snap()
        _delete_drama(did)
        _set_live_users({a: "stranger@example.com", 7: "a@example.com"})
        res = _restore(did)
        assert db.get_drama(did)["owner_user_id"] == 7
        assert res["owners_reassigned"] == 1 and res["copy_has_accounts"] is True

    def test_clashing_id_without_match_goes_to_pc_owner(self, isolated_db):
        a, _b = self._two_users()
        did = db.create_drama(title_en="A's", owner_user_id=a)
        _snap()
        _delete_drama(did)
        _set_live_users({a: "stranger@example.com"})
        res = _restore(did)
        assert db.get_drama(did)["owner_user_id"] is None
        assert res["owners_reassigned"] == 1

    def test_swapped_ids(self, isolated_db):
        a, b = self._two_users()
        da = db.create_drama(title_en="A's", owner_user_id=a)
        dbb = db.create_drama(title_en="B's", owner_user_id=b)
        _snap()
        _delete_drama(da)
        _delete_drama(dbb)
        _set_live_users({a: "b@example.com", b: "a@example.com"})
        _restore(da)
        _restore(dbb)
        assert db.get_drama(da)["owner_user_id"] == b
        assert db.get_drama(dbb)["owner_user_id"] == a

    def test_same_id_and_email_kept(self, isolated_db):
        a, _b = self._two_users()
        did = db.create_drama(title_en="A's", owner_user_id=a)
        _snap()
        _delete_drama(did)
        res = _restore(did)
        assert db.get_drama(did)["owner_user_id"] == a
        assert res["owners_reassigned"] == 0 and res["copy_has_accounts"] is True

    def test_copy_without_accounts_goes_to_pc_owner(self, isolated_db, monkeypatch):
        a, _b = self._two_users()
        did = db.create_drama(title_en="A's", owner_user_id=a)
        _snap()
        _delete_drama(did)
        real = drs.extract_db

        def no_users(zf, dest_dir, *args, **kw):
            path = real(zf, dest_dir, *args, **kw)
            with contextlib.closing(sqlite3.connect(path)) as c:
                c.execute("DROP TABLE user_permissions")
                c.execute("DROP TABLE users")
                c.commit()
            return path

        monkeypatch.setattr(drs, "extract_db", no_users)
        res = _restore(did)
        assert db.get_drama(did)["owner_user_id"] is None
        assert res["copy_has_accounts"] is False and res["owners_reassigned"] == 1

    def test_private_title_stays_private(self, isolated_db):
        a, b = self._two_users()
        did = db.create_drama(title_en="A's", owner_user_id=a, is_private=1)
        _snap()
        _delete_drama(did)
        _set_live_users({a: "b@example.com", b: "a@example.com"})
        _restore(did)
        restored = db.get_drama(did)
        assert restored["owner_user_id"] == b and restored["is_private"] == 1

    def test_recreated_series_owner_by_email(self, isolated_db):
        a, b = self._two_users()
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="A's saga", owner_user_id=a, is_private=0)
            did = _insert(c, "dramas", title_en="D", series_id=sid, owner_user_id=a,
                          status="aligned")
            c.commit()
        _snap()
        _delete_drama(did)
        _delete_series(sid)
        _set_live_users({a: "b@example.com", b: "a@example.com"})
        res = _restore(did)
        assert res["series"] == "recreated"
        new_sid = db.get_drama(did)["series_id"]
        assert _series_rows()[new_sid]["owner_user_id"] == b
        assert db.get_drama(did)["owner_user_id"] == b
        assert res["owners_reassigned"] == 2

    def test_private_series_check_uses_matched_owner(self, isolated_db):
        """The copy's owner id now names someone else; matched by email the
        drama is the live series owner's, so it links back into their
        private series rather than being dropped (or linked for a stranger)."""
        a, b = self._two_users()
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="Saga", owner_user_id=b, is_private=1)
            did = _insert(c, "dramas", title_en="D", series_id=sid, owner_user_id=b,
                          status="aligned")
            c.commit()
        _snap()
        _delete_drama(did)
        with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
            c.execute("UPDATE users SET email = 'tmp@example.com' WHERE id = ?", (a,))
            c.execute("UPDATE users SET email = 'a@example.com' WHERE id = ?", (b,))
            c.execute("UPDATE users SET email = 'b@example.com' WHERE id = ?", (a,))
            c.execute("UPDATE series SET owner_user_id = ? WHERE id = ?", (a,sid))
            c.commit()
        res = _restore(did)
        # copy owner b (b@) is now live id a; the live series is a's private one.
        assert res["series"] == "linked"
        assert db.get_drama(did)["owner_user_id"] == a

    def _private_saga(self, drama_owner):
        """Copy: private series 'Saga' owned by x@ with drama D owned by
        `drama_owner`; live afterwards: x@'s id belongs to someone else and
        the same series (id and name) is Y's private series."""
        from services import auth_service
        x = auth_service.add_user("x@example.com")["id"]
        y = auth_service.add_user("y@example.com")["id"]
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="Saga", owner_user_id=x, is_private=1)
            did = _insert(c, "dramas", title_en="D", series_id=sid,
                          owner_user_id=x if drama_owner == "x" else None, status="aligned")
            c.commit()
        _snap()
        _delete_drama(did)
        _set_live_users({x: "stranger@example.com", y: "y@example.com"})
        with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
            c.execute("UPDATE series SET owner_user_id = ? WHERE id = ?", (y, sid))
            c.commit()
        return did, sid

    def test_unmatched_owner_never_links_into_someone_elses_private_series(self, isolated_db):
        did, _sid = self._private_saga("x")
        before = _series_rows()
        res = _restore(did)
        assert res["series"] == "dropped_private"
        assert db.get_drama(did)["series_id"] is None
        assert db.get_drama(did)["owner_user_id"] is None
        assert _series_rows() == before

    def test_pc_owner_title_still_links_into_private_series(self, isolated_db):
        did, sid = self._private_saga("pc")
        res = _restore(did)
        assert res["series"] == "linked"
        assert db.get_drama(did)["series_id"] == sid

    def test_empty_users_table_counts_as_no_accounts(self, isolated_db, monkeypatch):
        a, _b = self._two_users()
        did = db.create_drama(title_en="A's", owner_user_id=a)
        _snap()
        _delete_drama(did)
        real = drs.extract_db

        def empty_users(zf, dest_dir, *args, **kw):
            path = real(zf, dest_dir, *args, **kw)
            with contextlib.closing(sqlite3.connect(path)) as c:
                c.execute("DELETE FROM user_permissions")
                c.execute("DELETE FROM users")
                c.commit()
            return path

        monkeypatch.setattr(drs, "extract_db", empty_users)
        res = _restore(did)
        assert db.get_drama(did)["owner_user_id"] is None
        assert res["copy_has_accounts"] is False and res["owners_reassigned"] == 1

    def test_audit_records_counts_only(self, isolated_db):
        a, _b = self._two_users()
        did = db.create_drama(title_en="A's", owner_user_id=a)
        _snap()
        _delete_drama(did)
        _set_live_users({a: "stranger@example.com", 7: "a@example.com"})
        _restore(did)
        detail = _audit_details()[-1]
        assert "1 owner" in detail
        assert "@" not in detail


class TestRestoreMedia:
    def _media_drama(self):
        a = db.create_drama(title_en="A")
        d = _ddir(a)
        os.makedirs(os.path.join(d, "clips"), exist_ok=True)
        with open(os.path.join(d, "audio.wav"), "wb") as fh:
            fh.write(b"AUDIO" * 100)
        with open(os.path.join(d, "clips", "c.wav"), "wb") as fh:
            fh.write(b"CLIP")
        return a

    def test_full_snapshot_restores_folder(self, isolated_db):
        a = self._media_drama()
        b = db.create_drama(title_en="B")
        os.makedirs(_ddir(b), exist_ok=True)
        with open(os.path.join(_ddir(b), "b.wav"), "wb") as fh:
            fh.write(b"B")
        _snap(include_media=True)
        _delete_drama(a)
        assert not os.path.exists(_ddir(a))
        with open(os.path.join(_ddir(b), "b.wav"), "wb") as fh:
            fh.write(b"B-changed")
        res = _restore(a)
        assert res["media_restored"] is True and res["snapshot_kind"] == "full"
        assert _read(os.path.join(_ddir(a), "audio.wav")) == b"AUDIO" * 100
        assert _read(os.path.join(_ddir(a), "clips", "c.wav")) == b"CLIP"
        assert _read(os.path.join(_ddir(b), "b.wav")) == b"B-changed"
        assert not [n for n in os.listdir(db.DRAMAS_DIR) if n.startswith(".")]

    def test_full_snapshot_restore_as_new_gets_its_own_folder(self, isolated_db):
        a = self._media_drama()
        _snap(include_media=True)
        with open(os.path.join(_ddir(a), "audio.wav"), "wb") as fh:
            fh.write(b"live")
        res = _restore(a)
        new = res["drama_id"]
        assert res["restored_as_new"] and res["media_restored"]
        assert _read(os.path.join(_ddir(new), "audio.wav")) == b"AUDIO" * 100
        assert _read(os.path.join(_ddir(a), "audio.wav")) == b"live"

    def test_db_only_snapshot_restores_no_media(self, isolated_db):
        a = self._media_drama()
        _snap(include_media=False)
        _delete_drama(a)
        res = _restore(a)
        assert res["media_restored"] is False
        assert not os.path.exists(_ddir(a))

    def test_full_snapshot_drama_without_files(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap(include_media=True)
        _delete_drama(a)
        res = _restore(a)
        assert res["media_restored"] is False and res["drama_id"] == a

    def test_commit_failure_removes_only_the_restored_folder(self, isolated_db, monkeypatch):
        a = self._media_drama()
        b = db.create_drama(title_en="B")
        os.makedirs(_ddir(b))
        with open(os.path.join(_ddir(b), "b.wav"), "wb") as fh:
            fh.write(b"B")
        _snap(include_media=True)
        _delete_drama(a)
        armed, real_move = [], drs.move_media_in

        def move(*args, **kw):
            real_move(*args, **kw)
            assert os.path.isdir(_ddir(a))     # in place before the commit
            armed.append(1)

        def commit(self):
            if armed:
                armed.clear()
                raise sqlite3.OperationalError("disk I/O error")
            return sqlite3.Connection.commit(self)
        monkeypatch.setattr(drs, "move_media_in", move)
        monkeypatch.setattr(db._TrackedConnection, "commit", commit, raising=False)
        with pytest.raises(ServiceError):
            _restore(a)
        monkeypatch.undo()
        assert db.get_drama(a) is None and not os.path.lexists(_ddir(a))
        assert sorted(os.listdir(db.DRAMAS_DIR)) == [str(b)]
        assert _read(os.path.join(_ddir(b), "b.wav")) == b"B"
