"""
epub_io with a stand-in for ebooklib, so these run on a core-only install:
a failed write leaves the previous export and no temp file behind, an
[[IMG:...]] placeholder embeds only a plain file inside images_dir (line text
can name any path), and an imported book can't write outside images_dir.
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


def _images(book):
    return {i.file_name: i.content for i in book.items if hasattr(i, "media_type")}


def _chapter_text(book):
    return "".join(i.content for i in book.items if getattr(i, "file_name", "").endswith(".xhtml"))


def test_only_a_plain_file_inside_images_dir_is_embedded(fake_epub, tmp_path):
    images = tmp_path / "drama" / "epub_images"
    images.mkdir(parents=True)
    (images / "ok.png").write_bytes(b"png")
    (images / "sub").mkdir()
    (images / "sub" / "deep.png").write_bytes(b"deep")
    secret = tmp_path / "drama" / ".env"
    secret.write_text("ANTHROPIC_API_KEY=sk-ant-secret")
    names = ["ok.png", "../.env", str(secret), "sub/deep.png", "sub\\deep.png", "..", "sub",
             "nul.png"]
    line = Line(idx=0, start=0, end=1, zh="", en=" ".join(f"[[IMG:{n}]]" for n in names))

    epub_io.export_epub([line], "T", "", str(tmp_path / "out.epub"), images_dir=str(images))

    book = fake_epub["book"]
    assert _images(book) == {"images/ok.png": b"png"}
    assert "sk-ant-secret" not in _chapter_text(book)
    assert "[[IMG:" not in _chapter_text(book)


def test_a_link_out_of_images_dir_is_not_followed(fake_epub, tmp_path):
    images = tmp_path / "epub_images"
    images.mkdir()
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"secret")
    try:
        os.symlink(str(secret), str(images / "pic.png"))
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    line = Line(idx=0, start=0, end=1, zh="", en="[[IMG:pic.png]]")
    epub_io.export_epub([line], "T", "", str(tmp_path / "out.epub"), images_dir=str(images))
    assert _images(fake_epub["book"]) == {}


class _Doc:
    def __init__(self, name, html):
        self.name, self.html = name, html

    def get_type(self):
        return 9

    def get_name(self):
        return self.name

    def get_content(self):
        return self.html.encode("utf-8")


class _Img:
    def get_content(self):
        return b"img"


def test_an_imported_book_cannot_write_outside_images_dir(fake_epub, tmp_path, monkeypatch):
    pytest.importorskip("bs4")
    hrefs = {"text/ok.png", "text/a\\..\\..\\evil.png", "text/c:evil.png", "text/nul.png"}
    srcs = ["ok.png", "a\\..\\..\\evil.png", "c:evil.png", "nul.png", "missing.png", ""]
    html = "<p>start</p>" + "".join(f'<img src="{s}"/>' for s in srcs) + "<p>end</p>"
    book = types.SimpleNamespace(
        get_items=lambda: [_Doc("text/ch1.xhtml", html)],
        get_item_with_href=lambda href: _Img() if href in hrefs else None)
    epub_mod = sys.modules["ebooklib.epub"]
    monkeypatch.setattr(epub_mod, "read_epub", lambda path: book, raising=False)
    monkeypatch.setattr(sys.modules["ebooklib"], "ITEM_DOCUMENT", 9, raising=False)
    images = tmp_path / "lib" / "epub_images"

    text = epub_io.import_epub_text("book.epub", images_dir=str(images))

    assert sorted(os.listdir(images)) == ["ok.png"]
    assert sorted(os.listdir(tmp_path)) == ["lib"] and os.listdir(tmp_path / "lib") == ["epub_images"]
    assert "[[IMG:ok.png]]" in text and text.count("[[IMG:") == 1
    assert "start" in text and "end" in text


def _chapters(book):
    return [(i.title, i.content) for i in book.items if getattr(i, "file_name", "").endswith(".xhtml")]


def test_chapters_follow_the_novels_own_headings(fake_epub, tmp_path):
    lines = [Line(idx=i, start=0, end=1, zh=zh, en=en) for i, (zh, en) in enumerate([
        ("作者的话", "A note from the author."),
        ("第一章 雨夜", "Chapter 1: A Rainy Night"),
        ("下雨了。", "It was raining."),
        ("第二章", "Chapter 2"),
        ("天晴了。", "The sky cleared."),
    ])]
    epub_io.export_epub(lines, "T", "", str(tmp_path / "out.epub"), lines_per_chapter=2)
    chapters = _chapters(fake_epub["book"])
    assert [t for t, _ in chapters] == ["Chapter 1", "Chapter 1: A Rainy Night", "Chapter 2"]
    assert "A note from the author." in chapters[0][1]
    assert "It was raining." in chapters[1][1] and "<h1>Chapter 1: A Rainy Night</h1>" in chapters[1][1]
    assert chapters[1][1].count("A Rainy Night") == 1   # the heading isn't repeated as a paragraph
    assert "The sky cleared." in chapters[2][1]


def test_without_headings_long_books_still_split_into_fixed_runs(fake_epub, tmp_path):
    lines = [Line(idx=i, start=0, end=1, zh="", en=f"Line {i}.") for i in range(5)]
    epub_io.export_epub(lines, "T", "", str(tmp_path / "out.epub"), lines_per_chapter=2)
    assert [t for t, _ in _chapters(fake_epub["book"])] == ["Chapter 1", "Chapter 2", "Chapter 3"]


def test_a_heading_title_is_escaped(fake_epub, tmp_path):
    lines = [Line(idx=0, start=0, end=1, zh="第一章", en="Chapter 1 <b>&</b>"),
             Line(idx=1, start=0, end=1, zh="好。", en="Fine.")]
    epub_io.export_epub(lines, "T", "", str(tmp_path / "out.epub"))
    assert "<h1>Chapter 1 &lt;b&gt;&amp;&lt;/b&gt;</h1>" in _chapters(fake_epub["book"])[0][1]


def test_a_lone_heading_does_not_make_the_rest_of_the_book_one_chapter(fake_epub, tmp_path):
    lines = [Line(idx=0, start=0, end=1, zh="序章", en="Prologue")]
    lines += [Line(idx=i, start=0, end=1, zh="", en=f"Line {i}.") for i in range(1, 6)]
    epub_io.export_epub(lines, "T", "", str(tmp_path / "out.epub"), lines_per_chapter=2)
    chapters = _chapters(fake_epub["book"])
    assert [t for t, _ in chapters] == ["Prologue", "Prologue (2)", "Prologue (3)"]
    assert "Line 1." in chapters[0][1] and "Line 2." in chapters[1][1] and "Line 5." in chapters[2][1]
