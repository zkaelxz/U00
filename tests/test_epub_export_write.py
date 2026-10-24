"""
epub_io.export_epub with a stand-in for ebooklib, so it runs on a core-only
install: a failed write leaves the previous export and no temp file behind.
"""
import os
import sys
import types

import pytest

import epub_io
from core import Line


class _Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.content = kw.get("content", "")


class _Book:
    def __init__(self):
        self.items = []

    def __getattr__(self, name):
        if name.startswith("set_") or name == "add_author":
            return lambda *a, **k: None
        raise AttributeError(name)

    def add_item(self, item):
        self.items.append(item)


@pytest.fixture
def fake_epub(monkeypatch):
    written = {}
    mod = types.ModuleType("ebooklib.epub")
    mod.EpubBook = _Book
    mod.EpubImage = mod.EpubHtml = _Item
    mod.EpubNcx = mod.EpubNav = lambda: _Item()

    def write_epub(path, book):
        with open(path, "wb") as fh:
            fh.write(b"epub")
        written["book"] = book
    mod.write_epub = write_epub
    pkg = types.ModuleType("ebooklib")
    pkg.epub = mod
    monkeypatch.setitem(sys.modules, "ebooklib", pkg)
    monkeypatch.setitem(sys.modules, "ebooklib.epub", mod)
    return written


def test_a_failed_write_keeps_the_old_export_and_leaves_no_temp_file(fake_epub, tmp_path,
                                                                     monkeypatch):
    out = tmp_path / "translated.epub"
    out.write_bytes(b"previous")

    def broken(path, book):
        with open(path, "wb") as fh:
            fh.write(b"half")
        raise OSError("disk full")
    monkeypatch.setattr(sys.modules["ebooklib.epub"], "write_epub", broken)
    with pytest.raises(OSError):
        epub_io.export_epub([Line(idx=0, start=0, end=1, zh="", en="hi")], "T", "", str(out))
    assert out.read_bytes() == b"previous"
    assert os.listdir(tmp_path) == ["translated.epub"]
