"""
novel_text.py -- the raw source novel as text: chunking it into narration-sized
lines, finding paragraph ends, and using it as transcription context.
"""

import re


def chunk_novel_text(raw_text: str, max_chars: int = 200):
    """Splits novel prose into narration-sized chunks: paragraph-aware,
    falling back to sentence splits for long paragraphs. Used for the
    'novel narration' content mode where there's no source audio to
    align to -- these chunks become the Lines directly."""
    raw_text = raw_text.strip()
    paragraphs = [p.strip() for p in re.split(r"\n+", raw_text) if p.strip()]
    chunks = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            # ASCII . ! ? only end a sentence before whitespace, so "3.5" and "e.g." stay whole.
            sentences = re.split(r"(?<=[。！？…～])|(?<=[.!?])(?=\s)", para)
            buf = ""
            for s in sentences:
                if len(buf) + len(s) > max_chars and buf:
                    chunks.append(buf)
                    buf = s
                else:
                    buf += s
                # No sentence end to split on: wrap at the limit rather than emit an oversized line.
                while len(buf) > max_chars:
                    chunks.append(buf[:max_chars])
                    buf = buf[max_chars:]
            if buf:
                chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


def novel_paragraph_ends(lines, source_text: str):
    """Set of line idx whose line ends a paragraph of `source_text` -- the
    novel text the lines were chunked from (chunk_novel_text only ever
    splits a paragraph into pieces that join back into it). None when the
    lines no longer match the source (edited, merged or re-split since),
    rather than a guess that would put breaks in the wrong places."""
    paragraphs = [re.sub(r"\s+", "", p) for p in re.split(r"\n+", source_text or "") if p.strip()]
    ends, buf, p = set(), "", 0
    for ln in lines:
        piece = re.sub(r"\s+", "", ln.zh or "")
        if not piece:
            continue
        if p >= len(paragraphs):
            return None
        buf += piece
        if buf == paragraphs[p]:
            ends.add(ln.idx)
            buf, p = "", p + 1
        elif not paragraphs[p].startswith(buf):
            return None
    return ends if p == len(paragraphs) and not buf else None


# ---------------------------------------------------------------------------
# Using the raw source novel as transcription context
# ---------------------------------------------------------------------------

def extract_novel_excerpt_for_prompt(novel_text: str, max_chars: int = 800) -> str:
    """
    Pulls a usable excerpt from the raw novel for Whisper's initial_prompt.

    Whisper's initial_prompt has a real, hard limit -- only roughly the
    last ~224 tokens actually influence decoding, and anything before that
    is silently wasted. So this deliberately does NOT hand over a whole
    novel; it takes a bounded excerpt from early in the text, which is
    normally where the drama's episode 1 audio corresponds to.

    Kept separate from build_initial_prompt() (which lists proper nouns)
    so a caller can combine both: names first as the highest-value part,
    then a slice of real prose for phrasing and rhythm, trimmed to fit.
    """
    text = re.sub(r"\s+", " ", (novel_text or "").strip())
    if not text:
        return ""
    return text[:max_chars]


def combine_initial_prompt(name_prompt: str, novel_excerpt: str, max_chars: int = 900) -> str:
    """
    Merges the name-list prompt with a novel excerpt, names first since
    they're the highest-value part and must not get truncated away.
    """
    parts = [p for p in (name_prompt.strip(), novel_excerpt.strip()) if p]
    combined = "".join(parts)
    return combined[:max_chars]


_NOVEL_FALLBACK_ENCODINGS = ("utf-8-sig", "gb18030", "cp949", "big5", "cp932")
_KANA = re.compile(r"[\u3040-\u30ff]")


def decode_novel_bytes(data: bytes) -> str:
    """Novel text from a .txt/.md file in the encoding it was saved in. Chinese web
    novels are often GBK and Japanese ones Shift-JIS; decoding those as UTF-8 with
    errors ignored leaves a mostly empty text with no error."""
    for encoding in _NOVEL_FALLBACK_ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        # GB18030 accepts most Shift-JIS byte pairs and returns CJK noise, so text
        # that reads as Japanese kana under Shift-JIS is taken as Shift-JIS.
        if encoding != "utf-8-sig":
            try:
                japanese = data.decode("cp932")
            except UnicodeDecodeError:
                return text
            if len(_KANA.findall(japanese)) >= 0.1 * max(len(japanese), 1):
                return japanese
        return text
    raise ValueError("This text file is not in a text encoding Baihe can read "
                     "(tried UTF-8, GBK, Shift-JIS, Korean and Big5). Save it as UTF-8 and try again.")


def load_novel_text_for_context(file_bytes: bytes, filename: str) -> str:
    """
    Extracts plain text from an uploaded novel file for use as
    transcription/alignment context. Supports .txt/.md directly and
    .epub via epub_io (falls back to a clear error rather than silently
    returning nothing if ebooklib isn't installed).
    """
    name = (filename or "").lower()
    if name.endswith(".epub"):
        import tempfile
        import os as _os
        try:
            import epub_io
        except ImportError as exc:
            raise ImportError(
                "Reading .epub needs an extra package:\n    pip install ebooklib"
            ) from exc
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            return epub_io.import_epub_text(tmp_path)
        finally:
            _os.unlink(tmp_path)
    return decode_novel_bytes(file_bytes)
