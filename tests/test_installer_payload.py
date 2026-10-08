"""Step 80b: installer/build_installer.py's payload assembly -- what ships,
what never does (.env, library/, model caches, tests/, ...), the bundled
Python's ._pth, the wheel-download command and the ISCC command line. No
network, npm or Inno Setup: those are mocked or pointed at fake files."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "installer"))

import build_installer as bi  # noqa: E402


def _touch(root, rel, text="x"):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def fake_repo(tmp_path):
    repo = tmp_path / "repo"
    for rel in (
        # Ships
        "app.py", "portable.py", "db.py", "check_setup.py", "README.md", "__init__.py",
        "requirements-core.txt", "requirements-media.txt", "constraints.txt",
        "api/__init__.py", "api/__main__.py", "services/settings_service.py",
        "sources/adapters/site.py", "assets/app_icon.ico", "extension/manifest.json", "extension/icons/icon-16.png",
        "frontend/dist/index.html", "frontend/dist/assets/index-abc.js",
        "installer/launcher.py", "installer/postinstall.py", "run_tests.py",
        # Never ships
        ".env", ".env.local", ".env.example", "services/.env",
        "library/library.db", "library/dramas/1/audio.mp3",
        "sources/library/cache.json",
        "model_cache/huggingface/x.bin", "venv/Scripts/python.exe", ".venv/bin/python",
        "tests/test_x.py", "tests/conftest.py", "docs/notes.md", "scripts/build_release.py",
        "frontend/src/App.tsx", "frontend/node_modules/x/index.js", "frontend/package.json",
        "installer/baihe.iss", "installer/build_installer.py",
        ".github/workflows/x.yml", ".claude/settings.json", ".github/CODEOWNERS",
        "start.bat", "start.ps1", "uninstall.bat", "uninstall_path_cleanup.ps1",
        "make_shortcut.bat", "make_lock.bat", "pytest.ini", "conftest.py",
        "tools/.env/pip.ini", "tools/repo_map.py", ".env.venv/Scripts/python.exe",
        "CLAUDE.md", "FILE_ORGANIZATION.md", ".gitignore",
        "PORTABLE", "PYTHON_VERSION", "INSTALLED",
        "api/__pycache__/server.cpython-312.pyc", "services/x.pyc",
        "api_server.log", "cookies.txt", "cookies-site.txt", "secret.key", "cert.pem",
        "build/installer/payload/x", "dist/baihe-frontend-1.zip",
    ):
        _touch(repo, rel)
    return repo


SHIPS = {
    "app.py", "portable.py", "db.py", "check_setup.py", "README.md", "__init__.py",
    "requirements-core.txt", "requirements-media.txt", "constraints.txt",
    "api/__init__.py", "api/__main__.py", "services/settings_service.py",
    "sources/adapters/site.py", "assets/app_icon.ico", "extension/manifest.json", "extension/icons/icon-16.png",
    "frontend/dist/index.html", "frontend/dist/assets/index-abc.js",
    "installer/launcher.py", "installer/postinstall.py", "run_tests.py",
}


class TestStageApp:
    def test_stages_exactly_the_app(self, fake_repo, tmp_path):
        dest = tmp_path / "payload" / "app"
        staged = bi.stage_app(fake_repo, dest)
        assert set(staged) == SHIPS
        on_disk = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        assert on_disk == SHIPS

    def test_no_secrets_or_user_data(self, fake_repo, tmp_path):
        dest = tmp_path / "app"
        bi.stage_app(fake_repo, dest)
        names = {p.name for p in dest.rglob("*")}
        for forbidden in (".env", ".env.local", ".env.example", "library", "model_cache",
                          "venv", ".venv", "tests", "cookies.txt", "secret.key", "PORTABLE",
                          "INSTALLED", "start.bat", "node_modules", "__pycache__"):
            assert forbidden not in names, forbidden

    def test_passes_its_own_safety_check(self, fake_repo, tmp_path):
        dest = tmp_path / "app"
        bi.stage_app(fake_repo, dest)
        _touch(dest, "process_guard.py")   # check_payload only needs the key files present
        bi.check_payload(dest)

    def test_needs_a_built_frontend(self, fake_repo, tmp_path):
        (fake_repo / "frontend" / "dist" / "index.html").unlink()
        with pytest.raises(bi.BuildError, match="npm run build"):
            bi.stage_app(fake_repo, tmp_path / "app")

    def test_needs_the_runtime_installer_scripts(self, fake_repo, tmp_path):
        (fake_repo / "installer" / "launcher.py").unlink()
        with pytest.raises(bi.BuildError, match="launcher.py"):
            bi.stage_app(fake_repo, tmp_path / "app")

    def test_restaging_replaces_the_old_tree(self, fake_repo, tmp_path):
        dest = tmp_path / "app"
        _touch(dest, "stale_module.py")
        bi.stage_app(fake_repo, dest)
        assert not (dest / "stale_module.py").exists()

    def test_git_checkout_ships_only_tracked_files(self, fake_repo, tmp_path):
        if not shutil.which("git"):
            pytest.skip("git not available")
        _touch(fake_repo, "client_secret_123.json")    # untracked: never ships
        _touch(fake_repo, "hf_token.txt")
        run = lambda *a: subprocess.run(["git", "-C", str(fake_repo), *a], check=True,  # noqa: E731
                                        capture_output=True, timeout=60)
        run("init", "-q")
        # Everything except the two stray files and the built frontend.
        tracked = [p.relative_to(fake_repo).as_posix() for p in fake_repo.rglob("*")
                   if p.is_file() and ".git" not in p.parts
                   and p.name not in ("client_secret_123.json", "hf_token.txt")
                   and "frontend/dist" not in p.relative_to(fake_repo).as_posix()]
        run("add", "-f", "--", *tracked)
        assert bi.tracked_files(fake_repo) == sorted(tracked)
        staged = set(bi.stage_app(fake_repo, tmp_path / "app"))
        assert staged == SHIPS
        assert "client_secret_123.json" not in staged and "hf_token.txt" not in staged

    def test_not_a_git_checkout(self, fake_repo):
        assert bi.tracked_files(fake_repo) is None

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlinks_are_skipped(self, fake_repo, tmp_path):
        outside = _touch(tmp_path, "outside/secret.txt")
        os.symlink(outside, fake_repo / "linked.txt")
        os.symlink(outside.parent, fake_repo / "linked_dir")
        staged = bi.stage_app(fake_repo, tmp_path / "app")
        assert "linked.txt" not in staged
        assert not any(s.startswith("linked_dir") for s in staged)


class TestIsExcluded:
    @pytest.mark.parametrize("rel", [
        ".env", "api/.env", ".env.production", ".ENV", ".env/pip.ini", "a/.env.venv/x", "library/x", "a/library/b",
        "model_cache/x", "tests/test_a.py", "docs/a.md", "frontend/src/a.ts",
        "venv/x", "x/__pycache__/y.pyc", "start.bat", "INSTALLED", "cookies.txt",
        "a.pem", "a.log", "library.db", "installer/baihe.iss", "installer\\build_installer.py",
        "tools/repo_map.py",
    ])
    def test_excluded(self, rel):
        assert bi.is_excluded(rel)

    @pytest.mark.parametrize("rel", [
        "app.py", "api/routers/library_routes.py", "services/library_admin_service.py",
        "portable.py", "requirements-core.txt", "assets/app_icon.ico", "sources/http.py",
    ])
    def test_kept(self, rel):
        assert not bi.is_excluded(rel)


class TestCheckPayload:
    def _good(self, tmp_path):
        dest = tmp_path / "app"
        for rel in ("frontend/dist/index.html", "api/__main__.py", "portable.py", "process_guard.py",
                    "requirements-core.txt", "constraints.txt", "check_setup.py",
                    "installer/launcher.py", "installer/postinstall.py"):
            _touch(dest, rel)
        return dest

    def test_ok(self, tmp_path):
        bi.check_payload(self._good(tmp_path))

    @pytest.mark.parametrize("rel", [".env", "services/.env.local", "library/library.db",
                                     "model_cache/x", "tests/t.py", "INSTALLED"])
    def test_rejects(self, tmp_path, rel):
        dest = self._good(tmp_path)
        _touch(dest, rel)
        with pytest.raises(bi.BuildError, match="isn't safe to ship"):
            bi.check_payload(dest)

    def test_rejects_a_missing_key_file(self, tmp_path):
        dest = self._good(tmp_path)
        (dest / "portable.py").unlink()
        with pytest.raises(bi.BuildError, match="missing: portable.py"):
            bi.check_payload(dest)


def _fake_embed_zip(tmp_path, version="3.12.10", extra=None):
    tag = "".join(version.split(".")[:2])
    path = tmp_path / "embed.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("python.exe", "exe")
        zf.writestr(f"python{tag}.zip", "stdlib")
        zf.writestr(f"python{tag}._pth", f"python{tag}.zip\n.\n\n#import site\n")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


class TestPreparePython:
    def test_rewrites_the_pth_for_site_packages_and_the_app(self, tmp_path):
        z = _fake_embed_zip(tmp_path)
        dest = bi.prepare_python(z, tmp_path / "python", hashlib.sha256(z.read_bytes()).hexdigest())
        pth = (dest / "python312._pth").read_bytes().decode("ascii")
        lines = pth.split("\r\n")
        assert lines[:5] == ["python312.zip", ".", r"Lib\site-packages", r"..\app", "import site"]
        assert "#import site" not in pth
        assert (dest / "Lib" / "site-packages").is_dir()
        assert (dest / "python.exe").is_file()

    def test_refuses_a_hash_mismatch(self, tmp_path):
        z = _fake_embed_zip(tmp_path)
        with pytest.raises(bi.BuildError, match="SHA-256"):
            bi.prepare_python(z, tmp_path / "python", "0" * 64)

    def test_refuses_path_traversal(self, tmp_path):
        z = _fake_embed_zip(tmp_path, extra={"../evil.txt": "x"})
        with pytest.raises(bi.BuildError, match="Unsafe path"):
            bi.prepare_python(z, tmp_path / "python", hashlib.sha256(z.read_bytes()).hexdigest())

    def test_pinned_python_is_https_and_hashed(self):
        assert bi.PYTHON_EMBED_URL.startswith("https://www.python.org/ftp/python/")
        assert bi.PYTHON_VERSION in bi.PYTHON_EMBED_URL
        assert len(bi.PYTHON_EMBED_SHA256) == 64


def _lock_repo(tmp_path, wheels):
    """A repo whose requirements-core.txt names fastapi/pip, and a lock
    pinning the given {wheel file name: bytes}."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "requirements-core.txt").write_text("fastapi>=1\n" if "fastapi-1-py3-none-any.whl" in wheels else "")
    lines = []
    for name, data in wheels.items():
        parts = name.split("-")
        lines.append(f"{parts[0]}=={parts[1]} \\\n    --hash=sha256:{hashlib.sha256(data).hexdigest()}")
    lock = tmp_path / "wheels.lock.txt"
    lock.write_text("\n".join(lines) + "\n")
    return repo, lock


class TestWheels:
    def test_download_command(self, tmp_path):
        cmd = bi.wheel_download_command(bi.REPO_ROOT, tmp_path / "wheels", "3.12.10", "py")
        assert cmd[:4] == ["py", "-m", "pip", "download"]
        assert "--only-binary=:all:" in cmd
        assert cmd[cmd.index("--platform") + 1] == "win_amd64"
        assert cmd[cmd.index("--python-version") + 1] == "3.12"
        assert cmd[cmd.index("-r") + 1].endswith("requirements-core.txt")
        assert os.path.basename(cmd[cmd.index("-c") + 1]) in ("constraints.txt", "constraints.lock.txt")
        assert cmd[-1] == "pip"

    def test_lock_file_wins_when_present(self, tmp_path):
        (tmp_path / "constraints.txt").write_text("")
        assert bi.constraints_for(tmp_path).name == "constraints.txt"
        (tmp_path / "constraints.lock.txt").write_text("")
        assert bi.constraints_for(tmp_path).name == "constraints.lock.txt"

    def test_download_wheels_requires_pip(self, tmp_path, monkeypatch):
        repo, lock = _lock_repo(tmp_path, {"fastapi-1-py3-none-any.whl": b"f",
                                           "pip-26-py3-none-any.whl": b"p"})

        class R:
            returncode = 0

        def fake_run(cmd, timeout):
            dest = Path(cmd[cmd.index("-d") + 1])
            (dest / "fastapi-1-py3-none-any.whl").write_bytes(b"f")
            return R()
        monkeypatch.setattr(bi.subprocess, "run", fake_run)
        with pytest.raises(bi.BuildError, match="pip's own wheel"):
            bi.download_wheels(repo, tmp_path / "wheels", lock_path=lock)

    def test_download_wheels_failure(self, tmp_path, monkeypatch):
        repo, lock = _lock_repo(tmp_path, {"pip-26-py3-none-any.whl": b"p"})

        class R:
            returncode = 1
        monkeypatch.setattr(bi.subprocess, "run", lambda cmd, timeout: R())
        with pytest.raises(bi.BuildError, match="pip download"):
            bi.download_wheels(repo, tmp_path / "wheels", lock_path=lock)

    def test_download_wheels_ok_copies_the_lock(self, tmp_path, monkeypatch):
        repo, lock = _lock_repo(tmp_path, {"pip-26-py3-none-any.whl": b"p"})

        class R:
            returncode = 0

        def fake_run(cmd, timeout):
            assert "--require-hashes" in cmd and "--no-deps" in cmd
            (Path(cmd[cmd.index("-d") + 1]) / "pip-26-py3-none-any.whl").write_bytes(b"p")
            return R()
        monkeypatch.setattr(bi.subprocess, "run", fake_run)
        assert bi.download_wheels(repo, tmp_path / "wheels", lock_path=lock) == ["pip-26-py3-none-any.whl"]
        assert (tmp_path / "wheels" / "wheels.lock.txt").read_text() == lock.read_text()

    def test_download_wheels_fails_on_a_swapped_wheel_even_if_pip_succeeded(self, tmp_path, monkeypatch):
        repo, lock = _lock_repo(tmp_path, {"pip-26-py3-none-any.whl": b"p"})

        class R:
            returncode = 0

        def fake_run(cmd, timeout):
            (Path(cmd[cmd.index("-d") + 1]) / "pip-26-py3-none-any.whl").write_bytes(b"evil")
            return R()
        monkeypatch.setattr(bi.subprocess, "run", fake_run)
        with pytest.raises(bi.BuildError, match="pip-26-py3-none-any.whl: SHA-256"):
            bi.download_wheels(repo, tmp_path / "wheels", lock_path=lock)

    def test_locked_download_command(self, tmp_path):
        cmd = bi.locked_download_command(tmp_path / "l.txt", tmp_path / "w", "3.12.10", "py")
        assert cmd[:4] == ["py", "-m", "pip", "download"]
        for flag in ("--require-hashes", "--no-deps", "--only-binary=:all:"):
            assert flag in cmd
        assert cmd[cmd.index("--platform") + 1] == "win_amd64"
        assert cmd[cmd.index("-r") + 1] == str(tmp_path / "l.txt") and "-c" not in cmd

    def test_lock_must_cover_requirements(self, tmp_path):
        repo, lock = _lock_repo(tmp_path, {"pip-26-py3-none-any.whl": b"p"})
        (repo / "requirements-core.txt").write_text("# c\nRequests>=2  # x\nzzz_pkg>=1\n")
        with pytest.raises(bi.BuildError, match="requests, zzz-pkg"):
            bi.check_lock_covers_requirements(repo, lock)
        with pytest.raises(bi.BuildError, match="doesn't exist"):
            bi.check_lock_covers_requirements(repo, tmp_path / "nope.txt")

    def test_committed_lock_covers_requirements_and_parses(self):
        bi.check_lock_covers_requirements(bi.REPO_ROOT)
        lock = bi.postinstall.parse_lock(bi.WHEEL_LOCK.read_text(encoding="utf-8"))
        assert all(hashes for _, hashes in lock.values())

    def test_format_lock_round_trips(self, tmp_path):
        wheels = tmp_path / "w"
        wheels.mkdir()
        (wheels / "Pydantic_Core-2.1-cp312-cp312-win_amd64.whl").write_bytes(b"a")
        (wheels / "pip-26-py3-none-any.whl").write_bytes(b"b")
        text = bi.format_lock(wheels)
        assert "pydantic-core==2.1 \\\n    --hash=sha256:" in text
        assert bi.postinstall.verify_wheels(wheels, text) == 2

    def test_size_estimate_and_manifest(self, tmp_path):
        wheels = tmp_path / "payload" / "wheels"
        wheels.mkdir(parents=True)
        for name, size in (("pip-26-py3-none-any.whl", 1000), ("fastapi-1-py3-none-any.whl", 234)):
            with zipfile.ZipFile(wheels / name, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("data", b"\0" * size)
        assert bi.installed_size_estimate(wheels) == 1234
        manifest = json.loads(bi.write_manifest(tmp_path / "payload", "1.2.3").read_text())
        assert manifest["app_version"] == "1.2.3"
        assert manifest["python"]["version"] == bi.PYTHON_VERSION
        assert [w["file"] for w in manifest["wheels"]] == ["fastapi-1-py3-none-any.whl",
                                                            "pip-26-py3-none-any.whl"]
        assert all(len(w["sha256"]) == 64 for w in manifest["wheels"])
        assert manifest["installed_size_estimate_bytes"] == 1234


class TestStageService:
    """payload/service/: the files service.py copies into its admin-only folder."""

    def _stage(self, tmp_path, monkeypatch):
        z = _fake_embed_zip(tmp_path)
        real, digest = bi.prepare_python, hashlib.sha256(z.read_bytes()).hexdigest()
        monkeypatch.setattr(bi, "prepare_python", lambda zp, dest: real(zp, dest, digest))
        winsw = tmp_path / "winsw.exe"
        winsw.write_bytes(b"winsw")
        monkeypatch.setattr(bi, "WINSW_SHA256", hashlib.sha256(b"winsw").hexdigest())
        caddy = tmp_path / "caddy.exe"
        caddy.write_bytes(b"caddy")
        monkeypatch.setattr(bi, "CADDY_SHA256", hashlib.sha256(b"caddy").hexdigest())
        licenses = tmp_path / "caddy-licenses"
        (licenses / "github.com_caddyserver_caddy_v2").mkdir(parents=True)
        (licenses / "github.com_caddyserver_caddy_v2" / "LICENSE").write_text("Apache", encoding="utf-8")
        payload = tmp_path / "payload"
        bi.stage_service(payload, z, winsw, caddy, licenses)
        return payload / "service"

    def test_pins_are_sha256(self):
        assert len(bi.WINSW_SHA256) == 64 and all(c in "0123456789abcdef" for c in bi.WINSW_SHA256)
        assert bi.WINSW_URL.startswith("https://") and bi.WINSW_VERSION in bi.WINSW_URL
        assert "MIT License" in bi.WINSW_LICENSE.read_text(encoding="utf-8")

    def test_layout(self, tmp_path, monkeypatch):
        service = self._stage(tmp_path, monkeypatch)
        assert (service / "wrapper" / "BaiheStudio.exe").read_bytes() == b"winsw"
        assert (service / "wrapper" / "licenses" / "WinSW-LICENSE.txt").is_file()
        assert (service / "helper" / "python" / "python.exe").is_file()
        for name in ("service.py", "service_menu.ps1"):
            assert ((service / "helper" / "lib" / "installer" / name).read_bytes()
                    == (Path(bi.INSTALLER_DIR) / name).read_bytes())

    def test_helper_interpreter_loads_nothing_from_user_writable_folders(self, tmp_path, monkeypatch):
        helper = self._stage(tmp_path, monkeypatch) / "helper" / "python"
        pth = (helper / "python312._pth").read_bytes().decode("ascii")
        assert pth.split("\r\n")[:2] == ["python312.zip", "."]
        assert "import site" not in pth and "site-packages" not in pth and "app" not in pth
        assert not (helper / "Lib").exists()

    def test_caddy_layout(self, tmp_path, monkeypatch):
        service = self._stage(tmp_path, monkeypatch)
        assert (service / "caddy" / "caddy.exe").read_bytes() == b"caddy"
        assert (service / "caddy" / "BaiheCaddy.exe").read_bytes() == b"winsw"
        assert (service / "caddy" / "licenses" / "WinSW-LICENSE.txt").is_file()
        assert (service / "caddy" / "licenses" / "github.com_caddyserver_caddy_v2" / "LICENSE").is_file()
        # The elevated script reads the template from its own copy, not from app/.
        assert ((service / "helper" / "lib" / "deploy" / "caddy" / "Caddyfile.template").read_bytes()
                == bi.CADDY_TEMPLATE.read_bytes())
        # Nothing but the binary, its wrapper and licences: no Caddyfile ships.
        assert sorted(p.name for p in (service / "caddy").iterdir()) == [
            "BaiheCaddy.exe", "caddy.exe", "licenses"]

    def test_service_script_ships_only_there(self):
        assert "service.py" not in bi.RUNTIME_INSTALLER_FILES
        assert "service_menu.ps1" not in bi.RUNTIME_INSTALLER_FILES

    def test_refuses_an_unpinned_wrapper(self, tmp_path):
        winsw = tmp_path / "winsw.exe"
        winsw.write_bytes(b"something else")
        with pytest.raises(bi.BuildError, match="winsw.exe: SHA-256"):
            bi.stage_service(tmp_path / "payload", tmp_path / "z.zip", winsw, winsw, tmp_path)

    def test_refuses_an_unpinned_caddy(self, tmp_path, monkeypatch):
        winsw = tmp_path / "winsw.exe"
        winsw.write_bytes(b"winsw")
        monkeypatch.setattr(bi, "WINSW_SHA256", hashlib.sha256(b"winsw").hexdigest())
        caddy = tmp_path / "caddy.exe"
        caddy.write_bytes(b"not the pinned build")
        with pytest.raises(bi.BuildError, match="caddy.exe: SHA-256"):
            bi.stage_service(tmp_path / "payload", tmp_path / "z.zip", winsw, caddy, tmp_path)


class TestCaddyBuild:
    def test_pins(self):
        assert len(bi.CADDY_SHA256) == 64 and all(c in "0123456789abcdef" for c in bi.CADDY_SHA256)
        assert bi.CADDY_GO_VERSION.startswith("go1.")
        go_mod = (bi.CADDY_SOURCE_DIR / "go.mod").read_text(encoding="utf-8")
        assert f"toolchain {bi.CADDY_GO_VERSION}" in go_mod
        assert f"github.com/caddyserver/caddy/v2 v{bi.CADDY_VERSION}" in go_mod
        assert "github.com/mholt/caddy-ratelimit" in go_mod
        assert (bi.CADDY_SOURCE_DIR / "go.sum").stat().st_size > 0

    def test_workflow_go_matches_the_pinned_toolchain(self):
        wf = (Path(bi.REPO_ROOT) / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        assert f"go/{bi.CADDY_GO_VERSION[2:]}.windows" in wf or f"{bi.CADDY_GO_VERSION}.windows-amd64.zip" in wf

    def test_source_is_pinned_to_lf_so_the_hash_is_the_same_on_a_windows_checkout(self):
        # A CRLF main.go builds a different binary (03e740b... instead of e09cc7eb...).
        attrs = (Path(bi.REPO_ROOT) / ".gitattributes").read_text(encoding="utf-8")
        assert "installer/caddy/* text eol=lf" in attrs.splitlines()
        for name in ("go.mod", "go.sum", "main.go"):
            assert b"\r" not in (bi.CADDY_SOURCE_DIR / name).read_bytes()

    def test_environment_and_command_are_reproducible(self):
        env = bi.caddy_build_env({"PATH": "x", "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local"})
        assert (env["GOOS"], env["GOARCH"], env["GOAMD64"], env["CGO_ENABLED"]) == (
            "windows", "amd64", "v1", "0")
        assert env["GOFLAGS"] == "-mod=readonly" and env["GOTOOLCHAIN"] == bi.CADDY_GO_VERSION
        cmd = bi.caddy_build_command("go", "out.exe")
        assert "-trimpath" in cmd and "-buildvcs=false" in cmd and "-ldflags=-s -w" in cmd

    def _fake_run(self, content, calls):
        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[1] == "build":
                Path(cmd[cmd.index("-o") + 1]).write_bytes(content)
            out = "" if cmd[1] == "build" else ("/goroot\n" if cmd[1] == "env" else "")
            return subprocess.CompletedProcess(cmd, 0, out, "")
        return run

    def test_a_build_with_the_wrong_hash_is_refused(self, tmp_path):
        with pytest.raises(bi.BuildError, match="not the pinned"):
            bi.build_caddy(tmp_path, run=self._fake_run(b"other", []))

    def test_a_build_with_the_pinned_hash_is_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bi, "CADDY_SHA256", hashlib.sha256(b"caddy").hexdigest())
        calls = []
        exe, licenses = bi.build_caddy(tmp_path, run=self._fake_run(b"caddy", calls))
        assert exe.read_bytes() == b"caddy" and licenses.parent == tmp_path
        assert calls[0][1] == "build"

    def test_licences_of_compiled_modules_are_collected(self, tmp_path):
        mod = tmp_path / "mod"
        mod.mkdir()
        (mod / "LICENSE").write_text("a", encoding="utf-8")
        (mod / "NOTICE.txt").write_text("b", encoding="utf-8")
        (mod / "main.go").write_text("c", encoding="utf-8")
        goroot = tmp_path / "goroot"
        goroot.mkdir()
        (goroot / "LICENSE").write_text("go", encoding="utf-8")
        dest = tmp_path / "out"
        n = bi.collect_licenses(f"github.com/x/y\t{mod}\n{bi.CADDY_MODULE}\t{tmp_path}\n", goroot, dest)
        assert n == 3
        assert (dest / "github.com_x_y" / "LICENSE").is_file() and (dest / "go" / "LICENSE").is_file()
        assert not (dest / "github.com_x_y" / "main.go").exists()


class TestVersionAndIscc:
    @pytest.mark.parametrize("version,expected", [
        ("0.1.0", "0.1.0.0"), ("1.2", "1.2.0.0"), ("0.1.0-dev", "0.1.0.0"),
        ("2.0.1+abc", "2.0.1.0"), ("1.2.3.4.5", "1.2.3.4"),
    ])
    def test_numeric_version(self, version, expected):
        assert bi.numeric_version(version) == expected

    def test_numeric_version_needs_numbers(self):
        with pytest.raises(bi.BuildError):
            bi.numeric_version("dev")

    def test_iscc_command(self, tmp_path):
        cmd = bi.iscc_command("ISCC.exe", tmp_path / "payload", tmp_path / "out", "0.2.0-rc1", 123)
        assert cmd[0] == "ISCC.exe"
        assert "/DAppVersion=0.2.0-rc1" in cmd
        assert "/DAppVersionNumeric=0.2.0.0" in cmd
        assert "/DExtraDiskSpace=123" in cmd
        assert cmd[-1].endswith("baihe.iss")

    def test_main_rejects_an_unsafe_version(self, capsys):
        assert bi.main(["--version", "1.0\"; rm -rf /"]) == 2
        assert "--version" in capsys.readouterr().err


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class TestWheelHashVerifier:
    def _dir(self, tmp_path, files):
        d = tmp_path / "wheels"
        d.mkdir()
        for name, data in files.items():
            (d / name).write_bytes(data)
        return d

    def test_match(self, tmp_path):
        d = self._dir(tmp_path, {"a_b-1.0-py3-none-any.whl": b"x", "c-2-cp312-cp312-win_amd64.whl": b"y"})
        lock = (f"# c\na-b==1.0 \\\n    --hash=sha256:{_sha(b'x')} \\\n    --hash=sha256:{'0' * 64}\n"
                f"c==2 --hash=sha256:{_sha(b'y')}\n")
        assert bi.postinstall.verify_wheels(d, lock) == 2

    def test_mismatch_names_the_wheel(self, tmp_path):
        d = self._dir(tmp_path, {"a-1-py3-none-any.whl": b"changed"})
        with pytest.raises(bi.postinstall.LockError, match="a-1-py3-none-any.whl: SHA-256"):
            bi.postinstall.verify_wheels(d, f"a==1 --hash=sha256:{_sha(b'x')}\n")

    def test_version_mismatch(self, tmp_path):
        d = self._dir(tmp_path, {"a-2-py3-none-any.whl": b"x"})
        with pytest.raises(bi.postinstall.LockError, match="pins a==1"):
            bi.postinstall.verify_wheels(d, f"a==1 --hash=sha256:{_sha(b'x')}\n")

    def test_extra_wheel_not_in_the_lock(self, tmp_path):
        d = self._dir(tmp_path, {"a-1-py3-none-any.whl": b"x", "sneaky-9-py3-none-any.whl": b"z"})
        with pytest.raises(bi.postinstall.LockError, match="sneaky-9-py3-none-any.whl: not in wheels.lock.txt"):
            bi.postinstall.verify_wheels(d, f"a==1 --hash=sha256:{_sha(b'x')}\n")

    def test_locked_package_with_no_wheel(self, tmp_path):
        d = self._dir(tmp_path, {"a-1-py3-none-any.whl": b"x"})
        lock = f"a==1 --hash=sha256:{_sha(b'x')}\nb==1 --hash=sha256:{_sha(b'q')}\n"
        with pytest.raises(bi.postinstall.LockError, match="b: pinned .* no wheel"):
            bi.postinstall.verify_wheels(d, lock)

    def test_reports_every_offender(self, tmp_path):
        d = self._dir(tmp_path, {"a-1-py3-none-any.whl": b"bad", "extra-1-py3-none-any.whl": b"e"})
        with pytest.raises(bi.postinstall.LockError) as e:
            bi.postinstall.verify_wheels(d, f"a==1 --hash=sha256:{_sha(b'x')}\n")
        assert "a-1-py3-none-any.whl" in str(e.value) and "extra-1-py3-none-any.whl" in str(e.value)

    @pytest.mark.parametrize("lock,match", [
        ("a>=1 --hash=sha256:" + "0" * 64, "isn't a pinned"),
        ("a==1\n", "no --hash"),
        ("a==1 --hash=sha256:abc", "unexpected"),
        ("a==1 --hash=md5:" + "0" * 32, "unexpected"),
        ("a==1 --hash=sha256:" + "0" * 64 + " --index-url=https://x", "unexpected"),
        ("a==1 ; python_version>'3' --hash=sha256:" + "0" * 64, "unexpected"),
        ("--hash=sha256:" + "0" * 64, "isn't a pinned"),
        ("a==1 --hash=sha256:" + "0" * 64 + "\na==2 --hash=sha256:" + "1" * 64, "listed twice"),
        ("a==1 --hash=sha256:" + "0" * 64 + " \\\n", "trailing backslash"),
        ("# only a comment\n", "no packages"),
    ])
    def test_malformed_lock(self, tmp_path, lock, match):
        d = self._dir(tmp_path, {"a-1-py3-none-any.whl": b"x"})
        with pytest.raises(bi.postinstall.LockError, match=match):
            bi.postinstall.verify_wheels(d, lock)

    def test_not_a_wheel_name(self, tmp_path):
        d = self._dir(tmp_path, {"weird.whl": b"x"})
        with pytest.raises(bi.postinstall.LockError, match="weird.whl isn't a wheel"):
            bi.postinstall.verify_wheels(d, f"a==1 --hash=sha256:{_sha(b'x')}\n")

    def test_build_wrapper_raises_build_error(self, tmp_path):
        d = self._dir(tmp_path, {"a-1-py3-none-any.whl": b"bad"})
        lock = tmp_path / "l.txt"
        lock.write_text(f"a==1 --hash=sha256:{_sha(b'x')}\n")
        with pytest.raises(bi.BuildError, match="a-1-py3-none-any.whl"):
            bi.verify_wheel_hashes(d, lock)


class TestDownloadCap:
    class _Resp:
        def __init__(self, body, headers=None):
            self._body, self.headers = body, headers or {}
            self.reads = 0

        def read(self, n):
            self.reads += 1
            chunk, self._body = self._body[:n], self._body[n:]
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _serve(self, monkeypatch, resp):
        seen = {}

        def urlopen(url, timeout=None):
            seen["timeout"] = timeout
            return resp
        monkeypatch.setattr(bi.urllib.request, "urlopen", urlopen)
        return seen

    def test_writes_the_file(self, monkeypatch, tmp_path):
        seen = self._serve(monkeypatch, self._Resp(b"abc"))
        dest = bi.download("https://example.invalid/x.zip", tmp_path / "x.zip")
        assert dest.read_bytes() == b"abc" and seen["timeout"] == 120
        assert not (tmp_path / "x.zip.part").exists()

    def test_over_the_cap_leaves_nothing(self, monkeypatch, tmp_path):
        self._serve(monkeypatch, self._Resp(b"x" * 101))
        with pytest.raises(bi.BuildError) as exc:
            bi.download("https://example.invalid/x.zip", tmp_path / "x.zip", max_bytes=100)
        assert "example.invalid" not in str(exc.value)
        assert list(tmp_path.iterdir()) == []

    def test_declared_length_over_the_cap_refused_before_reading(self, monkeypatch, tmp_path):
        resp = self._Resp(b"x", headers={"Content-Length": "101"})
        self._serve(monkeypatch, resp)
        with pytest.raises(bi.BuildError):
            bi.download("https://example.invalid/x.zip", tmp_path / "x.zip", max_bytes=100)
        assert resp.reads == 0 and list(tmp_path.iterdir()) == []

    def test_slow_transfer_hits_the_deadline(self, monkeypatch, tmp_path):
        self._serve(monkeypatch, self._Resp(b"x" * (3 << 20)))
        ticks = iter(range(0, 1000, 10))
        with pytest.raises(bi.BuildError):
            bi.download("https://example.invalid/x.zip", tmp_path / "x.zip",
                        deadline_seconds=15, clock=lambda: next(ticks))
        assert list(tmp_path.iterdir()) == []
