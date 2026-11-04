"""
installer/service_update.py and installer/ed25519_verify.py: the elevated
updater the boot service uses for app-only updates. Folders and the zip are
real (tmp_path); the service manager and the health probe are fakes, so
nothing starts. Windows can't run here: what these pin is the decisions
(what is trusted, what is refused, what is put back).
"""

import base64
import json
import os
import zipfile

import pytest

from installer import app_swap as sw
from installer import ed25519_verify, service_layout, service_update as su
from lib import release_signature
from services import update_service

ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")

OLD, NEW = "0.1.0", "0.2.0"
NAME = f"BaiheStudio-App-{NEW}.zip"


def _raw_public(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


# --- the pure-Python verifier -----------------------------------------------------

def test_the_rfc_8032_empty_message_vector_verifies():
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e3970"
        "1cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519_verify.verify(public, signature, b"")


@pytest.mark.parametrize("size", [0, 1, 70, 5000])
def test_it_agrees_with_cryptography_on_valid_and_altered_signatures(size):
    key = ed.Ed25519PrivateKey.generate()
    public, message = _raw_public(key), os.urandom(size)
    signature = key.sign(message)
    assert ed25519_verify.verify(public, signature, message)
    assert not ed25519_verify.verify(public, signature, message + b"x")
    flipped = bytearray(signature)
    flipped[10] ^= 1
    assert not ed25519_verify.verify(public, bytes(flipped), message)
    other = _raw_public(ed.Ed25519PrivateKey.generate())
    assert not ed25519_verify.verify(other, signature, message)


@pytest.mark.parametrize("public,signature", [(b"", b""), (b"x" * 32, b"y" * 64), (b"\xff" * 32, b"\xff" * 64),
                                              (bytes(32), bytes(64))])
def test_malformed_input_is_false_never_an_exception(public, signature):
    assert ed25519_verify.verify(public, signature, b"m") is False


# --- agreement with the app's own copies -------------------------------------------

def test_the_signed_text_is_the_one_the_release_job_signs():
    sha = "ab" * 32
    assert su.signed_message(NAME, sha) == release_signature.signed_message(NAME, sha)


def test_versions_order_as_the_app_orders_them():
    ordered = ["0.1.0-beta.2", "0.1.0-beta.10", "0.1.0", "0.1.1", "0.2.0-beta.1", "0.2.0", "1.0"]
    keys = [su.version_key(v) for v in ordered]
    assert keys == sorted(keys)
    assert [update_service.parse_version(v) for v in ordered] == sorted(
        update_service.parse_version(v) for v in ordered)
    assert su.version_key("nonsense") is None and su.version_key("0.1.0\n..") is None


# --- the update --------------------------------------------------------------------

class FakeService:
    """The BaiheStudio service and what answers on its port."""

    def __init__(self, healthy_after_start=True):
        self.running, self.stops, self.starts = True, 0, 0
        self.healthy_after_start = healthy_after_start

    def stop(self, name, run, sleep=None):
        self.stops += 1
        self.running = False

    def start(self, name, run, sleep=None):
        self.starts += 1
        self.running = True

    def state(self, name, run):
        return "RUNNING" if self.running else "STOPPED"

    def health(self, port):
        return self.running and (self.starts == 0 or self.healthy_after_start)


class Log:
    def __init__(self):
        self.lines = []

    def log(self, text):
        self.lines.append(text)


@pytest.fixture
def key():
    return ed.Ed25519PrivateKey.generate()


@pytest.fixture
def env(tmp_path, key, monkeypatch):
    root, data = tmp_path / "Baihe Studio", tmp_path / "data"
    app = root / "app"
    (app / "installer").mkdir(parents=True)
    (app / "api").mkdir()
    (app / "api" / "__main__.py").write_text("old")
    (app / "INSTALLED").write_text(str(data))
    manifest = {"product": "Baihe Studio", "app_version": OLD,
                "python": {"embed_sha256": "p"}, "wheels": [{"file": "a.whl", "sha256": "1"}],
                "services": {"caddy": {"sha256": "c"}, "winsw": {"sha256": "s"}}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    (data / "library" / "updates").mkdir(parents=True)
    layout = service_layout.Layout(root, data, tmp_path / "Program Files" / "Baihe Studio Services")
    layout.public_key.parent.mkdir(parents=True)
    layout.public_key.write_text(release_signature.format_public_key(_raw_public(key)) + "\n")
    svc = FakeService()
    monkeypatch.setattr(su, "stop_service", svc.stop)
    monkeypatch.setattr(su, "start_service", svc.start)
    monkeypatch.setattr(su, "query_state", svc.state)
    return layout, svc, manifest


def _release(layout, key, version=NEW, base=None, signer=None, tamper=False, request_version=None):
    """Writes what the server leaves for BaiheUpdate: the zip, its signature and the request."""
    updates = layout.data / "library" / "updates"
    name = f"BaiheStudio-App-{version}.zip"
    entries = {r: f"# {r}\n" for r in sw.REQUIRED_ENTRIES}
    entries["api/__main__.py"] = "new"
    entries[sw.APP_MANIFEST_NAME] = json.dumps({
        "product": "Baihe Studio", "layer": "app", "version": version, "min_base_version": "0.1.0",
        "base": base if base is not None else sw.base_fingerprint(sw.installed_manifest(layout.root)),
        "db_schema_sha256": "d"})
    with zipfile.ZipFile(updates / name, "w") as zf:
        for entry, data in entries.items():
            zf.writestr(entry, data)
    import hashlib
    sha = hashlib.sha256((updates / name).read_bytes()).hexdigest()
    if tamper:
        sha = "0" * 64
    signature = (signer or key).sign(su.signed_message(name, sha))
    (updates / (name + ".sig")).write_text(release_signature.format_signature(signature))
    (updates / su.REQUEST_NAME).write_text(json.dumps({"version": request_version or version,
                                                       "name": name}))


def _apply(layout, svc):
    return su.apply_update(layout, Log(), 8600, health=svc.health, sleep=lambda s: None)


def _app_text(layout):
    return (layout.root / "app" / "api" / "__main__.py").read_text()


def test_a_signed_newer_update_is_unpacked_switched_and_kept(env, key):
    layout, svc, _ = env
    _release(layout, key)
    assert "0.1.0 to 0.2.0" in _apply(layout, svc)
    assert _app_text(layout) == "new"
    assert (layout.root / "app" / "INSTALLED").read_text() == str(layout.data)
    assert (layout.root / "app-0.1.0" / "api" / "__main__.py").read_text() == "old"
    assert json.loads((layout.root / "manifest.json").read_text())["app_version"] == NEW
    assert sw.read_result(layout.root)["code"] == "updated"
    assert svc.running and svc.stops == 1 and svc.starts == 1
    assert not (layout.data / "library" / "updates" / su.REQUEST_NAME).exists()
    assert not layout.staging.exists()


@pytest.mark.parametrize("problem", ["other_signer", "tampered", "no_key_file", "bad_key_line"])
def test_a_release_the_admin_copys_key_does_not_vouch_for_is_refused_before_anything_stops(
        env, key, problem):
    layout, svc, _ = env
    _release(layout, key, signer=ed.Ed25519PrivateKey.generate() if problem == "other_signer" else None,
             tamper=problem == "tampered")
    if problem == "no_key_file":
        layout.public_key.unlink()
    if problem == "bad_key_line":
        layout.public_key.write_text("# only a comment\n")
    with pytest.raises(service_layout.ServiceError):
        _apply(layout, svc)
    assert _app_text(layout) == "old" and svc.stops == 0 and svc.running
    assert not (layout.root / f"app-{NEW}").exists() and not layout.staging.exists()
    assert sw.read_journal(layout.root) is None


def test_the_request_is_spent_even_when_the_update_is_refused(env, key):
    layout, svc, _ = env
    _release(layout, key, tamper=True)
    with pytest.raises(service_layout.ServiceError):
        _apply(layout, svc)
    assert not (layout.data / "library" / "updates" / su.REQUEST_NAME).exists()


@pytest.mark.parametrize("version", ["0.1.0", "0.0.9"])
def test_a_signed_but_not_newer_release_is_refused(env, key, version):
    # A genuine old release could carry a fixed-since bug: the service account
    # picks which file to hand over, so the version must go forward.
    layout, svc, _ = env
    _release(layout, key, version=version)
    with pytest.raises(service_layout.ServiceError, match="isn't newer"):
        _apply(layout, svc)
    assert svc.stops == 0 and _app_text(layout) == "old"


def test_a_request_whose_version_and_file_name_disagree_is_refused(env, key):
    layout, svc, _ = env
    _release(layout, key, request_version="0.9.0")
    with pytest.raises(service_layout.ServiceError, match="doesn't name a release file"):
        _apply(layout, svc)
    assert svc.stops == 0


def test_a_base_the_install_does_not_have_needs_the_full_installer(env, key):
    layout, svc, _ = env
    _release(layout, key, base={"python_sha256": "other"})
    with pytest.raises(service_layout.ServiceError, match="full installer"):
        _apply(layout, svc)
    assert svc.stops == 0 and _app_text(layout) == "old"


def test_a_linked_updates_folder_is_not_read_through(env, key, tmp_path):
    layout, svc, _ = env
    _release(layout, key)
    updates = layout.data / "library" / "updates"
    elsewhere = tmp_path / "elsewhere"
    updates.rename(elsewhere)
    try:
        os.symlink(elsewhere, updates, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links need extra rights here")
    with pytest.raises(service_layout.ServiceError, match="link"):
        _apply(layout, svc)
    assert svc.stops == 0


def test_an_unfinished_earlier_update_blocks_a_new_one(env, key):
    layout, svc, _ = env
    sw.work_dir(layout.root).mkdir()
    sw.write_journal(layout.root, sw.CHECKING, OLD, "0.1.5", OLD)
    _release(layout, key)
    with pytest.raises(service_layout.ServiceError, match="unfinished"):
        _apply(layout, svc)
    assert svc.stops == 0


def test_a_new_version_that_does_not_answer_is_rolled_back_and_the_old_one_runs_again(env, key):
    layout, svc, _ = env
    svc.healthy_after_start = False
    _release(layout, key)

    def healthy_once_old_is_back(port):
        return svc.running and (_app_text(layout) == "old" or svc.starts == 0)

    with pytest.raises(service_layout.ServiceError, match="previous version was put back"):
        su.apply_update(layout, Log(), 8600, health=healthy_once_old_is_back, sleep=lambda s: None)
    assert _app_text(layout) == "old" and svc.running
    assert json.loads((layout.root / "manifest.json").read_text())["app_version"] == OLD
    assert sw.read_result(layout.root)["code"] == "rolled_back_health"


def test_a_locked_app_folder_is_not_left_queued_for_a_launcher_the_service_never_runs(
        env, key, monkeypatch):
    layout, svc, _ = env
    _release(layout, key)

    def locked(*args, **kwargs):
        raise sw.Locked()

    monkeypatch.setattr(sw, "switch_in", locked)
    with pytest.raises(service_layout.ServiceError, match="couldn't be switched"):
        _apply(layout, svc)
    assert sw.read_journal(layout.root) is None
    assert sw.read_result(layout.root)["code"] == "rolled_back_switch"
    assert _app_text(layout) == "old" and svc.running


def test_the_updater_never_asks_for_a_message_box_or_a_signin_recovery(env, key, monkeypatch):
    # Nobody is signed in where it runs: a message box in session 0 would wait forever.
    layout, svc, _ = env
    _release(layout, key)
    monkeypatch.setattr(sw, "_message", lambda text: pytest.fail("message box"))
    monkeypatch.setattr(sw, "register_recovery", lambda root: pytest.fail("sign-in recovery"))
    _apply(layout, svc)


def test_a_signature_file_that_is_not_base64_is_refused(env, key):
    layout, svc, _ = env
    _release(layout, key)
    (layout.data / "library" / "updates" / (NAME + ".sig")).write_text(
        base64.b64encode(b"short").decode())
    with pytest.raises(service_layout.ServiceError, match="signature"):
        _apply(layout, svc)
    assert svc.stops == 0


def test_a_refusal_before_the_switch_leaves_a_result_the_app_can_show(env, key):
    layout, svc, _ = env
    _release(layout, key, tamper=True)
    with pytest.raises(service_layout.ServiceError):
        _apply(layout, svc)
    result = sw.read_result(layout.root)
    assert (result["code"], result["old"], result["new"]) == ("not_started", OLD, NEW)
