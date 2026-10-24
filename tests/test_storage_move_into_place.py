"""
storage.move_into_place: a same-volume move replaces dst in one step; a move
across volumes copies beside dst first, so a failure leaves dst as it was and
no partial copy behind; any other error is raised with nothing changed.
"""
import errno
import os

import pytest

import storage


def _pair(tmp_path):
    src = tmp_path / "in" / "new.wav"
    src.parent.mkdir()
    src.write_bytes(b"new")
    dst = tmp_path / "out" / "audio.wav"
    dst.parent.mkdir()
    dst.write_bytes(b"old")
    return src, dst


def _cross_volume(monkeypatch):
    real = os.replace

    def replace(a, b):
        if os.path.dirname(a) != os.path.dirname(b):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(a, b)
    monkeypatch.setattr(storage.os, "replace", replace)


def test_same_volume_replaces_dst(tmp_path):
    src, dst = _pair(tmp_path)
    storage.move_into_place(str(src), str(dst))
    assert dst.read_bytes() == b"new" and not src.exists()


def test_across_volumes_it_copies_beside_dst_then_replaces(tmp_path, monkeypatch):
    src, dst = _pair(tmp_path)
    _cross_volume(monkeypatch)
    storage.move_into_place(str(src), str(dst))
    assert dst.read_bytes() == b"new" and not src.exists()
    assert os.listdir(dst.parent) == ["audio.wav"]


def test_a_failed_cross_volume_copy_keeps_dst_and_src_and_leaves_no_part(tmp_path, monkeypatch):
    src, dst = _pair(tmp_path)
    _cross_volume(monkeypatch)

    def disk_full(a, b):
        with open(b, "wb") as fh:
            fh.write(b"ne")
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(storage.shutil, "copyfile", disk_full)
    with pytest.raises(OSError) as exc:
        storage.move_into_place(str(src), str(dst))
    assert exc.value.errno == errno.ENOSPC
    assert dst.read_bytes() == b"old" and src.read_bytes() == b"new"
    assert os.listdir(dst.parent) == ["audio.wav"]


def test_any_other_replace_error_is_raised_with_nothing_copied(tmp_path, monkeypatch):
    src, dst = _pair(tmp_path)

    def denied(a, b):
        raise OSError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(storage.os, "replace", denied)
    copies = []
    monkeypatch.setattr(storage.shutil, "copyfile", lambda *a: copies.append(a))
    with pytest.raises(OSError) as exc:
        storage.move_into_place(str(src), str(dst))
    assert exc.value.errno == errno.EACCES and copies == []
    assert dst.read_bytes() == b"old" and src.read_bytes() == b"new"
