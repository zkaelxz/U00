"""
Automatic backup copies belong to one library: services/auto_backup_service
tags every copy with the library's id and a per-library sequence number, so
two libraries sharing a backup folder never rotate out each other's copies,
and the default restore pick is the highest sequence (never a file time or
a wall-clock guess) or, when that can't be told, an explicit choose_copy.

Everything runs against an isolated temp library (isolated_db); backups are
written by calling the job body directly (no thread), with _now faked.
"""

import contextlib
import datetime
import errno
import json
import logging
import os
import zipfile

import pytest

import background_jobs
import db
from services import auto_backup_service as abs_
from services import library_admin_service as las
from services import workspace_job_service as wjs
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

UTC = datetime.timezone.utc
_DROP = object()


def _at(day, hour=3, month=3):
    return datetime.datetime(2026, month, day, hour, 0, 0, tzinfo=UTC)


def _name(when):
    return f"baihe_snapshot-{when:%Y%m%d-%H%M%S}.zip"


def _default_dir():
    return os.path.join(db.LIBRARY_DIR, "backups", "auto")


def _run_at(monkeypatch, when):
    """One real backup run with _now faked to `when`; returns the new copy's
    file name."""
    monkeypatch.setattr(abs_, "_now", lambda: when)
    abs_._backup_job(abs_.JOB_ID, False)
    return _name(when)


def _identity():
    return db.get_app_setting(abs_.IDENTITY_KEY, None)


def _become(identity):
    """Switches this test library to another library's identity (its id and
    sequence), the way a second PC sharing the backup folder would be."""
    db.set_app_setting(abs_.IDENTITY_KEY, identity)


def _manifest(path):
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read("manifest.json"))


def _rewrite_manifest(path, **changes):
    """Rewrites the copy's manifest: each key set to the value, or removed
    for _DROP. Everything else in the zip is kept as it was."""
    with zipfile.ZipFile(path) as zf:
        members = {i.filename: zf.read(i) for i in zf.infolist()}
    manifest = json.loads(members["manifest.json"])
    for key, value in changes.items():
        if value is _DROP:
            manifest.pop(key, None)
        else:
            manifest[key] = value
    members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _make_legacy(path):
    """A copy as written before copies carried a library id."""
    _rewrite_manifest(path, library_id=_DROP, sequence=_DROP)


def _files(folder=None):
    folder = folder or _default_dir()
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _choose(fn):
    with pytest.raises(ConflictError) as e:
        fn()
    assert e.value.details["reason"] == "choose_copy"
    return e.value.details["candidates"]


def _restore(did, snapshot=None):
    return abs_.restore_drama(did, confirm=True, confirm_text="RESTORE", snapshot=snapshot)


# --------------------------------------------------------------------------
# identity and sequence
# --------------------------------------------------------------------------

class TestIdentity:
    def test_made_once_random_and_written_into_every_copy(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        assert _identity() is None
        first = _run_at(monkeypatch, _at(2))
        ident = _identity()
        assert len(ident["library_id"]) == 32 and ident["sequence"] == 1
        assert ident["created_at"] == abs_._iso(_at(2))
        assert ident["written"] == [[1, first]]
        second = _run_at(monkeypatch, _at(3))
        assert _identity() == {"library_id": ident["library_id"], "sequence": 2,
                               "created_at": ident["created_at"],
                               "written": [[1, first], [2, second]]}
        for name, seq in ((first, 1), (second, 2)):
            m = _manifest(os.path.join(_default_dir(), name))
            assert m["library_id"] == ident["library_id"] and m["sequence"] == seq

    def test_two_libraries_get_different_ids(self, isolated_db):
        a = abs_._identity()[0]
        db.set_app_setting(abs_.IDENTITY_KEY, None)
        assert abs_._identity()[0] != a

    @pytest.mark.parametrize("stored", ["junk", {"library_id": "../x", "sequence": 3},
                                        {"library_id": 5}, [1, 2]])
    def test_invalid_stored_identity_is_replaced(self, isolated_db, stored):
        db.set_app_setting(abs_.IDENTITY_KEY, stored)
        library_id, seq = abs_._identity()
        assert abs_._LIBRARY_ID_RE.fullmatch(library_id) and seq == 0
        assert _identity()["library_id"] == library_id

    def test_sequence_never_reuses_a_number_in_the_folder(self, isolated_db, monkeypatch):
        """A library.db restored by hand rolls the stored counter back; the
        next copy still numbers above every copy of this library there."""
        db.create_drama(title_en="A")
        for day in (2, 3, 4):
            _run_at(monkeypatch, _at(day))
        ident = _identity()
        _become({"library_id": ident["library_id"], "sequence": 0})
        new = _run_at(monkeypatch, _at(5))
        assert _manifest(os.path.join(_default_dir(), new))["sequence"] == 4
        assert _identity()["sequence"] == 4


# --------------------------------------------------------------------------
# a shared folder: two libraries never delete each other's copies
# --------------------------------------------------------------------------

class TestSharedFolder:
    def test_many_rotation_runs_never_touch_the_other_librarys_copies(
            self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        shared = str(tmp_path / "synced")
        os.makedirs(shared)
        abs_.set_settings(folder=shared)
        libs = {"A": {"library_id": "a" * 32, "sequence": 0},
                "B": {"library_id": "b" * 32, "sequence": 0}}
        written = {"A": set(), "B": set()}
        start = datetime.datetime(2026, 3, 1, tzinfo=UTC)
        for n in range(60):                       # 30 days, each library twice a day
            who = "A" if n % 2 == 0 else "B"
            other = "B" if who == "A" else "A"
            when = start + datetime.timedelta(hours=12 * n + (0 if who == "A" else 5))
            others_before = {f: _read(os.path.join(shared, f))
                             for f in _files(shared) if f in written[other]}
            _become(libs[who])
            written[who].add(_run_at(monkeypatch, when))
            libs[who] = _identity()
            # the other library's copies are all still there, byte for byte
            assert {f: _read(os.path.join(shared, f)) for f in others_before} == others_before
        present = set(_files(shared))
        for who in ("A", "B"):
            mine = present & written[who]
            assert 1 <= len(mine) <= 4, (who, sorted(mine))
        assert present == (present & written["A"]) | (present & written["B"])
        # as library B sees it: A's copies are listed, unmanaged, slotless
        _become(libs["B"])
        info = abs_.snapshot_info()
        for c in info["copies"]:
            assert c["managed"] is (c["name"] in written["B"]), c
            if c["name"] in written["A"]:
                assert c["kept_as"] is None
        assert info["default_copy"] is None or info["default_copy"] in written["B"]

    def test_delete_all_deletes_only_this_librarys_copies(self, isolated_db, tmp_path,
                                                          monkeypatch):
        db.create_drama(title_en="A")
        shared = str(tmp_path)
        abs_.set_settings(folder=shared)
        _become({"library_id": "b" * 32, "sequence": 0})
        theirs = _run_at(monkeypatch, _at(2))
        _become({"library_id": "a" * 32, "sequence": 0})
        mine = [_run_at(monkeypatch, _at(3)), _run_at(monkeypatch, _at(4))]
        legacy = _run_at(monkeypatch, _at(5, hour=1))
        _make_legacy(os.path.join(shared, legacy))    # id-less, in a custom folder
        before = _files(shared)
        # naming an unmanaged copy needs include_unmanaged
        for name in (theirs, legacy):
            with pytest.raises(ConflictError) as e:
                abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=name)
            assert e.value.details == {"reason": "unmanaged"}
        assert _files(shared) == before
        assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True) == \
            {"deleted": True, "count": 2, "kept_unmanaged": 2}
        assert _files(shared) == sorted([theirs, legacy])
        # none of ours left: a plain delete-all has nothing to delete
        with pytest.raises(NotFoundError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        assert _files(shared) == sorted([theirs, legacy])
        # an explicit choice deletes them
        assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=theirs,
                                    include_unmanaged=True)["count"] == 1
        assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True,
                                    include_unmanaged=True)["count"] == 1
        assert _files(shared) == []
        assert mine  # written, then deleted above

    def test_folder_move_takes_only_managed_copies(self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        src, dest = tmp_path / "src", tmp_path / "dest"
        src.mkdir()
        dest.mkdir()
        abs_.set_settings(folder=str(src))
        _become({"library_id": "b" * 32, "sequence": 0})
        theirs = _run_at(monkeypatch, _at(2))
        _become({"library_id": "a" * 32, "sequence": 0})
        mine = _run_at(monkeypatch, _at(3))
        abs_.set_settings(folder=str(dest))
        assert _files(str(src)) == [theirs] and _files(str(dest)) == [mine]


# --------------------------------------------------------------------------
# legacy, unowned and ambiguous copies are never rotated out
# --------------------------------------------------------------------------

class TestUnmanagedNeverRotated:
    def test_legacy_copies_in_a_custom_folder_stay(self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        abs_.set_settings(folder=str(tmp_path))
        legacy = [_run_at(monkeypatch, _at(d, month=1)) for d in (5, 12, 19)]
        for name in legacy:
            _make_legacy(str(tmp_path / name))
        for day in range(1, 29):
            _run_at(monkeypatch, _at(day))
        assert set(legacy) <= set(_files(str(tmp_path)))
        info = abs_.snapshot_info()
        for c in info["copies"]:
            if c["name"] in legacy:
                assert c["managed"] is False and c["kept_as"] is None
        assert sum(c["managed"] for c in info["copies"]) == 4

    def test_legacy_copies_in_the_default_folder_are_adopted(self, isolated_db, monkeypatch):
        """Only this library writes inside it, so an id-less copy there is
        this library's from before the update: it ages out as before."""
        db.create_drama(title_en="A")
        legacy = [_run_at(monkeypatch, _at(d)) for d in (2, 3)]
        for name in legacy:
            _make_legacy(os.path.join(_default_dir(), name))
        # as if this library's id was made after the older app wrote them
        _become({**_identity(), "created_at": abs_._iso(_at(3, hour=4))})
        assert all(c["managed"] for c in abs_.snapshot_info()["copies"])
        for day in (9, 16, 23, 24):
            _run_at(monkeypatch, _at(day))
        assert not set(legacy) & set(_files())

    def test_default_folder_linked_out_of_the_library_adopts_nothing(
            self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        outside = tmp_path / "shared"
        outside.mkdir()
        os.makedirs(os.path.join(db.LIBRARY_DIR, "backups"))
        os.symlink(outside, _default_dir())
        old = _run_at(monkeypatch, _at(2))
        _make_legacy(str(outside / old))
        for day in (9, 16, 23, 24, 30):
            _run_at(monkeypatch, _at(day))
        assert old in _files(str(outside))

    @pytest.mark.parametrize("change", [
        {"library_id": "c" * 32},                   # another library
        {"sequence": _DROP},                        # this id, no sequence
        {"sequence": "7"}, {"sequence": 0}, {"sequence": True},
        {"library_id": 42, "sequence": _DROP},      # an id that isn't one
    ])
    def test_copies_that_prove_no_owner_stay(self, isolated_db, monkeypatch, change):
        db.create_drama(title_en="A")
        odd = _run_at(monkeypatch, _at(2))
        _rewrite_manifest(os.path.join(_default_dir(), odd), **change)
        data = _read(os.path.join(_default_dir(), odd))
        for day in (9, 16, 23, 24, 30):
            _run_at(monkeypatch, _at(day))
        assert _read(os.path.join(_default_dir(), odd)) == data
        [entry] = [c for c in abs_.snapshot_info()["copies"] if c["name"] == odd]
        assert entry["managed"] is False and entry["kept_as"] is None

    def test_copy_that_cannot_be_opened_is_never_deleted(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        old = os.path.join(_default_dir(), _run_at(monkeypatch, _at(2)))
        real = abs_.zipfile.ZipFile

        def locked(file, *a, **k):
            if isinstance(file, str) and os.path.abspath(file) == old:
                raise PermissionError(13, "in use")
            return real(file, *a, **k)
        monkeypatch.setattr(abs_.zipfile, "ZipFile", locked)
        for day in (9, 16, 23, 30):
            _run_at(monkeypatch, _at(day))
        assert os.path.isfile(old)
        # it might be the newest, so nothing is picked silently
        cands = _choose(abs_.list_snapshot_dramas)
        assert os.path.basename(old) not in [c["name"] for c in cands]


# --------------------------------------------------------------------------
# the default pick: the highest sequence, else choose_copy
# --------------------------------------------------------------------------

class TestDefaultPick:
    def test_file_times_are_ignored(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        names = [_run_at(monkeypatch, _at(d)) for d in (2, 3, 4)]
        monkeypatch.setattr(abs_, "_now", lambda: _at(4, hour=6))
        for name, ts in zip(names, (4e9, 1e9, 2e9)):     # newest copy gets the oldest mtime
            os.utime(os.path.join(_default_dir(), name), (ts, ts))
        assert abs_.list_snapshot_dramas()["name"] == names[-1]
        assert abs_.snapshot_info()["default_copy"] == names[-1]

    def test_clock_set_back_uses_the_sequence_not_created_at(self, isolated_db, monkeypatch):
        """The clock went back a few hours between two runs: the later run
        has the earlier name and created_at, and is still the default."""
        a = db.create_drama(title_en="Before")
        first = _run_at(monkeypatch, _at(10, hour=12))
        db.update_drama(a, title_en="After")
        second = _run_at(monkeypatch, _at(10, hour=9))
        assert second < first                       # listed after the older copy
        info = abs_.snapshot_info()
        assert info["copies"][0]["name"] == first
        assert info["default_copy"] == second and info["choose_copy"] is False
        assert info["created_at"] == abs_._iso(_at(10, hour=9))
        db.delete_drama(a)
        res = _restore(a)
        assert res["snapshot"] == second and db.get_drama(a)["title_en"] == "After"
        # the copy dated after the new one takes no slot and stays
        assert first in _files()

    def test_equal_sequence_asks(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        one = _run_at(monkeypatch, _at(2))
        two = _run_at(monkeypatch, _at(3))
        _rewrite_manifest(os.path.join(_default_dir(), one), sequence=2)
        cands = _choose(abs_.list_snapshot_dramas)
        assert {c["name"]: c["sequence"] for c in cands} == {one: 2, two: 2}
        assert abs_.snapshot_info()["choose_copy"] is True

    def test_sequence_and_dates_far_apart_asks(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        low = _run_at(monkeypatch, _at(20))
        high = _run_at(monkeypatch, _at(2))            # clock set back 18 days
        cands = _choose(abs_.list_snapshot_dramas)
        assert [c["name"] for c in cands] == [low, high]
        assert all(set(c) == {"name", "created_at", "sequence", "size", "managed"}
                   for c in cands)
        assert db.LIBRARY_DIR not in json.dumps(cands)

    def test_newer_legacy_copy_asks(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        mine = _run_at(monkeypatch, _at(2))
        newer = _run_at(monkeypatch, _at(3))
        _make_legacy(os.path.join(_default_dir(), newer))  # a copy written by an older app
        cands = _choose(abs_.list_snapshot_dramas)
        # dated after this library's id was made, so not adopted either
        assert {c["name"]: c["managed"] for c in cands} == {mine: True, newer: False}

    def test_older_legacy_copy_does_not_ask(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        old = _run_at(monkeypatch, _at(2))
        _make_legacy(os.path.join(_default_dir(), old))
        mine = _run_at(monkeypatch, _at(3))
        assert abs_.list_snapshot_dramas()["name"] == mine

    def test_another_librarys_newer_copy_asks_an_older_one_does_not(
            self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        abs_.set_settings(folder=str(tmp_path))
        _become({"library_id": "b" * 32, "sequence": 0})
        theirs_old = _run_at(monkeypatch, _at(2))
        b = _identity()
        _become({"library_id": "a" * 32, "sequence": 0})
        mine = _run_at(monkeypatch, _at(3))
        assert abs_.list_snapshot_dramas()["name"] == mine
        a = _identity()
        _become(b)
        theirs_new = _run_at(monkeypatch, _at(4))
        _become(a)
        cands = _choose(abs_.list_snapshot_dramas)
        assert {c["name"]: c["managed"] for c in cands} == {
            theirs_new: False, mine: True, theirs_old: False}

    def test_only_other_copies_asks_and_a_named_one_restores(self, isolated_db, tmp_path,
                                                             monkeypatch):
        a = db.create_drama(title_en="Theirs")
        abs_.set_settings(folder=str(tmp_path))
        _become({"library_id": "b" * 32, "sequence": 0})
        theirs = _run_at(monkeypatch, _at(2))
        _become({"library_id": "a" * 32, "sequence": 0})
        db.delete_drama(a)
        _choose(lambda: _restore(a))
        assert db.get_drama(a) is None
        info = abs_.snapshot_info()
        assert info["choose_copy"] is True and info["copies"][0]["managed"] is False
        res = _restore(a, snapshot=theirs)
        assert res["snapshot"] == theirs and db.get_drama(a)["title_en"] == "Theirs"
        # restoring a drama from another library's copy leaves the id alone
        assert (_identity()["library_id"], _identity()["sequence"]) == ("a" * 32, 0)


# --------------------------------------------------------------------------
# a whole-library restore keeps this library's identity
# --------------------------------------------------------------------------

def _library_zip(tmp_path):
    path = str(tmp_path / "whole.zip")
    las.write_backup_zip(path, include_media=False)
    return _read(path)


class TestWholeLibraryRestore:
    def test_identity_and_sequence_survive(self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        _become({"library_id": "b" * 32, "sequence": 2})
        other_librarys_backup = _library_zip(tmp_path)
        _become({"library_id": "a" * 32, "sequence": 9})
        wjs.restore_library_backup(other_librarys_backup, db.LIBRARY_DIR)
        assert _identity() == {"library_id": "a" * 32, "sequence": 9}
        new = _run_at(monkeypatch, _at(2))
        m = _manifest(os.path.join(_default_dir(), new))
        assert (m["library_id"], m["sequence"]) == ("a" * 32, 10)

    def test_no_identity_yet_takes_none_from_the_backup(self, isolated_db, tmp_path):
        db.create_drama(title_en="A")
        _become({"library_id": "b" * 32, "sequence": 2})
        other_librarys_backup = _library_zip(tmp_path)
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (abs_.IDENTITY_KEY,))
            conn.commit()
        wjs.restore_library_backup(other_librarys_backup, db.LIBRARY_DIR)
        assert _identity() is None
        assert abs_._identity()[0] != "b" * 32

    def test_kept_key_is_the_identity_key(self):
        assert abs_.IDENTITY_KEY in wjs._RESTORE_KEPT_APP_SETTINGS


# --------------------------------------------------------------------------
# clones: a library folder copied by hand to a second PC carries the same id
# --------------------------------------------------------------------------

def _present(folder, names):
    return {n: _read(os.path.join(folder, n)) for n in names
            if os.path.isfile(os.path.join(folder, n))}


class TestClones:
    def test_two_clones_sharing_one_folder_never_delete_each_others_copies(
            self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        shared = str(tmp_path / "synced")
        os.makedirs(shared)
        abs_.set_settings(folder=shared)
        for day in (1, 2, 3):                   # before the library was copied
            _run_at(monkeypatch, _at(day))
        split = _identity()
        clones = {"A": dict(split), "B": dict(split)}
        written = {"A": set(), "B": set()}
        when = _at(4)
        # uneven: back-to-back runs on one PC, then the other
        for who in "AABABBBAAAABBABAAB" * 2:
            other = "B" if who == "A" else "A"
            theirs = _present(shared, written[other])
            _become(clones[who])
            written[who].add(_run_at(monkeypatch, when))
            clones[who] = _identity()
            assert _present(shared, theirs) == theirs, who
            when += datetime.timedelta(hours=9)
        # each still rotates its own copies when it runs twice in a row
        for who in ("A", "B"):
            assert len(_present(shared, written[who])) < len(written[who])
        # B wrote last: to A, B's newer copy (numbered above A's counter)
        # makes the newest copy unclear, and it isn't A's to delete
        _become(clones["A"])
        info = abs_.snapshot_info()
        assert info["choose_copy"] is True
        managed = {c["name"] for c in info["copies"] if c["managed"]}
        assert not managed & written["B"]
        b_left = _present(shared, written["B"])
        abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        assert _present(shared, written["B"]) == b_left and b_left

    def test_hand_restored_library_db_prunes_nothing_once(self, isolated_db, monkeypatch,
                                                          caplog):
        db.create_drama(title_en="A")
        early = [_run_at(monkeypatch, _at(d)) for d in (2, 3)]
        restored = _identity()                  # library.db as it was then
        later = [_run_at(monkeypatch, _at(d)) for d in (9, 16, 17)]
        _become(restored)
        # copies numbered above the counter: nothing is picked silently
        cands = _choose(abs_.list_snapshot_dramas)
        assert set(later) <= {c["name"] for c in cands}
        before = _present(_default_dir(), early + later)
        caplog.set_level(logging.WARNING, logger=abs_.__name__)
        new = _run_at(monkeypatch, _at(18))
        assert _present(_default_dir(), early + later) == before
        [warning] = [r.getMessage() for r in caplog.records if "counter" in r.getMessage()]
        assert db.LIBRARY_DIR not in warning
        assert _manifest(os.path.join(_default_dir(), new))["sequence"] == 6
        # the counter is above every copy now: the next run rotates again,
        # among the copies this library recorded writing; the ones written
        # after the restored state stay (unmanaged, deletable by name)
        _run_at(monkeypatch, _at(23))
        files = set(_files())
        assert early[1] not in files and early[0] in files
        assert set(later) <= files
        info = abs_.snapshot_info()
        assert not any(c["managed"] for c in info["copies"] if c["name"] in later)
        assert info["choose_copy"] is False

    def test_same_number_under_another_name_is_not_this_librarys(self, isolated_db,
                                                                 monkeypatch):
        """A clone that wrote the same number (a synced folder that caught
        up late) owns its copy: only the name this library recorded counts."""
        db.create_drama(title_en="A")
        mine = _run_at(monkeypatch, _at(2))
        twin = _name(_at(2, hour=4))
        with open(os.path.join(_default_dir(), mine), "rb") as src, \
                open(os.path.join(_default_dir(), twin), "wb") as dst:
            dst.write(src.read())
        entries = {c["name"]: c for c in abs_.snapshot_info()["copies"]}
        assert entries[mine]["managed"] is True and entries[twin]["managed"] is False
        for day in (9, 16, 23, 24, 30):
            _run_at(monkeypatch, _at(day))
        assert twin in _files()


# --------------------------------------------------------------------------
# legacy adoption needs a date before this library's id; other libraries'
# folders are refused
# --------------------------------------------------------------------------

class TestLegacyAdoptionAndForeignFolders:
    @pytest.mark.parametrize("created_at", [_DROP, "yesterday", 5])
    def test_untagged_copy_without_a_date_is_not_adopted(self, isolated_db, monkeypatch,
                                                         created_at):
        db.create_drama(title_en="A")
        old = _run_at(monkeypatch, _at(2))
        _rewrite_manifest(os.path.join(_default_dir(), old), library_id=_DROP,
                          sequence=_DROP, created_at=created_at)
        _become({**_identity(), "created_at": abs_._iso(_at(9))})
        for day in (9, 16, 23, 24, 30):
            _run_at(monkeypatch, _at(day))
        assert old in _files()

    def test_identity_records_when_it_was_made(self, isolated_db, monkeypatch):
        monkeypatch.setattr(abs_, "_now", lambda: _at(7))
        abs_._identity()
        assert _identity()["created_at"] == abs_._iso(_at(7))
        # an identity stored without it gets one, keeping id and number
        _become({"library_id": "a" * 32, "sequence": 3})
        monkeypatch.setattr(abs_, "_now", lambda: _at(8))
        assert abs_._identity() == ("a" * 32, 3)
        assert _identity()["created_at"] == abs_._iso(_at(8))

    @pytest.mark.parametrize("sub", [(), ("backups", "auto"), ("somewhere", "deeper")])
    def test_folder_inside_another_library_is_refused(self, isolated_db, tmp_path, sub):
        other = tmp_path / "other-library"
        other.mkdir()
        (other / "library.db").write_bytes(b"")
        folder = other.joinpath(*sub)
        folder.mkdir(parents=True, exist_ok=True)
        with pytest.raises(InvalidInputError):
            abs_.set_settings(folder=str(folder))
        assert abs_.get_settings()["folder"] == ""

    def test_own_backups_folder_is_still_allowed(self, isolated_db):
        inside = os.path.join(db.LIBRARY_DIR, "backups", "mine")
        os.makedirs(inside)
        assert abs_.set_settings(folder=inside)["folder"] == inside


# --------------------------------------------------------------------------
# a foreign or broken file is listed, never a 500, and deletable by name
# --------------------------------------------------------------------------

def _unsupported_compression(path):
    """Marks every member as compressed with a method zipfile can't read."""
    data = bytearray(_read(path))
    for sig, off in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        pos = data.find(sig)
        while pos != -1:
            data[pos + off:pos + off + 2] = (99).to_bytes(2, "little")
            pos = data.find(sig, pos + 4)
    with open(path, "wb") as fh:
        fh.write(data)


class TestForeignBadFiles:
    def test_read_copy_calls_unknown_compression_damaged(self, isolated_db, tmp_path):
        path = tmp_path / "x.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", b"{}")
        _unsupported_compression(str(path))
        with zipfile.ZipFile(path) as zf, pytest.raises(NotImplementedError):
            zf.read("manifest.json")
        assert abs_._read_copy(str(path)) == (abs_._DAMAGED, None)

    @pytest.mark.parametrize("created_at", ["0001-01-01T00:00:00+14:00",
                                            "9999-12-31T23:59:59-14:00",
                                            "9999-12-31T23:59:59+00:00"])
    def test_odd_dates_never_raise(self, isolated_db, monkeypatch, tmp_path, created_at):
        db.create_drama(title_en="A")
        mine = _run_at(monkeypatch, _at(2))
        top = _run_at(monkeypatch, _at(3))
        _rewrite_manifest(os.path.join(_default_dir(), top), created_at=created_at)
        info = abs_.snapshot_info()
        assert {c["name"] for c in info["copies"]} == {mine, top}
        abs_.list_snapshot_dramas(mine)
        abs_.list_snapshot_dramas(top)
        with contextlib.suppress(ConflictError):
            abs_.list_snapshot_dramas()
        _run_at(monkeypatch, _at(4))
        dest = tmp_path / "moved"
        dest.mkdir()
        abs_.set_settings(folder=str(dest))
        assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=top,
                                    include_unmanaged=True)["count"] == 1

    def test_listing_moving_and_deleting_around_foreign_bad_files(self, isolated_db,
                                                                  tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        src, dest = tmp_path / "src", tmp_path / "dest"
        src.mkdir()
        dest.mkdir()
        abs_.set_settings(folder=str(src))
        mine = _run_at(monkeypatch, _at(2))
        bad_codec = _name(_at(3))
        with open(src / mine, "rb") as a, open(src / bad_codec, "wb") as b:
            b.write(a.read())
        _unsupported_compression(str(src / bad_codec))
        bad_date = "baihe_snapshot-20261399-250000.zip"     # not a real date
        (src / bad_date).write_bytes(_read(str(src / mine)))
        info = abs_.snapshot_info()
        by_name = {c["name"]: c for c in info["copies"]}
        assert set(by_name) == {mine, bad_codec, bad_date}
        assert by_name[bad_codec]["readable"] is False
        assert by_name[bad_codec]["managed"] is False
        assert by_name[bad_date]["managed"] is False
        assert info["choose_copy"] is True     # a damaged copy named after the pick
        abs_.set_settings(folder=str(dest))
        assert _files(str(dest)) == [mine]
        assert sorted(_files(str(src))) == sorted([bad_codec, bad_date])
        abs_.set_settings(folder=str(src))
        for name in (bad_codec, bad_date):
            with pytest.raises(ConflictError):
                abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=name)
            assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=name,
                                        include_unmanaged=True)["count"] == 1
        assert _files(str(src)) == [mine]


# --------------------------------------------------------------------------
# a folder move or delete waits for a restore, bulk delete or cleanup
# --------------------------------------------------------------------------

@contextlib.contextmanager
def _held(kind):
    if kind == "restore":
        assert background_jobs.acquire_exclusive("Library restore")
        try:
            yield
        finally:
            background_jobs.release_exclusive()
    else:
        assert background_jobs.enter_maintenance()
        try:
            yield
        finally:
            background_jobs.exit_maintenance()


class TestRefusedWhileLibraryHeld:
    @pytest.mark.parametrize("kind", ["restore", "maintenance"])
    def test_folder_move_and_delete_refused(self, isolated_db, tmp_path, monkeypatch, kind):
        db.create_drama(title_en="A")
        name = _run_at(monkeypatch, _at(2))
        dest = tmp_path / "dest"
        dest.mkdir()
        with _held(kind):
            with pytest.raises(ConflictError):
                abs_.set_settings(folder=str(dest))
            with pytest.raises(ConflictError):
                abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=name)
            with pytest.raises(ConflictError):
                abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
            # other settings still save
            assert abs_.set_settings(enabled=True)["enabled"] is True
        assert _files() == [name] and _files(str(dest)) == []
        assert abs_.get_settings()["folder"] == ""
        assert not background_jobs.maintenance_active()
        abs_.set_settings(folder=str(dest))
        assert _files(str(dest)) == [name]
        assert not background_jobs.maintenance_active()


# --------------------------------------------------------------------------
# the new copy never replaces a file
# --------------------------------------------------------------------------

class TestNewCopyNeverOverwrites:
    def test_file_appearing_under_the_name_is_kept(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        os.makedirs(_default_dir())
        taken = os.path.join(_default_dir(), _name(_at(2)))
        real_link = os.link

        def racing_link(src, dest, *a, **k):
            if dest == taken and not os.path.exists(taken):
                with open(taken, "wb") as fh:      # another PC wrote it just now
                    fh.write(b"theirs")
            return real_link(src, dest, *a, **k)
        monkeypatch.setattr(abs_.os, "link", racing_link)
        _run_at(monkeypatch, _at(2))
        assert _read(taken) == b"theirs"
        nxt = _name(_at(2) + datetime.timedelta(seconds=1))
        assert _files() == sorted([_name(_at(2)), nxt])
        assert _manifest(os.path.join(_default_dir(), nxt))["sequence"] == 1

    @pytest.mark.parametrize("err", [OSError(errno.EPERM, "no hard links"),
                                     AttributeError("link")])
    def test_file_system_without_hard_links(self, isolated_db, monkeypatch, err):
        db.create_drama(title_en="A")

        def no_link(*a, **k):
            raise err
        monkeypatch.setattr(abs_.os, "link", no_link)
        first = _run_at(monkeypatch, _at(2))
        second = _run_at(monkeypatch, _at(3))
        assert _files() == [first, second]      # no partial file left behind
        assert _manifest(os.path.join(_default_dir(), second))["sequence"] == 2
        # and a taken name is still never replaced
        taken = os.path.join(_default_dir(), _name(_at(4)))
        with open(taken, "wb") as fh:
            fh.write(b"theirs")
        _run_at(monkeypatch, _at(4))
        assert _read(taken) == b"theirs"
        assert _name(_at(4) + datetime.timedelta(seconds=1)) in _files()
