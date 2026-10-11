"""A cross-process lock on a file, for work two Baihe processes must not do at once
(a model download that installs into one shared folder)."""
import contextlib
import os
import time

POLL_SECONDS = 0.2


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass  # closing the descriptor releases it either way


@contextlib.contextmanager
def hold(path: str, poll: float = POLL_SECONDS):
    """Holds an exclusive lock on `path` (created if missing), waiting for whoever has
    it. The operating system drops the lock when the holder's process dies, so a crash
    never leaves a stale one. Separate open() calls conflict even inside one process,
    so threads are covered too."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
    try:
        while not _try_lock(fd):
            time.sleep(poll)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
