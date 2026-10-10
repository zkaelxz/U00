"""
Disk usage: a linked folder (symlink, junction, folder on another volume) is
sized the same at every level, shown apart from the folder's own bytes, and
stays impossible to clear or move. Temp folders only.
"""

import json
import os

import pytest

import db
from services import auto_backup_service, disk_usage_service as dus
from services.service_errors import InvalidInputError


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def _link(target, link):
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")


def _item(res, name):
    return next(i for i in res["items"] if i["name"] == name)


@pytest.fixture
def tree(isolated_db):
    lib = db.LIBRARY_DIR
    root = os.path.dirname(lib)
    _write(os.path.join(lib, "dramas", "1", "audio.mp3"), 5000)
    _write(os.path.join(lib, "backups", "small.zip"), 10)
    return root


@pytest.fixture
def big(tmp_path):
    """A 100 kB folder outside the data folder."""
    out = tmp_path / "elsewhere"
    _write(str(out / "a.zip"), 70_000)
    _write(str(out / "b.zip"), 30_000)
    return out


def _on_another_volume(monkeypatch, folder):
    """Reports everything at or under `folder` as a different device, as a
    mounted volume would (a test can't mount one)."""
    prefix = os.path.normcase(os.path.realpath(folder))

    def other(path):
        p = os.path.normcase(os.path.abspath(path))
        return p == prefix or p.startswith(prefix + os.sep)

    def with_dev(st, path):
        values = list(st)
        values[2] = 987654321 if other(path) else values[2]
        return os.stat_result(values)

    real_lstat, real_scandir = os.lstat, os.scandir

    class Entry:
        def __init__(self, e):
            self._e = e

        def __getattr__(self, name):
            return getattr(self._e, name)

        def stat(self, follow_symlinks=True):
            return with_dev(self._e.stat(follow_symlinks=follow_symlinks), self._e.path)

    class Scan:
        def __init__(self, it):
            self._it = it

        def __iter__(self):
            return (Entry(e) for e in self._it)

        def __next__(self):
            return Entry(next(self._it))

        def __enter__(self):
            self._it.__enter__()
            return self

        def __exit__(self, *a):
            return self._it.__exit__(*a)

        def close(self):
            self._it.close()

    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: with_dev(real_lstat(p, *a, **k), p))
    monkeypatch.setattr(os, "scandir", lambda p=".": Scan(real_scandir(p)))


def _everything(item):
    return item["size_bytes"] + (item["linked_bytes"] or 0)


class TestSameSizeAtEveryLevel:
    def test_folder_on_another_volume_adds_up_in_parent_and_child(self, tree, monkeypatch):
        # The reported case: the parent's total left out a folder the child view then counted.
        _write(os.path.join(tree, "library", "backups", "big.zip"), 100_000)
        _on_another_volume(monkeypatch, os.path.join(tree, "library", "backups"))
        from_root = _item(dus.scan(""), "library")
        lib = dus.scan("library")
        backups = _item(lib, "backups")
        assert backups["is_link"] and backups["protected"]
        assert backups["linked_bytes"] >= 100_000
        assert from_root["linked_bytes"] == backups["linked_bytes"]
        assert _everything(from_root) == sum(_everything(i) for i in lib["items"])
        assert lib["total_bytes"] == sum(i["size_bytes"] for i in lib["items"])
        assert lib["linked_bytes"] == backups["linked_bytes"]

    def test_symlink_inside_a_folder_is_reported_alike_from_both_levels(self, tree, big):
        _link(big, os.path.join(tree, "library", "backups", "auto"))
        from_root = _item(dus.scan(""), "library")
        backups = _item(dus.scan("library"), "backups")
        link = _item(dus.scan("library/backups"), "auto")
        assert link["is_link"] and link["linked_bytes"] == 100_000 and link["linked_files"] == 2
        assert backups["linked_bytes"] == from_root["linked_bytes"] == 100_000
        assert backups["contains_link"] and backups["size_bytes"] < 10_000
        assert dus.scan("library")["linked_bytes"] == 100_000

    def test_the_folders_own_bytes_stay_apart_from_linked_ones(self, tree, big):
        before = dus.scan("library")["total_bytes"]
        _link(big, os.path.join(tree, "library", "backups", "auto"))
        res = dus.scan("library")
        assert res["total_bytes"] - before < 10_000
        assert res["linked_bytes"] == 100_000
        assert abs(sum(i["percent_of_parent"] for i in res["items"]) - 100) < 0.5


class TestNoDoubleCountAndGuards:
    def test_a_link_to_a_folder_inside_the_data_folder_adds_nothing(self, tree):
        inside = os.path.join(tree, "library", "dramas", "1")
        _link(inside, os.path.join(tree, "library", "backups", "again"))
        res = dus.scan("library")
        assert res["linked_bytes"] == 0
        assert _item(dus.scan("library/backups"), "again")["linked_bytes"] is None

    def test_two_links_to_one_folder_count_it_once(self, tree, big):
        _link(big, os.path.join(tree, "library", "backups", "one"))
        _link(big, os.path.join(tree, "library", "backups", "two"))
        assert dus.scan("library")["linked_bytes"] == 100_000

    def test_a_link_back_to_itself_ends(self, tree, big):
        _link(big, os.path.join(tree, "library", "backups", "auto"))
        _link(os.path.join(tree, "library", "backups"), str(big / "back"))
        res = dus.scan("library")
        assert res["linked_bytes"] < 200_000 and not res["partial"]

    def test_a_link_to_a_folder_holding_the_data_folder_is_not_walked(self, tree):
        _link(os.path.dirname(tree), os.path.join(tree, "library", "backups", "up"))
        res = dus.scan("library")
        assert res["linked_bytes"] == 0
        assert _item(dus.scan("library/backups"), "up")["linked_bytes"] is None

    def test_per_scan_cap_on_linked_folders(self, tree, tmp_path, monkeypatch):
        monkeypatch.setattr("services.disk_usage_links.MAX_LINK_TARGETS", 2)
        for n in range(4):
            target = tmp_path / f"t{n}"
            _write(str(target / "f"), 100)
            _link(target, os.path.join(tree, "library", "backups", f"l{n}"))
        res = dus.scan("library")
        assert res["linked_bytes"] == 200
        assert _item(res, "backups")["linked_complete"] is False

    def test_scan_budget_still_applies_to_linked_folders(self, tree, big, monkeypatch):
        _link(big, os.path.join(tree, "library", "backups", "auto"))
        monkeypatch.setattr(dus, "MAX_ENTRIES", 5)
        res = dus.scan("library")
        assert res["partial"] is True

    def test_responses_hold_no_target_path(self, tree, big):
        _link(big, os.path.join(tree, "library", "backups", "auto"))
        for rel in ("", "library", "library/backups"):
            text = json.dumps(dus.scan(rel))
            assert str(big) not in text and "elsewhere" not in text
            assert tree not in text


class TestStillNeverCleared:
    def test_a_linked_item_stays_non_clearable_with_the_new_sizes(self, tree, big):
        _link(big, os.path.join(tree, "library", "backups", "auto"))
        link = _item(dus.scan("library/backups"), "auto")
        assert link["linked_bytes"] == 100_000 and link["protected"]
        with pytest.raises(InvalidInputError):
            dus.clear("library/backups/auto", confirm=True,
                      expected_size_bytes=link["size_bytes"], expected_file_count=link["file_count"])
        with pytest.raises(InvalidInputError):
            dus.clear("library/backups/auto", confirm=True, expected_size_bytes=100_000,
                      expected_file_count=2)
        assert (big / "a.zip").exists()

    def test_a_folder_holding_a_link_is_still_refused_whole(self, tree, big):
        _link(big, os.path.join(tree, "library", "backups", "auto"))
        item = _item(dus.scan("library"), "backups")
        assert item["contains_link"] and item["linked_bytes"] == 100_000
        with pytest.raises(InvalidInputError) as exc:
            dus.clear("library/backups", confirm=True, expected_size_bytes=item["size_bytes"],
                      expected_file_count=item["file_count"], confirm_irreplaceable=True)
        assert "link or junction" in exc.value.message
        assert (big / "b.zip").exists()

    def test_a_folder_on_another_volume_is_not_clearable(self, tree, monkeypatch):
        _write(os.path.join(tree, "library", "backups", "big.zip"), 100_000)
        _on_another_volume(monkeypatch, os.path.join(tree, "library", "backups"))
        item = _item(dus.scan("library"), "backups")
        assert item["protected"] and item["movable"]["supported"] is False
        with pytest.raises(InvalidInputError):
            dus.clear("library/backups", confirm=True, expected_size_bytes=item["size_bytes"],
                      expected_file_count=item["file_count"], confirm_irreplaceable=True)
        assert os.path.exists(os.path.join(tree, "library", "backups", "big.zip"))


class TestDeviceNumberUnknown:
    """os.DirEntry.stat() reports st_dev 0 on Windows; that must not read as
    another volume."""

    @staticmethod
    def _dir_stat(dev):
        return os.stat_result((0o040755, 1, dev, 1, 0, 0, 0, 0, 0, 0))

    def test_zero_device_is_not_another_volume(self, tmp_path, monkeypatch):
        folder = tmp_path / "plain"
        folder.mkdir()
        assert dus._on_another_volume(str(folder), self._dir_stat(0)) is False
        monkeypatch.setattr(os, "lstat", lambda p, *a, **k: self._dir_stat(0))
        assert dus._on_another_volume(str(folder), os.lstat(str(folder))) is False

    def test_different_device_is_another_volume(self, tmp_path, monkeypatch):
        folder = tmp_path / "mounted"
        folder.mkdir()
        monkeypatch.setattr(os, "lstat", lambda p, *a, **k: self._dir_stat(7))
        assert dus._on_another_volume(str(folder), self._dir_stat(9)) is True
        assert dus._on_another_volume(str(folder), self._dir_stat(7)) is False

    def test_walk_with_zero_entry_devices_finds_no_link(self, tree):
        real_scandir = os.scandir

        class Entry:
            def __init__(self, e):
                self._e = e

            def __getattr__(self, name):
                return getattr(self._e, name)

            def stat(self, follow_symlinks=True):
                values = list(self._e.stat(follow_symlinks=follow_symlinks))
                values[2] = 0
                return os.stat_result(values)

        class Scan:
            def __init__(self, it):
                self._it = it

            def __iter__(self):
                return self

            def __next__(self):
                return Entry(next(self._it))

            def __enter__(self):
                self._it.__enter__()
                return self

            def __exit__(self, *a):
                return self._it.__exit__(*a)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os, "scandir", lambda p=".": Scan(real_scandir(p)))
            item = _item(dus.scan(""), "library")
        assert not item["contains_link"] and item["linked_bytes"] is None


class TestNetworkTargets:
    @pytest.mark.parametrize("target,local", [
        (r"\\host\share\dir", False),
        (r"\\?\UNC\host\share", False),
        (r"\\?\GLOBALROOT\Device\Mup\h\s", False),
        (r"\\.\pipe\x", False),
        (r"\\?\pipe\x", False),
        (r"\\?\Volume{01234567-89ab-cdef-0123-456789abcdef}\data", False),
        (r"\\?\C:\data", True),
        (r"\\.\C:\data", True),
        (r"\\?\C:", True),
        (r"C:\data", True),
        (r"..\data", True),
        ("/mnt/data", True),
    ])
    def test_target_allowlist(self, target, local):
        from services import disk_usage_links as links
        assert links._is_local_target(target) is local

    @pytest.mark.parametrize("target", [
        r"\\host\share",
        r"\\?\GLOBALROOT\Device\Mup\h\s",
        r"\\.\pipe\x",
        r"\\?\pipe\x",
        r"\\?\Volume{01234567-89ab-cdef-0123-456789abcdef}\data",
    ])
    def test_non_local_target_is_skipped_before_any_open(self, tmp_path, monkeypatch, target):
        from services import disk_usage_links as links
        measured, opened = [], []
        sizes = links.LinkedSizes(str(tmp_path), [], dus._within, lambda *a: measured.append(a))

        def readlink(p):
            opened.append(p)
            if p == "link":
                return target
            raise AssertionError("the target must not be opened")

        def forbidden(*a, **k):
            raise AssertionError("a non-local path must not be stat'ed or resolved")

        monkeypatch.setattr(os, "readlink", readlink)
        monkeypatch.setattr(os.path, "realpath", forbidden)
        monkeypatch.setattr(os, "stat", forbidden)
        assert sizes.of(["link"], budget=None) == (0, 0, False)
        assert opened == ["link"]
        assert measured == []

    def test_link_below_a_symlinked_parent_to_a_share_is_skipped(self, tmp_path, monkeypatch):
        from services import disk_usage_links as links
        measured, opened = [], []
        sizes = links.LinkedSizes(str(tmp_path), [], dus._within, lambda *a: measured.append(a))
        parent = os.path.join("x", "a")
        monkeypatch.setattr(os.path, "islink", lambda p: p == parent)

        def readlink(p):
            opened.append(p)
            if p == parent:
                return r"\\host\share"
            raise AssertionError("nothing below the share may be opened")

        monkeypatch.setattr(os, "readlink", readlink)
        monkeypatch.setattr(os.path, "realpath", lambda p: (_ for _ in ()).throw(AssertionError(p)))
        assert sizes.of([os.path.join(parent, "b")], budget=None) == (0, 0, False)
        assert opened == [parent]
        assert measured == []


class TestLinkChains:
    def _sizes(self, tmp_path, measured):
        from services import disk_usage_links as links
        return links.LinkedSizes(str(tmp_path), [], dus._within, lambda *a: measured.append(a))

    def _fake(self, monkeypatch, chain):
        def readlink(p):
            if p in chain:
                return chain[p]
            raise OSError("not a link")

        def no_realpath(p):
            raise AssertionError("realpath must not open a chain that is not all local")

        monkeypatch.setattr(os, "readlink", readlink)
        monkeypatch.setattr(os.path, "realpath", no_realpath)

    def test_local_link_to_link_to_network_is_skipped(self, tmp_path, monkeypatch):
        measured = []
        self._fake(monkeypatch, {"a": "b", "b": r"\\host\share"})
        assert self._sizes(tmp_path, measured).of(["a"], budget=None) == (0, 0, False)
        assert measured == []

    def test_loop_is_skipped(self, tmp_path, monkeypatch):
        measured = []
        self._fake(monkeypatch, {"a": "b", "b": "a"})
        assert self._sizes(tmp_path, measured).of(["a"], budget=None) == (0, 0, False)
        assert measured == []

    def test_too_many_hops_is_skipped(self, tmp_path, monkeypatch):
        from services import disk_usage_links as links
        measured = []
        n = links.MAX_LINK_HOPS + 2
        self._fake(monkeypatch, {f"l{i}": f"l{i + 1}" for i in range(n)})
        assert self._sizes(tmp_path, measured).of(["l0"], budget=None) == (0, 0, False)
        assert measured == []

    def test_real_chain_of_local_links_is_measured(self, tree, big):
        mid = os.path.join(tree, "library", "backups", "mid")
        _link(big, mid)
        _link(mid, os.path.join(tree, "library", "backups", "auto"))
        assert _item(dus.scan("library/backups"), "auto")["linked_bytes"] == 100_000


class TestLinkBudget:
    """The link time budget starts at the first link looked at, and a spent
    budget only turns a folder that would have been measured into incomplete."""

    @pytest.fixture
    def parts(self, tmp_path, big):
        from services import disk_usage_links as links
        from types import SimpleNamespace
        root = tmp_path / "root"
        root.mkdir()
        measured = []

        def measure(real, parts_, budget):
            measured.append(real)
            return SimpleNamespace(size=100_000, files=2, unreadable=False)

        sizes = links.LinkedSizes(str(root), [], dus._within, measure)
        folder_link = root / "folder"
        _link(big, folder_link)
        return sizes, measured, str(folder_link), root

    def test_clock_starts_on_the_first_lookup_not_at_construction(self, parts, monkeypatch):
        from types import SimpleNamespace
        from services import disk_usage_links as links
        sizes, measured, folder_link, _ = parts
        assert sizes._deadline is None
        sizes.of([folder_link], SimpleNamespace(hit=False))
        assert sizes._deadline is not None and measured

    def test_spent_deadline_marks_a_folder_incomplete_without_measuring(self, parts):
        from types import SimpleNamespace
        import time
        sizes, measured, folder_link, _ = parts
        sizes._deadline = time.monotonic() - 1
        assert sizes.of([folder_link], SimpleNamespace(hit=False)) == (0, 0, False)
        assert measured == []

    def test_walk_budget_hit_marks_a_folder_incomplete_without_measuring(self, parts):
        from types import SimpleNamespace
        sizes, measured, folder_link, _ = parts
        assert sizes.of([folder_link], SimpleNamespace(hit=True)) == (0, 0, False)
        assert measured == []

    def test_spent_budget_leaves_file_links_unmeasured_and_folder_links_incomplete(self, parts, tmp_path):
        from types import SimpleNamespace
        import time
        sizes, measured, _, root = parts
        plain = tmp_path / "plain.txt"
        plain.write_text("x")
        file_link = root / "file"
        _link(plain, file_link)
        inside = root / "inside"
        inside.mkdir()
        in_root_link = root / "to_inside"
        _link(inside, in_root_link)
        sizes._deadline = time.monotonic() - 1
        for budget in (SimpleNamespace(hit=False), SimpleNamespace(hit=True)):
            # Only Windows says "file" in the link's own entry; elsewhere
            # telling needs the target, which a spent budget no longer opens.
            expected = None if hasattr(os.lstat(file_link), "st_file_attributes") else (0, 0, False)
            assert sizes.of([str(file_link)], budget) == expected
            assert sizes.of([str(in_root_link)], budget) == (0, 0, False)
        assert measured == []


class TestOverlappingTargets:
    def test_two_links_to_one_target_report_the_second_as_incomplete(self, tree, big):
        base = os.path.join(tree, "library", "backups")
        _link(big, os.path.join(base, "one"))
        _link(big, os.path.join(base, "two"))
        items = dus.scan("library/backups")
        shown = [_item(items, n)["linked_complete"] for n in ("one", "two")]
        assert sorted(shown) == [False, True]


class TestSkippedTargets:
    @pytest.mark.parametrize("which", ["program", "home"])
    def test_link_to_program_or_home_folder_is_not_followed(self, tree, big, monkeypatch, which):
        target = big / which
        _write(str(target / "f.bin"), 1000)
        if which == "program":
            monkeypatch.setattr(dus, "_program_dir", lambda: os.path.realpath(str(target)))
        else:
            monkeypatch.setattr(dus, "_home_dirs", lambda: [os.path.realpath(str(target))])
        _link(target, os.path.join(tree, "library", "backups", "auto"))
        link = _item(dus.scan("library/backups"), "auto")
        assert link["is_link"] and link["protected"]
        assert not link["linked_bytes"]


class TestMoveRefusesLinks:
    # Only the automatic-backup folder is movable, so these target it: any
    # other path is refused as unsupported before a link or volume is looked at.
    def test_move_refuses_a_link(self, tree, big, tmp_path):
        auto = os.path.join(db.LIBRARY_DIR, *auto_backup_service.DEFAULT_SUBDIR)
        _link(big, auto)
        (tmp_path / "dest").mkdir()
        with pytest.raises(InvalidInputError, match="Links and junctions"):
            dus.move("/".join(("library", *auto_backup_service.DEFAULT_SUBDIR)),
                     str(tmp_path / "dest"), confirm=True)
        assert (big / "a.zip").exists()

    def test_move_refuses_a_folder_on_another_volume(self, tree, monkeypatch, tmp_path):
        auto = os.path.join(db.LIBRARY_DIR, *auto_backup_service.DEFAULT_SUBDIR)
        _write(os.path.join(auto, "copy.zip"), 10)
        _on_another_volume(monkeypatch, auto)
        (tmp_path / "dest").mkdir()
        with pytest.raises(InvalidInputError):
            dus.move("/".join(("library", *auto_backup_service.DEFAULT_SUBDIR)),
                     str(tmp_path / "dest"), confirm=True)
        assert os.path.exists(os.path.join(auto, "copy.zip"))


def test_linked_sizes_stop_starting_targets_past_their_time_budget(tmp_path, monkeypatch):
    import time
    from services import disk_usage_links as links
    measured = []
    root = tmp_path / "root"
    root.mkdir()
    sizes = links.LinkedSizes(str(root), [], dus._within, lambda *a: measured.append(a))
    target = tmp_path / "t"
    target.mkdir()
    monkeypatch.setattr(links, "_local_target", lambda p: str(target))
    for name in ("a", "b"):
        _link(target, root / name)
    sizes._deadline = time.monotonic() - 1
    assert sizes.of([str(root / "a"), str(root / "b")], budget=None) == (0, 0, False)
    assert measured == []


def test_nothing_on_a_link_target_is_touched_past_the_deadline(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    from services import disk_usage_links as links
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "t"
    target.mkdir()
    touched = []
    monkeypatch.setattr(links, "_local_target", lambda p: touched.append(p))
    real_stat = os.stat
    monkeypatch.setattr(links.os, "stat", lambda p, *a, **k: touched.append(p) or real_stat(p, *a, **k))
    link = root / "folder"
    _link(target, link)
    sizes = links.LinkedSizes(str(root), [], dus._within, lambda *a: None)
    sizes._deadline = time.monotonic() - 1
    assert sizes.of([str(link)] * 5, SimpleNamespace(hit=False)) == (0, 0, False)
    assert touched == []


def test_windows_directory_attribute_decides_without_stat_of_the_target(tmp_path, monkeypatch):
    import stat
    from services import disk_usage_links as links
    fake = type("S", (), {"st_mode": stat.S_IFLNK, "st_file_attributes": 0x10})()
    monkeypatch.setattr(links.os, "lstat", lambda p: fake)
    monkeypatch.setattr(links.os, "stat", lambda p: (_ for _ in ()).throw(AssertionError("target touched")))
    assert links._may_be_folder("X:\\gone") is True
    fake.st_file_attributes = 0
    assert links._may_be_folder("X:\\gone") is False


def test_without_windows_attributes_a_link_is_assumed_a_folder_and_never_stated(monkeypatch):
    import stat
    from services import disk_usage_links as links
    fake = type("S", (), {"st_mode": stat.S_IFLNK})()
    monkeypatch.setattr(links.os, "lstat", lambda p: fake)
    monkeypatch.setattr(links.os, "stat", lambda p, *a, **k: (_ for _ in ()).throw(AssertionError("target touched")))
    assert links._may_be_folder("/Volumes/gone") is True
