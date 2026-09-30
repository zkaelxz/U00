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
import json
import os
import zipfile

import pytest

import db
from services import auto_backup_service as abs_
from services import library_admin_service as las
from services import workspace_job_service as wjs
from services.service_errors import ConflictError, NotFoundError

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
        second = _run_at(monkeypatch, _at(3))
        assert _identity() == {"library_id": ident["library_id"], "sequence": 2}
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
        assert {c["name"]: c["managed"] for c in cands} == {mine: True, newer: True}

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
        _become({"library_id": "a" * 32, "sequence": 0})
        mine = _run_at(monkeypatch, _at(3))
        assert abs_.list_snapshot_dramas()["name"] == mine
        _become({"library_id": "b" * 32, "sequence": 1})
        theirs_new = _run_at(monkeypatch, _at(4))
        _become({"library_id": "a" * 32, "sequence": 1})
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
        assert _identity() == {"library_id": "a" * 32, "sequence": 0}


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
