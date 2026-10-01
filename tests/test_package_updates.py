"""
tests/test_package_updates.py -- Diagnostics "Packages": installed versions
and the explicit "Check for updates" (PyPI, cached), which offers Update
only when a newer release is allowed by constraints.txt, the installed
packages that depend on it and the known limitations. PyPI and pip are
faked; no network, no install.
"""
import json

import pytest

import diagnostics
from services import diagnostics_gaps_service as svc

_v, SPEC, _r = diagnostics._packaging()


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.closed = False

    def iter_content(self, size):
        body = json.dumps(self._data).encode()
        for i in range(0, len(body), size):
            yield body[i:i + size]

    def close(self):
        self.closed = True


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
    assert seen["allow_redirects"] is False and seen["stream"] is True


def test_pypi_release_versions_stops_reading_past_the_size_cap(monkeypatch):
    import requests
    resp = _Resp(200, {"releases": {"1.0.0": _file()}, "pad": "x" * 200})
    monkeypatch.setattr(diagnostics, "PYPI_JSON_MAX_BYTES", 100)
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
    assert diagnostics.pypi_release_versions("jieba") is None
    assert resp.closed


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
    monkeypatch.setattr(svc, "installable_packages", lambda: set(installed) | {"anthropic"})
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
    assert set(p) == {"jieba", "pypdf", "torch"}          # anthropic isn't installed
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
    with pytest.raises(svc.AdminActionStale):             # the confirmed version is required
        svc.upgrade_dependency("jieba", confirm=True)
    assert svc.upgrade_dependency("jieba", confirm=True, target="0.42.1")["ok"] is True
    (cmd,) = seen
    assert "jieba==0.42.1" in cmd and "--upgrade" not in cmd
    assert cmd[cmd.index("-c") + 1].endswith("constraints.txt")
    # pip may have moved other packages too: every cached target is dropped
    assert svc._cached_update("jieba") is None


def test_upgrade_refused_when_the_check_found_nothing_allowed(monkeypatch):
    _fake_env(monkeypatch, {"pypdf": "5.0.0"}, {"pypdf": ["5.0.0"]})
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: False)
    monkeypatch.setattr(svc, "_stream_tree", lambda *a, **k: pytest.fail("no pip"))
    svc.check_package_updates()
    with pytest.raises(svc.AdminActionStale):
        svc.upgrade_dependency("pypdf", confirm=True, target="5.0.0")
    with pytest.raises(svc.AdminActionRefused):
        svc.upgrade_dependency("pypdf", confirm=True)


def test_presets_carry_installed_versions(monkeypatch):
    monkeypatch.setattr(diagnostics, "check_dependency", lambda imp: imp in ("jieba", "fastapi"))
    monkeypatch.setattr(diagnostics, "get_installed_version",
                        {"jieba": "0.42.1", "fastapi": "0.115.0"}.get)
    p = svc.get_install_presets()["packages"]
    assert p["jieba"]["installed_version"] == "0.42.1"
    assert p["fastapi"]["installed_version"] == "0.115.0"      # required ones too
    assert p["fastapi"]["installable"] is False
    assert p["pypinyin"]["installed_version"] is None


def test_hostile_release_keys_never_become_a_target():
    """PyPI's release keys only reach pip as str(packaging.Version)."""
    releases = ["--index-url=https://evil.example/simple", "1.0 --pre", "9.9; rm -rf /", "1.1"]
    out = _c("jieba", "1.0", releases)
    assert out["status"] == "update" and out["target"] == "1.1"


def test_a_concurrent_check_never_starts_a_second_fan_out(monkeypatch):
    calls = _fake_env(monkeypatch, {"jieba": "0.42.0"}, {"jieba": ["0.42.0"]})
    assert svc._UPDATES_FETCH.acquire(blocking=False)
    try:
        with pytest.raises(svc.AdminActionStale):       # nothing cached yet
            svc.check_package_updates()
        svc._UPDATES.update(checked_at=1.0, packages={"jieba": {"name": "jieba"}})
        assert svc.check_package_updates()["packages"] == {"jieba": {"name": "jieba"}}
    finally:
        svc._UPDATES_FETCH.release()
    assert calls == []


def test_upgrade_refuses_a_target_the_check_no_longer_offers(monkeypatch):
    _fake_env(monkeypatch, {"jieba": "0.42.0"}, {"jieba": ["0.42.0", "0.42.1"]})
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: False)
    monkeypatch.setattr(svc, "_stream_tree", lambda *a, **k: pytest.fail("no pip"))
    with pytest.raises(svc.AdminActionStale):              # no check yet
        svc.upgrade_dependency("jieba", confirm=True, target="0.42.1")
    svc.check_package_updates()
    with pytest.raises(svc.AdminActionStale):              # a newer check changed it
        svc.upgrade_dependency("jieba", confirm=True, target="0.42.0")


def test_install_and_torch_setup_clear_the_cached_check(monkeypatch):
    _fake_env(monkeypatch, {"jieba": "0.42.0"}, {"jieba": ["0.42.0", "0.42.1"]})
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: False)

    def fake(cmd, timeout):
        yield {"returncode": 0, "timed_out": False}
    monkeypatch.setattr(svc, "_stream_tree", fake)
    svc.check_package_updates()
    svc.install_dependency("jieba", confirm=True)
    assert svc._cached_update("jieba") is None
    svc.check_package_updates()
    monkeypatch.setattr(diagnostics, "nvidia_driver_info", lambda: None)
    monkeypatch.setattr(svc, "verify_torch", lambda: {"torch": "2.11.0+cpu", "error": None})
    svc.setup_gpu_torch("cpu", confirm=True)
    assert svc._cached_update("jieba") is None


def test_a_check_overtaken_by_an_install_stores_nothing(monkeypatch):
    installed = {"jieba": "0.42.0"}
    _fake_env(monkeypatch, installed, {"jieba": ["0.42.0", "0.42.1"]})

    def fetch_during_an_install(dist):
        svc._clear_update_cache()           # an install finishes while PyPI answers
        return ["0.42.0", "0.42.1"]
    monkeypatch.setattr(diagnostics, "pypi_release_versions", fetch_during_an_install)
    out = svc.check_package_updates()
    assert out["packages"]["jieba"]["status"] == "update"     # the caller still sees it
    assert svc._cached_update("jieba") is None                  # but Update can't use it
