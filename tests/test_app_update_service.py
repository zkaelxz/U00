"""
services/app_update_service.py: the app-only offer, the verified zip
download and the hand-off to installer/app_swap.py. HTTP, the process start
and Windows are faked; the zip, the install folder and the library are real
files under tmp_path.
"""

import datetime
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from installer import app_swap  # noqa: E402
from lib import release_signature as rs  # noqa: E402
from services import app_update_service as aus  # noqa: E402
from services import update_service as us  # noqa: E402
from services.service_errors import ConflictError, UnsupportedOperationError  # noqa: E402
from tests.test_update_service import FakeHttp, _release_obj  # noqa: E402
from lib.errors import DependencyUnavailableError  # noqa: E402

API_URL = "https://api.github.com/repos/zkaelxz/U00/releases?per_page=10"
BASE = "https://github.com/zkaelxz/U00/releases/download/v0.2.0/"
ZIP_NAME = "BaiheStudio-App-0.2.0.zip"
ZIP_URL, HASH_URL, SIG_URL = BASE + ZIP_NAME, BASE + ZIP_NAME + ".sha256", BASE + ZIP_NAME + ".sig"
MANIFEST_URL = BASE + "BaiheStudio-App-0.2.0.json"
INSTALLED_MANIFEST = {"product": "Baihe Studio", "app_version": "0.1.0",
                      "python": {"embed_sha256": "p"},
                      "wheels": [{"file": "a.whl", "sha256": "1"}],
                      "services": {"caddy": {"sha256": "c"}, "winsw": {"sha256": "s"}}}


def _app_manifest(**over):
    doc = {"product": "Baihe Studio", "layer": "app", "version": "0.2.0",
           "min_base_version": "0.1.0", "base": app_swap.base_fingerprint(INSTALLED_MANIFEST),
           "db_schema_sha256": None}
    doc.update(over)
    return doc


def _zip_bytes(tmp_path, manifest):
    path = tmp_path / "build.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for rel in app_swap.REQUIRED_ENTRIES:
            if rel != app_swap.APP_MANIFEST_NAME:
                zf.writestr(rel, f"# {rel}\n")
        zf.writestr(app_swap.APP_MANIFEST_NAME, json.dumps(manifest))
    return path.read_bytes()


@pytest.fixture
def keys():
    private, public = rs.generate()
    return rs.parse_private_key(private), public


@pytest.fixture
def env(isolated_db, monkeypatch, tmp_path, keys):
    """An installed copy at tmp_path/'Baihe Studio' with this test's own key."""
    root = tmp_path / "Baihe Studio"
    app = root / "app"
    (app / "installer").mkdir(parents=True)
    (app / "installer" / "app_swap.py").write_text("# helper\n")
    (app / "INSTALLED").write_text("D:\\Baihe\n")
    (root / "manifest.json").write_text(json.dumps(INSTALLED_MANIFEST))
    key_file = tmp_path / "release_public_key.txt"
    key_file.write_text(keys[1] + "\n")
    temp = tmp_path / "temp"
    temp.mkdir()
    http = FakeHttp()
    monkeypatch.setattr(us.requests, "get", http.get)
    monkeypatch.setattr(us.tempfile, "tempdir", str(temp))
    monkeypatch.setattr(us, "_APP_DIR", str(app))
    monkeypatch.setattr(us, "PUBLIC_KEY_PATH", str(key_file))
    monkeypatch.setattr(us, "_state", dict(us._state, download="idle", latest=None))
    monkeypatch.setattr(us, "_release", None)
    monkeypatch.setattr(us, "_verified", None)
    monkeypatch.setattr(us, "_is_installed", lambda: True)
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    monkeypatch.setattr(us, "current_version", lambda: "0.1.0")
    monkeypatch.setattr(aus, "_state", dict(aus._state, offer="none", offer_version=None,
                                            download="idle", downloaded_bytes=0,
                                            download_error=None))
    monkeypatch.setattr(aus, "_release", None)
    monkeypatch.setattr(aus, "_verified", None)
    monkeypatch.setenv(aus.LAUNCHER_TOKEN_ENV, "t")
    monkeypatch.delenv(us.REPO_ENV, raising=False)
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_env_names", lambda names, env_path=None: None)
    started = []
    monkeypatch.setattr(aus.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))
    return {"root": root, "http": http, "started": started, "seed": keys[0], "tmp": tmp_path}


def _serve(env, manifest=None, zip_manifest=None, signed=True, sign_name=ZIP_NAME,
           sig_bytes=None):
    manifest = manifest or _app_manifest(db_schema_sha256=aus._library_schema())
    data = _zip_bytes(env["tmp"], zip_manifest or manifest)
    sha = hashlib.sha256(data).hexdigest()
    release = _release_obj(with_sig=True)
    release["assets"] += [
        {"name": ZIP_NAME, "size": len(data), "browser_download_url": ZIP_URL},
        {"name": ZIP_NAME + ".sha256", "size": 90, "browser_download_url": HASH_URL},
        {"name": "BaiheStudio-App-0.2.0.json", "size": 200, "browser_download_url": MANIFEST_URL}]
    if signed:
        release["assets"].append({"name": ZIP_NAME + ".sig", "size": 89,
                                  "browser_download_url": SIG_URL})
        env["http"].add(SIG_URL, body=sig_bytes if sig_bytes is not None else
                        rs.format_signature(rs.sign(env["seed"], sign_name, sha)).encode())
    http = env["http"]
    http.add(API_URL, body=json.dumps([release]).encode())
    http.add(MANIFEST_URL, body=json.dumps(manifest).encode())
    http.add(HASH_URL, body=f"{sha}  {ZIP_NAME}\n".encode())
    http.add(ZIP_URL, body=data, headers={"Content-Length": str(len(data))})
    return data


def _download(env):
    aus.download()
    with aus._lock:
        aus._state["download"] = "verified"


# --- the offer ------------------------------------------------------------------

def test_offered_when_the_base_layer_matches(env):
    _serve(env)
    s = us.check()
    assert s["app_update"] == "available" and s["app_update_here"] is True
    assert s["update_available"] is True        # the full installer stays on offer too


def test_needs_the_full_installer_when_wheels_or_caddy_differ(env):
    other = dict(app_swap.base_fingerprint(INSTALLED_MANIFEST), caddy_sha256="new")
    _serve(env, manifest=_app_manifest(base=other))
    assert us.check()["app_update"] == "needs_full_installer"
    with pytest.raises(ConflictError, match="no app-only update"):
        aus.download()


def test_needs_the_full_installer_when_the_base_is_too_old(env):
    _serve(env, manifest=_app_manifest(min_base_version="0.1.5"))
    assert us.check()["app_update"] == "needs_full_installer"


def test_a_release_without_an_app_zip_offers_only_the_installer(env):
    env["http"].add(API_URL, body=json.dumps([_release_obj(with_sig=True)]).encode())
    s = us.check()
    assert s["app_update"] == "none" and s["update_available"] is True


def test_an_unreadable_manifest_copy_means_no_offer(env):
    _serve(env)
    env["http"].add(MANIFEST_URL, status=404)
    assert us.check()["app_update"] == "none"


def test_no_offer_under_the_boot_service(env, monkeypatch):
    _serve(env)
    monkeypatch.delenv(aus.LAUNCHER_TOKEN_ENV)
    us.check()
    _download(env)
    s = us.status()
    assert s["app_update_here"] is False and s["can_apply_app"] is False
    with pytest.raises(UnsupportedOperationError, match="boot service"):
        aus.apply()


# --- download -------------------------------------------------------------------

def test_downloads_and_verifies_the_signed_zip(env):
    _serve(env)
    us.check()
    _download(env)
    s = us.status()
    assert s["app_verified"] and s["app_verified_version"] == "0.2.0" and s["can_apply_app"]
    assert (Path(us.updates_dir()) / ZIP_NAME).is_file()


def test_the_download_runs_on_its_own_thread(env):
    _serve(env)
    us.check()
    assert aus.start_download()["app_download"] == "downloading"
    aus._download_thread.join(30)
    assert not aus._download_thread.is_alive()
    s = us.status()
    assert s["app_download"] == "verified" and s["can_apply_app"]
    with pytest.raises(ConflictError, match="no app-only update"):
        env["http"].add(API_URL, body=b"[]")
        us.check()
        aus.start_download()


def test_a_missing_signature_is_refused_before_the_zip_downloads(env):
    _serve(env, signed=False)
    us.check()
    with pytest.raises(ConflictError, match="isn't signed"):
        aus.download()
    assert not any(url == ZIP_URL for url, _ in env["http"].calls)


@pytest.mark.parametrize("case", ["installer_name", "malformed", "other_key"])
def test_a_wrong_signature_is_refused_and_the_zip_deleted(env, case):
    if case == "installer_name":
        # A valid signature, but for the installer's name: it must not pass for the zip.
        _serve(env, sign_name="BaiheStudio-Setup-0.2.0.exe")
    elif case == "malformed":
        _serve(env, sig_bytes=b"not base64!")
    else:
        other_seed = rs.parse_private_key(rs.generate()[0])
        env_other = dict(env, seed=other_seed)
        _serve(env_other)
    us.check()
    with pytest.raises(ConflictError):
        aus.download()
    assert not (Path(us.updates_dir()) / ZIP_NAME).exists()
    assert not aus.status_fields()["app_verified"]


def test_an_oversize_zip_is_refused(env, monkeypatch):
    _serve(env)
    us.check()
    monkeypatch.setattr(aus, "MAX_APP_ZIP_BYTES", 10)
    with pytest.raises(DependencyUnavailableError, match="larger than allowed"):
        aus.download()


# --- apply ----------------------------------------------------------------------

def test_apply_unpacks_beside_the_app_and_hands_over(env, monkeypatch):
    monkeypatch.setenv("BAIHE_API_PORT", "8611")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "user-key")
    _serve(env)
    us.check()
    _download(env)
    assert aus.apply() == {"started": True, "version": "0.2.0"}
    root = env["root"]
    staged = root / "app-0.2.0"
    assert (staged / "installer" / "launcher.py").is_file()
    assert (staged / "INSTALLED").read_text() == "D:\\Baihe\n"
    assert (root / "app" / "INSTALLED").is_file()       # the running app is untouched
    assert app_swap.read_journal(root) == {"state": "staged", "old": "0.1.0", "new": "0.2.0",
                                           "base_version": "0.1.0", "port": 8611, "step": 0}
    assert (root / "app-update" / "app_swap.py").read_text() == "# helper\n"
    ((cmd, kw),) = env["started"]
    assert cmd[1:] == ["-I", str(root / "app-update" / "app_swap.py"), "apply", "--root", str(root)]
    assert kw["cwd"] == str(root / "app-update")
    # The restarted server needs the user's keys; only this server's own
    # shutdown token and job name stay behind.
    assert aus.LAUNCHER_TOKEN_ENV not in kw["env"]
    assert "BAIHE_PROCESS_GROUP_NAME" not in kw["env"]
    assert kw["env"]["DEEPSEEK_API_KEY"] == "user-key"
    assert not list(Path(us.tempfile.gettempdir()).iterdir())   # the private copy is gone


def test_apply_refuses_while_a_job_runs(env, monkeypatch):
    _serve(env)
    us.check()
    _download(env)
    monkeypatch.setattr(aus.background_jobs, "active_job_ids", lambda: ["j1"])
    with pytest.raises(ConflictError, match="running jobs"):
        aus.apply()
    assert not (env["root"] / "app-0.2.0").exists() and env["started"] == []


def test_apply_refuses_a_zip_whose_own_manifest_names_another_base(env):
    # The manifest copy read for the offer matches; the signed zip's own doesn't.
    other = dict(app_swap.base_fingerprint(INSTALLED_MANIFEST), wheels_sha256="new")
    _serve(env, zip_manifest=_app_manifest(base=other))
    us.check()
    _download(env)
    with pytest.raises(ConflictError, match="full installer"):
        aus.apply()
    assert not (env["root"] / "app-0.2.0").exists() and env["started"] == []
    assert app_swap.read_journal(env["root"]) is None


def test_apply_refuses_a_changed_download(env):
    _serve(env)
    us.check()
    _download(env)
    (Path(us.updates_dir()) / ZIP_NAME).write_bytes(b"swapped")
    with pytest.raises(ConflictError, match="Download it again"):
        aus.apply()
    assert env["started"] == []


def test_a_database_change_needs_a_recent_backup(env, monkeypatch):
    _serve(env, manifest=_app_manifest(db_schema_sha256="0" * 64))
    us.check()
    _download(env)
    from services import auto_backup_service
    monkeypatch.setattr(auto_backup_service, "settings_overview", lambda: {"last_run_at": None})
    with pytest.raises(ConflictError, match="Back up the library first"):
        aus.apply()
    assert not (env["root"] / "app-0.2.0").exists()
    recent = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    monkeypatch.setattr(auto_backup_service, "settings_overview", lambda: {"last_run_at": recent})
    assert aus.apply()["started"] is True


def test_an_unchanged_database_needs_no_backup(env, monkeypatch):
    _serve(env, manifest=_app_manifest(db_schema_sha256=aus._library_schema()))
    us.check()
    _download(env)
    from services import auto_backup_service
    monkeypatch.setattr(auto_backup_service, "settings_overview", lambda: {"last_run_at": None})
    assert aus.apply()["started"] is True


def test_a_second_apply_is_refused_while_the_first_is_starting(env, monkeypatch):
    import threading
    _serve(env)
    us.check()
    _download(env)
    inside, release = threading.Event(), threading.Event()
    start_helper = aus._start_helper

    def slow_start(root):
        inside.set()
        assert release.wait(10)
        start_helper(root)
    monkeypatch.setattr(aus, "_start_helper", slow_start)
    results = []
    first = threading.Thread(target=lambda: results.append(aus.apply()))
    first.start()
    assert inside.wait(10)
    with pytest.raises(ConflictError, match="already starting"):
        aus.apply()
    release.set()
    first.join(10)
    assert results == [{"started": True, "version": "0.2.0"}]
    assert len(env["started"]) == 1


def test_a_failed_apply_lets_the_next_one_start(env, monkeypatch):
    _serve(env)
    us.check()
    _download(env)
    monkeypatch.setattr(aus.background_jobs, "active_job_ids", lambda: ["j1"])
    with pytest.raises(ConflictError, match="running jobs"):
        aus.apply()
    monkeypatch.setattr(aus.background_jobs, "active_job_ids", lambda: [])
    assert aus.apply()["started"] is True


def test_apply_refuses_while_an_earlier_swap_is_unfinished(env):
    _serve(env)
    us.check()
    _download(env)
    app_swap.write_journal(env["root"], app_swap.QUEUED, "0.1.0", "0.2.0", "0.1.0")
    with pytest.raises(ConflictError, match="still being finished"):
        aus.apply()


def test_a_failed_helper_start_undoes_the_staging(env, monkeypatch):
    _serve(env)
    us.check()
    _download(env)

    def refuse(cmd, **kw):
        raise OSError("no")
    monkeypatch.setattr(aus.subprocess, "Popen", refuse)
    with pytest.raises(DependencyUnavailableError, match="couldn't start the update"):
        aus.apply()
    assert not (env["root"] / "app-0.2.0").exists()
    assert app_swap.read_journal(env["root"]) is None


def test_the_last_swap_is_reported_in_plain_words(env):
    app_swap.write_result(env["root"], "rolled_back_health", "0.1.0", "0.2.0")
    s = us.status()
    assert s["app_last_result"] == "Version 0.2.0 didn't start, so Baihe Studio went back to 0.1.0."
    for code in app_swap.RESULT_CODES:
        assert code in aus.RESULT_MESSAGES


def test_status_never_carries_a_path_or_url(env):
    _serve(env)
    us.check()
    _download(env)
    text = json.dumps(us.status())
    assert "http" not in text and str(env["tmp"]) not in text


def test_app_update_routes_are_registered():
    from api.routers import update_routes
    paths = {r.path for r in update_routes.router.routes}
    assert {"/api/system/update/app/download", "/api/system/update/app/apply"} <= paths


def test_old_app_zips_are_cleaned_once_installed(env, monkeypatch):
    folder = Path(us.updates_dir())
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("BaiheStudio-App-0.1.0.zip", "BaiheStudio-App-0.2.0.zip",
                 "BaiheStudio-App-0.3.0.zip.part"):
        (folder / name).write_bytes(b"x")
    monkeypatch.setattr(us, "include_prereleases", lambda: False)
    us.cleanup_leftovers()
    assert sorted(p.name for p in folder.iterdir()) == ["BaiheStudio-App-0.2.0.zip"]



def test_a_full_disk_while_copying_the_zip_says_so_not_download_again(env, monkeypatch):
    import errno
    _serve(env)
    us.check()
    _download(env)

    def full(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device", dst)
    monkeypatch.setattr(aus.shutil, "copyfile", full)
    with pytest.raises(DependencyUnavailableError, match="^The disk is full.$"):
        aus.apply()
    assert aus.status_fields()["app_verified"] is True      # freeing space is enough
    assert not list(Path(us.tempfile.gettempdir()).iterdir())
    assert env["started"] == []


@pytest.mark.parametrize("error, message", [
    (OSError(28, "No space left on device"), "^The disk is full.$"),
    (PermissionError(13, "Access is denied", "x.pyc"), "couldn't start the update"),
])
def test_a_failed_unpack_gives_a_reason_and_changes_nothing(env, monkeypatch, error, message):
    _serve(env)
    us.check()
    _download(env)

    def fail(zip_path, dest, cap=app_swap.MAX_UNPACKED_BYTES):
        raise error
    monkeypatch.setattr(aus.app_swap, "unpack", fail)
    with pytest.raises(DependencyUnavailableError, match=message):
        aus.apply()
    assert app_swap.read_journal(env["root"]) is None and env["started"] == []
    assert not list(Path(us.tempfile.gettempdir()).iterdir())
