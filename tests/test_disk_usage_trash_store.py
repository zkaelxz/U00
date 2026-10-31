"""
services/disk_usage_trash_store.py failure paths: a move into Trash that
fails or is called off halfway leaves the item where it was (or, when it
can't be put back, keeps it in Trash), and damaged manifests, entries and
Trash roots are refused rather than read through. Temp folders only.
"""

import errno
import json
import os

import pytest

import db
from services import disk_usage_guard as guard
from services import disk_usage_trash_store as store
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, ServiceError

PARTS = ["library", "source_cache", "x.html"]


@pytest.fixture
def root(isolated_db):
    data = os.path.dirname(db.LIBRARY_DIR)
    os.makedirs(os.path.join(db.LIBRARY_DIR, "source_cache"), exist_ok=True)
    with open(os.path.join(db.LIBRARY_DIR, "source_cache", "x.html"), "wb") as fh:
        fh.write(b"x" * 400)
    return data


def _original(root):
    return os.path.join(root, *PARTS)


def _move(still_ok=None):
    real = guard._resolve(PARTS)
    item = {"kind": "file", "size_bytes": 400, "file_count": 1, "_deepest_path": len(real)}
    return store._move_to_trash(PARTS, real, item, still_ok=still_ok)


def _entries(root):
    trash = os.path.join(root, guard.TRASH_DIRNAME)
    return sorted(os.listdir(trash)) if os.path.isdir(trash) else []


class TestMoveToTrashFailures:
    def test_a_failed_rename_leaves_the_item_and_no_entry(self, root, monkeypatch):
        def broken(src, dst):
            raise OSError(errno.EACCES, "in use")
        monkeypatch.setattr(store, "_rename", broken)
        with pytest.raises(ServiceError) as exc:
            _move()
        assert exc.value.message == store.TRASH_FAILED
        assert os.path.getsize(_original(root)) == 400
        assert _entries(root) == []

    def test_a_manifest_that_cannot_be_written_leaves_the_item_and_no_entry(
            self, root, monkeypatch):
        def broken(*_a):
            raise OSError(errno.ENOSPC, "disk full")
        monkeypatch.setattr(store, "_write_manifest", broken)
        with pytest.raises(ServiceError):
            _move()
        assert os.path.isfile(_original(root))
        assert _entries(root) == []

    def test_called_off_before_the_rename_moves_nothing(self, root):
        with pytest.raises(ConflictError) as exc:
            _move(still_ok=lambda: False)
        assert exc.value.details == {"reason": "changed"}
        assert os.path.isfile(_original(root))
        assert _entries(root) == []

    def test_called_off_right_after_the_rename_puts_the_item_back(self, root):
        answers = iter([True, False])
        with pytest.raises(ConflictError):
            _move(still_ok=lambda: next(answers))
        assert os.path.getsize(_original(root)) == 400
        assert _entries(root) == []

    def test_an_item_that_cannot_be_put_back_stays_in_trash(self, root, monkeypatch):
        answers = iter([True, False])

        def no_way_back(src, dst, kind):
            raise OSError(errno.EACCES, "denied")
        monkeypatch.setattr(store, "_rename_no_overwrite", no_way_back)
        with pytest.raises(ServiceError) as exc:
            _move(still_ok=lambda: next(answers))
        assert "Restore it from the Trash list" in exc.value.message
        (entry,) = _entries(root)
        payload = os.path.join(root, guard.TRASH_DIRNAME, entry, store.TRASH_PAYLOAD)
        assert os.path.getsize(payload) == 400
        assert store._read_manifest(os.path.dirname(payload))["parts"] == PARTS

    def test_something_already_back_in_place_is_left_and_the_payload_kept(
            self, root, monkeypatch):
        answers = iter([True, False])

        def squatted(src, dst, kind):
            raise FileExistsError(dst)
        monkeypatch.setattr(store, "_rename_no_overwrite", squatted)
        with pytest.raises(ConflictError):
            _move(still_ok=lambda: next(answers))
        (entry,) = _entries(root)
        assert os.path.isfile(os.path.join(root, guard.TRASH_DIRNAME, entry, store.TRASH_PAYLOAD))

    def test_another_volume_is_refused_before_anything_moves(self, root, monkeypatch):
        monkeypatch.setattr(store, "_same_volume", lambda a, b: False)
        with pytest.raises(ServiceError) as exc:
            _move()
        assert exc.value.message == store.TRASH_FAILED
        assert os.path.isfile(_original(root))

    def test_a_path_too_long_after_the_move_is_refused(self, root):
        real = guard._resolve(PARTS)
        item = {"kind": "file", "size_bytes": 400, "file_count": 1,
                "_deepest_path": len(real) + store.MAX_PATH}
        with pytest.raises(ServiceError):
            store._move_to_trash(PARTS, real, item)
        assert os.path.isfile(_original(root))

    def test_an_entry_folder_that_cannot_be_made_is_refused(self, root, monkeypatch):
        store._trash_root(create=True)
        real_mkdir = os.mkdir

        def refuse(path, *a, **k):
            if os.path.dirname(path) == os.path.join(root, guard.TRASH_DIRNAME):
                raise OSError(errno.EACCES, "denied")
            return real_mkdir(path, *a, **k)
        monkeypatch.setattr(store.os, "mkdir", refuse)
        with pytest.raises(ServiceError):
            _move()
        assert os.path.isfile(_original(root))


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-overwrite rename")
class TestRenameNoOverwrite:
    def _pair(self, tmp_path):
        src, dst = tmp_path / "src.bin", tmp_path / "dst.bin"
        src.write_bytes(b"mine")
        return str(src), str(dst)

    def test_a_file_is_linked_then_unlinked(self, tmp_path):
        src, dst = self._pair(tmp_path)
        store._rename_no_overwrite(src, dst, "file")
        assert not os.path.exists(src) and open(dst, "rb").read() == b"mine"

    def test_an_existing_file_is_never_replaced(self, tmp_path):
        src, dst = self._pair(tmp_path)
        with open(dst, "wb") as fh:
            fh.write(b"theirs")
        with pytest.raises(FileExistsError):
            store._rename_no_overwrite(src, dst, "file")
        assert open(src, "rb").read() == b"mine" and open(dst, "rb").read() == b"theirs"

    @pytest.mark.parametrize("err", [errno.EPERM, errno.EXDEV, errno.EMLINK])
    def test_without_hard_links_it_falls_back_to_a_checked_rename(self, tmp_path, monkeypatch,
                                                                  err):
        src, dst = self._pair(tmp_path)

        def no_links(*_a, **_k):
            raise OSError(err, "no hard links")
        monkeypatch.setattr(store.os, "link", no_links)
        store._rename_no_overwrite(src, dst, "file")
        assert not os.path.exists(src) and open(dst, "rb").read() == b"mine"

    def test_the_fallback_still_refuses_an_existing_file(self, tmp_path, monkeypatch):
        src, dst = self._pair(tmp_path)
        with open(dst, "wb") as fh:
            fh.write(b"theirs")

        def no_links(*_a, **_k):
            raise OSError(errno.EPERM, "no hard links")
        monkeypatch.setattr(store.os, "link", no_links)
        with pytest.raises(FileExistsError):
            store._rename_no_overwrite(src, dst, "file")
        assert open(dst, "rb").read() == b"theirs" and os.path.exists(src)

    def test_any_other_link_error_is_raised_and_moves_nothing(self, tmp_path, monkeypatch):
        src, dst = self._pair(tmp_path)

        def io_error(*_a, **_k):
            raise OSError(errno.EIO, "I/O error")
        monkeypatch.setattr(store.os, "link", io_error)
        with pytest.raises(OSError) as exc:
            store._rename_no_overwrite(src, dst, "file")
        assert exc.value.errno == errno.EIO
        assert os.path.exists(src) and not os.path.exists(dst)

    def test_a_failed_folder_rename_removes_only_its_own_empty_folder(self, tmp_path,
                                                                     monkeypatch):
        src, dst = tmp_path / "a", tmp_path / "b"
        src.mkdir()
        (src / "f").write_bytes(b"1")

        def broken(*_a):
            raise OSError(errno.EACCES, "denied")
        monkeypatch.setattr(store, "_rename", broken)
        with pytest.raises(OSError):
            store._rename_no_overwrite(str(src), str(dst), "folder")
        assert (src / "f").exists() and not dst.exists()


class TestManifest:
    def _entry(self, tmp_path, data, raw=None):
        entry = tmp_path / "20260101-000000-aaaaaaaa"
        entry.mkdir()
        (entry / store.TRASH_MANIFEST).write_text(raw if raw is not None else json.dumps(data))
        return str(entry)

    GOOD = {"original_path": "library/a.bin", "kind": "file", "size_bytes": 1, "file_count": 1,
            "trashed_at": "2026-01-01T00:00:00+00:00"}

    def test_a_good_manifest_reads_back(self, tmp_path):
        got = store._read_manifest(self._entry(tmp_path, self.GOOD))
        assert got == {"parts": ["library", "a.bin"], "kind": "file", "size_bytes": 1,
                       "file_count": 1, "trashed_at": "2026-01-01T00:00:00+00:00"}

    @pytest.mark.parametrize("change", [
        {"original_path": "../outside"}, {"original_path": "/etc/passwd"},
        {"original_path": ""}, {"original_path": None}, {"kind": "link"},
        {"size_bytes": -1}, {"size_bytes": True}, {"size_bytes": "1"},
        {"file_count": None}, {"file_count": 1.5},
    ])
    def test_a_damaged_field_makes_the_whole_manifest_unread(self, tmp_path, change):
        assert store._read_manifest(self._entry(tmp_path, {**self.GOOD, **change})) is None

    def test_a_non_string_time_is_dropped_but_the_rest_kept(self, tmp_path):
        got = store._read_manifest(self._entry(tmp_path, {**self.GOOD, "trashed_at": 5}))
        assert got["trashed_at"] is None and got["parts"] == ["library", "a.bin"]

    @pytest.mark.parametrize("raw", ["[1, 2]", "\"text\"", "{not json", "\udcff"])
    def test_not_an_object_or_not_json_is_unread(self, tmp_path, raw):
        try:
            entry = self._entry(tmp_path, None, raw=raw)
        except UnicodeEncodeError:
            entry = str(tmp_path / "20260101-000000-aaaaaaaa")
            with open(os.path.join(entry, store.TRASH_MANIFEST), "wb") as fh:
                fh.write(b"\xff\xfe{")
        assert store._read_manifest(entry) is None

    def test_an_oversized_manifest_is_not_read(self, tmp_path):
        data = {**self.GOOD, "pad": "x" * 20000}
        assert store._read_manifest(self._entry(tmp_path, data)) is None

    def test_a_missing_manifest_is_unread(self, tmp_path):
        entry = tmp_path / "20260101-000000-aaaaaaaa"
        entry.mkdir()
        assert store._read_manifest(str(entry)) is None

    def test_a_linked_manifest_is_not_followed(self, tmp_path):
        target = tmp_path / "real.json"
        target.write_text(json.dumps(self.GOOD))
        entry = tmp_path / "20260101-000000-aaaaaaaa"
        entry.mkdir()
        try:
            os.symlink(str(target), str(entry / store.TRASH_MANIFEST))
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        assert store._read_manifest(str(entry)) is None


class TestTrashRootAndEntries:
    def test_no_trash_folder_is_none_until_asked_to_create(self, root):
        assert store._trash_root() is None
        made = store._trash_root(create=True)
        assert os.path.isdir(made) and store._trash_root() == made

    def test_a_file_where_the_trash_folder_goes_is_refused(self, root):
        with open(os.path.join(root, guard.TRASH_DIRNAME), "w") as fh:
            fh.write("not a folder")
        for create in (False, True):
            with pytest.raises(ServiceError, match="isn't an ordinary folder"):
                store._trash_root(create=create)

    def test_a_trash_folder_that_cannot_be_made_is_refused(self, root, monkeypatch):
        def refuse(*_a, **_k):
            raise OSError(errno.EACCES, "denied")
        monkeypatch.setattr(store.os, "mkdir", refuse)
        with pytest.raises(ServiceError) as exc:
            store._trash_root(create=True)
        assert exc.value.message == store.TRASH_FAILED

    @pytest.mark.parametrize("bad", ["..", "../x", "20260101-000000-AAAAAAAA",
                                     "20260101-000000-aaaaaaaa/..", "", None, 5,
                                     "20260101-000000-aaaaaaa"])
    def test_an_id_outside_the_pattern_is_refused(self, root, bad):
        trash = store._trash_root(create=True)
        with pytest.raises(InvalidInputError):
            store._entry_path(bad, trash)

    def test_a_missing_entry_is_not_found(self, root):
        trash = store._trash_root(create=True)
        with pytest.raises(NotFoundError):
            store._entry_path("20260101-000000-aaaaaaaa", trash)

    def test_a_file_named_like_an_entry_is_refused_and_skipped_by_the_list(self, root):
        trash = store._trash_root(create=True)
        with open(os.path.join(trash, "20260101-000000-aaaaaaaa"), "w") as fh:
            fh.write("x")
        os.mkdir(os.path.join(trash, "20260102-000000-bbbbbbbb"))
        os.mkdir(os.path.join(trash, "not-an-id"))
        with pytest.raises(InvalidInputError):
            store._entry_path("20260101-000000-aaaaaaaa", trash)
        assert [name for name, _ in store._trash_entries(trash)] == ["20260102-000000-bbbbbbbb"]

    def test_an_unreadable_trash_folder_counts_as_partial(self, root, monkeypatch):
        store._trash_root(create=True)

        def broken(*_a, **_k):
            raise OSError(errno.EIO, "I/O error")
        monkeypatch.setattr(store.os, "scandir", broken)
        summary, measured = store._trash_totals()
        assert summary == {"size_bytes": 0, "item_count": 0, "partial": True}
        assert measured.unreadable is True

    def test_drop_entry_shell_never_removes_a_payload(self, tmp_path):
        entry = tmp_path / "20260101-000000-aaaaaaaa"
        entry.mkdir()
        (entry / store.TRASH_MANIFEST).write_text("{}")
        (entry / store.TRASH_PAYLOAD).write_bytes(b"keep")
        store._drop_entry_shell(str(entry))
        assert (entry / store.TRASH_PAYLOAD).read_bytes() == b"keep"
        assert not (entry / store.TRASH_MANIFEST).exists()
