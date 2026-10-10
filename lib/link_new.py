"""
lib/link_new.py -- give a finished file its final name without ever replacing
an existing file there.
"""

import contextlib
import os


def link_new(src: str, dest: str):
    """Gives `src` the name `dest` without ever replacing a file there
    (FileExistsError when one is there): a hard link, then src's name is
    removed. A file system without hard links (FAT/exFAT drives, some
    network shares) gets dest created exclusively first and src renamed
    over that empty placeholder, which only this call can have made."""
    try:
        os.link(src, dest)
    except FileExistsError:
        raise
    except (OSError, AttributeError, NotImplementedError):
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
        os.close(fd)
        try:
            os.replace(src, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(dest)
            raise
        return
    # The copy is in place under both names; a leftover partial name is
    # swept by cleanup_stale_leftovers.
    with contextlib.suppress(OSError):
        os.remove(src)
