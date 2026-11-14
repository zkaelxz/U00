"""Write a file so a crash or a failed write never leaves it truncated."""
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


def replace_file(src: str, dst: str) -> None:
    """os.replace(src, dst), waiting up to about a second while Windows
    reports `dst` as held by another handle. The last error is raised when it
    stays held, and every other error at once."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            held = _ON_WINDOWS and getattr(exc, "winerror", None) in _HELD_WINERRORS
            if not held or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY * (attempt + 1))


def atomic_write(path: str, data, binary: bool = False, *, prefix: str = "", suffix: str = ".tmp") -> None:
    """Writes `data` to a temp file in path's folder, then os.replace()s it
    over `path`, so a crash mid-write never leaves a truncated file. The temp
    name is unique per call, so two writers never share one, and it is removed
    again when the write fails. `prefix`/`suffix` let a caller keep a temp name
    the "Leftover temp files" cleanup (storage.py, any `*.tmp` in a title's
    folder) does not match, so it cannot delete the file mid-save."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=prefix, suffix=suffix)
    try:
        with (os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding="utf-8")) as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
