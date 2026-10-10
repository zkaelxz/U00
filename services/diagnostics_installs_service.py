"""
services/diagnostics_installs_service.py -- the long-running Diagnostics
actions the React page runs as background jobs:

- Q06 "Install" for an optional package and the GPU PyTorch setup: pip runs
  for minutes (a CUDA torch is ~2.5 GB), so each is a job that holds the
  library exclusively while it runs (no other job, restore or cleanup can
  start) and can be cancelled: Cancel kills pip's whole process tree and
  releases the hold. The package name and torch variant come from the
  service's whitelists, never a URL or command from the request.

- Q02 "Install Deno": the JavaScript runtime yt-dlp needs for YouTube (and
  other sites') formats. A system tool, not a pip package. On Windows with
  winget it runs `winget install -e --id DenoLand.Deno` (a fixed command;
  winget puts it on PATH); otherwise it downloads Deno's official release
  zip for this OS/CPU from a static table below (never a URL from the
  request), follows at most MAX_REDIRECTS hops, each https and on an
  allowlisted host, with timeout= on every request and a byte cap, checks
  it against the release's own .sha256sum file and unpacks only the deno
  binary into ~/.deno/bin, where Deno's own installer puts it.
- Q06 "Test first": diagnostics.check_upgrade_candidate for one package's
  update target (a throwaway environment plus this app's test suite, so
  minutes, not seconds). The version comes only from the last "Check for
  updates" (the same cached target the Upgrade route installs).

Both start through the install guard of diagnostics_gaps_service
(confirm=true; 409 while any job, restore, reset, cleanup or install is in
progress) and run as ordinary background_jobs jobs, so they show in the
running-jobs list and an install can't start under them. Their progress
and final result are read back from GET routes here (nothing is returned
as a path; every output line goes through diagnostics.redact_for_support).
"""

import contextlib
import hashlib
import os
import platform
import re
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from urllib.parse import urljoin, urlsplit

import background_jobs
import db
import diagnostics
from lib import proc as proc_run
from services import diagnostics_gaps_service as gaps
from services import drama_service
from services.service_errors import ConflictError

DENO_JOB_ID = "deno_install"
UPGRADE_CHECK_JOB_ID = "upgrade_check"
DEPENDENCY_JOB_ID = "dependency_install"
_OUTPUT_TAIL = 40

# Static download table: (OS, CPU) -> the official release asset. Only
# these exact URLs are ever requested (plus "<asset>.sha256sum" next to
# the versioned one they redirect to).
_DENO_RELEASES = "https://github.com/denoland/deno/releases/latest/download/"
DENO_DOWNLOADS = {
    ("Windows", "x86_64"): _DENO_RELEASES + "deno-x86_64-pc-windows-msvc.zip",
    ("Linux", "x86_64"): _DENO_RELEASES + "deno-x86_64-unknown-linux-gnu.zip",
    ("Linux", "aarch64"): _DENO_RELEASES + "deno-aarch64-unknown-linux-gnu.zip",
    ("Darwin", "x86_64"): _DENO_RELEASES + "deno-x86_64-apple-darwin.zip",
    ("Darwin", "aarch64"): _DENO_RELEASES + "deno-aarch64-apple-darwin.zip",
}
# Hosts a hop may land on: GitHub's release pages and its asset storage.
DENO_ALLOWED_HOSTS = frozenset({"github.com", "objects.githubusercontent.com",
                                "release-assets.githubusercontent.com"})
_VERSIONED_PATH = re.compile(r"^/denoland/deno/releases/download/(v[0-9][0-9A-Za-z.+-]*)/"
                             r"(deno-[a-z0-9_-]+\.zip)$")
MAX_REDIRECTS = 5
DENO_MAX_BYTES = 250 * 1024 * 1024          # the zip is ~45 MB
CHECKSUM_MAX_BYTES = 4096
REQUEST_TIMEOUT = (15, 60)                  # connect, per-read (seconds)
DOWNLOAD_DEADLINE_SECONDS = 30 * 60
WINGET_TIMEOUT_SECONDS = 20 * 60
_WINGET_CMD = ["winget", "install", "-e", "--id", "DenoLand.Deno",
               "--accept-package-agreements", "--accept-source-agreements",
               "--disable-interactivity"]
_CHUNK = 65536
_SHA256 = re.compile(r"\b([0-9A-Fa-f]{64})\b")
_CPU = {"amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64",
        "arm64": "aarch64", "aarch64": "aarch64"}

_STATE_LOCK = threading.Lock()
_DENO_RESULT = {"last": None}
_UPGRADE_CHECK = {"package": None, "target": None, "tail": [], "last": None}
_DEPENDENCY = {"kind": None, "package": None, "last": None}


class DenoInstallFailed(Exception):
    """A fixed, plain-English reason; never carries a URL or path."""


class AlreadyInstalled(gaps.AdminActionRefused, ConflictError):
    pass


class DependencyInstallFailed(Exception):
    """Ends the install job as an error; the details are in the stored result."""


def _redact(text) -> str:
    return diagnostics.redact_for_support("" if text is None else str(text))


def _job_view(job_id: str):
    job = background_jobs.get_status(job_id)
    if not job:
        return None
    return {"status": job.get("status"), "progress": float(job.get("progress") or 0.0),
            "message": _redact(job.get("message") or "")[:300],
            "error": _redact(job.get("error"))[:300] if job.get("error") else None}


# ---------------------------------------------------------------------------
# Q02: Deno
# ---------------------------------------------------------------------------

def _winget_links_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or ""
    return os.path.join(base, "Microsoft", "WinGet", "Links", "deno.exe") if base else ""


def _deno_installed_somewhere() -> bool:
    if shutil.which("deno"):
        return True
    link = _winget_links_path() if platform.system() == "Windows" else ""
    return (os.path.exists(diagnostics.deno_default_install_path())
            or bool(link and os.path.exists(link)))


def _deno_download_url():
    cpu = _CPU.get((platform.machine() or "").lower())
    return DENO_DOWNLOADS.get((platform.system(), cpu))


def _use_winget() -> bool:
    return platform.system() == "Windows" and bool(shutil.which("winget"))


def get_deno_status() -> dict:
    """What the Diagnostics JS-runtime row needs: the runtime yt-dlp would
    find, whether Deno is on PATH or only installed (needs a restart),
    whether this machine can install it from here, and the install job."""
    rt = diagnostics.check_js_runtime()
    with _STATE_LOCK:
        last = dict(_DENO_RESULT["last"]) if _DENO_RESULT["last"] else None
    return {"runtime_found": bool(rt["found"]), "runtime_name": rt["name"],
            "deno_on_path": bool(shutil.which("deno")),
            "deno_installed": _deno_installed_somewhere(),
            "can_install": _use_winget() or _deno_download_url() is not None,
            "install_method": "winget" if _use_winget() else "download",
            "job_id": DENO_JOB_ID, "job": _job_view(DENO_JOB_ID), "last_result": last}


def start_deno_install(confirm: bool = False) -> dict:
    """PC only (the route is local_only()). 409 while any job, restore,
    reset, cleanup or install runs, or when Deno is already installed (on
    PATH, or in ~/.deno/bin or winget's links folder though not on PATH:
    an existing binary is never replaced); 422 unconfirmed or on an OS/CPU
    with neither winget nor a table row."""
    gaps.guard(confirm)
    if _deno_installed_somewhere():
        raise AlreadyInstalled("Deno is already installed.")
    if not _use_winget() and _deno_download_url() is None:
        raise gaps.AdminActionNotPossible(
            "There is no Deno download for this system here; install Deno yourself.")
    if not background_jobs.start_job(DENO_JOB_ID, _deno_job, description="Installing Deno"):
        raise gaps.AdminActionJobsRunning(
            "A Deno install or a library restore is already running; try again when it ends.")
    return {"job_id": DENO_JOB_ID, "started": True}


def _finish_deno(ok: bool, message: str, tail: list, trust_exit_code: bool = False):
    """winget's exit code is trusted (it installs where it likes); a
    download counts only once the binary is where the check looks."""
    on_path = bool(shutil.which("deno"))
    installed = ok and (trust_exit_code or _deno_installed_somewhere())
    result = {"ok": installed, "on_path": on_path, "needs_restart": installed and not on_path,
              "message": _redact(message)[:300], "output_tail": tail[-_OUTPUT_TAIL:]}
    with _STATE_LOCK:
        _DENO_RESULT["last"] = result
    background_jobs.set_result(DENO_JOB_ID, {"status": "ok" if installed else "failed",
                                             "detail": result["message"]})
    if not installed:
        raise DenoInstallFailed(result["message"])


def _deno_job():
    tail, winget = [], _use_winget()

    def say(frac, line):
        tail.append(_redact(line)[:300])
        del tail[:-_OUTPUT_TAIL]
        background_jobs.update_progress(DENO_JOB_ID, frac, _redact(line)[:200])
    try:
        if winget:
            say(0.05, "Installing Deno with winget...")
            ok = _run_winget(say)
            message = "Deno installed." if ok else "winget could not install Deno; see the output."
        else:
            _download_and_unpack(say)
            ok, message = True, "Deno installed."
    except background_jobs.JobCancelled:
        with _STATE_LOCK:
            _DENO_RESULT["last"] = {"ok": False, "on_path": False, "needs_restart": False,
                                    "message": "Cancelled.", "output_tail": tail}
        raise
    except DenoInstallFailed as exc:
        ok, message = False, str(exc)
    except Exception:      # noqa: BLE001 -- never echo an exception (URLs, paths)
        ok, message = False, "The Deno install failed."
    if ok and not shutil.which("deno"):
        message = ("Deno was installed. Restart Baihe (and its terminal) so it is found on "
                   "PATH; on Linux or macOS, add .deno/bin in your home folder to PATH.")
    _finish_deno(ok, message, tail, trust_exit_code=winget)


def _run_winget(say) -> bool:
    returncode, timed_out = None, False
    for item in proc_run.stream_tree(
            list(_WINGET_CMD), WINGET_TIMEOUT_SECONDS,
            cancel=lambda: background_jobs.is_cancel_requested(DENO_JOB_ID)):
        if "line" in item:
            if item["line"].strip():
                say(0.5, item["line"])
        elif item.get("cancelled"):
            raise background_jobs.JobCancelled()
        else:
            returncode, timed_out = item.get("returncode"), item.get("timed_out")
    if timed_out:
        say(0.95, "(stopped: winget took too long)")
    return returncode == 0 and not timed_out


def _check_hop(url: str):
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in DENO_ALLOWED_HOSTS \
            or parts.port not in (None, 443) or parts.username or parts.password:
        raise DenoInstallFailed("The Deno download was redirected somewhere unexpected.")


def _get(url: str):
    import requests
    _check_hop(url)
    return requests.get(url, stream=True, timeout=REQUEST_TIMEOUT, allow_redirects=False,
                        headers={"User-Agent": "Baihe-Subtitler (Deno setup)"})


def _resolve_latest(url: str):
    """The versioned github.com URL `latest/download/<asset>` points at, so
    the zip and its checksum come from the same release."""
    resp = _get(url)
    try:
        if resp.status_code not in (301, 302, 303, 307, 308):
            raise DenoInstallFailed("Could not find the latest Deno release.")
        target = urljoin(url, resp.headers.get("Location") or "")
    finally:
        resp.close()
    _check_hop(target)
    m = _VERSIONED_PATH.match(urlsplit(target).path)
    if urlsplit(target).hostname != "github.com" or not m \
            or m.group(2) != url.rsplit("/", 1)[1]:
        raise DenoInstallFailed("Could not find the latest Deno release.")
    return target, m.group(1)


def _fetch(url: str, out, max_bytes: int, on_chunk=None):
    """Follows at most MAX_REDIRECTS allowlisted https hops, then streams
    the body into `out`, stopping past `max_bytes` or the overall deadline."""
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
    for _ in range(MAX_REDIRECTS + 1):
        resp = _get(url)
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, resp.headers.get("Location") or "")
                continue
            if resp.status_code != 200:
                raise DenoInstallFailed(f"The Deno download failed (HTTP {resp.status_code}).")
            total = int(resp.headers.get("Content-Length") or 0)
            if total > max_bytes:
                raise DenoInstallFailed("The Deno download is larger than expected.")
            got = 0
            for chunk in resp.iter_content(_CHUNK):
                got += len(chunk)
                if got > max_bytes:
                    raise DenoInstallFailed("The Deno download is larger than expected.")
                if time.monotonic() > deadline:
                    raise DenoInstallFailed("The Deno download took too long.")
                out.write(chunk)
                if on_chunk:
                    on_chunk(got, total)
            return got
        finally:
            resp.close()
    raise DenoInstallFailed("The Deno download was redirected too many times.")


def _download_and_unpack(say):
    import io
    url = _deno_download_url()
    say(0.05, "Finding the latest Deno release...")
    versioned, version = _resolve_latest(url)
    say(0.1, f"Downloading Deno {version}...")
    checksum = io.BytesIO()
    _fetch(versioned + ".sha256sum", checksum, CHECKSUM_MAX_BYTES)
    m = _SHA256.search(checksum.getvalue().decode("utf-8", "replace"))
    if not m:
        raise DenoInstallFailed("The Deno release has no readable checksum; nothing was installed.")
    expected = m.group(1).lower()

    last = {"pct": -1}

    def progress(got, total):
        if background_jobs.is_cancel_requested(DENO_JOB_ID):
            raise background_jobs.JobCancelled()
        if total:
            pct = int(got * 100 / total)
            if pct // 5 != last["pct"] // 5:
                last["pct"] = pct
                background_jobs.update_progress(DENO_JOB_ID, 0.1 + 0.8 * got / total,
                                                f"Downloading Deno {version}... {pct}%")

    work = tempfile.mkdtemp(prefix="baihe_deno_")
    try:
        zip_path = os.path.join(work, "deno.zip")
        digest = hashlib.sha256()

        class _Hashing:
            def __init__(self, f):
                self.f = f

            def write(self, b):
                digest.update(b)
                self.f.write(b)
        with open(zip_path, "wb") as f:
            _fetch(versioned, _Hashing(f), DENO_MAX_BYTES, progress)
        if digest.hexdigest() != expected:
            raise DenoInstallFailed("The Deno download did not match its checksum; "
                                    "nothing was installed.")
        say(0.92, "Checksum OK. Unpacking...")
        _unpack_binary(zip_path)
        say(0.98, f"Deno {version} installed.")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _unpack_binary(zip_path: str):
    """Only the single top-level `deno`/`deno.exe` member is written, into
    a file created exclusively: an existing binary there is never replaced
    (the start refuses that case; this closes the gap to the job running)."""
    dest = diagnostics.deno_default_install_path()
    name = os.path.basename(dest)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            member = next((i for i in zf.infolist() if i.filename == name), None)
            if member is None or member.file_size > DENO_MAX_BYTES * 4:
                raise DenoInstallFailed("The Deno download did not contain Deno.")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                out = open(dest, "xb")
            except FileExistsError:
                raise DenoInstallFailed("Deno is already installed; nothing was changed.") from None
            try:
                with zf.open(member) as src, out:
                    shutil.copyfileobj(src, out, _CHUNK)
            except BaseException:
                try:
                    os.remove(dest)       # only the file this call created
                except OSError:
                    pass
                raise
    except zipfile.BadZipFile:
        raise DenoInstallFailed("The Deno download was not a valid zip file.") from None
    os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# Q06: package install and GPU PyTorch setup
# ---------------------------------------------------------------------------

_CANCELLED_HINT = ("Cancelled. If pip was part-way through replacing files, run the install "
                   "again to finish it.")


def get_dependency_install() -> dict:
    """The latest install job's package, state and result (kind is
    "package" or "gpu_torch"); nothing here is a path or a key."""
    with _STATE_LOCK:
        state = {"kind": _DEPENDENCY["kind"], "package": _DEPENDENCY["package"],
                 "result": dict(_DEPENDENCY["last"]) if _DEPENDENCY["last"] else None}
    return {**state, "job_id": DEPENDENCY_JOB_ID, "job": _job_view(DEPENDENCY_JOB_ID)}


def start_dependency_install(name: str, confirm: bool = False) -> dict:
    """PC only (the route is local_only()). 422 unconfirmed, 404 for a name
    that is not an installable package, 409 while any job, restore, reset,
    cleanup or install runs."""
    gaps.guard(confirm)
    if name not in gaps.installable_packages():
        raise gaps.AdminActionUnknownPackage("Unknown or non-installable package.")
    return _start_install_job("package", name, f"Installing {name}", name)


def start_gpu_torch_setup(variant: str = None, confirm: bool = False) -> dict:
    """PC only. The refusals of gaps.prepare_gpu_torch_setup (422/409), then
    the setup runs as a job."""
    variant = gaps.prepare_gpu_torch_setup(variant, confirm)
    return _start_install_job("gpu_torch", "torch", f"Setting up PyTorch ({variant})", variant)


def _start_install_job(kind: str, package: str, description: str, arg: str) -> dict:
    # Started and labelled under one lock, as for the update test: the state
    # names a package only once its job really started.
    with _STATE_LOCK:
        if not background_jobs.start_job(DEPENDENCY_JOB_ID, _dependency_job, kind, arg,
                                         description=description):
            raise gaps.AdminActionJobsRunning(
                "An install or a library restore is already running; try again when it ends.")
        _DEPENDENCY.update(kind=kind, package=package, last=None)
    return {"job_id": DEPENDENCY_JOB_ID, "started": True}


def _other_job_running(own_job_id: str) -> bool:
    """library_admin_service.any_job_running's rule (jobs here, plus fresh
    job_records rows from another process), without counting this install's
    own running job against itself."""
    if any(j.get("status") in ("running", "queued")
           for jid, j in background_jobs.list_all_jobs().items() if jid != own_job_id):
        return True
    cutoff = time.time() - drama_service.STALE_JOB_RECORD_SECONDS
    return any(r.get("status") in ("running", "queued") and (r.get("updated_at") or 0) >= cutoff
               for r in db.list_job_records() if r.get("job_id") != own_job_id)


@contextlib.contextmanager
def _job_hold(job_id: str):
    """The library, exclusively, for the life of the install job: nothing
    else can start (409) until the install ends or is cancelled, and the
    hold is released on every exit."""
    if not background_jobs.acquire_exclusive("Dependency install", ignore_job_id=job_id):
        raise gaps.AdminActionJobsRunning(
            "A job, restore, cleanup or another install is in progress; try again when it ends.")
    try:
        if _other_job_running(job_id):      # re-check under the hold, as reset does
            raise gaps.AdminActionJobsRunning(
                "A background job is running or queued; wait for it to finish.")
        yield
    finally:
        background_jobs.release_exclusive()


def _store_dependency_result(result: dict, status: str):
    with _STATE_LOCK:
        _DEPENDENCY["last"] = result
    background_jobs.set_result(DEPENDENCY_JOB_ID, {"status": status, "detail": result.get("hint")
                                                   or ("Done." if result.get("ok") else "")})


def _dependency_job(kind: str, arg: str):
    with _STATE_LOCK:
        package = _DEPENDENCY["package"]
    failure = {"package": package, "ok": False, "output_tail": [], "hint": None}
    try:
        with _job_hold(DEPENDENCY_JOB_ID):
            background_jobs.update_progress(DEPENDENCY_JOB_ID, 0.02, "Starting pip...")
            if kind == "gpu_torch":
                result = gaps.setup_gpu_torch(arg, True, job_id=DEPENDENCY_JOB_ID)
            else:
                result = gaps.install_dependency(arg, True, job_id=DEPENDENCY_JOB_ID)
    except background_jobs.JobCancelled:
        _store_dependency_result({**failure, "cancelled": True, "hint": _CANCELLED_HINT},
                                 "cancelled")
        raise
    except gaps.AdminActionRefused as exc:      # lost the race for the hold
        _store_dependency_result({**failure, "hint": str(exc)}, "failed")
        raise DependencyInstallFailed(str(exc)) from None
    except Exception as exc:     # noqa: BLE001 -- never echo an exception (paths, keys)
        _store_dependency_result(
            {**failure, "hint": f"The install stopped unexpectedly ({type(exc).__name__})."},
            "failed")
        raise DependencyInstallFailed("The install stopped unexpectedly.") from None
    result = {**result, "cancelled": False}
    _store_dependency_result(result, "ok" if result["ok"] else "failed")
    if not result["ok"]:
        raise DependencyInstallFailed("The install did not finish; see its output.")


# ---------------------------------------------------------------------------
# Q06: "Test first" for an update
# ---------------------------------------------------------------------------

def get_upgrade_check() -> dict:
    with _STATE_LOCK:
        state = {"package": _UPGRADE_CHECK["package"], "target": _UPGRADE_CHECK["target"],
                 "output_tail": list(_UPGRADE_CHECK["tail"]),
                 "result": dict(_UPGRADE_CHECK["last"]) if _UPGRADE_CHECK["last"] else None}
    return {**state, "job_id": UPGRADE_CHECK_JOB_ID, "job": _job_view(UPGRADE_CHECK_JOB_ID)}


def start_upgrade_check(name: str, target: str = None, confirm: bool = False) -> dict:
    """PC only. `target` must be the last "Check for updates" target for
    `name` (409 otherwise, as for Upgrade); the job installs exactly that
    version (with constraints.txt) into a throwaway environment and runs
    this app's tests against it. Your real install is not touched."""
    if name in diagnostics.TORCH_FAMILY:
        gaps.guard(confirm)
        raise gaps.AdminActionNotPossible(
            "torch, torchvision and torchaudio are set up together under GPU PyTorch.")
    gaps.guard(confirm)
    if name not in gaps.installable_packages():
        raise gaps.AdminActionUnknownPackage("Unknown or non-installable package.")
    checked = gaps.cached_update(name)
    if checked is None or checked.get("status") != "update" or not checked.get("target"):
        raise gaps.AdminActionStale("Check for updates first; there is no update to test.")
    if target != checked["target"]:
        raise gaps.AdminActionStale("The update check has changed since; check for updates again.")
    dist, version = checked["dist"], checked["target"]
    # Started and labelled under one lock: the state names a package only
    # once its job really started, and the job's own writes (tail, result)
    # wait for this lock, so a second request can't relabel the first run.
    with _STATE_LOCK:
        if not background_jobs.start_job(UPGRADE_CHECK_JOB_ID, _upgrade_check_job, name, dist,
                                         version, description=f"Testing {name} {version}"):
            raise gaps.AdminActionJobsRunning(
                "An update test or a library restore is already running; try again when it ends.")
        _UPGRADE_CHECK.update(package=name, target=version, tail=[], last=None)
    return {"job_id": UPGRADE_CHECK_JOB_ID, "started": True}


_PHASES = (("Creating a throwaway", 0.05), ("Installing ", 0.15),
           ("Running this app's test suite", 0.35), ("test(s) failed -- re-running", 0.8))
_RESULT_KEYS = ("ok", "verdict", "reason", "version", "new_failures",
                "preexisting_failures", "conflicts")


def _upgrade_check_job(name: str, dist: str, version: str):
    def cancelled():
        return background_jobs.is_cancel_requested(UPGRADE_CHECK_JOB_ID)
    # The runner polls `cancelled`, so Cancel kills a quiet pip/pytest tree too.
    gen = diagnostics.check_upgrade_candidate(dist, version, project_root=gaps.default_project_root(),
                                              cancel=cancelled)
    frac, final = 0.0, None
    try:
        for item in gen:
            if cancelled():
                gen.close()          # kills the running pip/pytest
                raise background_jobs.JobCancelled()
            if item.get("done"):
                final = item
                continue
            line = _redact(item.get("line", ""))[:300]
            for marker, value in _PHASES:
                if marker in line:
                    frac = max(frac, value)
            with _STATE_LOCK:
                _UPGRADE_CHECK["tail"] = (_UPGRADE_CHECK["tail"] + [line])[-_OUTPUT_TAIL:]
            if line.strip():
                background_jobs.update_progress(UPGRADE_CHECK_JOB_ID, frac, line[:200])
    finally:
        gen.close()
    if cancelled():
        raise background_jobs.JobCancelled()     # the generator ended early because of Cancel
    final = final or {"ok": False, "verdict": "incomplete", "reason": "the test run stopped"}
    result = {}
    for k in _RESULT_KEYS:
        v = final.get(k)
        if isinstance(v, list):
            v = [_redact(x)[:300] for x in v[:50]]
        elif isinstance(v, str):
            v = _redact(v)[:500]
        result[k] = v
    result["ok"] = bool(result["ok"])
    with _STATE_LOCK:
        _UPGRADE_CHECK["last"] = result
    background_jobs.set_result(UPGRADE_CHECK_JOB_ID, {"status": result["verdict"],
                                                      "detail": result["reason"]})
