"""
Tests for serving the built React app from FastAPI (api/static_frontend.py),
using a temporary fake `dist` folder -- no real build, no network.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings, load_settings
from api.server import create_app


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "site" / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>FAKE-INDEX</html>")
    (d / "assets" / "app.js").write_text("console.log('fake')")
    (tmp_path / "site" / "secret.txt").write_text("TOP-SECRET")
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    return d


def _client(dist_dir, **settings):
    app = create_app(ApiSettings(**settings), frontend_dist=dist_dir)
    return TestClient(app, raise_server_exceptions=False)


class TestServing:
    def test_root_serves_index(self, dist):
        r = _client(dist).get("/")
        assert r.status_code == 200
        assert "FAKE-INDEX" in r.text
        assert r.headers["cache-control"] == "no-cache"

    def test_asset_served(self, dist):
        r = _client(dist).get("/assets/app.js")
        assert r.status_code == 200 and "fake" in r.text

    def test_unknown_page_path_gets_index(self, dist):
        r = _client(dist).get("/some/deep/link")
        assert r.status_code == 200 and "FAKE-INDEX" in r.text

    def test_missing_asset_is_404_not_index(self, dist):
        r = _client(dist).get("/assets/missing.js")
        assert r.status_code == 404 and "FAKE-INDEX" not in r.text

    def test_api_routes_not_shadowed(self, dist):
        c = _client(dist)
        assert c.get("/api/health").json() == {"status": "ok"}
        assert c.get("/api/docs").status_code == 200
        assert c.get("/api/openapi.json").status_code == 200

    def test_unknown_api_path_stays_json_404(self, dist):
        c = _client(dist)
        for p in ("/api/nope", "/api", "/api/"):
            r = c.get(p)
            assert r.status_code == 404, p
            assert "FAKE-INDEX" not in r.text
            assert r.json()["error"]["code"] == "not_found"

    def test_post_to_unknown_path_is_not_served(self, dist):
        assert _client(dist).post("/anything").status_code == 405


class TestTraversal:
    @pytest.mark.parametrize("path", [
        "/../secret.txt", "/..%2fsecret.txt", "/%2e%2e/secret.txt",
        "/assets/../../secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt",
        "/assets/..%2f..%2fsecret.txt", "/..%5csecret.txt",
    ])
    def test_never_serves_outside_dist(self, dist, path):
        r = _client(dist).get(path)
        assert "TOP-SECRET" not in r.text
        assert r.status_code in (200, 404)

    def test_absolute_path_component(self, dist, tmp_path):
        secret = tmp_path / "secret.txt"
        r = _client(dist).get("//" + str(secret).lstrip("/"))
        assert "TOP-SECRET" not in r.text


class TestDisabledOrMissing:
    def test_no_dist_is_api_only(self, tmp_path):
        c = _client(tmp_path / "nope")
        assert c.get("/").status_code == 404
        assert c.get("/api/health").status_code == 200

    def test_dist_without_index_is_api_only(self, tmp_path):
        (tmp_path / "assets").mkdir()
        assert _client(tmp_path).get("/").status_code == 404

    def test_switch_off(self, dist):
        c = _client(dist, serve_frontend=False)
        assert c.get("/").status_code == 404
        assert c.get("/api/health").status_code == 200

    def test_missing_dist_logs_build_hint(self, tmp_path, caplog):
        with caplog.at_level("INFO", logger="api.static_frontend"):
            _client(tmp_path / "nope")
        assert any("npm run build" in m for m in caplog.messages)


class TestConfig:
    def test_default_on(self):
        assert load_settings({}).serve_frontend is True

    @pytest.mark.parametrize("value,expected", [("0", False), ("false", False),
                                                ("1", True), ("On", True)])
    def test_switch(self, value, expected):
        assert load_settings({"BAIHE_API_SERVE_FRONTEND": value}).serve_frontend is expected

    def test_bad_value_refused(self):
        with pytest.raises(ValueError):
            load_settings({"BAIHE_API_SERVE_FRONTEND": "maybe"})

    def test_cors_and_host_unchanged(self, dist):
        s = load_settings({})
        assert s.host == "127.0.0.1" and s.cors_origins == ()
        r = _client(dist).get("/", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in r.headers
