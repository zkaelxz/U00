"""
epub_io.py -- EPUB import/export for novel-narration mode. Read
chapter text out of an .epub you already own, and export a finished
translation back as a proper .epub for e-reader apps.

Requires `pip install ebooklib beautifulsoup4`.
"""


def import_epub_text(epub_path: str, chapter_range: tuple = None) -> str:
    """Extracts plain text from an EPUB's chapters, in reading order,
    joined with blank lines between chapters. chapter_range: optional
    (start, end) 0-indexed chapter slice, for importing just part of a
    long book instead of the whole thing at once."""
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
        text = soup.get_text(separator="\n").strip()
        if text:
            texts.append(text)
    return "\n\n".join(texts)


def get_epub_chapter_count(epub_path: str) -> int:
    """Quick chapter count without extracting text, for showing a
    range picker before committing to importing the whole book."""
    import ebooklib
    from ebooklib import epub
    book = epub.read_epub(epub_path)
    return len([item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT])


def export_epub(lines, title: str, author: str, out_path: str, field: str = "en",
                 lines_per_chapter: int = 200):
    """Exports translated (or original) lines as a proper .epub, split
    into chapters of `lines_per_chapter` lines each so long novels
    don't become one giant unreadable chapter. field: 'en' for the
    translation, 'zh' for the raw text (e.g. exporting a bilingual
    reading copy would need two calls or a custom merge)."""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier(f"baihe-subtitler-{title}")
    book.set_title(title)
    book.set_language("en" if field == "en" else "zh")
    if author:
        book.add_author(author)

    chapters = []
    for i in range(0, len(lines), lines_per_chapter):
        chunk = lines[i:i + lines_per_chapter]
        chapter_num = i // lines_per_chapter + 1
        html_paragraphs = "".join(f"<p>{getattr(ln, field, '')}</p>\n" for ln in chunk if getattr(ln, field, ""))
        c = epub.EpubHtml(title=f"Chapter {chapter_num}", file_name=f"chap_{chapter_num:03d}.xhtml",
                           lang="en" if field == "en" else "zh")
        c.content = f"<h1>Chapter {chapter_num}</h1>\n{html_paragraphs}"
        book.add_item(c)
        chapters.append(c)

    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    epub.write_epub(out_path, book)
    return out_path
