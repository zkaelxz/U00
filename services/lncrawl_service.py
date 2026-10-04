"""
services/lncrawl_service.py -- optional import of a web novel through the
user-installed lightnovel-crawler program (Step 115b).

lightnovel-crawler (https://github.com/lncrawl/lightnovel-crawler) is
GPL-3.0-or-later. Baihe never imports, copies or ships any of its code: it
only runs the separately installed `lncrawl` program as its own process
(arm's-length use) and reads the EPUB that program writes. Nothing here
imports `lncrawl`, and detection looks for the program, not the module.

The program is found on PATH or at the PC-only Settings path `lncrawl_cmd`
(like `tesseract_cmd`); a configured path must name an existing file whose
name is lncrawl / lightnovel-crawler (no extension or .exe), so the setting
can't point at an arbitrary program or a batch file.

Safety of a run (the lncrawl 4.x CLI: `lncrawl crawl --noin -f epub
{--all | --first N | --last N} <url>`):
- the argv is a fixed list built here, never a shell; the only user values
  are the URL (checked: http/https, no userinfo, no control characters, not
  starting with "-", passed after "--") and a chapter count (an int);
- the URL is checked with `services.url_guard` first (private, loopback and
  link-local hosts refused). lncrawl does its own fetching and redirects,
  which Baihe's per-request pinning cannot cover; that is why the routes are
  local_only();
- lncrawl's data folder (LNCRAWL_DATA_PATH) is a fresh temp folder inside
  the drama folder, removed in `finally`; the child gets the environment
  minus anything that looks like a key, token or password;
- a timeout, a cap on the output it prints and on the size of its data
  folder; cancel or any cap kills the whole process tree (POSIX session;
  on Windows a Job Object, so its children die with it, plus taskkill /T);
- stopping the app cancels every running import and kills its tree
  (`shutdown`, from the API's lifespan and atexit); a data folder left
  behind by a crash is swept at the next start (`cleanup_stale_workdirs`);
- what it printed is redacted (keys, tokens, paths, URL queries) and only a
  short tail is ever shown or stored;
- the EPUB goes through `novel_attach_service`'s EPUB path with its size,
  entry-count and zip-bomb limits.
"""
import codecs
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from typing import Optional
from urllib.parse import urlsplit

import background_jobs
import db
from services import drama_service, novel_attach_service, settings_service, url_guard
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

JOB_PREFIX = "lncrawl_"
PROGRAM_NAMES = ("lncrawl", "lightnovel-crawler", "lightnovel_crawler")
# Never .cmd/.bat: Windows runs a batch file through cmd.exe, which would
# read "&", "|" or "%" in the pasted URL as shell syntax even with shell=False.
_PROGRAM_EXTENSIONS = ("", ".exe")
RANGES = ("all", "first", "last")
MAX_CHAPTER_COUNT = novel_attach_service.MAX_EPUB_ENTRIES
MAX_URL_LENGTH = 2000
TIMEOUT_SECONDS = 90 * 60
MAX_OUTPUT_BYTES = 20 * 1024 * 1024        # everything it prints, total
MAX_WORKDIR_BYTES = 1024 * 1024 * 1024     # its data folder: chapters, images, EPUB
MAX_EPUB_FILES_SCANNED = 20000
LOG_TAIL_LINES = 20
LOG_TAIL_CHARS = 480                       # jobs_service keeps result strings <= 500
_POLL_SECONDS = 0.5
_SIZE_CHECK_SECONDS = 5.0
_KILL_WAIT_SECONDS = 10.0
_REAP_JOIN_SECONDS = 2.0
# A work folder older than this can't belong to a live run (the timeout
# ends every run sooner), so the startup sweep may remove it.
STALE_WORKDIR_SECONDS = TIMEOUT_SECONDS + 30 * 60

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-_]")
_URL = re.compile(r"(https?://[^\s/?#'\"<>]+)[^\s'\"<>]*", re.IGNORECASE)
_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|COOKIE|SESSION|AUTH", re.IGNORECASE)


# --- detection -------------------------------------------------------------

def _is_program_name(path: str) -> bool:
    base = os.path.basename(path).lower()
    return any(base == name + ext for name in PROGRAM_NAMES for ext in _PROGRAM_EXTENSIONS)


def find_program() -> Optional[str]:
    """The lncrawl program to run, or None. The Settings path wins when set
    (and must be a real lncrawl file); otherwise PATH."""
    configured = settings_service.get_lncrawl_cmd()
    if configured:
        path = os.path.abspath(os.path.expanduser(configured))
        if _is_program_name(path) and os.path.isfile(path):
            return path
        return None
    # On Windows ask for the .exe by name, so an lncrawl.cmd earlier on PATH
    # can't hide it.
    names = [n + ".exe" for n in PROGRAM_NAMES] if os.name == "nt" else []
    for name in names + list(PROGRAM_NAMES):
        found = shutil.which(name)
        if found and _is_program_name(found):     # skips an lncrawl.cmd/.bat shim
            return found
    return None


def is_installed() -> bool:
    return find_program() is not None


def get_status() -> dict:
    """Booleans only: whether lncrawl was found, and whether a Settings path
    is set (a set but wrong path reads installed: false)."""
    return {"installed": is_installed(),
            "path_configured": bool(settings_service.get_lncrawl_cmd())}


# --- input checks and argv -------------------------------------------------

def check_url(url) -> str:
    """The stripped URL, or InvalidInputError. http/https only, a host, no
    userinfo, no whitespace/control characters, public host (url_guard)."""
    if not isinstance(url, str):
        raise InvalidInputError("Paste the novel's web address.")
    url = url.strip()
    if not url:
        raise InvalidInputError("Paste the novel's web address.")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidInputError("The address is too long.")
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise InvalidInputError("The address contains characters that are not allowed.")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise InvalidInputError(url_guard.BAD_URL) from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise InvalidInputError(url_guard.BAD_URL)
    try:
        url_guard.resolve_public(url)
    except url_guard.URLResolveError:
        raise InvalidInputError(url_guard.RESOLVE_FAILED) from None
    except url_guard.UnsafeURLError as e:
        raise InvalidInputError(str(e)) from None
    return url


def check_range(chapters, count):
    """(range, count): "all" takes no count; "first"/"last" need 1..MAX."""
    if chapters not in RANGES:
        raise InvalidInputError(f"chapters must be one of {', '.join(RANGES)}.")
    if chapters == "all":
        return chapters, None
    if isinstance(count, bool) or not isinstance(count, int) \
            or not 1 <= count <= MAX_CHAPTER_COUNT:
        raise InvalidInputError(f"Pick how many chapters, from 1 to {MAX_CHAPTER_COUNT}.")
    return chapters, count


def build_argv(program: str, url: str, chapters: str = "all", count: Optional[int] = None) -> list:
    """The fixed argument list. Only `url` and `count` come from the user;
    both are checked here again, and the URL goes after "--" so it can
    never be read as an option."""
    chapters, count = check_range(chapters, count)
    if not isinstance(url, str) or not url or url.startswith("-") \
            or urlsplit(url).scheme.lower() not in ("http", "https"):
        raise InvalidInputError(url_guard.BAD_URL)
    argv = [program, "crawl", "--noin", "--format", "epub"]
    argv += ["--all"] if chapters == "all" else [f"--{chapters}", str(count)]
    return argv + ["--", url]


def child_env(data_dir: str) -> dict:
    """The parent's environment minus anything secret-looking (and Baihe's
    own settings), with lncrawl pointed at the temp data folder."""
    env = {k: v for k, v in os.environ.items()
           if not _SECRET_ENV.search(k) and not k.upper().startswith(("BAIHE_", "LNCRAWL_"))
           and k.upper() != "DATABASE_URL"}
    # LNCRAWL_CONFIG / DATABASE_URL would point lncrawl at a config or
    # database outside the temp folder (lncrawl 4.x config.py).
    env.update({"LNCRAWL_DATA_PATH": data_dir, "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1", "NO_COLOR": "1", "TERM": "dumb"})
    return env


def redact_output(text: str, hide=()) -> str:
    """Printable, secret-free text: ANSI codes removed, keys/tokens
    redacted, the given paths (temp folder, program) and the home folder
    replaced, and every URL cut to scheme and host."""
    from translate_engines import redact_secrets
    text = _ANSI.sub("", text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)
    home = os.path.expanduser("~")
    for path, label in sorted(((p, lab) for p, lab in hide if p), key=lambda x: -len(x[0])):
        text = text.replace(path, label)
    if home and len(home) > 1:
        text = text.replace(home, "~")
    text = _URL.sub(lambda m: m.group(1), text)
    return redact_secrets(text)


def output_tail(text: str) -> str:
    lines = [ln.rstrip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines[-LOG_TAIL_LINES:])[-LOG_TAIL_CHARS:]


# --- the job ---------------------------------------------------------------

def job_id_for(drama_id: int) -> str:
    return f"{JOB_PREFIX}{drama_id}"


def start_import(drama_id: int, url, chapters: str = "all", count: Optional[int] = None,
                 mode: str = "replace") -> dict:
    """Checks everything, then starts the background job. Returns {job_id}."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if mode not in ("append", "replace"):
        raise InvalidInputError("mode must be one of append, replace.")
    chapters, count = check_range(chapters, count)
    program = find_program()
    if program is None:
        raise DependencyUnavailableError(
            "lightnovel-crawler is not installed. Install it yourself (see Diagnostics), "
            "or set its program path in Settings.")
    url = check_url(url)
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A job is running for this drama. Wait for it to finish or cancel it.")
    job_id = job_id_for(drama_id)
    started = background_jobs.start_job(
        job_id, _run_job, job_id, drama_id, program, url, chapters, count, mode,
        description=f"Import with lightnovel-crawler (drama {drama_id})")
    if not started:
        raise ConflictError("A lightnovel-crawler import is already running for this drama.")
    return {"job_id": job_id}


class _OutputReader:
    """Reads the child's merged stdout/stderr in a thread: keeps a bounded
    tail and counts the total, flagging when it passes MAX_OUTPUT_BYTES."""

    def __init__(self, stream):
        self.total = 0
        self.overflow = False
        self._tail = deque(maxlen=64)
        self._stream = stream
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.thread = threading.Thread(target=self._read, daemon=True, name="lncrawl-output")
        self.thread.start()

    def _read(self):
        try:
            while True:
                chunk = self._stream.read1(8192) if hasattr(self._stream, "read1") \
                    else self._stream.read(8192)
                if not chunk:
                    break
                self.total += len(chunk)
                if self.total > MAX_OUTPUT_BYTES:
                    self.overflow = True
                    break
                self._tail.append(self._decoder.decode(chunk))
        except (OSError, ValueError):
            pass

    def text(self) -> str:
        return "".join(self._tail)[-8 * LOG_TAIL_CHARS:]


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _find_epub(work: str) -> Optional[str]:
    """The newest regular *.epub file inside `work` (no symlinks, nothing
    that resolves outside it)."""
    real_work = os.path.realpath(work)
    best, best_mtime, seen = None, -1.0, 0
    for root, dirs, files in os.walk(work, followlinks=False):
        for name in files:
            seen += 1
            if seen > MAX_EPUB_FILES_SCANNED:
                return best
            if not name.lower().endswith(".epub"):
                continue
            path = os.path.join(root, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            if os.path.commonpath([real_work, os.path.realpath(path)]) != real_work:
                continue
            mtime = os.path.getmtime(path)
            if mtime > best_mtime:
                best, best_mtime = path, mtime
    return best


def _fail(job_id: str, message: str, output: str = "") -> RuntimeError:
    """The job's error (one short line, so the UI can show it) and, in its
    result, the redacted tail of what lncrawl printed."""
    tail = output_tail(output)
    background_jobs.set_result(job_id, {"failed_reason": "lncrawl",
                                        **({"detail": tail} if tail else {})})
    return RuntimeError(message)


# --- running processes: kill helpers, the Windows Job Object, shutdown -----

_active = {}                 # job_id -> (Popen, Windows job handle or None)
_active_lock = threading.Lock()
_atexit_registered = False


def _windows_job(proc):
    """A Windows Job Object holding `proc` (and so every process it starts
    afterwards), set to kill them all when the job is closed or terminated.
    None elsewhere or on any failure (taskkill /T still applies). It nests
    inside the server's own job (process_guard), so a cancel can end just
    this tree."""
    if os.name != "nt":
        return None
    import process_guard
    return process_guard.create_kill_on_close_job(int(proc._handle))


def _windows_job_call(name: str, job, *args):
    try:
        import ctypes
        from ctypes import wintypes
        fn = getattr(ctypes.WinDLL("kernel32", use_last_error=True), name)
        fn.argtypes = (wintypes.HANDLE,) + tuple(wintypes.UINT for _ in args)
        fn(job, *args)
    except Exception:
        pass


def _kill_running(proc, job):
    """lncrawl is still running (cancel, a cap, the timeout, shutdown)."""
    if job is not None:
        _windows_job_call("TerminateJobObject", job, 1)
    background_jobs.kill_tree(proc)


def _kill_leftovers(proc, job):
    """lncrawl has exited but something it started still holds the output
    pipe. The Job Object ends them on Windows. On POSIX the process group
    is killed: its id can't have been reused while a member (the pipe
    holder) is alive. Never taskkill by PID here: lncrawl's own PID may
    already belong to another process."""
    if job is not None:
        _windows_job_call("TerminateJobObject", job, 1)
    elif os.name != "nt":
        background_jobs.kill_tree(proc)


def _register(job_id, proc, job):
    global _atexit_registered
    with _active_lock:
        _active[job_id] = (proc, job)
        if not _atexit_registered:
            import atexit
            atexit.register(shutdown)
            _atexit_registered = True


def _unregister(job_id):
    with _active_lock:
        _active.pop(job_id, None)


def running_imports() -> int:
    with _active_lock:
        return len(_active)


def shutdown(wait: float = _KILL_WAIT_SECONDS) -> int:
    """App shutdown: cancels every lncrawl import running in this process
    and kills its process tree, then waits (bounded) for the jobs to clean
    up their work folders. Returns how many were stopped; never raises."""
    with _active_lock:
        items = list(_active.items())
    for job_id, (proc, job) in items:
        try:
            background_jobs.request_cancel(job_id)
            if proc.poll() is None:
                _kill_running(proc, job)
        except Exception:
            pass
    deadline = time.monotonic() + wait
    while items and running_imports() and time.monotonic() < deadline:
        time.sleep(0.05)
    return len(items)


def cleanup_stale_workdirs(max_age: float = STALE_WORKDIR_SECONDS, now: float = None) -> int:
    """Startup sweep: removes `.lncrawl_*` work folders in drama folders that
    no run in this process owns and that are older than max_age (longer than
    any run may last), e.g. after a crash or a Windows grandchild that kept
    a file open. Symlinks are left alone. Returns how many were removed;
    never raises."""
    now = time.time() if now is None else now
    removed = 0
    try:
        dramas = os.listdir(db.DRAMAS_DIR)
    except OSError:
        return 0
    for drama in dramas:
        base = os.path.join(db.DRAMAS_DIR, drama)
        try:
            if os.path.islink(base) or not os.path.isdir(base):
                continue
            if drama.isdigit() and background_jobs.is_running(job_id_for(int(drama))):
                continue
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if not name.startswith(".lncrawl_"):
                continue
            path = os.path.join(base, name)
            try:
                if os.path.islink(path) or not os.path.isdir(path):
                    continue
                if now - os.lstat(path).st_mtime < max_age:
                    continue
                shutil.rmtree(path)
                removed += 1
            except OSError:
                pass
    return removed


def _run_process(job_id: str, argv: list, work: str) -> str:
    """Runs lncrawl; returns its (redacted) output. Raises JobCancelled on
    cancel and RuntimeError on a timeout, a cap, or a non-zero exit."""
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    proc = subprocess.Popen(argv, cwd=work, env=child_env(work), shell=False,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **group)
    job = _windows_job(proc)
    _register(job_id, proc, job)
    reader = _OutputReader(proc.stdout)
    started = time.monotonic()
    next_size_check = started + _SIZE_CHECK_SECONDS
    stop_reason = None
    try:
        while proc.poll() is None:
            now = time.monotonic()
            if background_jobs.is_cancel_requested(job_id):
                stop_reason = "cancel"
            elif now - started >= TIMEOUT_SECONDS:
                stop_reason = "timeout"
            elif reader.overflow:
                stop_reason = "output"
            elif now >= next_size_check:
                next_size_check = now + _SIZE_CHECK_SECONDS
                if _dir_size(work) > MAX_WORKDIR_BYTES:
                    stop_reason = "size"
            if stop_reason:
                break
            minutes = int((now - started) // 60)
            background_jobs.update_progress(
                job_id, 0.1, f"lightnovel-crawler is downloading... ({minutes} min)")
            time.sleep(_POLL_SECONDS)
        if stop_reason is None and background_jobs.is_cancel_requested(job_id):
            stop_reason = "cancel"     # killed from outside (app shutdown) after a cancel
    finally:
        try:
            if proc.poll() is None:
                _kill_running(proc, job)
                try:
                    proc.wait(timeout=_KILL_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
            reader.thread.join(timeout=_REAP_JOIN_SECONDS)
            if reader.thread.is_alive():
                _kill_leftovers(proc, job)
                reader.thread.join(timeout=_KILL_WAIT_SECONDS)
            if not reader.thread.is_alive():
                # close() would block on the reader's pending read otherwise
                # (a survivor still holding the pipe); then the daemon thread
                # and the pipe are left to the process instead.
                try:
                    proc.stdout.close()
                except Exception:
                    pass
            if job is not None:
                _windows_job_call("CloseHandle", job)    # KILL_ON_JOB_CLOSE
        finally:
            _unregister(job_id)
    output = redact_output(reader.text(), hide=((work, "<work folder>"), (argv[0], "lncrawl")))
    if stop_reason == "cancel":
        raise background_jobs.JobCancelled(job_id)
    if stop_reason == "timeout":
        raise _fail(job_id, f"lightnovel-crawler did not finish within {TIMEOUT_SECONDS // 60} minutes "
                    "and was stopped.", output)
    if stop_reason == "output" or reader.overflow:
        raise _fail(job_id, "lightnovel-crawler printed too much output and was stopped.", output)
    if stop_reason == "size":
        raise _fail(job_id, "lightnovel-crawler's download grew past "
                    f"{MAX_WORKDIR_BYTES // (1024 * 1024)} MB and was stopped.", output)
    if proc.returncode != 0:
        raise _fail(job_id, f"lightnovel-crawler stopped with exit code {proc.returncode}.", output)
    return output


def _run_job(job_id, drama_id, program, url, chapters, count, mode):
    work = None
    try:
        background_jobs.update_progress(job_id, 0.05, "Starting lightnovel-crawler...")
        work = tempfile.mkdtemp(prefix=".lncrawl_", dir=db.drama_dir(drama_id))
        output = _run_process(job_id, build_argv(program, url, chapters, count), work)
        epub = _find_epub(work)
        if epub is None:
            raise _fail(job_id, "lightnovel-crawler finished but made no EPUB. Check that it supports "
                        "this site and the address is the novel's main page.", output)
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        background_jobs.update_progress(job_id, 0.9, "Attaching the EPUB text...")
        with open(epub, "rb") as f:
            result = novel_attach_service.attach_epub_from_job(drama_id, f, mode)
        background_jobs.set_result(job_id, {"char_count": result["char_count"],
                                            "epub_chapters": result["epub_chapters"]})
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)
