"""
lib/link_new.py: the final name is never taken over from a file already
there, with or without hard links, and a failed fallback leaves no empty
placeholder under the final name.
"""
import errno
import os

import pytest

from lib import link_new as ln


def _src(tmp_path, data=b"new"):
    src = tmp_path / "part.tmp"
    src.write_bytes(data)
    return str(src), str(tmp_path / "final.bin")


def _no_links(monkeypatch):
    def refuse(*_a, **_k):
        raise OSError(errno.EPERM, "no hard links")
    monkeypatch.setattr(ln.os, "link", refuse)


def test_a_hard_link_then_the_partial_name_is_removed(tmp_path):
    src, dest = _src(tmp_path)
    ln.link_new(src, dest)
    assert open(dest, "rb").read() == b"new" and not os.path.exists(src)


@pytest.mark.parametrize("hard_links", [True, False])
def test_an_existing_file_is_never_replaced(tmp_path, monkeypatch, hard_links):
    src, dest = _src(tmp_path)
    with open(dest, "wb") as fh:
        fh.write(b"old")
    if not hard_links:
        _no_links(monkeypatch)
    with pytest.raises(FileExistsError):
        ln.link_new(src, dest)
    assert open(dest, "rb").read() == b"old" and open(src, "rb").read() == b"new"


@pytest.mark.parametrize("missing", [AttributeError, NotImplementedError])
def test_no_link_support_at_all_falls_back_to_the_placeholder(tmp_path, monkeypatch, missing):
    src, dest = _src(tmp_path)

    def absent(*_a, **_k):
        raise missing("no os.link")
    monkeypatch.setattr(ln.os, "link", absent)
    ln.link_new(src, dest)
    assert open(dest, "rb").read() == b"new" and not os.path.exists(src)


def test_a_failed_fallback_rename_removes_its_placeholder_and_keeps_the_source(
        tmp_path, monkeypatch):
    src, dest = _src(tmp_path)
    _no_links(monkeypatch)

    def broken(*_a, **_k):
        raise OSError(errno.EACCES, "in use")
    monkeypatch.setattr(ln.os, "replace", broken)
    with pytest.raises(OSError) as exc:
        ln.link_new(src, dest)
    assert exc.value.errno == errno.EACCES
    assert not os.path.exists(dest) and open(src, "rb").read() == b"new"
