"""Write a file so a crash or a failed write never leaves it truncated."""
import errno
import os
import tempfile
import time

# Windows refuses a replace while another handle has the target open (another
# thread's replace of the same file, a reader, an antivirus scan of the file
# just written) with PermissionError, winerror 5 or 32, until that handle
# closes. A POSIX rename has no such state, so a PermissionError there is real.
_ON_WINDOWS = os.name == "nt"
_HELD_WINERRORS = {5, 32}
_REPLACE_ATTEMPTS = 10
_REPLACE_DELAY = 0.02
# How a filesystem says it cannot fsync at all (some network shares and FUSE
# mounts). On Windows os.fsync goes through the CRT's _commit, which reports a
# share or sync drive refusing FlushFileBuffers as EBADF with no winerror; the
# descriptor was opened just before, so EBADF cannot mean a bad one there.
# Anything else, such as EIO or ENOSPC, means the data may not be on disk.
_NO_FSYNC_ERRNOS = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
_NO_FSYNC_WINDOWS_ERRNOS = {errno.EBADF}
_NO_FSYNC_WINERRORS = {1}


def replace_file(src: str, dst: str) -> None:
    """os.replace(src, dst), waiting up to about a second while Windows
    reports `dst` as held by another handle. The last error is raised when it
    stays held, and every other error at once."""
    _while_held(os.replace, src, dst)


def remove_file(path: str) -> None:
    """os.remove(path), waiting up to about a second while Windows reports it
    as held (a killed process that has not closed it yet). Raises as
    replace_file does."""
    _while_held(os.remove, path)


def _while_held(action, *paths) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            action(*paths)
            return
        except PermissionError as exc:
            held = _ON_WINDOWS and getattr(exc, "winerror", None) in _HELD_WINERRORS
            if not held or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY * (attempt + 1))


def _flush_to_disk(f) -> None:
    # Without this, NTFS (and ext4 with delayed allocation) can keep the
    # rename after a power cut and lose the data, leaving an empty file where
    # the old one was. A filesystem that cannot fsync still gets the rename;
    # a failed fsync must not replace a good file with one that may be lost.
    try:
        f.flush()
        os.fsync(f.fileno())
    except OSError as e:
        unsupported = (e.errno in _NO_FSYNC_ERRNOS
                       or (_ON_WINDOWS and (e.errno in _NO_FSYNC_WINDOWS_ERRNOS
                                            or getattr(e, "winerror", None) in _NO_FSYNC_WINERRORS)))
        if not unsupported:
            raise


def atomic_write(path: str, data, binary: bool = False, *, prefix: str = "", suffix: str = ".tmp") -> None:
    """Writes `data` to a temp file in path's folder, then os.replace()s it
    over `path`, so a crash or power cut mid-write never leaves a truncated
    file (the temp file is fsynced before the rename). The temp
    name is unique per call, so two writers never share one, and it is removed
    again when the write fails. `prefix`/`suffix` let a caller keep a temp name
    the "Leftover temp files" cleanup (storage.py, any `*.tmp` in a title's
    folder) does not match, so it cannot delete the file mid-save."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=prefix, suffix=suffix)
    try:
        with (os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding="utf-8")) as f:
            f.write(data)
            _flush_to_disk(f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
