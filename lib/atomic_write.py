"""Write a file so a crash or a failed write never leaves it truncated."""
import os
import tempfile


def atomic_write(path: str, data, binary: bool = False, *, prefix: str = "", suffix: str = ".tmp") -> None:
    """Writes `data` to a temp file in path's folder, then os.replace()s it
    over `path`, so a crash mid-write never leaves a truncated file. The temp
    name is unique per call, so two writers never share one, and it is removed
    again when the write fails. `prefix`/`suffix` let a caller keep a temp name
    the "Leftover temp files" cleanup (storage.py, any `*.tmp` in a title's
    folder) does not match, so it cannot delete the file mid-save."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=prefix, suffix=suffix)
    try:
        with os.fdopen(fd, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
