"""
services/app_update_service.py: the app-only offer, the verified zip
download and the hand-off to installer/app_swap.py. HTTP, the process start
and Windows are faked; the zip, the install folder and the library are real
files under tmp_path.
"""

import contextlib
import datetime
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

import db  # noqa: E402
from installer import app_swap  # noqa: E402
from lib import release_signature as rs  # noqa: E402
from services import app_update_service as aus  # noqa: E402
from services import shutdown_service  # noqa: E402
from services import update_service as us  # noqa: E402
from lib.errors import ConflictError, UnsupportedOperationError  # noqa: E402
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
    monkeypatch.setattr(shutdown_service, "_token", "t" * shutdown_service.MIN_TOKEN_LENGTH)
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


def test_a_beta_base_is_new_enough_for_the_shipped_minimum(env):
    from installer import build_app_zip
    beta = dict(INSTALLED_MANIFEST, app_version="0.1.0-beta.3")
    (env["root"] / "manifest.json").write_text(json.dumps(beta))
    _serve(env, manifest=_app_manifest(min_base_version=build_app_zip.MIN_BASE_VERSION,
                                       base=app_swap.base_fingerprint(beta)))
    assert us.check()["app_update"] == "available"


def test_a_release_without_an_app_zip_offers_only_the_installer(env):
    env["http"].add(API_URL, body=json.dumps([_release_obj(with_sig=True)]).encode())
    s = us.check()
    assert s["app_update"] == "none" and s["update_available"] is True


def test_an_unreadable_manifest_copy_means_no_offer(env):
    _serve(env)
    env["http"].add(MANIFEST_URL, status=404)
    assert us.check()["app_update"] == "none"


def test_the_launcher_check_survives_the_servers_startup(monkeypatch):
    # python -m api removes the token from os.environ at startup; the update
    # card must still know the launcher started it.
    monkeypatch.setattr(shutdown_service, "_token", "")
    monkeypatch.setenv(aus.LAUNCHER_TOKEN_ENV, "t" * shutdown_service.MIN_TOKEN_LENGTH)
    shutdown_service.take_token_from_environment()
    assert aus.LAUNCHER_TOKEN_ENV not in os.environ
    assert aus.launcher_started() is True


def test_no_offer_under_the_boot_service_without_the_updater(env, monkeypatch):
    _serve(env)
    monkeypatch.setattr(shutdown_service, "_token", "")
    monkeypatch.delenv(aus.LAUNCHER_TOKEN_ENV, raising=False)
    monkeypatch.setenv("ProgramFiles", str(env["tmp"] / "no Program Files"))
    us.check()
    _download(env)
    s = us.status()
    assert s["app_update_here"] is False and s["can_apply_app"] is False
    assert s["app_update_by_service"] is False
    with pytest.raises(UnsupportedOperationError, match="no app-update helper"):
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


def test_a_check_keeps_the_releases_schema_for_the_full_installers_backup_check(env):
    _serve(env, manifest=_app_manifest(db_schema_sha256="1" * 64))
    us.check()
    assert aus._offer_schema == ("0.2.0", "1" * 64)


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


def test_apply_is_refused_while_the_full_setup_is_open(env, monkeypatch):
    """Setup replaces the app folder the swap renames: one at a time."""
    class OpenSetup:
        def poll(self):
            return None
    _serve(env)
    us.check()
    _download(env)
    monkeypatch.setattr(us, "_setup", OpenSetup())
    with pytest.raises(ConflictError) as exc:
        aus.apply()
    assert str(exc.value) == ("The full installer is starting or open. Finish or close it, "
                              "then try again.")
    assert not (env["root"] / "app-0.2.0").exists() and env["started"] == []
    assert aus._applying is False


def test_full_setup_is_refused_while_an_app_update_is_under_way(env, monkeypatch):
    monkeypatch.setattr(us, "_setup", None)
    monkeypatch.setattr(us, "_verified", {"version": "0.2.0", "name": "x.exe", "path": "x",
                                          "sha256": "0" * 64, "signature": ""})
    started = []
    monkeypatch.setattr(us, "_install", lambda verified: started.append(verified))
    applying = ("The app-only update is being applied. Wait for Baihe Studio to restart, "
                "then try again.")
    monkeypatch.setattr(aus, "_applying", True)
    with pytest.raises(ConflictError) as exc:
        us.install()
    assert str(exc.value) == applying
    monkeypatch.setattr(aus, "_applying", False)
    app_swap.write_journal(env["root"], app_swap.SWITCHING, "0.1.0", "0.2.0", "0.1.0")
    with pytest.raises(ConflictError) as exc:
        us.install()
    assert str(exc.value) == applying
    assert started == [] and us._installing is False


def test_full_setup_replaces_a_queued_app_update(env, monkeypatch):
    """A locked file leaves the switch queued with no helper running; Setup deletes
    the journal, so the in-app Install must not stay refused behind it."""
    verified = {"version": "0.2.0", "name": "x.exe", "path": "x", "sha256": "0" * 64,
                "signature": ""}
    monkeypatch.setattr(us, "_setup", None)
    monkeypatch.setattr(us, "_verified", verified)
    started = []
    monkeypatch.setattr(us, "_install", lambda v: started.append(v) or {"launched": True})
    app_swap.write_journal(env["root"], app_swap.QUEUED, "0.1.0", "0.2.0", "0.1.0")
    assert us.install() == {"launched": True}
    assert started == [verified]
    # A journal that can't be read is not "queued": it still refuses.
    (env["root"] / "app-update" / "swap.json").write_text("{", encoding="utf-8")
    with pytest.raises(ConflictError):
        us.install()


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
    assert s["app_last_result"] == ("Version 0.2.0 didn't start, so Baihe Studio went back to "
                                    "0.1.0. Couldn't tell whether the newer version changed the "
                                    "library.")
    for code in app_swap.RESULT_CODES:
        assert code in aus.RESULT_MESSAGES


def _backups(monkeypatch, created_at="2026-11-12T10:00:00+00:00"):
    """The backup copies the update sees; tests drop entries to stand in for rotation."""
    from services import auto_backup_service
    copies = ([{"name": "baihe-auto-1.zip", "created_at": created_at, "readable": True,
                "managed": True, "kept_as": None}] if created_at else [])
    monkeypatch.setattr(auto_backup_service, "settings_overview", lambda: {"copies": copies})
    return copies


def _start_update(env):
    _serve(env)
    us.check()
    _download(env)
    assert aus.apply()["started"] is True


def _change_library(sql="ALTER TABLE dramas ADD COLUMN from_the_newer_version TEXT"):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(sql)
        conn.commit()


def _rolled_back(env, new="0.2.0"):
    app_swap.clear_journal(env["root"])
    app_swap.write_result(env["root"], "rolled_back_health", "0.1.0", new)
    return us.status()


def test_a_rollback_after_the_tables_changed_says_so_and_names_the_backup(env, monkeypatch):
    _backups(monkeypatch)
    _start_update(env)
    _change_library()
    s = _rolled_back(env)
    assert s["app_rollback_library_changed"] is True
    assert s["app_rollback_backup_at"] == "2026-11-12T10:00:00+00:00"
    assert "had already changed the library's tables" in s["app_last_result"]
    assert "restore the backup from before the update" in s["app_last_result"]


def test_a_rollback_with_the_tables_unchanged_adds_nothing(env, monkeypatch):
    _backups(monkeypatch)
    _start_update(env)
    s = _rolled_back(env)
    assert s["app_rollback_library_changed"] is False and s["app_rollback_backup_at"] is None
    assert s["app_last_result"] == "Version 0.2.0 didn't start, so Baihe Studio went back to 0.1.0."


def test_a_new_index_counts_as_a_change(env, monkeypatch):
    _backups(monkeypatch)
    _start_update(env)
    _change_library("CREATE INDEX from_the_newer_version ON dramas (title_zh, id)")
    assert _rolled_back(env)["app_rollback_library_changed"] is True


def test_a_change_to_the_sources_database_counts(env, monkeypatch):
    import sqlite3
    from sources import store
    with contextlib.closing(sqlite3.connect(store.db_path())) as conn:
        conn.execute("CREATE TABLE kept (id INTEGER)")
        conn.commit()
    _backups(monkeypatch)
    _start_update(env)
    with contextlib.closing(sqlite3.connect(store.db_path())) as conn:
        conn.execute("CREATE TABLE from_the_newer_version (id INTEGER)")
        conn.commit()
    assert _rolled_back(env)["app_rollback_library_changed"] is True


def test_a_rollback_with_no_backup_still_warns(env, monkeypatch):
    _backups(monkeypatch, created_at=None)
    _start_update(env)
    _change_library()
    s = _rolled_back(env)
    assert s["app_rollback_library_changed"] is True and s["app_rollback_backup_at"] is None
    assert "had already changed the library's tables" in s["app_last_result"]
    assert "restore the backup" not in s["app_last_result"]


def test_a_backup_rotated_away_since_is_not_named(env, monkeypatch):
    copies = _backups(monkeypatch)
    _start_update(env)
    _change_library()
    copies[:] = [{"name": "baihe-auto-2.zip", "created_at": "2026-11-13T10:00:00+00:00",
                  "readable": True, "managed": True, "kept_as": None}]
    s = _rolled_back(env)
    assert s["app_rollback_library_changed"] is True and s["app_rollback_backup_at"] is None


def test_a_later_attempt_keeps_the_warning_from_the_first(env, monkeypatch):
    _backups(monkeypatch)
    _start_update(env)
    _change_library()
    _rolled_back(env)
    # A second try at the same release: the record from before the first
    # change stays, whether this try writes a result of its own or not.
    _start_update(env)
    s = us.status()
    assert s["app_rollback_library_changed"] is True
    assert s["app_rollback_backup_at"] == "2026-11-12T10:00:00+00:00"
    s = _rolled_back(env)
    assert s["app_rollback_library_changed"] is True
    assert s["app_rollback_backup_at"] == "2026-11-12T10:00:00+00:00"


def test_only_this_librarys_own_copy_is_named(env, monkeypatch):
    copies = _backups(monkeypatch)
    copies[:0] = [{"name": "other-library.zip", "created_at": "2026-11-12T11:00:00+00:00",
                   "readable": True, "managed": False, "kept_as": None},
                  {"name": "baihe-auto-0.zip", "created_at": "2026-11-12T10:30:00+00:00",
                   "readable": True, "managed": True, "kept_as": "sources"}]
    _start_update(env)
    _change_library()
    assert _rolled_back(env)["app_rollback_backup_at"] == "2026-11-12T10:00:00+00:00"


def test_sources_db_created_after_the_rollback_is_not_a_change(env, monkeypatch):
    from sources import store
    _backups(monkeypatch)
    assert not os.path.exists(store.db_path())
    _start_update(env)
    os.remove(store.db_path())
    store._initialised.clear()
    with contextlib.closing(store.connect()):
        pass
    assert _rolled_back(env)["app_rollback_library_changed"] is False


def test_a_later_rollback_names_the_version_that_changed_the_library(env, monkeypatch):
    _backups(monkeypatch)
    _start_update(env)
    _change_library()
    _rolled_back(env)
    aus._remember_library("0.1.0", "0.3.0")
    s = _rolled_back(env, new="0.3.0")
    assert s["app_rollback_library_changed"] is True
    assert "Version 0.2.0 had already changed the library's tables" in s["app_last_result"]
    assert s["app_last_result"].startswith("Version 0.3.0 didn't start")


def test_a_rollback_with_no_record_says_it_couldnt_tell(env):
    s = _rolled_back(env)
    assert s["app_rollback_library_unknown"] is True
    assert s["app_rollback_library_changed"] is False
    assert s["app_last_result"].endswith(
        "Couldn't tell whether the newer version changed the library.")


def test_an_unreadable_sources_db_says_it_couldnt_tell(env, monkeypatch):
    import sqlite3
    from sources import store
    _backups(monkeypatch)
    _start_update(env)

    def damaged():
        raise sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(store, "connect", damaged)
    s = _rolled_back(env)
    assert s["app_rollback_library_unknown"] is True
    assert s["app_rollback_library_changed"] is False


def test_a_library_change_is_still_seen_with_sources_db_unreadable(env, monkeypatch):
    import sqlite3
    from sources import store
    _backups(monkeypatch)
    _start_update(env)
    _change_library()

    def damaged():
        raise sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(store, "connect", damaged)
    s = _rolled_back(env)
    assert s["app_rollback_library_changed"] is True
    assert s["app_rollback_library_unknown"] is False


def test_a_kept_update_or_unchanged_rollback_is_not_unknown(env, monkeypatch):
    _backups(monkeypatch)
    _start_update(env)
    assert _rolled_back(env)["app_rollback_library_unknown"] is False
    app_swap.write_result(env["root"], "updated", "0.1.0", "0.2.0")
    assert us.status()["app_rollback_library_unknown"] is False


def test_a_record_without_the_library_layout_says_it_couldnt_tell(env):
    db.set_app_setting(aus.BEFORE_UPDATE_KEY, {"old": "0.1.0", "attempts": ["0.2.0"],
                                               "layout": "x" * 64, "backup_name": None,
                                               "backup_at": None})
    s = _rolled_back(env)
    assert s["app_rollback_library_unknown"] is True
    assert s["app_rollback_library_changed"] is False


def test_a_record_from_another_update_is_not_used(env, monkeypatch):
    db.set_app_setting(aus.BEFORE_UPDATE_KEY, {"old": "0.0.9", "attempts": ["0.1.0"],
                                               "layout": "x" * 64, "backup_name": None,
                                               "backup_at": None})
    app_swap.write_result(env["root"], "rolled_back_health", "0.1.0", "0.2.0")
    s = us.status()
    assert s["app_rollback_library_changed"] is False and s["app_rollback_library_unknown"] is True


def test_a_kept_update_never_warns_about_the_library(env, monkeypatch):
    db.set_app_setting(aus.BEFORE_UPDATE_KEY, {"old": "0.1.0", "attempts": ["0.2.0"],
                                               "layout": "x" * 64, "backup_name": None,
                                               "backup_at": None})
    app_swap.write_result(env["root"], "updated", "0.1.0", "0.2.0")
    assert us.status()["app_rollback_library_changed"] is False


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
    (OSError(22, "Invalid argument", "x.pyc"), "couldn't start the update"),
    (OSError(30, "Read-only file system", "x.pyc"), "couldn't start the update"),
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


# --- the boot service: hand-off to the BaiheUpdate helper ---------------------------

class FakeSc:
    def __init__(self, returncode=0):
        self.calls, self.returncode = [], returncode

    def __call__(self, cmd, timeout):
        self.calls.append(cmd)

        class Result:
            pass
        result = Result()
        result.returncode = self.returncode
        return result


@pytest.fixture
def service_env(env, monkeypatch):
    """The boot service (no launcher token) on a PC whose Setup installed the helper."""
    monkeypatch.setattr(shutdown_service, "_token", "")
    monkeypatch.delenv(aus.LAUNCHER_TOKEN_ENV, raising=False)
    program_files = env["tmp"] / "Program Files"
    wrapper = program_files / aus.ADMIN_FOLDER / "service" / (aus.UPDATER_SERVICE + ".exe")
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(b"MZ")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    sc = FakeSc()
    monkeypatch.setattr(aus, "run_captured", sc)
    env["sc"] = sc
    return env


def test_the_service_hands_a_verified_update_to_the_updater_and_unpacks_nothing(service_env):
    env = service_env
    _serve(env)
    us.check()
    _download(env)
    s = us.status()
    assert s["app_update_here"] and s["app_update_by_service"] and s["can_apply_app"]
    assert aus.apply() == {"started": True, "version": "0.2.0"}
    updates = Path(us.updates_dir())
    assert json.loads((updates / aus.REQUEST_NAME).read_text()) == {"version": "0.2.0",
                                                                    "name": ZIP_NAME}
    signature = rs.parse_signature((updates / (ZIP_NAME + ".sig")).read_bytes())
    sha = hashlib.sha256((updates / ZIP_NAME).read_bytes()).hexdigest()
    assert rs.verify(us._release_public_key(), signature, ZIP_NAME, sha)
    assert [c[1:] for c in env["sc"].calls] == [["start", "BaiheUpdate"]]
    assert not (env["root"] / "app-0.2.0").exists() and env["started"] == []
    assert app_swap.read_journal(env["root"]) is None      # the helper writes it, not this server


def test_the_service_refuses_while_a_job_runs_and_leaves_no_request(service_env, monkeypatch):
    env = service_env
    _serve(env)
    us.check()
    _download(env)
    monkeypatch.setattr(aus.background_jobs, "active_job_ids", lambda: ["j1"])
    with pytest.raises(ConflictError, match="running jobs"):
        aus.apply()
    assert env["sc"].calls == [] and not (Path(us.updates_dir()) / aus.REQUEST_NAME).exists()


def test_a_helper_that_wont_start_leaves_no_request_behind(service_env):
    env = service_env
    env["sc"].returncode = 5
    _serve(env)
    us.check()
    _download(env)
    with pytest.raises(DependencyUnavailableError, match="couldn't start the update"):
        aus.apply()
    assert not (Path(us.updates_dir()) / aus.REQUEST_NAME).exists()


def test_the_service_refuses_a_zip_for_another_base_before_asking_the_updater(service_env):
    env = service_env
    _serve(env)
    us.check()
    _download(env)
    other = dict(app_swap.base_fingerprint(INSTALLED_MANIFEST), caddy_sha256="new")
    with aus._lock:
        aus._verified["path"] = str(env["tmp"] / "other.zip")
    (env["tmp"] / "other.zip").write_bytes(_zip_bytes(env["tmp"], _app_manifest(base=other)))
    with pytest.raises(ConflictError, match="different Python"):
        aus.apply()
    assert env["sc"].calls == []


def _settle_download():
    thread = aus._download_thread
    if thread is not None:
        thread.join(10)


def test_automatic_updates_are_off_unless_the_owner_turned_them_on(service_env):
    env = service_env
    _serve(env)
    s = us.check()
    assert s["app_auto_apply"] is False
    assert s["app_download"] == "idle" and env["sc"].calls == []


def test_with_the_opt_in_a_check_downloads_and_applies(service_env):
    env = service_env
    aus.set_service_auto_apply(True)
    _serve(env)
    us.check()
    _settle_download()
    assert [c[1:] for c in env["sc"].calls] == [["start", "BaiheUpdate"]]
    assert (Path(us.updates_dir()) / aus.REQUEST_NAME).is_file()


def test_with_the_opt_in_but_a_job_running_the_next_check_applies_it(service_env, monkeypatch):
    env = service_env
    aus.set_service_auto_apply(True)
    _serve(env)
    monkeypatch.setattr(aus.background_jobs, "active_job_ids", lambda: ["j1"])
    us.check()
    _settle_download()
    assert env["sc"].calls == [] and us.status()["app_verified"] is True
    monkeypatch.setattr(aus.background_jobs, "active_job_ids", lambda: [])
    us.check()
    assert [c[1:] for c in env["sc"].calls] == [["start", "BaiheUpdate"]]


def test_the_opt_in_does_nothing_for_a_launcher_started_server(env):
    aus.set_service_auto_apply(True)
    _serve(env)
    us.check()
    _settle_download()
    assert us.status()["app_download"] == "idle"
