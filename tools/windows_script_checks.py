"""Runs setup.bat, start.bat and uninstall.bat on a Windows runner, in temp folders, with their prompts
answered through stdin. Used by the `windows-scripts` job in .github/workflows/tests.yml.

    python tools/windows_script_checks.py setup|install|uninstall

`install` also covers the upgrade: it seeds a library in the fresh install, runs the scripts again over it
and checks the data survived; then it backs the library up to a zip and restores it in the installed app. start.bat is stopped before it would launch the server: another listener
holds its port, so it stops at its "port already in use" exit after the venv, dependencies and setup check.
No step may leave a server running. Dev tooling, so it is not covered by the lib.proc rule in
tests/test_static_analysis.py.
"""
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Not copied into the temp install: none of it is needed to run the scripts or the app's API.
NOT_STAGED = (".git", ".github", ".claude", "frontend", "node_modules", "tests", "docs", "reports",
              "research_notes", "venv", "library", "__pycache__")
# A space on purpose: real installs live under paths like "C:\Users\Jane Doe\Baihe Studio".
STAGE_NAME = "Baihe Studio"
LIBRARY_DRAMA = "CI upgrade drama"
SHORT = 300
INSTALL = 900
PROMPT_STATE_VARS = ("BAIHE_DATA_DIR", "HF_HOME", "TORCH_HOME", "OLLAMA_MODELS", "BAIHE_CI",
                     "BAIHE_SERVER_ONLY", "BAIHE_FORCE_DEPS", "BAIHE_API_PORT", "PYTHON_VERSION")

failures = []


def temp_dir():
    # The venv's files can still be held for a moment on Windows; a leftover temp folder is not a failure.
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def check(ok, message):
    print(("  ok    " if ok else "  FAIL  ") + message, flush=True)
    if not ok:
        failures.append(message)


def group(title):
    print(f"::group::{title}", flush=True)


def endgroup():
    print("::endgroup::", flush=True)


class Result:
    def __init__(self, code, out, timed_out):
        self.code, self.out, self.timed_out = code, out, timed_out


def run_bat(stage, bat, args=(), answers="", env=None, timeout=SHORT):
    """Runs one script from `stage` with `answers` on stdin; on a timeout the whole process tree is killed.

    The answers go through a file, not a pipe: cmd.exe drops whatever is left in a pipe after the first
    `set /p`, so only the first prompt would ever see its answer."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        answers_file = os.path.join(scratch, "answers.txt")
        with open(answers_file, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(answers)
        with open(answers_file, "rb") as stdin:
            proc = subprocess.Popen(
                ["cmd", "/d", "/c", bat, *args], cwd=stage, env=env, stdin=stdin,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            timed_out = False
            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
                try:
                    out, _ = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    out = ""
    print(out, flush=True)
    print(f"  [{bat} {' '.join(args)}] exit code {proc.returncode}{' (TIMED OUT, killed)' if timed_out else ''}",
          flush=True)
    return Result(proc.returncode, out, timed_out)


def stage_app(parent):
    stage = os.path.join(parent, STAGE_NAME)
    copy_app(stage)
    return stage


def copy_app(stage):
    shutil.copytree(ROOT, stage, ignore=shutil.ignore_patterns(*NOT_STAGED), dirs_exist_ok=True)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in PROMPT_STATE_VARS}
    env.update(extra)
    return env


def home_env(home, **extra):
    """A throwaway user profile, so the uninstaller's shortcut and cache paths never touch the runner's."""
    return clean_env(
        USERPROFILE=home, APPDATA=os.path.join(home, "AppData", "Roaming"),
        LOCALAPPDATA=os.path.join(home, "AppData", "Local"), **extra)


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def path_without_winget():
    """PATH minus any folder holding winget, so setup.bat can only report and never install."""
    keep = [p for p in os.environ.get("PATH", "").split(os.pathsep)
            if p and not os.path.exists(os.path.join(p, "winget.exe"))]
    return os.pathsep.join(keep)


def free_port_listener():
    """A socket that accepts connections and never answers HTTP, standing in for "something else on the port"."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    return sock, sock.getsockname()[1]


def venv_python(stage):
    return os.path.join(stage, "venv", "Scripts", "python.exe")


def run_app_python(stage, code, env):
    done = subprocess.run([venv_python(stage), "-c", code], cwd=stage, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=SHORT)
    print(done.stdout + done.stderr, flush=True)
    return done


# ---------------------------------------------------------------- setup.bat

def check_setup():
    group("setup.bat: reports what is installed, installs nothing")
    with temp_dir() as parent:
        stage = stage_app(parent)
        res = run_bat(stage, "setup.bat", answers="n\n\n\n", env=clean_env(PATH=path_without_winget()))
        check(not res.timed_out, "setup.bat finished instead of hanging on a prompt")
        check("Git: found" in res.out, "reports Git as found")
        check("Python: found" in res.out, "reports Python as found")
        check("Installing " not in res.out, "does not try to install anything without winget")
        check("winget (Windows Package Manager) wasn't found" in res.out, "says winget is unavailable")
        check("Ready: everything Baihe needs" in res.out or "still missing:" in res.out,
              "ends with its ready or still-missing summary")
        check(res.code in (0, 1), f"exit code is 0 (ready) or 1 (something missing), got {res.code}")
    endgroup()


# ------------------------------------------------------------ uninstall.bat

def seed_install(stage, home, with_library=True):
    touch(os.path.join(stage, "venv", "Scripts", "python.exe"))
    touch(os.path.join(stage, "model_cache", "hub", "model.bin"))
    if with_library:
        touch(os.path.join(stage, "library", "library.db"))
        touch(os.path.join(stage, "library", "dramas", "1", "note.txt"))
    touch(os.path.join(home, "Desktop", "Baihe Subtitler.lnk"))
    touch(os.path.join(home, "AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs",
                       "Baihe Subtitler.lnk"))


def leftover_dirs(home):
    dirs = {
        "HF_HOME": os.path.join(home, "left", "hf"),
        "TORCH_HOME": os.path.join(home, "left", "torch"),
        "OLLAMA_MODELS": os.path.join(home, "left", "ollama"),
    }
    for path in dirs.values():
        touch(os.path.join(path, "blob"))
    touch(os.path.join(home, "AppData", "Local", "pip", "Cache", "http", "entry"))
    return dirs


def check_app_removed(stage, home):
    check(not os.path.exists(os.path.join(stage, "venv")), "venv\\ is removed")
    check(not os.path.exists(os.path.join(stage, "model_cache")), "model_cache\\ is removed")
    check(not os.path.exists(os.path.join(home, "Desktop", "Baihe Subtitler.lnk")), "desktop shortcut is removed")
    check(not os.path.exists(os.path.join(home, "AppData", "Roaming", "Microsoft", "Windows", "Start Menu",
                                          "Programs", "Baihe Subtitler.lnk")), "Start Menu entry is removed")


def check_leftovers(out, dirs, home):
    check("Left on this PC" in out, "prints the leftovers heading")
    check(f"Hugging Face model cache: {dirs['HF_HOME']}" in out, "lists the Hugging Face cache")
    check(f"PyTorch cache: {dirs['TORCH_HOME']}" in out, "lists the PyTorch cache")
    check(f"Ollama models: {dirs['OLLAMA_MODELS']}" in out, "lists the Ollama models")
    check(f"pip download cache: {os.path.join(home, 'AppData', 'Local', 'pip', 'Cache')}" in out,
          "lists the pip cache")
    check("  - Python: " in out, "lists the Python found on PATH")
    for path in dirs.values():
        check(os.path.exists(path), f"leftover {os.path.basename(path)} is not deleted")


def uninstall_case(name, library_answer, expect_deleted, data_dir_set):
    group(f"uninstall.bat: {name}")
    with temp_dir() as parent:
        stage, home = stage_app(parent), os.path.join(parent, "home")
        seed_install(stage, home)
        dirs = leftover_dirs(home)
        extra = dict(dirs)
        data_dir = os.path.join(parent, "elsewhere", "data")
        if data_dir_set:
            touch(os.path.join(data_dir, "library", "library.db"))
            extra["BAIHE_DATA_DIR"] = data_dir
        res = run_bat(stage, "uninstall.bat", answers=f"y\nn\n{library_answer}\n", env=home_env(home, **extra))
        check(not res.timed_out, "finished instead of hanging on a prompt")
        check(res.code == 0, f"exit code 0, got {res.code}")
        check_app_removed(stage, home)
        library = os.path.join(stage, "library")
        if expect_deleted:
            check(not os.path.exists(library), "library\\ is deleted")
            check("Deleted library\\." in res.out, "says the library was deleted")
        else:
            check(os.path.exists(os.path.join(library, "library.db")), "library\\library.db is kept")
            check(os.path.exists(os.path.join(library, "dramas", "1", "note.txt")), "library\\dramas is kept")
            check("Keeping library\\" in res.out, "says the library was kept")
        if data_dir_set:
            check("BAIHE_DATA_DIR is set, so your real library is NOT in this folder:" in res.out,
                  "explains that BAIHE_DATA_DIR's library is not touched")
            check(os.path.exists(os.path.join(data_dir, "library", "library.db")),
                  "the BAIHE_DATA_DIR library is untouched")
        else:
            check("BAIHE_DATA_DIR is set" not in res.out, "no BAIHE_DATA_DIR notice when it is not set")
        check_leftovers(res.out, dirs, home)
    endgroup()


def uninstall_without_local_library():
    group("uninstall.bat: BAIHE_DATA_DIR set and no library next to the script")
    with temp_dir() as parent:
        stage, home = stage_app(parent), os.path.join(parent, "home")
        seed_install(stage, home, with_library=False)
        dirs = leftover_dirs(home)
        data_dir = os.path.join(parent, "elsewhere", "data")
        touch(os.path.join(data_dir, "library", "library.db"))
        res = run_bat(stage, "uninstall.bat", answers="y\nn\nDELETE\n",
                      env=home_env(home, BAIHE_DATA_DIR=data_dir, **dirs))
        check(not res.timed_out and res.code == 0, f"finished with exit code 0, got {res.code}")
        check("No library\\ folder found next to this script" in res.out, "says there is no local library")
        check("Type DELETE" not in res.out, "does not ask about deleting when there is nothing to delete")
        check(os.path.exists(os.path.join(data_dir, "library", "library.db")),
              "typing DELETE still leaves the BAIHE_DATA_DIR library alone")
        check_app_removed(stage, home)
        check_leftovers(res.out, dirs, home)
    endgroup()


def uninstall_cancelled():
    group("uninstall.bat: answering n at the first prompt removes nothing")
    with temp_dir() as parent:
        stage, home = stage_app(parent), os.path.join(parent, "home")
        seed_install(stage, home)
        res = run_bat(stage, "uninstall.bat", answers="n\n", env=home_env(home))
        check(not res.timed_out and res.code == 0, f"finished with exit code 0, got {res.code}")
        check("Cancelled -- nothing was removed." in res.out, "says it cancelled")
        for rel in ("venv", "model_cache", "library"):
            check(os.path.exists(os.path.join(stage, rel)), f"{rel}\\ is untouched")
        check(os.path.exists(os.path.join(home, "Desktop", "Baihe Subtitler.lnk")), "desktop shortcut is untouched")
    endgroup()


def check_uninstall():
    uninstall_cancelled()
    # The typed-DELETE rule: only the exact word deletes; "y", "yes", other casing and Enter keep the library.
    for data_dir_set in (False, True):
        suffix = ", BAIHE_DATA_DIR set" if data_dir_set else ""
        uninstall_case(f"typing DELETE deletes the library{suffix}", "DELETE", True, data_dir_set)
        for answer, label in (("y", "y"), ("yes", "yes"), ("delete", "lower-case delete"), ("", "Enter")):
            uninstall_case(f"answering {label} keeps the library{suffix}", answer, False, data_dir_set)
    uninstall_without_local_library()


# ----------------------------------------------- start.bat: install, upgrade

def library_seed_code():
    return (
        "import core, db\n"
        "db.init_db()\n"
        f"did = db.create_drama(title_en={LIBRARY_DRAMA!r})\n"
        "db.save_lines(did, [core.Line(idx=0, start=0.0, end=1.0, zh='你好', en='Hello')])\n"
        "print('seeded drama', did)\n")


def library_verify_code():
    return (
        "import db\n"
        "db.init_db()\n"
        f"rows = [d for d in db.list_dramas() if d['title_en'] == {LIBRARY_DRAMA!r}]\n"
        "assert len(rows) == 1, rows\n"
        "lines = db.load_lines(rows[0]['id'])\n"
        "assert [(l['zh'], l['en']) for l in lines] == [('你好', 'Hello')], lines\n"
        "print('library data intact')\n")


def restore_round_trip_code():
    """A full backup zip of the seeded library, a title added afterwards, then the app's own restore: the
    Windows paths are the r+b fsync of the staged databases, the msvcrt restore lock and the folder renames."""
    return (
        "import os, db\n"
        "from services import library_admin_service as las\n"
        "db.init_db()\n"
        f"[seeded] = [d['id'] for d in db.list_dramas() if d['title_en'] == {LIBRARY_DRAMA!r}]\n"
        "media = os.path.join(db.drama_dir(seeded), 'audio.wav')\n"
        "open(media, 'wb').write(b'RIFF')\n"
        "parent = os.path.dirname(os.path.abspath(db.LIBRARY_DIR))\n"
        "zip_path = os.path.join(parent, 'round_trip_backup.zip')\n"
        "las.write_backup_zip(zip_path)\n"
        "db.create_drama(title_en='added after the backup')\n"
        "data = open(zip_path, 'rb').read()\n"
        "os.remove(zip_path)\n"
        "result = las.restore_backup(data, confirm=True, confirm_text='RESTORE')\n"
        "assert result['restored'] and not result['previous_library_kept'], result\n"
        "titles = sorted(d['title_en'] for d in db.list_dramas())\n"
        f"assert titles == [{LIBRARY_DRAMA!r}], titles\n"
        "assert open(media, 'rb').read() == b'RIFF'\n"
        "base = os.path.basename(os.path.abspath(db.LIBRARY_DIR))\n"
        "left = [n for n in os.listdir(parent) if n.startswith((base + '.pre_restore_', '.restore_staging_'))]\n"
        "assert not left, left\n"
        "assert not os.path.exists(os.path.abspath(db.LIBRARY_DIR) + '.restore-lock')\n"
        "print('restore round trip ok')\n")


def stops_before_server(stage, res, port):
    check(not res.timed_out, "start.bat finished instead of hanging")
    check(res.code == 1, f"exit code 1 (stopped at the busy port, as arranged), got {res.code}")
    check(f"Something else is already using port {port}" in res.out, "stopped at the port check, before the server")
    check("Starting Baihe Studio at" not in res.out, "never reached the server launch")
    check(not os.path.exists(os.path.join(stage, "api_server.log")), "no server log, so no server was started")


def check_install_and_upgrade():
    listener, port = free_port_listener()
    with contextlib.closing(listener), temp_dir() as parent:
        stage = stage_app(parent)
        env = clean_env(BAIHE_API_PORT=str(port))
        group("install: start.bat --ci in a fresh folder")
        res = run_bat(stage, "start.bat", ["--ci"], env=env, timeout=INSTALL)
        stops_before_server(stage, res, port)
        check("Setting up a virtual environment" in res.out, "created the venv")
        check("Installing dependencies" in res.out, "installed the dependencies")
        check(os.path.exists(venv_python(stage)), "venv\\Scripts\\python.exe exists")
        done = run_app_python(
            stage, "import requests, urllib3, bs4, anthropic, fastapi, multipart, uvicorn, numpy, PIL", env)
        check(done.returncode == 0, "the core packages import in the new venv")
        check("WARNING: frontend\\dist\\index.html is missing" in res.out,
              "a missing frontend\\dist is only a warning in --ci")
        seeded = run_app_python(stage, library_seed_code(), env)
        check(seeded.returncode == 0 and os.path.exists(os.path.join(stage, "library", "library.db")),
              "the app creates library\\library.db and takes data")
        endgroup()

        group("upgrade: new files over the install, same venv and library")
        copy_app(stage)
        res = run_bat(stage, "start.bat", ["--ci"], env=env, timeout=SHORT)
        stops_before_server(stage, res, port)
        check("Setting up a virtual environment" not in res.out, "kept the existing venv")
        check("Installing dependencies" not in res.out, "did not reinstall unchanged dependencies")
        verified = run_app_python(stage, library_verify_code(), env)
        check(verified.returncode == 0, "the library data survived the upgrade")
        endgroup()

        group("upgrade: changed requirements (BAIHE_FORCE_DEPS) reinstall over the existing venv")
        res = run_bat(stage, "start.bat", ["--ci"], env={**env, "BAIHE_FORCE_DEPS": "1"}, timeout=INSTALL)
        stops_before_server(stage, res, port)
        check("Installing dependencies" in res.out, "reinstalled the dependencies")
        verified = run_app_python(stage, library_verify_code(), env)
        check(verified.returncode == 0, "the library data survived the reinstall")
        endgroup()

        group("restore: a backup zip of the library restored over it")
        restored = run_app_python(stage, restore_round_trip_code(), env)
        check(restored.returncode == 0, "backup, restore and check of the library in the installed app")
        verified = run_app_python(stage, library_verify_code(), env)
        check(verified.returncode == 0, "the library data is as backed up after the restore")
        endgroup()


CHECKS = {"setup": check_setup, "install": check_install_and_upgrade, "uninstall": check_uninstall}


def main(argv):
    # The scripts print non-ASCII symbols; the runner's console code page cannot encode all of them.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) != 2 or argv[1] not in CHECKS:
        print(__doc__)
        return 2
    CHECKS[argv[1]]()
    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
