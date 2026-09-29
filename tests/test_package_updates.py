"""
tests/test_package_updates.py -- Diagnostics "Packages": installed versions
and the explicit "Check for updates" (PyPI, cached), which offers Update
only when a newer release is allowed by constraints.txt, the installed
packages that depend on it and the known limitations. PyPI and pip are
faked; no network, no install.
"""
import pytest

import diagnostics
from services import diagnostics_gaps_service as svc

_v, SPEC, _r = diagnostics._packaging()


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


def _file(yanked=False):
    return [{"filename": "x.whl", "yanked": yanked}]


@pytest.fixture(autouse=True)
def _clear_cache():
    svc._UPDATES.update(checked_at=None, packages={})
    yield
    svc._UPDATES.update(checked_at=None, packages={})


# ---- PyPI read ----

def test_pypi_release_versions_filters_and_uses_a_fixed_url(monkeypatch):
    import requests
    seen = {}

    def get(url, **kw):
        seen.update(kw, url=url)
        return _Resp(200, {"releases": {
            "1.0.0": _file(), "1.2.0": _file(), "2.0.0rc1": _file(), "1.3.0": _file(yanked=True),
            "1.4.0": [], "not a version": _file(), "1.1.0.dev1": _file()}})
    monkeypatch.setattr(requests, "get", get)
    assert sorted(diagnostics.pypi_release_versions("Sudachidict_Core")) == ["1.0.0", "1.2.0"]
    assert seen["url"] == "https://pypi.org/pypi/sudachidict-core/json"
    assert seen["timeout"] == diagnostics.PYPI_JSON_TIMEOUT


@pytest.mark.parametrize("bad", ["", "../x", "a b", "x/../../y", "-e"])
def test_pypi_release_versions_never_builds_a_url_from_a_bad_name(monkeypatch, bad):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("no request"))
    assert diagnostics.pypi_release_versions(bad) is None


def test_pypi_release_versions_none_on_failure(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(404, {}))
    assert diagnostics.pypi_release_versions("jieba") is None

    def boom(*a, **k):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(requests, "get", boom)
    assert diagnostics.pypi_release_versions("jieba") is None


# ---- classification ----

def _c(name, have, releases, constraints=None, required_by=None):
    return diagnostics.classify_update(name, have, releases, constraints or {}, required_by or {})


def test_up_to_date_and_unknown():
    assert _c("jieba", "0.42.1", ["0.42.0", "0.42.1"])["status"] == "up_to_date"
    assert _c("jieba", "0.42.1", None)["status"] == "unknown"
    assert _c("jieba", None, ["1.0"])["status"] == "unknown"


def test_update_to_the_newest_release():
    out = _c("jieba", "0.42.0", ["0.41", "0.42.0", "0.42.1"])
    assert out == {"status": "update", "latest": "0.42.1", "target": "0.42.1", "reason": None}


def test_constraints_hold_back_the_newest_but_allow_an_update():
    cons = {"transformers": (SPEC.SpecifierSet("<6"), "transformers<6")}
    out = _c("transformers", "5.1.0", ["5.1.0", "5.2.0", "6.0.0"], cons)
    assert out["status"] == "update" and out["target"] == "5.2.0" and out["latest"] == "6.0.0"
    assert "constraints.txt (transformers<6)" in out["reason"]


def test_held_back_when_no_newer_release_is_allowed():
    cons = {"transformers": (SPEC.SpecifierSet("<6"), "transformers<6")}
    out = _c("transformers", "5.2.0", ["5.2.0", "6.0.0"], cons)
    assert out["status"] == "held_back" and out["target"] is None
    assert "constraints.txt" in out["reason"]


def test_installed_dependents_hold_back_an_upgrade():
    req = {"huggingface-hub": [("transformers", SPEC.SpecifierSet(">=1.5,<2.0"))]}
    out = _c("huggingface_hub", "1.5.0", ["1.5.0", "1.6.0", "2.0.0"], required_by=req)
    assert out["status"] == "update" and out["target"] == "1.6.0"
    assert "transformers (needs huggingface_hub <2.0,>=1.5)" in out["reason"]
    out = _c("huggingface_hub", "1.6.0", ["1.6.0", "2.0.0"], required_by=req)
    assert out["status"] == "held_back"


def test_a_local_build_tag_is_not_an_older_version():
    assert _c("numpy", "2.1.0+local", ["2.1.0"])["status"] == "up_to_date"


def test_python_version_limitation(monkeypatch):
    monkeypatch.setitem(diagnostics.KNOWN_UPGRADE_LIMITATIONS, "jieba",
                        {"python_version": tuple(diagnostics.sys.version_info[:2]),
                         "reason": "no wheels"})
    out = _c("jieba", "0.42.0", ["0.42.0", "0.43.0"])
    assert out["status"] == "held_back" and "no wheels" in out["reason"]


def test_installed_dist_version_tries_alternates(monkeypatch):
    have = {"opencv-python-headless": "4.10.0.84"}
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda d: have.get(d))
    assert diagnostics.installed_dist_version("cv2") == ("opencv-python-headless", "4.10.0.84")
    assert diagnostics.installed_dist_version("PIL") == ("pillow", None)


def test_real_constraints_file_parses():
    cons = diagnostics.constraint_specifiers()
    assert "torch" in cons and cons["torch"][0].contains("2.11.0")
    assert not cons["transformers"][0].contains("6.0.0")


# ---- the service: check, cache, upgrade to exactly the target ----

def _fake_env(monkeypatch, installed, releases):
    monkeypatch.setattr(svc, "installable_packages", lambda: set(installed) | {"deepl"})
    monkeypatch.setattr(svc, "_package_installed", lambda n: n in installed)
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda d: installed.get(d))
    calls = []

    def fetch(dist):
        calls.append(dist)
        return releases.get(dist)
    monkeypatch.setattr(diagnostics, "pypi_release_versions", fetch)
    monkeypatch.setattr(diagnostics, "installed_requirements_on", lambda: {})
    return calls


def test_check_reports_each_installed_package_and_caches(monkeypatch):
    calls = _fake_env(monkeypatch, {"jieba": "0.42.0", "pypdf": "5.0.0", "torch": "2.11.0+cu128"},
                      {"jieba": ["0.42.0", "0.42.1"], "pypdf": ["5.0.0"]})
    out = svc.check_package_updates()
    p = out["packages"]
    assert set(p) == {"jieba", "pypdf", "torch"}          # deepl isn't installed
    assert p["jieba"]["status"] == "update" and p["jieba"]["target"] == "0.42.1"
    assert p["jieba"]["installed_version"] == "0.42.0"
    assert p["pypdf"]["status"] == "up_to_date"
    assert p["torch"]["status"] == "managed"
    assert sorted(calls) == ["jieba", "pypdf"]            # torch is never asked about
    svc.check_package_updates()
    assert len(calls) == 2                                # cached within the minute
    svc.check_package_updates(force=True)
    assert len(calls) == 4


def test_upgrade_installs_exactly_the_checked_target(monkeypatch):
    _fake_env(monkeypatch, {"jieba": "0.42.0"}, {"jieba": ["0.42.0", "0.42.1"]})
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: False)
    svc.check_package_updates()
    seen = []

    def fake(cmd, timeout):
        seen.append(cmd)
        yield {"returncode": 0, "timed_out": False}
    monkeypatch.setattr(svc, "_stream_tree", fake)
    assert svc.upgrade_dependency("jieba", confirm=True)["ok"] is True
    (cmd,) = seen
    assert "jieba==0.42.1" in cmd and "--upgrade" not in cmd
    assert cmd[cmd.index("-c") + 1].endswith("constraints.txt")
    assert svc._cached_update("jieba")["status"] == "up_to_date"


def test_upgrade_refused_when_the_check_found_nothing_allowed(monkeypatch):
    _fake_env(monkeypatch, {"pypdf": "5.0.0"}, {"pypdf": ["5.0.0"]})
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: False)
    monkeypatch.setattr(svc, "_stream_tree", lambda *a, **k: pytest.fail("no pip"))
    svc.check_package_updates()
    with pytest.raises(svc.AdminActionNotPossible):
        svc.upgrade_dependency("pypdf", confirm=True)


def test_presets_carry_installed_versions(monkeypatch):
    monkeypatch.setattr(diagnostics, "check_dependency", lambda imp: imp in ("jieba", "streamlit"))
    monkeypatch.setattr(diagnostics, "get_installed_version",
                        {"jieba": "0.42.1", "streamlit": "1.56.0"}.get)
    p = svc.get_install_presets()["packages"]
    assert p["jieba"]["installed_version"] == "0.42.1"
    assert p["streamlit"]["installed_version"] == "1.56.0"      # required ones too
    assert p["streamlit"]["installable"] is False
    assert p["pypinyin"]["installed_version"] is None
