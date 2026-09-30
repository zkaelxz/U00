"""
installer/build_installer.py -- assembles the Windows installer's payload
and, where Inno Setup is available, compiles BaiheStudio-Setup-<version>.exe
(Step 80b; design: docs/windows-installer-design.md).

    python installer/build_installer.py --version 0.1.0
        [--skip-frontend-build]   # frontend/dist is already built
        [--python-zip PATH]       # a local copy of the embeddable zip
        [--no-compile]            # stop after the payload (no ISCC)
        [--iscc PATH]             # ISCC.exe if it isn't in the usual place

Payload (build/installer/payload/):
    python/         the official embeddable Python, pinned by version and
                    SHA-256, with its ._pth set up for site-packages and ../app
    app/            the app's code (an allow-by-default copy with the
                    exclusions below), frontend/dist, and the two runtime
                    installer scripts (app/installer/launcher.py, postinstall.py)
    wheels/         requirements-core.txt's wheels plus pip's own, so the
                    install step never needs the network
    manifest.json   versions, the wheel list with hashes, and the size estimate

Never in the payload: .env and any other secrets, library/, model caches,
venvs, tests/, docs/, developer scripts and the source-checkout launchers
(start.bat and friends). is_excluded() is the single rule, and
check_payload() re-checks the staged tree before anything is compiled.

Wheels must be downloaded on Windows: `pip download --platform` still
evaluates environment markers for the machine it runs on, so a Linux
download silently drops Windows-only dependencies (colorama, tzdata, ...).
The GitHub Actions workflow runs this on windows-latest.

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
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER_DIR = REPO_ROOT / "installer"
DEFAULT_OUT = REPO_ROOT / "build" / "installer"

# The bundled interpreter. 3.12.10 is the last 3.12 release with Windows
# binaries; the hash was taken from python.org's own download (2026-09-30).
PYTHON_VERSION = "3.12.10"
PYTHON_EMBED_URL = (f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
                    f"python-{PYTHON_VERSION}-embed-amd64.zip")
PYTHON_EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"

# The runtime half of installer/ that ships; the rest (this script, the
# .iss) is build-only.
RUNTIME_INSTALLER_FILES = ("launcher.py", "postinstall.py")

# Excluded wherever they appear.
EXCLUDED_DIR_NAMES = frozenset({
    ".git", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", "library", "model_cache", "venv", ".venv",
})
# Excluded at the repo root only (frontend/dist is staged on its own).
EXCLUDED_TOP_LEVEL = frozenset({
    "tests", "docs", "scripts", "installer", "frontend", "build", "dist", "env",
    ".github", ".claude", ".streamlit",
})
EXCLUDED_FILE_NAMES = frozenset({
    # Per-copy state and markers that must never ship.
    "PORTABLE", "PYTHON_VERSION", "INSTALLED",
    # The source-checkout launchers; the installed app has its own.
    "start.bat", "start.ps1", "uninstall.bat", "uninstall_path_cleanup.ps1",
    "make_shortcut.bat", "make_lock.bat",
    # Developer-only files.
    "run_tests.py", "pytest.ini", "conftest.py", "CLAUDE.md", "FILE_ORGANIZATION.md",
    ".gitignore", ".gitattributes", "Thumbs.db", ".DS_Store",
})
EXCLUDED_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".log", ".db", ".sqlite", ".sqlite3", ".db-journal", ".db-wal",
    ".key", ".pem", ".pfx", ".p12", ".crt", ".zip", ".whl", ".swp", ".bak", ".tmp",
})


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
    name = parts[-1]
    lower = name.lower()
    if lower == ".env" or lower.startswith(".env."):
        return True
    if lower.startswith("cookies") and lower.endswith(".txt"):
        return True
    if name in EXCLUDED_FILE_NAMES:
        return True
    return any(lower.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def _copy(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def stage_app(repo_root, app_dest) -> list:
    """Copies the app into `app_dest`: every regular file not excluded by
    is_excluded(), then frontend/dist (which must be built) and the
    runtime installer scripts. Symlinks are skipped. Returns the staged
    paths, relative to app_dest, as posix strings (sorted)."""
    repo_root = Path(repo_root)
    app_dest = Path(app_dest)
    dist = repo_root / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise BuildError(f"{dist / 'index.html'} doesn't exist. Build the frontend first "
                         "(cd frontend && npm ci && npm run build), or drop --skip-frontend-build.")
    if app_dest.exists():
        shutil.rmtree(app_dest)
    staged = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        here = Path(dirpath)
        rel_dir = here.relative_to(repo_root)
        # Prune excluded directories so os.walk never descends into them.
        dirnames[:] = sorted(d for d in dirnames
                             if not (here / d).is_symlink()
                             and not is_excluded((rel_dir / d).as_posix() + "/x"))
        for name in sorted(filenames):
            src = here / name
            rel = (rel_dir / name).as_posix()
            if src.is_symlink() or not src.is_file() or is_excluded(rel):
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
        if path.is_file() and (lower == ".env" or lower.startswith(".env.")):
            problems.append(rel)
        elif path.is_dir() and path.name in ("library", "model_cache", "venv", ".venv", "tests"):
            problems.append(rel + "/")
        elif path.is_file() and path.name in ("INSTALLED", "PORTABLE"):
            problems.append(rel)
    for required in ("frontend/dist/index.html", "api/__main__.py", "portable.py",
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


def download_wheels(repo_root, wheels_dest, python_version=PYTHON_VERSION) -> list:
    if os.name != "nt":
        print("WARNING: downloading wheels on a non-Windows machine drops Windows-only "
              "dependencies (pip evaluates markers for this machine). Use this payload "
              "for local testing only; the release build runs on Windows.", file=sys.stderr)
    if ".".join(map(str, sys.version_info[:2])) != ".".join(python_version.split(".")[:2]):
        print(f"WARNING: this is Python {sys.version_info[0]}.{sys.version_info[1]} but the bundled "
              f"one is {python_version}; pip evaluates python_version markers for this "
              "interpreter, so build with the same minor version.", file=sys.stderr)
    wheels_dest = Path(wheels_dest)
    if wheels_dest.exists():
        shutil.rmtree(wheels_dest)
    wheels_dest.mkdir(parents=True)
    result = subprocess.run(wheel_download_command(repo_root, wheels_dest, python_version),
                            timeout=1800)
    if result.returncode != 0:
        raise BuildError(f"`pip download` failed (exit code {result.returncode}).")
    wheels = sorted(p.name for p in wheels_dest.glob("*.whl"))
    if not any(w.startswith("pip-") for w in wheels):
        raise BuildError("pip's own wheel wasn't downloaded; the install step needs it.")
    return wheels


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


def write_manifest(payload_dir, version, python_version=PYTHON_VERSION,
                   python_sha256=PYTHON_EMBED_SHA256) -> Path:
    """What this payload contains, for the upgrade logic the design keeps
    (docs/windows-installer-design.md, update manifest). Installed as
    <install dir>\\manifest.json."""
    payload_dir = Path(payload_dir)
    wheels_dir = payload_dir / "wheels"
    wheels = [{"file": p.name, "sha256": sha256_of(p), "bytes": p.stat().st_size}
              for p in sorted(wheels_dir.glob("*.whl"))]
    manifest = {
        "app_version": version,
        "python": {"version": python_version, "embed_sha256": python_sha256},
        "requirements": "requirements-core.txt",
        "wheels": wheels,
        "installed_size_estimate_bytes": installed_size_estimate(wheels_dir),
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
          compile_exe=True, iscc=None) -> Path:
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
    wheels = download_wheels(REPO_ROOT, payload / "wheels")
    print(f"Downloaded {len(wheels)} wheels.")
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
    parser.add_argument("--version", required=True, help="the app version, e.g. 0.1.0")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="build folder")
    parser.add_argument("--skip-frontend-build", action="store_true",
                        help="use the existing frontend/dist instead of running npm")
    parser.add_argument("--python-zip", help="a local copy of the embeddable Python zip")
    parser.add_argument("--no-compile", action="store_true", help="assemble the payload only")
    parser.add_argument("--iscc", help="path to Inno Setup's ISCC.exe")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", args.version):
        print("ERROR: --version may only use letters, digits, '.', '+' and '-'.", file=sys.stderr)
        return 2
    try:
        build(args.version, out_dir=args.out, skip_frontend_build=args.skip_frontend_build,
              python_zip=args.python_zip, compile_exe=not args.no_compile, iscc=args.iscc)
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
