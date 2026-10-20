"""Single-drama restore: refusing unsafe snapshots, series resolution and series id reuse."""

import contextlib
import datetime
import os
import zipfile

import pytest

import db
from services import auto_backup_copies as abc_
from services import auto_backup_files as abf_
from services import auto_backup_settings as abt_
from services import drama_restore_service as drs
from services import library_backup_restore_service as lbr
from services import library_restore_service as lrs
from services.service_errors import InvalidInputError
from tests.auto_backup_helpers import (
    _conn, _ddir, _default_path, _delete_drama, _delete_series, _dump_all, _insert, _read,
    _restore, _seed_world, _series_rows, _snap,
)


def _files_under(path):
    out = set()
    for root, dirs, files in os.walk(path):
        for d in dirs:
            out.add(os.path.relpath(os.path.join(root, d), path) + "/")
        for f in files:
            out.add(os.path.relpath(os.path.join(root, f), path))
    return out


class TestRestoreRejectsUnsafeSnapshot:
    def _craft(self, extra):
        """A real full snapshot of drama A, then rewritten at the snapshot
        path with `extra` members added: list of (ZipInfo|name, bytes)."""
        a = db.create_drama(title_en="A")
        os.makedirs(_ddir(a), exist_ok=True)
        with open(os.path.join(_ddir(a), "ok.wav"), "wb") as fh:
            fh.write(b"ok")
        path = _snap(include_media=True)
        with zipfile.ZipFile(path) as zf:
            members = [(i, zf.read(i)) for i in zf.infolist()]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for info, data in members:
                zf.writestr(info, data)
            for info, data in extra(a):
                zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        _delete_drama(a)
        return a

    def _assert_refused(self, a):
        before = _dump_all()
        listing = _files_under(db.LIBRARY_DIR)
        with pytest.raises(InvalidInputError) as e:
            _restore(a)
        assert db.LIBRARY_DIR not in str(e.value)
        assert _dump_all() == before
        assert _files_under(db.LIBRARY_DIR) == listing
        assert db.get_drama(a) is None

    @pytest.mark.parametrize("name", [
        "dramas/{a}/../../../evil.txt", "../evil.txt", "/etc/evil.txt", "C:/evil.txt",
        "dramas/{a}/./x.txt", "dramas/{a}/x.txt.",
    ])
    def test_bad_member_names(self, isolated_db, name):
        a = self._craft(lambda a: [(zipfile.ZipInfo(name.format(a=a)), b"evil")])
        self._assert_refused(a)

    def test_symlink_entry(self, isolated_db):
        def extra(a):
            info = zipfile.ZipInfo(f"dramas/{a}/link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            return [(info, b"/etc/passwd")]
        self._assert_refused(self._craft(extra))

    def test_too_many_drama_members(self, isolated_db, monkeypatch):
        monkeypatch.setattr(lbr, "RESTORE_MAX_MEMBERS", 6)
        a = self._craft(lambda a: [(f"dramas/{a}/f{i}.txt", b"x") for i in range(10)])
        self._assert_refused(a)

    def test_oversized_drama_member(self, isolated_db, monkeypatch):
        a = self._craft(lambda a: [(f"dramas/{a}/big.bin", b"\0" * 3_000_000)])
        with zipfile.ZipFile(_default_path()) as zf:
            db_size = zf.getinfo("library.db").file_size
        assert db_size < 2_000_000
        monkeypatch.setattr(drs, "_MAX_MEMBER_BYTES", 2_000_000)
        self._assert_refused(a)

    def test_oversized_library_db(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        path = _snap()
        _delete_drama(a)
        with zipfile.ZipFile(path) as zf:
            db_size = zf.getinfo("library.db").file_size
        monkeypatch.setattr(abf_, "_MAX_MEMBER_BYTES", db_size - 1)
        self._assert_refused(a)

    def test_upload_caps_still_reject_bomb_and_member_count(self, isolated_db, monkeypatch):
        """The whole-zip caps (expanded total, member count, member size)
        still apply to validate_backup_file's default (an upload); only
        the app's own snapshot skips them (check_limits=False)."""
        self._craft(lambda a: [(f"dramas/{a}/bomb.bin", b"\0" * 20_000_000)] +
                    [(f"dramas/{a}/f{i}", b"x") for i in range(10)])
        path = _default_path()
        assert os.path.getsize(path) < 2_000_000   # compresses hard
        lbr.validate_backup_file(path, check_disk=False)   # within the real caps
        lbr.validate_backup_file(path, check_disk=False, check_limits=False)
        with monkeypatch.context() as m:   # expanded total
            m.setattr(lbr, "_RESTORE_MIN_TOTAL_BYTES", 0)
            m.setattr(lbr, "_RESTORE_EXPANSION_FACTOR", 2)
            m.setattr(lbr, "_library_size", lambda: 0)
            with pytest.raises(InvalidInputError):
                lbr.validate_backup_file(path, check_disk=False)
            lbr.validate_backup_file(path, check_disk=False, check_limits=False)
            with open(path, "rb") as fh:
                with pytest.raises(InvalidInputError):
                    lbr.validate_backup_zip(fh.read())
        with monkeypatch.context() as m:   # member count
            m.setattr(lbr, "RESTORE_MAX_MEMBERS", 5)
            with pytest.raises(InvalidInputError):
                lbr.validate_backup_file(path, check_disk=False)
            lbr.validate_backup_file(path, check_disk=False, check_limits=False)
        with monkeypatch.context() as m:   # one member too large
            m.setattr(lrs, "MAX_RESTORE_MEMBER_BYTES", 10_000_000)
            with pytest.raises(InvalidInputError):
                lbr.validate_backup_file(path, check_disk=False)
            lbr.validate_backup_file(path, check_disk=False, check_limits=False)

    @pytest.mark.parametrize("name", ["../evil", "/abs", "C:/x"])
    def test_structural_checks_apply_without_limits(self, isolated_db, name):
        self._craft(lambda a: [(zipfile.ZipInfo(name), b"x")])
        with pytest.raises(InvalidInputError):
            lbr.validate_backup_file(_default_path(), check_disk=False, check_limits=False)

    def test_snapshot_larger_than_upload_caps_is_still_written(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        os.makedirs(_ddir(a), exist_ok=True)
        for i in range(8):
            with open(os.path.join(_ddir(a), f"f{i}.bin"), "wb") as fh:
                fh.write(b"\0" * 50_000)
        monkeypatch.setattr(lbr, "RESTORE_MAX_MEMBERS", 3)
        monkeypatch.setattr(lrs, "MAX_RESTORE_MEMBER_BYTES", 10_000)
        monkeypatch.setattr(lbr, "_RESTORE_MIN_TOTAL_BYTES", 0)
        monkeypatch.setattr(lbr, "_RESTORE_EXPANSION_FACTOR", 1)
        monkeypatch.setattr(lbr, "_library_size", lambda: 0)
        _snap(include_media=True)
        assert abt_._get_state()["last_error"] is None
        assert abc_.snapshot_info()["kind"] == "full"
        _delete_drama(a)
        monkeypatch.setattr(lbr, "RESTORE_MAX_MEMBERS", 100)
        res = _restore(a)
        assert res["media_restored"] and len(os.listdir(_ddir(a))) == 8

    def test_encrypted_flag(self, isolated_db):
        """zipfile clears flag_bits on write, so the encrypted bit is set in
        the central directory by hand afterwards."""
        import struct
        a = self._craft(lambda a: [(f"dramas/{a}/enc.bin", b"x")])
        target = f"dramas/{a}/enc.bin".encode()
        data = bytearray(_read(_default_path()))
        pos, hit = 0, False
        while True:
            pos = data.find(b"PK\x01\x02", pos)
            if pos < 0:
                break
            name_len = struct.unpack_from("<H", data, pos + 28)[0]
            if bytes(data[pos + 46:pos + 46 + name_len]) == target:
                flags = struct.unpack_from("<H", data, pos + 8)[0]
                struct.pack_into("<H", data, pos + 8, flags | 0x1)
                hit = True
            pos += 4
        assert hit
        with open(_default_path(), "wb") as fh:
            fh.write(data)
        with zipfile.ZipFile(_default_path()) as zf:
            assert zf.getinfo(target.decode()).flag_bits & 0x1
        self._assert_refused(a)

    def test_corrupt_library_db(self, isolated_db):
        a = db.create_drama(title_en="A")
        path = _snap()
        with zipfile.ZipFile(path) as zf:
            manifest = zf.read("manifest.json")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("library.db", b"SQLite format 3\x00" + b"\xff" * 4096)
            zf.writestr("manifest.json", manifest)
        _delete_drama(a)
        self._assert_refused(a)

    def test_missing_manifest(self, isolated_db):
        a = db.create_drama(title_en="A")
        path = _snap()
        with zipfile.ZipFile(path) as zf:
            dbb = zf.read("library.db")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("library.db", dbb)
        _delete_drama(a)
        self._assert_refused(a)


class TestRestoreSeriesResolution:
    def test_name_only_match_never_links(self, isolated_db):
        """Same name, different id (the series was deleted and a new one
        made with its name): the snapshot's series comes back as its own
        series, renamed "... (restored <date>)"."""
        w = _seed_world()
        a = w["a"]
        _snap()
        _delete_drama(a)
        _delete_series(w["series"])
        with contextlib.closing(_conn()) as c:
            other = _insert(c, "series", name="Saga", created_at="y")
            _insert(c, "series_characters", series_id=other, character_name="Lin")
            c.commit()
        res = _restore(a)
        sid = db.get_drama(a)["series_id"]
        assert sid not in (other, w["series"])
        today = datetime.date.today().isoformat()
        assert _series_rows()[sid]["name"] == f"Saga (restored {today})"
        assert _series_rows()[other]["name"] == "Saga"
        assert res["counts"]["series"] == 1
        with contextlib.closing(_conn()) as c:
            assert c.execute("SELECT COUNT(*) FROM series_characters WHERE series_id = ?",
                             (other,)).fetchone()[0] == 1
            names = {r[0] for r in c.execute(
                "SELECT character_name FROM series_characters WHERE series_id = ?", (sid,))}
            assert names == {"Lin", "Mei"}
            scids = [r[0] for r in c.execute(
                "SELECT series_character_id FROM characters WHERE drama_id = ? "
                "AND series_character_id IS NOT NULL", (a,))]
            own = {r[0] for r in c.execute("SELECT id FROM series_characters WHERE series_id = ?",
                                           (sid,))}
            assert scids and set(scids) <= own

    def test_restored_name_also_taken_gets_counter(self, isolated_db):
        w = _seed_world()
        a = w["a"]
        _snap()
        _delete_drama(a)
        _delete_series(w["series"])
        today = datetime.date.today().isoformat()
        with contextlib.closing(_conn()) as c:
            _insert(c, "series", name="Saga")
            _insert(c, "series", name=f"Saga (restored {today})")
            c.commit()
        _restore(a)
        sid = db.get_drama(a)["series_id"]
        assert _series_rows()[sid]["name"] == f"Saga (restored {today}, 2)"

    def test_private_series_of_someone_else_not_reused(self, isolated_db):
        from services import auth_service
        owner = auth_service.add_user("owner@example.com")
        kid = auth_service.add_user("kid@example.com")
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="Mine", owner_user_id=owner["id"], is_private=0)
            a = _insert(c, "dramas", title_en="Kid's", series_id=sid, owner_user_id=kid["id"],
                        is_private=0, status="aligned")
            c.commit()
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:   # the owner made the series private since
            c.execute("UPDATE series SET is_private = 1 WHERE id = ?", (sid,))
            c.commit()
            c.execute("INSERT INTO glossary_terms (series_id, term_original, term_translation) "
                      "VALUES (?, '甲', 'A')", (sid,))
            c.commit()
        before = _series_rows()
        res = _restore(a)
        # Nothing of the owner's now-private series is copied or re-shared:
        # the drama comes back with no series and the result says why.
        assert db.get_drama(a)["series_id"] is None
        assert res["series"] == "dropped_private"
        assert "series" not in res["counts"]
        assert _series_rows() == before
        with contextlib.closing(_conn()) as c:
            assert c.execute("SELECT COUNT(*) FROM glossary_terms").fetchone()[0] == 1
        assert db.get_drama(a)["owner_user_id"] == kid["id"]

    @pytest.mark.parametrize("drama_owner", ["none", "series_owner"])
    def test_private_series_reused_for_owner_or_pc_drama(self, isolated_db, drama_owner):
        from services import auth_service
        owner = auth_service.add_user("owner@example.com")
        drama_uid = None if drama_owner == "none" else owner["id"]
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="Mine", owner_user_id=owner["id"], is_private=1)
            a = _insert(c, "dramas", title_en="D", series_id=sid, owner_user_id=drama_uid,
                        status="aligned")
            c.commit()
        _snap()
        _delete_drama(a)
        res = _restore(a)
        assert db.get_drama(a)["series_id"] == sid
        assert res["series"] == "linked"
        assert "series" not in res["counts"]
        assert len(_series_rows()) == 1

    def test_reused_series_maps_characters_by_id(self, isolated_db):
        """On reuse a series_character id is kept when it still exists in
        that series; a removed one becomes NULL (characters) or drops the
        row (voice_suggestion_dismissals)."""
        w = _seed_world()
        a = w["a"]
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:
            # Mei removed from the series; Lin renamed (same id)
            c.execute("DELETE FROM series_characters WHERE id = ?", (w["sc"]["Mei"],))
            c.execute("UPDATE series_characters SET character_name = 'Lin Renamed' WHERE id = ?",
                      (w["sc"]["Lin"],))
            c.commit()
        res = _restore(a)
        assert db.get_drama(a)["series_id"] == w["series"]
        with contextlib.closing(_conn()) as c:
            chars = {r[0]: r[1] for r in c.execute(
                "SELECT speaker_label, series_character_id FROM characters WHERE drama_id = ?",
                (a,))}
            assert chars == {"S0": w["sc"]["Lin"], "S1": None}
            assert c.execute("SELECT COUNT(*) FROM voice_suggestion_dismissals WHERE drama_id = ?",
                             (a,)).fetchone()[0] == 0
        assert res["counts"]["voice_suggestion_dismissals"] == 0

    def test_drama_in_series_restored_not_private(self, isolated_db):
        w = _seed_world()
        a = w["a"]
        with contextlib.closing(_conn()) as c:   # an inconsistent old row
            c.execute("UPDATE dramas SET is_private = 1 WHERE id = ?", (a,))
            c.commit()
        _snap()
        _delete_drama(a)
        _restore(a)
        assert not db.get_drama(a)["is_private"]


class TestSeriesIdReuse:
    """Lead re-review: after a whole-library restore of an older library the
    auto snapshot survives, and a new series can reuse an id the snapshot
    used for a different series."""

    def _setup(self, private, other_owner):
        from services import auth_service
        owner = auth_service.add_user("owner@example.com")
        kid = auth_service.add_user("kid@example.com")
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="Old", owner_user_id=owner["id"], is_private=0)
            a = _insert(c, "dramas", title_en="D", series_id=sid, owner_user_id=kid["id"],
                        is_private=0, status="aligned")
            c.commit()
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:   # the id now names an unrelated series
            c.execute("UPDATE series SET name = 'Unrelated', is_private = ?, owner_user_id = ? "
                      "WHERE id = ?", (int(private), owner["id"] if other_owner else kid["id"],
                                       sid))
            c.commit()
        return sid, a

    def test_same_id_other_name_is_recreated_not_linked(self, isolated_db):
        sid, a = self._setup(private=False, other_owner=True)
        before = _series_rows()
        res = _restore(a)
        new_sid = db.get_drama(a)["series_id"]
        assert res["series"] == "recreated" and new_sid not in (None, sid)
        assert _series_rows()[new_sid]["name"] == "Old"
        assert _series_rows()[sid] == before[sid]

    def test_same_id_other_name_private_is_still_dropped(self, isolated_db):
        sid, a = self._setup(private=True, other_owner=True)
        before = _series_rows()
        res = _restore(a)
        assert res["series"] == "dropped_private"
        assert db.get_drama(a)["series_id"] is None
        assert _series_rows() == before
