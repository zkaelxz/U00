"""
check_setup.py -- start.bat's own "print anything missing in plain
words" step, run once on every launch before the app starts.

Deliberately a separate, tiny script rather than importing the app's own
modules: those pull in heavy, sometimes-optional dependencies
(torch, faster-whisper, ...) just to check whether they're present,
which is exactly backwards for a check meant to run BEFORE any of that
is trusted to work. Only imports diagnostics.py itself, which is
already careful to check importability without importing
(diagnostics.check_dependency).

Never blocks the launcher: prints what it finds and always exits 0 --
missing ffmpeg/a JS runtime/a GPU are all real limits worth knowing
about up front, but none of them should stop someone from opening the
app to see the same message again in the Diagnostics tab, or to use
whatever still works without them (a CPU-only run, no YouTube
downloads).
"""
import hashlib
import os
import sys

import diagnostics

# What start.bat installs from. A change to either since the last install
# that finished in this venv (an update whose install failed or was closed
# half-way, a hand-made `git pull`) means the install is owed again.
DEPENDENCY_FILES = ("requirements-core.txt", "constraints.txt")
DEPS_STAMP_NAME = "baihe-core-deps.sha256"
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _report_symbols(encoding):
    """The warning/info/ok symbols this report uses, falling back to
    plain ASCII when the target stream's encoding can't represent the
    real Unicode ones. A plain Windows cmd.exe console's default codepage
    (cp1252) can't encode U+26A0 (warning sign), so printing it raises
    UnicodeEncodeError. Checked per-symbol (not a single "is this
    stream UTF-8" test) so a console that can handle some of these but
    not others still gets the ones it can."""
    def pick(nice, ascii_fallback):
        try:
            nice.encode(encoding or "utf-8")
            return nice
        except (UnicodeEncodeError, LookupError):
            return ascii_fallback
    return {"warn": pick("⚠", "[!]"), "info": pick("ℹ", "[i]"), "ok": pick("✓", "[OK]")}


def _print_report(file=sys.stdout):
    sym = _report_symbols(getattr(file, "encoding", None))
    lines = []

    py = diagnostics.check_python_version()
    if not py["ok"]:
        lines.append(f"{sym['warn']} Python {py['version']} found -- this app needs Python 3.12 or newer.")

    ffmpeg = diagnostics.check_ffmpeg()
    if not ffmpeg["found"]:
        lines.append(
            f"{sym['warn']} ffmpeg not found on PATH -- required for every audio/video step in this app "
            "(transcription, alignment, export, dubbing). Install it from https://ffmpeg.org/ "
            "and make sure it's on PATH, then restart.")

    js = diagnostics.check_js_runtime()
    if not js["found"]:
        lines.append(
            f"{sym['warn']} No JavaScript runtime found (Deno, Node, Bun or QuickJS) -- downloading from "
            "YouTube silently loses formats without one, since late 2025. Only matters if you "
            "use the URL downloader or Live tab; install Deno (https://deno.land) if you do.")

    cuda = diagnostics.check_cuda()
    if not cuda["torch_installed"]:
        lines.append(
            f"{sym['info']} torch isn't installed -- GPU-accelerated transcription/dubbing/diarization "
            "won't be available (this app still runs fine on CPU, just slower). Open "
            "Diagnostics > Recommended installs in the app if you want it.")
    elif cuda["cuda_available"] is False:
        lines.append(
            f"{sym['info']} torch is installed but no CUDA GPU is available to it -- GPU-touching steps "
            "will run on CPU instead (slower, not broken). If you have an NVIDIA GPU and "
            "expected this to find it, check your driver and that you installed a CUDA build "
            "of torch, not the CPU-only one.")

    lines.extend(f"{sym['warn']} {w}" for w in diagnostics.startup_warnings())

    if not lines:
        print(f"{sym['ok']} ffmpeg, a JavaScript runtime, and CUDA are all set up.", file=file)
        return
    print("Setup check found something worth knowing about before you start:\n", file=file)
    for line in lines:
        print(line, file=file)
    print("\nNone of this stops the app from starting -- see the Diagnostics tab for the "
         "full picture anytime. Its Recommended installs card lists the optional extras.", file=file)


def dependency_digest(root=None) -> str:
    root = root or PROJECT_ROOT
    sha = hashlib.sha256()
    for name in DEPENDENCY_FILES:
        with open(os.path.join(root, name), "rb") as fh:
            sha.update(name.encode("ascii") + b"\0" + fh.read() + b"\0")
    return sha.hexdigest()


def _stamp_path(prefix=None) -> str:
    # In the venv itself: a new venv has no stamp and installs anyway.
    return os.path.join(prefix or sys.prefix, DEPS_STAMP_NAME)


def dependencies_changed(root=None, prefix=None) -> bool:
    """True only when this venv recorded an install and the files changed
    since. No stamp (a venv from before the stamp existed) is not a change:
    start.bat's import check decides then, as it always did."""
    try:
        with open(_stamp_path(prefix), encoding="ascii") as fh:
            recorded = fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return False
    try:
        return recorded != dependency_digest(root)
    except OSError:
        return False


def record_dependencies_installed(root=None, prefix=None) -> None:
    with open(_stamp_path(prefix), "w", encoding="ascii") as fh:
        fh.write(dependency_digest(root) + "\n")


def dependency_command(flag: str) -> int:
    """Exit 0 only for a definite "install again": any failure here falls
    back to start.bat's import check."""
    if flag == "--deps-changed":
        return 0 if dependencies_changed() else 1
    record_dependencies_installed()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] in (["--deps-changed"], ["--deps-installed"]):
        sys.exit(dependency_command(sys.argv[1]))
    _print_report()
    sys.exit(0)
