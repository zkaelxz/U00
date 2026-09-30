"""Step 115b: import with lightnovel-crawler (a user-installed GPL-3.0 program
Baihe only runs as a separate process). Fully mocked: subprocess.Popen,
shutil.which and the URL guard are fakes; real lncrawl never runs."""

import io
import os
import subprocess
import time
import zipfile

import pytest

import background_jobs
import db
import diagnostics
from services import lncrawl_service as svc
from services import novel_attach_service, settings_service, url_guard
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

URL = "https://novels.example.com/novel/some-title"


def _epub_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>')
        z.writestr("c.opf", '<package><manifest><item id="a" href="a.xhtml"/>'
                            '<item id="b" href="b.xhtml"/></manifest>'
                            '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        z.writestr("a.xhtml", "<html><body><p>Chapter one</p></body></html>")
        z.writestr("b.xhtml", "<html><body><p>Chapter two</p></body></html>")
    return buf.getvalue()


class FakeProc:
    """A Popen stand-in. `on_start(cwd, env)` runs at construction (e.g. to
    write the EPUB lncrawl would make); `finish_after` polls later it exits
    with `returncode`, or it never exits (None) until killed."""
    instances = []

    def __init__(self, argv, output=b"", returncode=0, finish_after=0, on_start=None, **kw):
        self.argv = argv
        self.kw = kw
        self.stdout = io.BytesIO(output)
        self._rc = returncode
        self._polls_left = finish_after
        self.killed = False
        self.returncode = None
        self.pid = 4242
        if on_start:
            on_start(kw.get("cwd"), kw.get("env"))
        FakeProc.instances.append(self)

    def poll(self):
        if self.returncode is not None:     # like Popen: an exit status sticks
            return self.returncode
        if self.killed:
            self.returncode = -9
        elif self._polls_left is not None:
            if self._polls_left <= 0:
                self.returncode = self._rc
            self._polls_left -= 1
        return self.returncode

    def wait(self, timeout=None):
        return self.poll()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    FakeProc.instances = []
    monkeypatch.setattr(svc, "_POLL_SECONDS", 0.01)
    monkeypatch.setattr(url_guard, "resolve_public", lambda url: "93.184.216.34")
    killed = []

    def kill_tree(proc):
        killed.append(proc)
        proc.killed = True
    monkeypatch.setattr(background_jobs, "_kill_tree", kill_tree)
    return killed


@pytest.fixture
def program(tmp_path, monkeypatch):
    path = tmp_path / "bin" / "lncrawl"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(svc.shutil, "which",
                        lambda name: str(path) if name == "lncrawl" else None)
    return str(path)


def _popen(monkeypatch, **fake_kw):
    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return FakeProc(argv, **fake_kw, **kw)
    monkeypatch.setattr(svc.subprocess, "Popen", popen)
    return seen


def _write_epub(cwd, env):
    out = os.path.join(env["LNCRAWL_DATA_PATH"], "novels", "x", "artifacts")
    os.makedirs(out)
    with open(os.path.join(out, "Some Title.epub"), "wb") as f:
        f.write(_epub_bytes())


def _wait(job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        s = background_jobs.get_status(job_id)
        if s and s["status"] not in ("running", "queued"):
            return s
        time.sleep(0.02)
    raise AssertionError("job did not finish")


# --- argv -------------------------------------------------------------------

class TestArgv:
    def test_fixed_argv_all(self):
        assert svc.build_argv("/x/lncrawl", URL) == [
            "/x/lncrawl", "crawl", "--noin", "--format", "epub", "--all", "--", URL]

    @pytest.mark.parametrize("rng", ["first", "last"])
    def test_first_last_count(self, rng):
        argv = svc.build_argv("lncrawl", URL, rng, 12)
        assert argv[5:] == [f"--{rng}", "12", "--", URL]

    @pytest.mark.parametrize("url", ["--config=/etc/x", "-c", "file:///etc/passwd", "ftp://x/y", ""])
    def test_url_can_never_be_an_option(self, url):
        with pytest.raises(InvalidInputError):
            svc.build_argv("lncrawl", url)

    @pytest.mark.parametrize("rng,count", [("range", 3), ("first", None), ("first", 0),
                                           ("last", 6000), ("first", True), ("first", "3")])
    def test_bad_range(self, rng, count):
        with pytest.raises(InvalidInputError):
            svc.build_argv("lncrawl", URL, rng, count)

    def test_popen_gets_list_no_shell_and_clean_env(self, isolated_db, program, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        monkeypatch.setenv("BAIHE_API_AUTH", "on")
        monkeypatch.setenv("HF_TOKEN", "hf_x")
        monkeypatch.setenv("LNCRAWL_CONFIG", "/home/me/lncrawl.json")
        monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/db")
        seen = _popen(monkeypatch, on_start=_write_epub)
        did = db.create_drama(title_en="D")
        job = svc.start_import(did, URL, "first", 5)["job_id"]
        assert _wait(job)["status"] == "done"
        argv, kw = seen["argv"], seen["kw"]
        assert isinstance(argv, list) and argv[0] == program
        assert argv[-2:] == ["--", URL] and "--first" in argv
        assert kw["shell"] is False and kw["stdin"] == subprocess.DEVNULL
        env = kw["env"]
        assert "ANTHROPIC_API_KEY" not in env and "HF_TOKEN" not in env
        assert not any(k.startswith("BAIHE_") for k in env)
        assert "LNCRAWL_CONFIG" not in env and "DATABASE_URL" not in env
        # lncrawl's data folder is a temp folder inside the drama folder
        assert os.path.dirname(env["LNCRAWL_DATA_PATH"]) == db.drama_dir(did)
        assert kw["cwd"] == env["LNCRAWL_DATA_PATH"]


# --- detection --------------------------------------------------------------

class TestDetection:
    def test_not_installed(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc.shutil, "which", lambda name: None)
        assert svc.get_status() == {"installed": False, "path_configured": False}
        assert diagnostics.check_dependency("lncrawl") is False

    def test_on_path(self, isolated_db, program):
        assert svc.find_program() == program
        assert diagnostics.check_dependency("lncrawl") is True

    def test_settings_path_must_be_an_lncrawl_file(self, isolated_db, tmp_path, monkeypatch):
        monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/lncrawl")
        other = tmp_path / "calc.exe"
        other.write_text("")
        settings_service.set_settings({"lncrawl_cmd": str(other)})
        # a configured path wins over PATH, and is refused if it isn't lncrawl
        assert svc.find_program() is None
        assert svc.get_status() == {"installed": False, "path_configured": True}
        good = tmp_path / "lncrawl.exe"
        good.write_text("")
        settings_service.set_settings({"lncrawl_cmd": str(good)})
        assert svc.find_program() == str(good)
        settings_service.set_settings({"lncrawl_cmd": str(tmp_path / "missing" / "lncrawl")})
        assert svc.find_program() is None

    @pytest.mark.parametrize("name", ["lncrawl.cmd", "lncrawl.bat", "LNCRAWL.CMD"])
    def test_batch_launchers_are_refused(self, isolated_db, tmp_path, monkeypatch, name):
        # cmd.exe would read "&" in a pasted URL as a command separator.
        launcher = tmp_path / name
        launcher.write_text("")
        monkeypatch.setattr(svc.shutil, "which", lambda n: str(launcher))
        assert svc.find_program() is None
        settings_service.set_settings({"lncrawl_cmd": str(launcher)})
        assert svc.find_program() is None

    def test_registered_not_offered_and_never_imported(self, isolated_db, monkeypatch):
        dep = diagnostics.OPTIONAL_DEPENDENCIES["lightnovel-crawler"]
        assert dep[0] == "lncrawl" and dep[2] == "feature" and "GPL-3.0" in dep[1]
        reason = diagnostics.NOT_OFFERED_FOR_INSTALL["lightnovel-crawler"]
        assert "you install yourself" in reason and "pipx install lightnovel-crawler" in reason
        from services import diagnostics_gaps_service
        assert "lightnovel-crawler" not in diagnostics_gaps_service.installable_packages()
        # detection never asks importlib about the lncrawl module
        calls = []
        monkeypatch.setattr(diagnostics.importlib.util, "find_spec",
                            lambda name, *a: calls.append(name))
        monkeypatch.setattr(svc.shutil, "which", lambda name: None)
        diagnostics.check_dependency("lncrawl")
        assert calls == []


# --- start checks -----------------------------------------------------------

class TestStart:
    def test_not_installed_503(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc.shutil, "which", lambda name: None)
        did = db.create_drama(title_en="D")
        with pytest.raises(DependencyUnavailableError):
            svc.start_import(did, URL)

    def test_unknown_drama(self, isolated_db, program):
        with pytest.raises(NotFoundError):
            svc.start_import(999, URL)

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd",
                                     "https://a b.com/", "http://", "not a url"])
    def test_bad_urls(self, isolated_db, program, url):
        did = db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            svc.start_import(did, url)

    def test_private_host_refused_before_launch(self, isolated_db, program, monkeypatch):
        def refuse(url):
            raise url_guard.UnsafeURLError(url_guard.NOT_PUBLIC)
        monkeypatch.setattr(url_guard, "resolve_public", refuse)
        seen = _popen(monkeypatch)
        did = db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError, match="not a public"):
            svc.start_import(did, "http://127.0.0.1:8000/admin")
        assert seen == {}

    def test_other_job_for_drama_409(self, isolated_db, program, monkeypatch):
        did = db.create_drama(title_en="D")
        monkeypatch.setattr(svc.drama_service, "job_running_for_drama", lambda d: True)
        with pytest.raises(ConflictError):
            svc.start_import(did, URL)

    def test_job_prefix_blocks_other_drama_actions(self):
        assert "lncrawl_" in background_jobs.DRAMA_JOB_PREFIXES


# --- the job ----------------------------------------------------------------

def _drama_tmp_dirs(did):
    return [n for n in os.listdir(db.drama_dir(did)) if n.startswith(".lncrawl_")]


class TestJob:
    def test_epub_is_attached_and_temp_removed(self, isolated_db, program, monkeypatch):
        _popen(monkeypatch, output=b"Fetching...\n", on_start=_write_epub)
        did = db.create_drama(title_en="D")
        job = svc.start_import(did, URL, mode="replace")["job_id"]
        s = _wait(job)
        assert s["status"] == "done", s
        assert s["result"] == {"char_count": len("Chapter one\n\nChapter two"), "epub_chapters": 2}
        assert "Chapter two" in novel_attach_service._read_novel(did)
        assert _drama_tmp_dirs(did) == []

    def test_epub_goes_through_the_attach_service(self, isolated_db, program, monkeypatch):
        _popen(monkeypatch, on_start=_write_epub)
        calls = []

        def attach(drama_id, fileobj, mode):
            calls.append((drama_id, fileobj.read()[:2], mode))
            return {"char_count": 1, "epub_chapters": 1}
        monkeypatch.setattr(novel_attach_service, "attach_epub_from_job", attach)
        did = db.create_drama(title_en="D")
        _wait(svc.start_import(did, URL, mode="append")["job_id"])
        assert calls == [(did, b"PK", "append")]

    def test_attach_limits_still_apply(self, isolated_db, program, monkeypatch):
        monkeypatch.setattr(novel_attach_service, "MAX_EPUB_BYTES", 10)
        _popen(monkeypatch, on_start=_write_epub)
        did = db.create_drama(title_en="D")
        s = _wait(svc.start_import(did, URL)["job_id"])
        assert s["status"] == "error" and "too large" in s["error"]
        assert _drama_tmp_dirs(did) == []

    def test_no_epub_is_an_error_with_redacted_tail(self, isolated_db, program, monkeypatch):
        out = (b"\x1b[31mError\x1b[0m fetching https://site.example/novel?token=abc123secret\n"
               b"Authorization: Bearer sk-ant-api03-abcdefghijklmnopqrstuvwxyz\n")
        _popen(monkeypatch, output=out)
        did = db.create_drama(title_en="D")
        s = _wait(svc.start_import(did, URL)["job_id"])
        assert s["status"] == "error" and "made no EPUB" in s["error"]
        detail = s["result"]["detail"]
        assert "abc123secret" not in detail and "sk-ant-api03" not in detail
        assert "\x1b" not in detail and "https://site.example" in detail
        assert _drama_tmp_dirs(did) == []

    def test_nonzero_exit(self, isolated_db, program, monkeypatch):
        _popen(monkeypatch, output=b"boom\n", returncode=2, on_start=_write_epub)
        did = db.create_drama(title_en="D")
        s = _wait(svc.start_import(did, URL)["job_id"])
        assert s["status"] == "error" and "exit code 2" in s["error"]
        assert novel_attach_service._read_novel(did) == ""

    def test_cancel_kills_the_process(self, isolated_db, program, monkeypatch, fast):
        _popen(monkeypatch, finish_after=None)       # never exits by itself
        did = db.create_drama(title_en="D")
        job = svc.start_import(did, URL)["job_id"]
        for _ in range(200):
            if FakeProc.instances:
                break
            time.sleep(0.01)
        background_jobs.request_cancel(job)
        s = _wait(job)
        assert s["status"] == "cancelled"
        assert fast == FakeProc.instances and FakeProc.instances[0].killed
        assert _drama_tmp_dirs(did) == []

    def test_a_pipe_held_open_by_a_leftover_child_does_not_hang_the_job(
            self, isolated_db, program, monkeypatch, fast):
        import threading
        release = threading.Event()

        class HeldPipe:
            """A grandchild still holds the write end: reads never return."""
            closed = False

            def read1(self, n):
                release.wait(30)
                return b""

            def close(self):
                self.closed = True
        pipe = HeldPipe()
        monkeypatch.setattr(svc, "_KILL_WAIT_SECONDS", 0.2)

        def popen(argv, **kw):
            proc = FakeProc(argv, on_start=_write_epub, **kw)
            proc.stdout = pipe
            return proc
        monkeypatch.setattr(svc.subprocess, "Popen", popen)
        did = db.create_drama(title_en="D")
        try:
            s = _wait(svc.start_import(did, URL)["job_id"], timeout=5)
        finally:
            release.set()
        assert s["status"] == "done"
        assert fast, "the process group is killed after a normal exit too"
        assert pipe.closed is False     # never closed under a blocked read

    def test_normal_exit_does_not_kill_by_pid(self, isolated_db, program, monkeypatch, fast):
        # lncrawl exited and nothing holds the pipe: its PID may already be
        # reused, so no kill is sent at all.
        _popen(monkeypatch, on_start=_write_epub)
        did = db.create_drama(title_en="D")
        assert _wait(svc.start_import(did, URL)["job_id"])["status"] == "done"
        assert fast == []
        assert svc.running_imports() == 0

    def test_app_shutdown_cancels_and_kills(self, isolated_db, program, monkeypatch, fast):
        _popen(monkeypatch, finish_after=None)
        did = db.create_drama(title_en="D")
        job = svc.start_import(did, URL)["job_id"]
        for _ in range(200):
            if svc.running_imports():
                break
            time.sleep(0.01)
        assert svc.shutdown(wait=5) == 1
        assert svc.running_imports() == 0
        s = _wait(job)
        assert s["status"] == "cancelled"
        assert fast and fast[0].killed
        assert _drama_tmp_dirs(did) == []

    def test_api_lifespan_stops_imports(self, isolated_db, monkeypatch):
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        calls = []
        monkeypatch.setattr(svc, "shutdown", lambda *a, **k: calls.append(1) or 0)
        with TestClient(create_app(ApiSettings())) as c:
            c.get("/api/health")
        assert calls == [1]

    def test_timeout_kills_the_process(self, isolated_db, program, monkeypatch, fast):
        monkeypatch.setattr(svc, "TIMEOUT_SECONDS", 0.05)
        _popen(monkeypatch, finish_after=None)
        did = db.create_drama(title_en="D")
        s = _wait(svc.start_import(did, URL)["job_id"])
        assert s["status"] == "error" and "did not finish" in s["error"]
        assert fast and fast[0].killed
        assert _drama_tmp_dirs(did) == []

    def test_output_cap_kills_the_process(self, isolated_db, program, monkeypatch, fast):
        monkeypatch.setattr(svc, "MAX_OUTPUT_BYTES", 1000)
        _popen(monkeypatch, output=b"x" * 50_000, finish_after=None)
        did = db.create_drama(title_en="D")
        s = _wait(svc.start_import(did, URL)["job_id"])
        assert s["status"] == "error" and "too much output" in s["error"]
        assert fast and fast[0].killed

    def test_data_folder_size_cap_kills_the_process(self, isolated_db, program, monkeypatch, fast):
        monkeypatch.setattr(svc, "MAX_WORKDIR_BYTES", 10)
        monkeypatch.setattr(svc, "_SIZE_CHECK_SECONDS", 0.0)
        _popen(monkeypatch, finish_after=None, on_start=_write_epub)
        did = db.create_drama(title_en="D")
        s = _wait(svc.start_import(did, URL)["job_id"])
        assert s["status"] == "error" and "grew past" in s["error"]
        assert fast and fast[0].killed
        assert _drama_tmp_dirs(did) == []


# --- startup sweep ---------------------------------------------------------

class TestStaleSweep:
    def _folder(self, did, name, age):
        path = os.path.join(db.drama_dir(did), name)
        os.makedirs(os.path.join(path, "novels"))
        old = time.time() - age
        os.utime(path, (old, old))
        return path

    def test_removes_only_old_unowned_work_folders(self, isolated_db, tmp_path):
        did = db.create_drama(title_en="D")
        stale = self._folder(did, ".lncrawl_old", svc.STALE_WORKDIR_SECONDS + 60)
        fresh = self._folder(did, ".lncrawl_new", 60)
        other = self._folder(did, ".ocr_old", svc.STALE_WORKDIR_SECONDS + 60)
        target = tmp_path / "elsewhere"
        target.mkdir()
        link = os.path.join(db.drama_dir(did), ".lncrawl_link")
        os.symlink(str(target), link)
        assert svc.cleanup_stale_workdirs() == 1
        assert not os.path.exists(stale)
        assert os.path.isdir(fresh) and os.path.isdir(other) and target.is_dir()

    def test_keeps_the_folder_of_a_running_import(self, isolated_db, monkeypatch):
        did = db.create_drama(title_en="D")
        stale = self._folder(did, ".lncrawl_old", svc.STALE_WORKDIR_SECONDS + 60)
        monkeypatch.setattr(background_jobs, "is_running", lambda jid: jid == f"lncrawl_{did}")
        assert svc.cleanup_stale_workdirs() == 0 and os.path.isdir(stale)

    def test_startup_runs_the_sweep(self, monkeypatch):
        import api.background as bg
        calls = []
        monkeypatch.setattr(bg, "_started", None)
        monkeypatch.setattr(svc, "cleanup_stale_workdirs", lambda: calls.append(1) or 0)
        from services import drama_service, auto_backup_service
        monkeypatch.setattr(drama_service, "cleanup_stale_tombstones", lambda: 0)
        monkeypatch.setattr(auto_backup_service, "cleanup_stale_leftovers", lambda: 0)
        monkeypatch.setattr(auto_backup_service, "periodic_tick", lambda: None)
        from sources import chapter_check
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda: None)
        from sources import store
        monkeypatch.setattr(store, "get_setting", lambda name: False)
        bg.start_background_services()
        assert calls == [1]


# --- redaction --------------------------------------------------------------

class TestRedaction:
    def test_paths_urls_keys_ansi(self):
        home = os.path.expanduser("~")
        text = (f"\x1b[1mSaved\x1b[0m to /tmp/work123/novels/a.epub\r\n"
                f"config {home}/.config/lncrawl\n"
                "GET https://api.site.example/v1/x?key=hunter2&page=3\n"
                "api_key=sk-abcdefghijklmnopqrstuvwxyz0123\n")
        out = svc.redact_output(text, hide=(("/tmp/work123", "<work folder>"),))
        assert "/tmp/work123" not in out and "<work folder>/novels/a.epub" in out
        assert home not in out
        assert "hunter2" not in out and "https://api.site.example" in out
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in out
        assert "\x1b" not in out and "\r" not in out

    def test_tail_is_bounded(self):
        tail = svc.output_tail("\n".join(f"line {i} " + "y" * 50 for i in range(500)))
        assert len(tail) <= svc.LOG_TAIL_CHARS and "line 499" in tail


# --- routes -----------------------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


class TestRoutes:
    def _client(self):
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)

    def test_status_and_start(self, isolated_db, program, monkeypatch):
        _popen(monkeypatch, on_start=_write_epub)
        c = self._client()
        assert c.get("/api/novel/lncrawl").json() == {"installed": True, "path_configured": False}
        did = db.create_drama(title_en="D")
        r = c.post(f"/api/novel/dramas/{did}/lncrawl", json={"url": URL, "chapters": "last",
                                                             "count": 3, "mode": "append"})
        assert r.status_code == 200 and r.json() == {"job_id": f"lncrawl_{did}"}
        assert _wait(f"lncrawl_{did}")["status"] == "done"
        assert "--last" in FakeProc.instances[0].argv

    def test_request_model_refuses_extra_flags(self, isolated_db, program, monkeypatch):
        seen = _popen(monkeypatch)
        c = self._client()
        did = db.create_drama(title_en="D")
        for body in ({"url": URL, "args": ["--config", "x"]}, {"url": URL, "chapters": "range"},
                     {"url": URL, "chapters": "first", "count": "3"}, {"url": URL, "count": 0}):
            assert c.post(f"/api/novel/dramas/{did}/lncrawl", json=body).status_code == 422
        assert seen == {}

    def test_not_installed_503(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc.shutil, "which", lambda name: None)
        c = self._client()
        assert c.get("/api/novel/lncrawl").json()["installed"] is False
        did = db.create_drama(title_en="D")
        assert c.post(f"/api/novel/dramas/{did}/lncrawl", json={"url": URL}).status_code == 503

    def test_pc_only(self, isolated_db, program, monkeypatch):
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        seen = _popen(monkeypatch)
        did = db.create_drama(title_en="D")
        remote = TestClient(create_app(ApiSettings(auth_mode="on")),
                            base_url="https://baihe.example.com", raise_server_exceptions=False)
        assert remote.get("/api/novel/lncrawl").status_code in (401, 403)
        assert remote.post(f"/api/novel/dramas/{did}/lncrawl",
                           json={"url": URL}).status_code in (401, 403)
        assert seen == {}
        from api.auth import iter_route_declarations
        decls = {(path, tuple(sorted(methods))): d
                 for _r, path, methods, d in iter_route_declarations(create_app(ApiSettings()))}
        assert decls[("/api/novel/lncrawl", ("GET",))] == [("local_only", None)]
        assert decls[("/api/novel/dramas/{drama_id}/lncrawl", ("POST",))] == [("local_only", None)]

    def test_settings_path_blanked_for_remote(self, isolated_db):
        settings_service.set_settings({"lncrawl_cmd": "/opt/bin/lncrawl"})
        from api.routers.settings_routes import _with_path_flags
        out = _with_path_flags(settings_service.get_settings_overview(), local=False)
        assert out["preferences"]["lncrawl_cmd"] == ""
        assert out["preferences"]["lncrawl_cmd_configured"] is True
