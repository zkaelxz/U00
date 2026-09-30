"""
tests/test_portable.py -- Step 10's portable.py: is_portable() and
activate_portable_mode(). No real Windows launcher/shortcut behavior is
tested here (that's start.bat/start.ps1/make_shortcut.bat/uninstall.bat,
which the roadmap's own exit condition says can't be unit-tested --
they need a person at a real Windows machine) -- this covers the actual
Python-level logic those scripts and app.py/cli.py depend on.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portable


class TestIsPortable:
    def test_off_by_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(portable, "_MARKER_PATH", str(tmp_path / "PORTABLE"))
        monkeypatch.delenv("BAIHE_PORTABLE", raising=False)
        monkeypatch.setattr(sys, "argv", ["app.py"])
        assert portable.is_portable() is False

    def test_marker_file_turns_it_on(self, monkeypatch, tmp_path):
        marker = tmp_path / "PORTABLE"
        marker.write_text("")
        monkeypatch.setattr(portable, "_MARKER_PATH", str(marker))
        monkeypatch.delenv("BAIHE_PORTABLE", raising=False)
        monkeypatch.setattr(sys, "argv", ["app.py"])
        assert portable.is_portable() is True

    def test_command_line_flag_turns_it_on(self, monkeypatch, tmp_path):
        monkeypatch.setattr(portable, "_MARKER_PATH", str(tmp_path / "PORTABLE"))
        monkeypatch.delenv("BAIHE_PORTABLE", raising=False)
        monkeypatch.setattr(sys, "argv", ["app.py", "--portable"])
        assert portable.is_portable() is True

    def test_env_var_turns_it_on(self, monkeypatch, tmp_path):
        monkeypatch.setattr(portable, "_MARKER_PATH", str(tmp_path / "PORTABLE"))
        monkeypatch.setattr(sys, "argv", ["app.py"])
        monkeypatch.setenv("BAIHE_PORTABLE", "1")
        assert portable.is_portable() is True
        monkeypatch.setenv("BAIHE_PORTABLE", "true")
        assert portable.is_portable() is True
        monkeypatch.setenv("BAIHE_PORTABLE", "0")
        assert portable.is_portable() is False


class TestActivatePortableMode:
    def _setup(self, monkeypatch, tmp_path, on: bool):
        monkeypatch.setattr(portable, "_MARKER_PATH", str(tmp_path / "PORTABLE"))
        monkeypatch.setattr(portable, "_INSTALLED_MARKER_PATH", str(tmp_path / "INSTALLED"))
        monkeypatch.delenv(portable.DATA_DIR_ENV, raising=False)
        monkeypatch.setattr(portable, "MODEL_CACHE_DIR", str(tmp_path / "model_cache"))
        monkeypatch.setattr(sys, "argv", ["app.py"])
        for var in portable._REDIRECTS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("BAIHE_PORTABLE", "1" if on else "0")

    def test_off_does_nothing(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, on=False)
        assert portable.activate_portable_mode() is False
        assert "HF_HOME" not in os.environ
        assert not (tmp_path / "model_cache").exists()

    def test_on_creates_the_cache_dir_and_sets_every_redirect(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, on=True)
        assert portable.activate_portable_mode() is True
        assert (tmp_path / "model_cache").is_dir()
        for var, subdir in portable._REDIRECTS.items():
            assert os.environ[var] == os.path.join(str(tmp_path / "model_cache"), subdir)

    def test_does_not_override_a_value_already_set(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, on=True)
        monkeypatch.setenv("HF_HOME", "/already/set/by/the/user")
        portable.activate_portable_mode()
        assert os.environ["HF_HOME"] == "/already/set/by/the/user"
        # An unrelated redirect is still set normally.
        assert os.environ["TORCH_HOME"] == os.path.join(str(tmp_path / "model_cache"), "torch")


class TestDataDir:
    """Step 80b: where an installed copy keeps library/, .env and models."""

    def _setup(self, monkeypatch, tmp_path, marker_text=None):
        marker = tmp_path / "app" / "INSTALLED"
        marker.parent.mkdir(parents=True, exist_ok=True)
        if marker_text is not None:
            marker.write_text(marker_text, encoding="utf-8-sig")
        monkeypatch.setattr(portable, "_INSTALLED_MARKER_PATH", str(marker))
        monkeypatch.setattr(portable, "_APP_DIR", str(tmp_path / "app"))
        monkeypatch.delenv(portable.DATA_DIR_ENV, raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    def test_source_checkout_keeps_data_next_to_the_code(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        assert portable.is_installed() is False
        assert portable.data_dir() == str(tmp_path / "app")

    def test_installed_marker_names_the_data_dir(self, monkeypatch, tmp_path):
        data = tmp_path / "My Data"
        self._setup(monkeypatch, tmp_path, f"# comment\n\n{data}\n# more\n")
        assert portable.is_installed() is True
        assert portable.data_dir() == str(data)

    def test_installed_without_a_path_uses_localappdata(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, "")
        assert portable.data_dir() == str(tmp_path / "Local" / "Baihe Studio")

    def test_a_relative_path_in_the_marker_is_ignored(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, "..\\somewhere\n")
        assert portable.data_dir() == str(tmp_path / "Local" / "Baihe Studio")

    def test_env_var_overrides_everything(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, str(tmp_path / "marker-data"))
        monkeypatch.setenv(portable.DATA_DIR_ENV, str(tmp_path / "env-data"))
        assert portable.data_dir() == str(tmp_path / "env-data")

    def test_installed_copy_redirects_model_caches_into_the_data_dir(self, monkeypatch, tmp_path):
        data = tmp_path / "data"
        self._setup(monkeypatch, tmp_path, str(data))
        monkeypatch.setattr(portable, "_MARKER_PATH", str(tmp_path / "PORTABLE"))
        monkeypatch.setenv("BAIHE_PORTABLE", "0")
        monkeypatch.setattr(sys, "argv", ["api"])
        for var in portable._REDIRECTS:
            monkeypatch.delenv(var, raising=False)
        assert portable.activate_portable_mode() is True
        for var, subdir in portable._REDIRECTS.items():
            assert os.environ[var] == os.path.join(str(data), "model_cache", subdir)
        assert (data / "model_cache").is_dir()

    def test_installed_layout_without_a_marker_still_counts_as_installed(self, monkeypatch, tmp_path):
        # An interrupted upgrade removes app\INSTALLED before rewriting it:
        # the library must not fall back to the program folder meanwhile.
        self._setup(monkeypatch, tmp_path)
        (tmp_path / "python").mkdir()
        (tmp_path / "python" / "python.exe").write_text("")
        (tmp_path / "app" / "installer").mkdir()
        (tmp_path / "app" / "installer" / "launcher.py").write_text("")
        assert portable.is_installed() is True
        assert portable.data_dir() == str(tmp_path / "Local" / "Baihe Studio")
