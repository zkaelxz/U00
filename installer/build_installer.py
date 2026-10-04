"""
installer/build_installer.py -- assembles the Windows installer's payload
and, where Inno Setup is available, compiles BaiheStudio-Setup-<version>.exe
(Step 80b; design: docs/windows-installer-design.md).

    python installer/build_installer.py --version 0.1.0
        [--skip-frontend-build]   # frontend/dist is already built
        [--python-zip PATH]       # a local copy of the embeddable zip
        [--no-compile]            # stop after the payload (no ISCC)
        [--iscc PATH]             # ISCC.exe if it isn't in the usual place
        [--go PATH]               # the Go that builds the bundled Caddy
    python installer/build_installer.py --update-lock
                                      # regenerate installer/wheels.lock.txt
                                      # (Windows, Python 3.12; see the design doc)

Payload (build/installer/payload/):
    python/         the official embeddable Python, pinned by version and
                    SHA-256, with its ._pth set up for site-packages and ../app
    app/            the app's code (an allow-by-default copy with the
                    exclusions below), frontend/dist, and the two runtime
                    installer scripts (app/installer/launcher.py, postinstall.py)
    service/        the boot service's files: helper/ (a second copy of the
                    interpreter with no site-packages, installer/service.py and
                    the Start-menu service menu, installer/service_menu.ps1)
                    and wrapper/ (WinSW, pinned by SHA-256, as BaiheStudio.exe
                    with its licence), and caddy/ (WinSW as BaiheCaddy.exe and
                    caddy.exe built from installer/caddy, with the licence files
                    of everything compiled into it). service.py copies them into
                    an admin-only folder; nothing elevated runs from app/ or
                    python/. Caddy stays off until the owner enables remote access.
    wheels/         the wheels pinned in installer/wheels.lock.txt (SHA-256
                    hashes; requirements-core.txt plus pip and every
                    transitive dependency) and a copy of that lock, so the
                    install step never needs the network
    manifest.json   versions, the wheel list with hashes, and the size estimate

Only tracked files ship (git ls-files; untracked files in a working tree
never do), and of those never: .env and any other secrets, library/, model
caches, venvs, tests/, docs/, developer scripts and the source-checkout
launchers (start.bat and friends). is_excluded() is the single rule, and
check_payload() re-checks the staged tree before anything is compiled.

Wheels must be downloaded on Windows: `pip download --platform` still
evaluates environment markers for the machine it runs on, so a Linux
download silently drops Windows-only dependencies (colorama, tzdata, ...).
The GitHub Actions workflow runs this on windows-latest. Building Caddy
needs Go (CADDY_GO_VERSION; the workflow installs it, pinned by hash).

Standard library only.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER_DIR = REPO_ROOT / "installer"
WHEEL_LOCK = INSTALLER_DIR / "wheels.lock.txt"
DEFAULT_OUT = REPO_ROOT / "build" / "installer"

# The bundled interpreter. 3.12.10 is the last 3.12 release with Windows
# binaries; the hash was taken from python.org's own download (2026-09-30).
PYTHON_VERSION = "3.12.10"
PYTHON_EMBED_URL = (f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
                    f"python-{PYTHON_VERSION}-embed-amd64.zip")
PYTHON_EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"

# baihe.iss's AppId (without Inno's leading "{{" escape).
INSTALLER_APP_ID = "973BDBB4-4ADC-4E54-973B-682E2A04362F"

# The runtime half of installer/ that ships; the rest (this script, the
# .iss) is build-only.
RUNTIME_INSTALLER_FILES = ("launcher.py", "postinstall.py")

# The Windows service wrapper (docs/windows-installer-design.md, "Boot
# service"): WinSW, MIT licence, the .NET Framework 4.6.1 build, which runs
# on the .NET Framework 4.8 that Windows 10 and 11 include. The hash was
# taken from the GitHub release download (2026-09-30).
WINSW_VERSION = "2.12.0"
WINSW_URL = f"https://github.com/winsw/winsw/releases/download/v{WINSW_VERSION}/WinSW.NET461.exe"
WINSW_SHA256 = "b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f"
WINSW_LICENSE = INSTALLER_DIR / "licenses" / "WinSW-LICENSE.txt"

# Caddy, the only internet-facing part: stock Caddy plus the rate_limit module
# deploy/caddy/Caddyfile.template needs, built from installer/caddy, where
# go.mod and go.sum pin every module by hash. The build is reproducible only
# from the same bytes: installer/caddy/main.go with CRLF line endings builds a
# different binary, which is why .gitattributes forces LF there (a Windows
# checkout otherwise converts it). Changing go.mod, go.sum, main.go or the Go
# version changes CADDY_SHA256.
CADDY_SOURCE_DIR = INSTALLER_DIR / "caddy"
CADDY_MODULE = "baihe.local/caddy"
CADDY_VERSION = "2.11.4"
CADDY_GO_VERSION = "go1.26.8"
CADDY_SHA256 = "e09cc7eb846934a7fd870b90c53a5b51527cda2fcaabced1490ea7f9478aa150"
CADDY_TEMPLATE = REPO_ROOT / "deploy" / "caddy" / "Caddyfile.template"
_LICENSE_FILE_RE = re.compile(r"(?i)(licen[cs]e|notice|copying|patents)([._-].*)?")

# Excluded wherever they appear.
EXCLUDED_DIR_NAMES = frozenset({
    ".git", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", "library", "model_cache", "venv", ".venv",
})
# Excluded at the repo root only (frontend/dist is staged on its own).
EXCLUDED_TOP_LEVEL = frozenset({
    "tests", "docs", "scripts", "installer", "frontend", "build", "dist", "env",
    ".github", ".claude",
})
EXCLUDED_FILE_NAMES = frozenset({
    # Per-copy state and markers that must never ship.
    "PORTABLE", "PYTHON_VERSION", "INSTALLED",
    # The source-checkout launchers; the installed app has its own.
    "start.bat", "start.ps1", "uninstall.bat", "uninstall_path_cleanup.ps1",
    "make_shortcut.bat", "make_lock.bat",
    # Developer-only files. (run_tests.py ships: Diagnostics' file check
    # expects it, diagnostics.EXPECTED_TOP_LEVEL_FILES.)
    "pytest.ini", "conftest.py", "CLAUDE.md", "FILE_ORGANIZATION.md",
    ".gitignore", ".gitattributes", "Thumbs.db", ".DS_Store",
})
EXCLUDED_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3", ".db-journal", ".db-wal",
    ".key", ".pem", ".pfx", ".p12", ".crt", ".zip", ".whl", ".swp", ".bak", ".tmp",
})


if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))
import postinstall  # noqa: E402  (the lock parser/verifier the installed app runs too)


class BuildError(Exception):
    """A plain-words reason the installer can't be built."""


def is_excluded(rel_path) -> bool:
    """True if `rel_path` (relative to the repo root, either slash style)
    must not ship. Secrets first: anything named .env or .env.* (keys),
    cookie jars, key/cert files."""
    parts = PurePosixPath(str(rel_path).replace("\\", "/")).parts
    if not parts:
        return True
    if parts[0] in EXCLUDED_TOP_LEVEL:
        return True
    if any(p in EXCLUDED_DIR_NAMES for p in parts):
        return True
    # .env / .env.* as a file or a folder (a venv named .env) anywhere.
    if any(p.lower() == ".env" or p.lower().startswith(".env.") for p in parts):
        return True
    name = parts[-1]
    lower = name.lower()
    if lower.startswith("cookies") and lower.endswith(".txt"):
        return True
    if name in EXCLUDED_FILE_NAMES:
        return True
    return any(lower.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def _copy(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def tracked_files(repo_root):
    """`git ls-files` for a git checkout (posix paths), or None if this
    isn't one or git isn't available. Staging from it means an untracked
    file in a developer's working tree (a client_secret_*.json, a token
    file) can never ship, whatever its name."""
    git = shutil.which("git")
    if not git or not (Path(repo_root) / ".git").exists():
        return None
    try:
        out = subprocess.run([git, "-C", str(repo_root), "ls-files", "-z"],
                             capture_output=True, timeout=120, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    return sorted(p for p in out.decode("utf-8").split("\0") if p)


def _walk_files(repo_root: Path) -> list:
    """Every regular file under repo_root, pruning excluded folders --
    the fallback for a source tree with no git metadata."""
    found = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        here = Path(dirpath)
        rel_dir = here.relative_to(repo_root)
        dirnames[:] = sorted(d for d in dirnames
                             if not (here / d).is_symlink()
                             and not is_excluded((rel_dir / d).as_posix() + "/x"))
        found.extend((rel_dir / name).as_posix() for name in sorted(filenames))
    return found


def stage_app(repo_root, app_dest, files=None) -> list:
    """Copies the app into `app_dest`: the tracked files (tracked_files(),
    or every file when there's no git metadata) not excluded by
    is_excluded(), then frontend/dist (which must be built, and is
    untracked) and the runtime installer scripts. Symlinks are skipped.
    Returns the staged paths, relative to app_dest, as posix strings
    (sorted)."""
    repo_root = Path(repo_root)
    app_dest = Path(app_dest)
    dist = repo_root / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise BuildError(f"{dist / 'index.html'} doesn't exist. Build the frontend first "
                         "(cd frontend && npm ci && npm run build), or drop --skip-frontend-build.")
    if app_dest.exists():
        shutil.rmtree(app_dest)
    if files is None:
        files = tracked_files(repo_root)
    if files is None:
        files = _walk_files(repo_root)
    staged = []
    for rel in files:
        src = repo_root / rel
        if is_excluded(rel) or any((repo_root / Path(*PurePosixPath(rel).parts[:i])).is_symlink()
                                   for i in range(1, len(PurePosixPath(rel).parts))):
            continue
        if src.is_symlink() or not src.is_file():
            continue
        _copy(src, app_dest / rel)
        staged.append(rel)
    for src in sorted(dist.rglob("*")):
        if src.is_file() and not src.is_symlink():
            rel = src.relative_to(repo_root).as_posix()
            _copy(src, app_dest / rel)
            staged.append(rel)
    for name in RUNTIME_INSTALLER_FILES:
        src = repo_root / "installer" / name
        if not src.is_file():
            raise BuildError(f"{src} is missing.")
        _copy(src, app_dest / "installer" / name)
        staged.append(f"installer/{name}")
    return sorted(staged)


def check_payload(app_dest) -> None:
    """Belt and braces: fails the build if anything secret-shaped or
    per-user made it into the staged app anyway."""
    app_dest = Path(app_dest)
    problems = []
    for path in app_dest.rglob("*"):
        rel = path.relative_to(app_dest).as_posix()
        lower = path.name.lower()
        if lower == ".env" or lower.startswith(".env."):
            problems.append(rel)
        elif path.is_dir() and path.name in ("library", "model_cache", "venv", ".venv", "tests"):
            problems.append(rel + "/")
        elif path.is_file() and path.name in ("INSTALLED", "PORTABLE"):
            problems.append(rel)
    for required in ("frontend/dist/index.html", "api/__main__.py", "portable.py", "process_guard.py",
                     "requirements-core.txt", "constraints.txt", "check_setup.py",
                     "installer/launcher.py", "installer/postinstall.py"):
        if not (app_dest / required).is_file():
            problems.append(f"missing: {required}")
    if problems:
        raise BuildError("The staged app isn't safe to ship: " + ", ".join(sorted(problems)))


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f)
    os.replace(tmp, dest)
    return dest


def pth_contents(python_version: str = PYTHON_VERSION) -> str:
    """The embeddable Python's ._pth: its stdlib zip, its own folder,
    site-packages (for pip and everything pip installs), ../app (so
    `python -m api` finds the code) and `import site` (off by default in
    the embeddable package, and needed for site-packages to work). A ._pth
    also keeps PYTHONPATH and the user's own site-packages out."""
    tag = "".join(python_version.split(".")[:2])
    return "\n".join([f"python{tag}.zip", ".", r"Lib\site-packages", r"..\app",
                      "import site", ""])


def prepare_python(zip_path, python_dest, expected_sha256=PYTHON_EMBED_SHA256,
                   python_version=PYTHON_VERSION) -> Path:
    zip_path = Path(zip_path)
    actual = sha256_of(zip_path)
    if expected_sha256 and actual != expected_sha256:
        raise BuildError(f"{zip_path.name} has SHA-256 {actual}, expected {expected_sha256}. "
                         "Refusing to bundle an interpreter that doesn't match the pinned one.")
    python_dest = Path(python_dest)
    if python_dest.exists():
        shutil.rmtree(python_dest)
    python_dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = (python_dest / info.filename).resolve()
            if python_dest.resolve() not in target.parents:
                raise BuildError(f"Unsafe path in {zip_path.name}: {info.filename}")
        zf.extractall(python_dest)
    tag = "".join(python_version.split(".")[:2])
    pth = python_dest / f"python{tag}._pth"
    if not pth.is_file():
        raise BuildError(f"{pth.name} isn't in the embeddable zip; is the version right?")
    pth.write_text(pth_contents(python_version), encoding="ascii", newline="\r\n")
    (python_dest / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    return python_dest


def constraints_for(repo_root) -> Path:
    repo_root = Path(repo_root)
    lock = repo_root / "constraints.lock.txt"
    return lock if lock.is_file() else repo_root / "constraints.txt"


def wheel_download_command(repo_root, wheels_dest, python_version=PYTHON_VERSION,
                           python_exe=None) -> list:
    """`pip download` of requirements-core.txt (with the same constraints
    file start.bat uses) plus pip itself, binary wheels only, for 64-bit
    Windows CPython <python_version>."""
    repo_root = Path(repo_root)
    major_minor = ".".join(python_version.split(".")[:2])
    return [python_exe or sys.executable, "-m", "pip", "download",
            "--only-binary=:all:", "--platform", "win_amd64",
            "--python-version", major_minor, "--implementation", "cp",
            "-d", str(wheels_dest),
            "-r", str(repo_root / "requirements-core.txt"),
            "-c", str(constraints_for(repo_root)),
            "pip"]


def locked_download_command(lock_path, wheels_dest, python_version=PYTHON_VERSION,
                            python_exe=None) -> list:
    """`pip download` of exactly the pinned set. --require-hashes makes pip
    itself refuse a file whose hash isn't in the lock (or a requirement
    without one); --no-deps means nothing outside the lock is resolved."""
    major_minor = ".".join(python_version.split(".")[:2])
    return [python_exe or sys.executable, "-m", "pip", "download",
            "--only-binary=:all:", "--platform", "win_amd64",
            "--python-version", major_minor, "--implementation", "cp",
            "--require-hashes", "--no-deps",
            "-d", str(wheels_dest), "-r", str(lock_path)]


def _requirement_names(text) -> set:
    names = set()
    for line in text.splitlines():
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match and not line.lstrip().startswith("#"):
            names.add(postinstall.canonical_name(match.group(1)))
    return names


def check_lock_covers_requirements(repo_root, lock_path=None) -> None:
    """Fails if requirements-core.txt (or pip) names a package the lock
    doesn't pin, i.e. the lock is stale for a dependency change."""
    lock_path = Path(lock_path or WHEEL_LOCK)
    if not lock_path.is_file():
        raise BuildError(f"{lock_path} doesn't exist. Generate it with "
                         "`python installer/build_installer.py --update-lock` (docs/windows-installer-design.md).")
    try:
        lock = postinstall.parse_lock(lock_path.read_text(encoding="utf-8"))
    except postinstall.LockError as e:
        raise BuildError(str(e))
    wanted = _requirement_names((Path(repo_root) / "requirements-core.txt").read_text(encoding="utf-8"))
    missing = sorted((wanted | {"pip"}) - set(lock))
    if missing:
        raise BuildError("wheels.lock.txt doesn't pin: " + ", ".join(missing)
                         + ". Regenerate it (--update-lock) and review the diff.")


def verify_wheel_hashes(wheels_dir, lock_path=None) -> int:
    """The build's own check, independent of pip: every wheel in wheels_dir
    is pinned with a matching SHA-256, and none is extra or missing."""
    lock_path = Path(lock_path or WHEEL_LOCK)
    try:
        return postinstall.verify_wheels(wheels_dir, lock_path.read_text(encoding="utf-8"))
    except postinstall.LockError as e:
        raise BuildError(str(e))


def _pip_platform_warnings(python_version):
    if os.name != "nt":
        print("WARNING: downloading wheels on a non-Windows machine drops Windows-only "
              "dependencies (pip evaluates markers for this machine). Use this payload "
              "for local testing only; the release build runs on Windows.", file=sys.stderr)
    if ".".join(map(str, sys.version_info[:2])) != ".".join(python_version.split(".")[:2]):
        print(f"WARNING: this is Python {sys.version_info[0]}.{sys.version_info[1]} but the bundled "
              f"one is {python_version}; pip evaluates python_version markers for this "
              "interpreter, so build with the same minor version.", file=sys.stderr)


def download_wheels(repo_root, wheels_dest, python_version=PYTHON_VERSION, lock_path=None) -> list:
    """Downloads the locked wheels, then re-verifies every file against the
    lock ourselves. No unhashed fallback: any failure stops the build."""
    lock_path = Path(lock_path or WHEEL_LOCK)
    check_lock_covers_requirements(repo_root, lock_path)
    wheels_dest = Path(wheels_dest)
    if wheels_dest.exists():
        shutil.rmtree(wheels_dest)
    wheels_dest.mkdir(parents=True)
    result = subprocess.run(locked_download_command(lock_path, wheels_dest, python_version),
                            timeout=1800)
    if result.returncode != 0:
        raise BuildError(f"`pip download --require-hashes` failed (exit code {result.returncode}); "
                         "see pip's message above for the package.")
    wheels = sorted(p.name for p in wheels_dest.glob("*.whl"))
    if not any(w.startswith("pip-") for w in wheels):
        raise BuildError("pip's own wheel wasn't downloaded; the install step needs it.")
    verify_wheel_hashes(wheels_dest, lock_path)
    shutil.copy2(lock_path, wheels_dest / postinstall.LOCK_NAME)
    return wheels


def format_lock(wheels_dir) -> str:
    """wheels.lock.txt text for the wheels in wheels_dir, one entry per
    package, in `pip install --require-hashes` format."""
    entries = {}
    for whl in sorted(Path(wheels_dir).glob("*.whl")):
        try:
            name, version = postinstall.wheel_identity(whl.name)
        except postinstall.LockError as e:
            raise BuildError(str(e))
        if name in entries and entries[name][0] != version:
            raise BuildError(f"{name} was downloaded in two versions ({entries[name][0]}, {version}).")
        entries.setdefault(name, (version, set()))[1].add(sha256_of(whl))
    lines = ["# installer/wheels.lock.txt -- SHA-256 pins for every wheel the Windows installer bundles",
             "# (requirements-core.txt, pip and all transitive dependencies; win_amd64, CPython 3.12).",
             "# Generated by `python installer/build_installer.py --update-lock`; do not edit by hand.",
             "# See docs/windows-installer-design.md, \"Pinned wheels\"."]
    for name in sorted(entries):
        version, hashes = entries[name]
        lines.append(f"{name}=={version} \\")
        ordered = sorted(hashes)
        lines.extend(f"    --hash=sha256:{h}" + (" \\" if i < len(ordered) - 1 else "")
                     for i, h in enumerate(ordered))
    return "\n".join(lines) + "\n"


def update_lock(repo_root=REPO_ROOT, lock_path=None, python_version=PYTHON_VERSION) -> int:
    """Resolves requirements-core.txt + pip (with the constraints file) from
    PyPI, the one step that trusts what PyPI serves right now, and writes
    the SHA-256 of each downloaded wheel to the lock. Review the diff."""
    lock_path = Path(lock_path or WHEEL_LOCK)
    _pip_platform_warnings(python_version)
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(wheel_download_command(repo_root, tmp, python_version), timeout=1800)
        if result.returncode != 0:
            raise BuildError(f"`pip download` failed (exit code {result.returncode}).")
        text = format_lock(tmp)
    postinstall.parse_lock(text)
    lock_path.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {lock_path} ({text.count('==')} packages). Review the diff before committing.")
    return 0


def installed_size_estimate(wheels_dir) -> int:
    """Bytes the wheels take once unpacked -- what Inno Setup adds to its
    own free-space check (ExtraDiskSpaceRequired), since pip's files
    aren't in its file list."""
    total = 0
    for whl in Path(wheels_dir).glob("*.whl"):
        with zipfile.ZipFile(whl) as zf:
            total += sum(info.file_size for info in zf.infolist())
    return total


def numeric_version(version: str) -> str:
    """"0.1.0-rc1" -> "0.1.0.0": Windows' file-version field takes four
    numbers only."""
    nums = re.findall(r"\d+", version.split("-")[0].split("+")[0])[:4]
    if not nums:
        raise BuildError(f"Version {version!r} has no numbers in it.")
    return ".".join((nums + ["0", "0", "0", "0"])[:4])


def caddy_build_env(base=None) -> dict:
    """Windows x64, no cgo, only the pinned modules (-mod=readonly: go.sum
    must already hold every hash), and exactly CADDY_GO_VERSION (Go fetches
    that toolchain, checksum-verified, if the installed one differs)."""
    env = dict(os.environ if base is None else base)
    env.update({"GOOS": "windows", "GOARCH": "amd64", "GOAMD64": "v1", "CGO_ENABLED": "0",
                "GOFLAGS": "-mod=readonly", "GOTOOLCHAIN": CADDY_GO_VERSION})
    return env


def caddy_build_command(go, output) -> list:
    # -trimpath and -buildvcs=false keep the build folder and this repo's
    # version-control state out of the binary.
    return [go, "build", "-trimpath", "-buildvcs=false", "-ldflags=-s -w", "-o", str(output), "."]


def caddy_modules_command(go) -> list:
    return [go, "list", "-deps", "-f", "{{with .Module}}{{.Path}}\t{{.Dir}}{{end}}", "."]


def collect_licenses(listing: str, goroot, dest) -> int:
    """Copies the licence and notice files of every module compiled into
    Caddy (`go list -deps` output: module path, tab, its folder in the
    checksum-verified module cache), and Go's own, into `dest`. Returns how
    many."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    sources = [("go", Path(goroot))] if goroot else []
    seen = set()
    for line in listing.splitlines():
        path, _, folder = line.strip().partition("\t")
        if path and folder and path != CADDY_MODULE and path not in seen:
            seen.add(path)
            sources.append((path, Path(folder)))
    count = 0
    for name, folder in sources:
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if f.is_file() and _LICENSE_FILE_RE.fullmatch(f.name):
                _copy(f, dest / name.replace("/", "_") / f.name)
                count += 1
    return count


def build_caddy(out_dir, go="go", run=subprocess.run) -> tuple:
    """Builds caddy.exe into `out_dir` and checks it against CADDY_SHA256;
    returns (caddy.exe, the licences folder)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = (out_dir / "caddy.exe").resolve()
    env = caddy_build_env()
    result = run(caddy_build_command(go, exe), cwd=str(CADDY_SOURCE_DIR), env=env, timeout=1800)
    if result.returncode != 0:
        raise BuildError(f"Building Caddy failed (exit code {result.returncode}). It needs Go "
                         f"({CADDY_GO_VERSION}) and network access to the Go module proxy.")
    actual = sha256_of(exe)
    if actual != CADDY_SHA256:
        raise BuildError(f"The Caddy build's SHA-256 is {actual}, not the pinned {CADDY_SHA256}. "
                         "If installer/caddy or CADDY_GO_VERSION changed on purpose, update "
                         "CADDY_SHA256; otherwise check that installer/caddy has LF line endings "
                         "and that the Go version matches.")
    listing = run(caddy_modules_command(go), cwd=str(CADDY_SOURCE_DIR), env=env, timeout=600,
                  capture_output=True, text=True)
    goroot = run([go, "env", "GOROOT"], cwd=str(CADDY_SOURCE_DIR), env=env, timeout=600,
                 capture_output=True, text=True)
    if listing.returncode != 0 or goroot.returncode != 0:
        raise BuildError("Listing the modules compiled into Caddy failed.")
    licenses = out_dir / "licenses"
    count = collect_licenses(listing.stdout, goroot.stdout.strip(), licenses)
    print(f"Built caddy.exe (SHA-256 matches); collected {count} licence files.")
    return exe, licenses


def stage_service(payload_dir, python_zip, winsw_exe, caddy_exe, caddy_licenses) -> None:
    """payload/service/: the files service.py copies into its admin-only
    folder. helper/python is the embeddable interpreter again with a ._pth
    that has no site-packages, no `import site` and no ../app, so the
    elevated script imports nothing a user-writable folder could supply;
    wrapper/ is WinSW under the service's name and caddy/ is WinSW under
    Caddy's name beside caddy.exe, each checked against its pin. The Caddy
    template is copied into helper/ because service.py reads it while
    elevated, and app/ is writable by the user."""
    for path, expected in ((winsw_exe, WINSW_SHA256), (caddy_exe, CADDY_SHA256)):
        actual = sha256_of(path)
        if actual != expected:
            raise BuildError(f"{Path(path).name}: SHA-256 {actual}, expected {expected}.")
    service = Path(payload_dir) / "service"
    if service.exists():
        shutil.rmtree(service)
    helper_python = prepare_python(python_zip, service / "helper" / "python")
    tag = "".join(PYTHON_VERSION.split(".")[:2])
    (helper_python / f"python{tag}._pth").write_text(f"python{tag}.zip\n.\n", encoding="ascii",
                                                   newline="\r\n")
    shutil.rmtree(helper_python / "Lib")
    _copy(INSTALLER_DIR / "service.py", service / "helper" / "lib" / "installer" / "service.py")
    # The Start-menu "Baihe Studio service" menu runs elevated, so it is
    # run only from the admin folder's copy.
    _copy(INSTALLER_DIR / "service_menu.ps1",
          service / "helper" / "lib" / "installer" / "service_menu.ps1")
    _copy(winsw_exe, service / "wrapper" / "BaiheStudio.exe")
    _copy(CADDY_TEMPLATE, service / "helper" / "lib" / "deploy" / "caddy" / "Caddyfile.template")
    _copy(WINSW_LICENSE, service / "wrapper" / "licenses" / "WinSW-LICENSE.txt")
    _copy(winsw_exe, service / "caddy" / "BaiheCaddy.exe")
    _copy(caddy_exe, service / "caddy" / "caddy.exe")
    _copy(WINSW_LICENSE, service / "caddy" / "licenses" / "WinSW-LICENSE.txt")
    shutil.copytree(caddy_licenses, service / "caddy" / "licenses", dirs_exist_ok=True)


def write_manifest(payload_dir, version, python_version=PYTHON_VERSION,
                   python_sha256=PYTHON_EMBED_SHA256) -> Path:
    """What this payload contains, for the upgrade logic the design keeps
    (docs/windows-installer-design.md, "7. Upgrade", the per-component
    manifest). Installed as <install dir>\\manifest.json."""
    payload_dir = Path(payload_dir)
    wheels_dir = payload_dir / "wheels"
    wheels = [{"file": p.name, "sha256": sha256_of(p), "bytes": p.stat().st_size}
              for p in sorted(wheels_dir.glob("*.whl"))]
    manifest = {
        # What Setup and the uninstaller check to tell a Baihe Studio
        # install folder from any other folder with a manifest.json.
        "product": "Baihe Studio",
        "app_id": INSTALLER_APP_ID,
        "app_version": version,
        "python": {"version": python_version, "embed_sha256": python_sha256},
        "requirements": "requirements-core.txt",
        "wheels": wheels,
        "installed_size_estimate_bytes": installed_size_estimate(wheels_dir),
        "services": {
            "winsw": {"version": WINSW_VERSION, "sha256": WINSW_SHA256},
            "caddy": {"version": CADDY_VERSION, "go": CADDY_GO_VERSION, "sha256": CADDY_SHA256},
        },
    }
    path = payload_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def _build_release_module():
    spec = importlib.util.spec_from_file_location(
        "build_release", REPO_ROOT / "scripts" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_iscc(explicit=None):
    if explicit:
        return explicit
    for candidate in (shutil.which("ISCC"), shutil.which("iscc"),
                      os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe"),
                      os.path.join(os.environ.get("ProgramFiles", ""), "Inno Setup 6", "ISCC.exe"),
                      os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe")):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def iscc_command(iscc, payload_dir, output_dir, version, extra_disk_bytes) -> list:
    return [iscc, "/Qp",
            f"/DAppVersion={version}",
            f"/DAppVersionNumeric={numeric_version(version)}",
            f"/DPayloadDir={Path(payload_dir).resolve()}",
            f"/DOutputDir={Path(output_dir).resolve()}",
            f"/DExtraDiskSpace={int(extra_disk_bytes)}",
            str(INSTALLER_DIR / "baihe.iss")]


def build(version, out_dir=DEFAULT_OUT, skip_frontend_build=False, python_zip=None,
          compile_exe=True, iscc=None, go="go") -> Path:
    out_dir = Path(out_dir)
    payload = out_dir / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    if not skip_frontend_build:
        _build_release_module().build_frontend(REPO_ROOT / "frontend")
    staged = stage_app(REPO_ROOT, payload / "app")
    check_payload(payload / "app")
    print(f"Staged {len(staged)} app files.")
    if python_zip is None:
        python_zip = out_dir / f"python-{PYTHON_VERSION}-embed-amd64.zip"
        if not python_zip.is_file() or sha256_of(python_zip) != PYTHON_EMBED_SHA256:
            print(f"Downloading {PYTHON_EMBED_URL} ...")
            download(PYTHON_EMBED_URL, python_zip)
    prepare_python(python_zip, payload / "python")
    _pip_platform_warnings(PYTHON_VERSION)
    wheels = download_wheels(REPO_ROOT, payload / "wheels")
    print(f"Downloaded {len(wheels)} wheels; all match installer/wheels.lock.txt.")
    winsw = out_dir / f"WinSW-{WINSW_VERSION}.NET461.exe"
    if not winsw.is_file() or sha256_of(winsw) != WINSW_SHA256:
        print(f"Downloading {WINSW_URL} ...")
        download(WINSW_URL, winsw)
    caddy_exe, caddy_licenses = build_caddy(out_dir / "caddy-build", go=go)
    stage_service(payload, python_zip, winsw, caddy_exe, caddy_licenses)
    manifest = write_manifest(payload, version)
    extra = json.loads(manifest.read_text(encoding="utf-8"))["installed_size_estimate_bytes"]
    if not compile_exe:
        print(f"Payload ready in {payload} (not compiled).")
        return payload
    iscc_path = find_iscc(iscc)
    if not iscc_path:
        raise BuildError("Inno Setup's ISCC.exe wasn't found. Install Inno Setup 6 "
                         "(https://jrsoftware.org/isinfo.php), pass --iscc, or use --no-compile.")
    output_dir = out_dir / "output"
    result = subprocess.run(iscc_command(iscc_path, payload, output_dir, version, extra),
                            timeout=3600)
    if result.returncode != 0:
        raise BuildError(f"ISCC failed (exit code {result.returncode}).")
    exe = output_dir / f"BaiheStudio-Setup-{version}.exe"
    if not exe.is_file():
        raise BuildError(f"ISCC finished but {exe} isn't there.")
    print(f"Wrote {exe}")
    return exe


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the Baihe Studio Windows installer.")
    parser.add_argument("--version", help="the app version, e.g. 0.1.0")
    parser.add_argument("--update-lock", action="store_true",
                        help="regenerate installer/wheels.lock.txt from PyPI and exit")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="build folder")
    parser.add_argument("--skip-frontend-build", action="store_true",
                        help="use the existing frontend/dist instead of running npm")
    parser.add_argument("--python-zip", help="a local copy of the embeddable Python zip")
    parser.add_argument("--no-compile", action="store_true", help="assemble the payload only")
    parser.add_argument("--iscc", help="path to Inno Setup's ISCC.exe")
    parser.add_argument("--go", default="go", help="the Go that builds the bundled Caddy")
    args = parser.parse_args(argv)
    if args.update_lock:
        try:
            return update_lock()
        except (BuildError, postinstall.LockError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    if not args.version:
        parser.error("--version is required (unless --update-lock)")
    if not re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", args.version):
        print("ERROR: --version may only use letters, digits, '.', '+' and '-'.", file=sys.stderr)
        return 2
    try:
        build(args.version, out_dir=args.out, skip_frontend_build=args.skip_frontend_build,
              python_zip=args.python_zip, compile_exe=not args.no_compile, iscc=args.iscc,
              go=args.go)
    except BuildError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:   # build_release.ReleaseError from the npm step
        if type(e).__name__ == "ReleaseError":
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
