"""
scripts/build_release.py -- packages the built React app as a release zip.

End users never need Node.js: the frontend is built once on a developer's
PC and attached to a GitHub release as `baihe-frontend-<version>.zip`
(GitHub Actions minutes are exhausted, so CI can't build it). The user
unzips it into the repo folder, which puts `frontend/dist/index.html` where
`start.bat` and `python -m api` look for it. See docs/RELEASE.md.

    python scripts/build_release.py            # zip an existing frontend/dist
    python scripts/build_release.py --build    # npm ci + npm run build first

Standard library only. The version comes from frontend/package.json.
Output: dist/baihe-frontend-<version>.zip, whose entries are all under
`frontend/dist/`.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class ReleaseError(Exception):
    """A plain-words reason the release zip can't be built."""


def read_version(frontend_dir):
    with open(Path(frontend_dir) / "package.json", encoding="utf-8") as f:
        version = json.load(f).get("version")
    if not version or not isinstance(version, str):
        raise ReleaseError("frontend/package.json has no \"version\" string.")
    return version


def build_frontend(frontend_dir):
    npm = shutil.which("npm")
    if npm is None:
        raise ReleaseError("npm was not found on PATH; install Node.js 22 to build the frontend.")
    for args in (["ci"], ["run", "build"]):
        result = subprocess.run([npm, *args], cwd=frontend_dir)
        if result.returncode != 0:
            raise ReleaseError(f"`npm {' '.join(args)}` failed (exit code {result.returncode}).")


def make_zip(repo_root, out_dir=None):
    """Zips `<repo_root>/frontend/dist/**` into
    `<out_dir>/baihe-frontend-<version>.zip` (out_dir defaults to
    `<repo_root>/dist`), with entries under `frontend/dist/`. Returns the
    zip path."""
    repo_root = Path(repo_root)
    frontend_dir = repo_root / "frontend"
    dist_dir = frontend_dir / "dist"
    if not (dist_dir / "index.html").is_file():
        raise ReleaseError(
            f"{dist_dir / 'index.html'} doesn't exist. Build the frontend first "
            "(cd frontend && npm ci && npm run build), or pass --build.")
    version = read_version(frontend_dir)
    out_dir = Path(out_dir) if out_dir is not None else repo_root / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"baihe-frontend-{version}.zip"
    tmp_path = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file() and not path.is_symlink():
                zf.write(path, path.relative_to(repo_root).as_posix())
    os.replace(tmp_path, zip_path)
    return zip_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the frontend release zip.")
    parser.add_argument("--build", action="store_true",
                        help="run `npm ci` and `npm run build` in frontend/ first")
    args = parser.parse_args(argv)
    try:
        if args.build:
            build_frontend(REPO_ROOT / "frontend")
        zip_path = make_zip(REPO_ROOT)
    except ReleaseError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Wrote {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
