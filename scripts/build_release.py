"""
scripts/build_release.py -- builds the React app into frontend/dist for the installer.

`installer/build_installer.py` calls `build_frontend`; a source checkout builds the same way
with `start.bat --build-frontend`. Standard library only.
"""

import shutil
import subprocess


class ReleaseError(Exception):
    """A plain-words reason the frontend can't be built."""


def build_frontend(frontend_dir):
    npm = shutil.which("npm")
    if npm is None:
        raise ReleaseError("npm was not found on PATH; install Node.js 22 to build the frontend.")
    for args in (["ci"], ["run", "build"]):
        result = subprocess.run([npm, *args], cwd=frontend_dir)
        if result.returncode != 0:
            raise ReleaseError(f"`npm {' '.join(args)}` failed (exit code {result.returncode}).")
