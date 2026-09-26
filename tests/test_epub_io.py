"""
tests/test_epub_io.py -- epub_io.py's import/export, including Step 23c
item 1's image round-trip (extract on import, re-embed on export).
Requires the optional `ebooklib`/`beautifulsoup4` extras -- skipped
cleanly on a core-only install.
"""
import os
import tempfile

import pytest

pytest.importorskip("ebooklib")
pytest.importorskip("bs4")

import epub_io
from core import Line

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_epub(path, chapter_html_list, image_files=None):
    """A minimal real .epub with one XHTML chapter per entry in
    chapter_html_list, and any (filename, bytes) pairs in image_files
    added as image items referenced by the first chapter."""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("test-book")
    book.set_title("Test Book")
    book.set_language("en")

    for fname, data in (image_files or []):
        img = epub.EpubImage(uid=fname, file_name=fname, media_type="image/png", content=data)
        book.add_item(img)

    chapters = []
    for i, html in enumerate(chapter_html_list):
        c = epub.EpubHtml(title=f"Chapter {i + 1}", file_name=f"chap_{i + 1:03d}.xhtml", lang="en")
        c.content = html
        book.add_item(c)
        chapters.append(c)

    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters
    epub.write_epub(path, book)


class TestImportPlainText:
    def test_extracts_text_in_reading_order(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "book.epub")
            _make_epub(path, ["<p>First chapter.</p>", "<p>Second chapter.</p>"])
            text = epub_io.import_epub_text(path)
            assert "First chapter." in text
            assert "Second chapter." in text
            assert text.index("First chapter.") < text.index("Second chapter.")
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_without_images_dir_images_are_silently_dropped(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "book.epub")
            _make_epub(path, ['<p>Before.</p><img src="img1.png"/><p>After.</p>'],
                       image_files=[("img1.png", PNG_BYTES)])
            text = epub_io.import_epub_text(path)
            assert "Before." in text and "After." in text
            assert "[[IMG:" not in text
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class TestImportImages:
    def test_image_extracted_and_marked_in_position(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "book.epub")
            images_dir = os.path.join(d, "images_out")
            _make_epub(path, ['<p>Before.</p><img src="img1.png"/><p>After.</p>'],
                       image_files=[("img1.png", PNG_BYTES)])
            text = epub_io.import_epub_text(path, images_dir=images_dir)

            assert "[[IMG:img1.png]]" in text
            assert text.index("Before.") < text.index("[[IMG:img1.png]]") < text.index("After.")
            extracted_path = os.path.join(images_dir, "img1.png")
            assert os.path.exists(extracted_path)
            with open(extracted_path, "rb") as f:
                assert f.read() == PNG_BYTES
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_image_with_no_matching_item_is_dropped_not_crashed(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "book.epub")
            images_dir = os.path.join(d, "images_out")
            # References an image that was never added to the book.
            _make_epub(path, ['<p>Before.</p><img src="missing.png"/><p>After.</p>'])
            text = epub_io.import_epub_text(path, images_dir=images_dir)
            assert "Before." in text and "After." in text
            assert "[[IMG:" not in text
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class TestExportImages:
    def _read_back(self, path):
        from ebooklib import epub
        return epub.read_epub(path)

    def test_placeholder_embeds_the_image_at_its_position(self):
        d = tempfile.mkdtemp()
        try:
            images_dir = os.path.join(d, "images")
            os.makedirs(images_dir)
            with open(os.path.join(images_dir, "img1.png"), "wb") as f:
                f.write(PNG_BYTES)

            lines = [
                Line(idx=0, start=0, end=1, zh="", en="Before."),
                Line(idx=1, start=1, end=2, zh="", en="[[IMG:img1.png]]"),
                Line(idx=2, start=2, end=3, zh="", en="After."),
            ]
            out_path = os.path.join(d, "out.epub")
            epub_io.export_epub(lines, "Title", "Author", out_path, field="en", images_dir=images_dir)

            import ebooklib
            book = self._read_back(out_path)
            chapter = next(item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT)
            html = chapter.get_content().decode("utf-8")
            assert "Before." in html and "After." in html
            assert 'img src="images/img1.png"' in html
            assert html.index("Before.") < html.index("images/img1.png") < html.index("After.")

            image_item = book.get_item_with_href("images/img1.png")
            assert image_item is not None
            assert image_item.get_content() == PNG_BYTES
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_placeholder_with_no_images_dir_is_dropped_not_left_as_text(self):
        d = tempfile.mkdtemp()
        try:
            lines = [Line(idx=0, start=0, end=1, zh="", en="Before. [[IMG:img1.png]] After.")]
            out_path = os.path.join(d, "out.epub")
            epub_io.export_epub(lines, "Title", "Author", out_path, field="en")

            import ebooklib
            book = self._read_back(out_path)
            chapter = next(item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT)
            html = chapter.get_content().decode("utf-8")
            assert "[[IMG:" not in html
            assert "Before." in html and "After." in html
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_placeholder_naming_a_missing_file_is_dropped(self):
        d = tempfile.mkdtemp()
        try:
            images_dir = os.path.join(d, "images")
            os.makedirs(images_dir)  # empty -- img1.png was never written here
            lines = [Line(idx=0, start=0, end=1, zh="", en="Before. [[IMG:img1.png]] After.")]
            out_path = os.path.join(d, "out.epub")
            epub_io.export_epub(lines, "Title", "Author", out_path, field="en", images_dir=images_dir)

            book = self._read_back(out_path)
            assert book.get_item_with_href("images/img1.png") is None
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class TestRoundTrip:
    def test_import_then_export_preserves_the_image_and_its_position(self):
        d = tempfile.mkdtemp()
        try:
            src_path = os.path.join(d, "source.epub")
            images_dir = os.path.join(d, "images")
            _make_epub(src_path, ['<p>Before.</p><img src="img1.png"/><p>After.</p>'],
                       image_files=[("img1.png", PNG_BYTES)])

            imported_text = epub_io.import_epub_text(src_path, images_dir=images_dir)
            paragraphs = [p for p in imported_text.split("\n") if p.strip()]
            lines = [Line(idx=i, start=i, end=i + 1, zh="", en=p) for i, p in enumerate(paragraphs)]

            out_path = os.path.join(d, "roundtrip.epub")
            epub_io.export_epub(lines, "Title", "Author", out_path, field="en", images_dir=images_dir)

            import ebooklib
            from ebooklib import epub
            book = epub.read_epub(out_path)
            chapter = next(item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT)
            html = chapter.get_content().decode("utf-8")

            assert "Before." in html and "After." in html
            assert 'img src="images/img1.png"' in html
            assert html.index("Before.") < html.index("images/img1.png") < html.index("After.")
            image_item = book.get_item_with_href("images/img1.png")
            assert image_item.get_content() == PNG_BYTES
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
