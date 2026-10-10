"""
lib/gzip_text.py -- gzip for stored text that must stay readable in both
forms: a value written before compression existed is plain text, a newer one
may be gzip bytes, and every reader goes through `unpack` so it never has to
know which one it got.
"""

import gzip
import zlib

GZIP_MAGIC = b"\x1f\x8b"
# Far above any real payload (a long title's line snapshot is ~10 MB), low
# enough that a crafted value from an imported backup can't fill memory.
MAX_UNPACKED_BYTES = 512 * 1024 * 1024


def pack_bytes(data: bytes) -> bytes:
    """mtime=0 so the same input always gives the same bytes."""
    return gzip.compress(data, compresslevel=6, mtime=0)


def pack(text: str) -> bytes:
    return pack_bytes(text.encode("utf-8"))


def is_packed(value) -> bool:
    return isinstance(value, (bytes, bytearray, memoryview)) and bytes(value[:2]) == GZIP_MAGIC


def gunzip_bytes(data: bytes, max_bytes: int = MAX_UNPACKED_BYTES) -> bytes:
    """Raises ValueError for damaged data or data that expands past max_bytes."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(bytes(data), max_bytes + 1)
    except zlib.error as exc:
        raise ValueError("damaged gzip data") from exc
    if len(out) > max_bytes or d.unconsumed_tail:
        raise ValueError("gzip data expands past the limit")
    if not d.eof:
        raise ValueError("truncated gzip data")
    return out


def unpack(value, max_bytes: int = MAX_UNPACKED_BYTES):
    """The text of a stored value: plain text as is, gzip bytes inflated,
    other bytes decoded as UTF-8; None stays None."""
    if value is None or isinstance(value, str):
        return value
    if is_packed(value):
        return gunzip_bytes(value, max_bytes).decode("utf-8")
    return bytes(value).decode("utf-8")
