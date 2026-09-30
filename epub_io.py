"""
epub_io.py -- EPUB import/export for novel-narration mode. Read
chapter text out of an .epub you already own, and export a finished
translation back as a proper .epub for e-reader apps.

Requires `pip install ebooklib beautifulsoup4`.
"""

import html
import mimetypes
import os
import posixpath
import re

from core import SOURCE_LANGUAGES

IMG_TOKEN_RE = re.compile(r"\[\[IMG:([^\]]+)\]\]")


def import_epub_text(epub_path: str, chapter_range: tuple = None, images_dir: str = None) -> str:
    """Extracts plain text from an EPUB's chapters, in reading order,
    joined with blank lines between chapters. chapter_range: optional
    (start, end) 0-indexed chapter slice, for importing just part of a
    long book instead of the whole thing at once.

    images_dir: Step 23c item 1 -- when given, any image an in-range
    chapter's HTML references is extracted there (its own filename,
    de-duplicated across chapters by that filename) and its position in
    the returned text is marked with a [[IMG:filename]] placeholder, so
    export_epub can put the same image back in the same spot later.
    Without images_dir (the default), behavior is unchanged from before
    this: images are silently dropped, same as a bare .get_text() always
    did."""
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(epub_path)
    chapters = [item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT]

    if chapter_range:
        start, end = chapter_range
        chapters = chapters[start:end]

    texts = []
    for ch in chapters:
        soup = BeautifulSoup(ch.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        if images_dir:
            chapter_dir = posixpath.dirname(ch.get_name())
            for img_tag in soup.find_all("img"):
                src = img_tag.get("src")
                if not src:
                    img_tag.decompose()
                    continue
                resolved_href = posixpath.normpath(posixpath.join(chapter_dir, src))
                image_item = book.get_item_with_href(resolved_href)
                if image_item is None:
                    img_tag.decompose()
                    continue
                fname = os.path.basename(resolved_href)
                os.makedirs(images_dir, exist_ok=True)
                with open(os.path.join(images_dir, fname), "wb") as f:
                    f.write(image_item.get_content())
                img_tag.replace_with(f"\n[[IMG:{fname}]]\n")
        text = soup.get_text(separator="\n").strip()
        if text:
            texts.append(text)
    return "\n\n".join(texts)


def export_epub(lines, title: str, author: str, out_path: str, field: str = "en",
                 lines_per_chapter: int = 200, images_dir: str = None,
                 source_language: str = None):
    """Exports translated (or original) lines as a proper .epub, split
    into chapters of `lines_per_chapter` lines each so long novels
    don't become one giant unreadable chapter. field: 'en' for the
    translation, 'zh' for the raw text (e.g. exporting a bilingual
    reading copy would need two calls or a custom merge).

    images_dir: Step 23c item 1 -- a directory (e.g. import_epub_text's
    own images_dir, or a drama's Scanlate page images) to resolve
    [[IMG:filename]] placeholders in a line's text against, embedding
    the matching file inline at that exact position instead of as plain
    text. A placeholder with no images_dir given, or naming a file that
    isn't actually there, is dropped rather than left as visible
    [[IMG:...]] text in the reader's output. Each distinct filename is
    embedded once even if it's referenced from more than one chapter.

    source_language: the drama's source language ('zh', 'ja', 'ko'), used
    as the book language when field is not 'en'; unknown falls back to 'zh'.

    The file is written to a temp file beside out_path and moved into
    place on success, so a crash never truncates an existing export."""
    from ebooklib import epub

    lang = "en" if field == "en" else (
        source_language if source_language in SOURCE_LANGUAGES else "zh")

    book = epub.EpubBook()
    book.set_identifier(f"baihe-subtitler-{title}")
    book.set_title(title)
    book.set_language(lang)
    if author:
        book.add_author(author)

    added_images = {}  # filename -> EpubImage already added to the book

    def _image_item(fname):
        if fname in added_images:
            return added_images[fname]
        if not images_dir:
            return None
        path = os.path.join(images_dir, fname)
        if not os.path.exists(path):
            return None
        media_type = mimetypes.guess_type(fname)[0] or "image/jpeg"
        with open(path, "rb") as f:
            item = epub.EpubImage(uid=f"img_{len(added_images)}", file_name=f"images/{fname}",
                                   media_type=media_type, content=f.read())
        book.add_item(item)
        added_images[fname] = item
        return item

    def _line_html(text):
        """One line's text as HTML fragments -- plain text as <p>...</p>,
        an [[IMG:filename]] placeholder as an <img> tag referencing the
        embedded image (once resolved), everything in its original
        left-to-right order within the line."""
        parts = []
        pos = 0
        for m in IMG_TOKEN_RE.finditer(text):
            before = text[pos:m.start()].strip()
            if before:
                parts.append(f"<p>{html.escape(before)}</p>")
            item = _image_item(m.group(1))
            if item:
                parts.append(f'<img src="{item.file_name}" alt=""/>')
            pos = m.end()
        tail = text[pos:].strip()
        if tail:
            parts.append(f"<p>{html.escape(tail)}</p>")
        return parts

    chapters = []
    for i in range(0, len(lines), lines_per_chapter):
        chunk = lines[i:i + lines_per_chapter]
        chapter_num = i // lines_per_chapter + 1
        html_parts = []
        for ln in chunk:
            text = getattr(ln, field, "")
            if text:
                html_parts.extend(_line_html(text))
        html_paragraphs = "\n".join(html_parts)
        c = epub.EpubHtml(title=f"Chapter {chapter_num}", file_name=f"chap_{chapter_num:03d}.xhtml",
                           lang=lang)
        c.content = f"<h1>Chapter {chapter_num}</h1>\n{html_paragraphs}"
        book.add_item(c)
        chapters.append(c)

    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    tmp_path = f"{out_path}.{os.getpid()}.tmp"
    try:
        epub.write_epub(tmp_path, book)
        os.replace(tmp_path, out_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return out_path
