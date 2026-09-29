"""API startup hook (api/background.py + create_app lifespan). Everything the
hook would start is faked: no thread, no port."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api import background
from api.api_config import ApiSettings, load_settings
from api.server import create_app


@pytest.fixture
def fakes(monkeypatch, isolated_db):
    calls = {"scheduler": 0, "page_server": 0}
    import page_server
    from sources import chapter_check

    def sched():
        calls["scheduler"] += 1
    started = {"on": False}

    def serve():
        calls["page_server"] += 1
        started["on"] = True
        return True
    monkeypatch.setattr(chapter_check, "ensure_scheduler_started", sched)
    monkeypatch.setattr(page_server, "ensure_server_started", serve)
    monkeypatch.setattr(page_server, "server_running", lambda: started["on"])
    monkeypatch.setattr(background, "_started", None)
    return calls


def test_settings_flag():
    assert ApiSettings().background_services is False
    assert load_settings({}).background_services is True
    assert load_settings({"BAIHE_API_BACKGROUND": "0"}).background_services is False
    with pytest.raises(ValueError):
        load_settings({"BAIHE_API_BACKGROUND": "maybe"})


def test_conftest_keeps_it_off_for_the_module_app():
    import api.server
    assert api.server.app.state.settings.background_services is False


def test_lifespan_off_starts_nothing(fakes):
    with TestClient(create_app(ApiSettings())) as c:
        assert c.get("/api/health").status_code == 200
    assert fakes == {"scheduler": 0, "page_server": 0}


def test_lifespan_on_starts_scheduler_not_page_server_by_default(fakes):
    with TestClient(create_app(ApiSettings(background_services=True))) as c:
        assert c.get("/api/health").status_code == 200
    assert fakes == {"scheduler": 1, "page_server": 0}


def test_page_server_only_when_enabled_and_idempotent(fakes):
    from sources import store as src_store
    src_store.set_setting("page_server_enabled", True)
    assert background.start_background_services() == {"chapter_scheduler": True,
                                                       "page_server": True}
    app = create_app(ApiSettings(background_services=True))
    with TestClient(app):
        pass
    with TestClient(app):
        pass
    assert fakes == {"scheduler": 1, "page_server": 1}


def test_failure_does_not_stop_startup(fakes, monkeypatch):
    from sources import chapter_check

    def boom():
        raise RuntimeError("C:\\Users\\me\\secret sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx")
    monkeypatch.setattr(chapter_check, "ensure_scheduler_started", boom)
    with TestClient(create_app(ApiSettings(background_services=True))) as c:
        assert c.get("/api/health").status_code == 200
    assert background._started == {"chapter_scheduler": False, "page_server": False}
