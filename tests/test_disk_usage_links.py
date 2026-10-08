"""
Disk usage: a linked folder (symlink, junction, folder on another volume) is
sized the same at every level, shown apart from the folder's own bytes, and
stays impossible to clear or move. Temp folders only.
"""

import json
import os

import pytest

import db
from services import disk_usage_service as dus
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
