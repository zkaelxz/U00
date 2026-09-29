"""The Playwright seeded API (frontend/e2e/serve_seeded_api.py) must never
run pip, reset a library or touch the real extension bridge, even when an
e2e mock leaks a request through to it (this happened once: a held route
Chromium let through on page close ran real pip installs)."""
import importlib.util
import inspect
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from services import diagnostics_gaps_service as diag
from services import extension_service as ext

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVE = os.path.join(ROOT, "frontend", "e2e", "serve_seeded_api.py")


def _load_serve():
    spec = importlib.util.spec_from_file_location("serve_seeded_api", SERVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def stubbed(isolated_db, monkeypatch):
    serve = _load_serve()
    serve.install_e2e_stubs(monkeypatch.setattr)
    return serve


def test_main_installs_the_stubs_before_the_app():
    src = inspect.getsource(_load_serve().main)
    assert "install_e2e_stubs()" in src
    assert src.index("install_e2e_stubs()") < src.index("create_app(")


def test_pip_can_never_run(stubbed, monkeypatch):
    # If anything still reached a real process runner, fail loudly.
    monkeypatch.setattr(diag, "_stream_tree", lambda *a, **k: pytest.fail("pip would have run"))
    assert diag._run_commands([(["pip", "install", "x"], 1)]) == {"ok": False, "output_tail": ["stubbed in e2e"]}
    client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    for action in ("install", "upgrade"):
        r = client.post(f"/api/diagnostics/dependencies/edge_tts/{action}", json={"confirm": True})
        assert r.status_code == 409


def test_reset_and_extension_are_stubbed(stubbed, monkeypatch):
    import page_server
    monkeypatch.setattr(page_server, "load_or_create_token", lambda: pytest.fail("real token read"))
    monkeypatch.setattr(page_server, "ensure_server_started", lambda: pytest.fail("bridge started"))
    client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = client.post("/api/diagnostics/reset-library", json={"confirm": True, "confirm_text": "RESET"})
    assert r.status_code == 409
    r = client.post("/api/extension/token", json={"confirm": True})
    assert r.status_code == 200 and r.json() == {"token": stubbed.E2E_STUB_TOKEN}
    r = client.post("/api/extension/enabled", json={"enabled": True})
    assert r.status_code == 200 and r.json() == {"enabled": True, "running": False, "restart_needed": False}
    assert ext.reveal_token(confirm=True) == {"token": stubbed.E2E_STUB_TOKEN}
