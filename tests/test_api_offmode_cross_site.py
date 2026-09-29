"""Off mode (auth off) cross-site rule on every /api POST/PUT/PATCH.

A page on another loopback port (Streamlit, a dev server) passes the
loopback Origin check, and off mode has no CSRF token, so a no-preflight
"simple" POST must be refused on every route, not just local_only ones:
it must be JSON or carry X-Baihe-Local: 1."""
from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app

OTHER_PORT = {"Origin": "http://127.0.0.1:8501"}


def _local(auth="off"):
    return TestClient(create_app(ApiSettings(auth_mode=auth)), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False)


PERMISSION_ROUTES = ("/api/glossary/dramas/1/from-novel", "/api/jobs/nope/cancel",
                     "/api/glossary/dramas/1/from-novel/apply")


def test_simple_posts_refused_on_permission_routes(isolated_db):
    c = _local()
    for path in PERMISSION_ROUTES:
        assert c.post(path, headers=OTHER_PORT).status_code == 403, path
        assert c.post(path, content=b"x", headers={**OTHER_PORT, "Content-Type": "text/plain"}
                      ).status_code == 403, path
        assert c.post(path, data={"a": "b"}, headers=OTHER_PORT).status_code == 403, path


def test_json_body_route_without_content_type_refused(isolated_db):
    c = _local()
    r = c.post("/api/glossary/dramas/1/from-novel/apply", content=b'{"terms": ["x"]}')
    assert r.status_code == 403


def test_json_or_header_passes(isolated_db):
    c = _local()
    assert c.post("/api/jobs/nope/cancel", headers={"X-Baihe-Local": "1"}).status_code == 404
    assert c.post("/api/jobs/nope/cancel", json={}).status_code == 404
    r = c.post("/api/glossary/dramas/1/from-novel/apply", json={"terms": ["x"]})
    assert r.status_code not in (401, 403)


def test_multipart_without_header_refused_before_parse(isolated_db, monkeypatch):
    read = []
    import starlette.formparsers as fp
    orig = fp.MultiPartParser.parse

    async def spy(self):
        read.append(1)
        return await orig(self)
    monkeypatch.setattr(fp.MultiPartParser, "parse", spy)
    c = _local()
    r = c.post("/api/jobs/nope/cancel", files={"f": ("a.txt", b"0" * 4096)})
    assert r.status_code == 403 and read == []


def test_auth_on_unchanged_for_non_local_only_routes(isolated_db):
    """With auth on the CSRF token already forces a preflight, so the gate
    stays scoped to local_only routes: a bodyless POST gets the usual
    auth answer (401 here, no session), not the gate's 403."""
    c = _local("on")
    assert c.post("/api/jobs/nope/cancel").status_code == 401


def test_every_mutating_route_refuses_a_headerless_contentless_post(isolated_db):
    """Walks every POST/PUT/PATCH route: with auth off, a request with no
    X-Baihe-Local header and no Content-Type (a no-cors Blob body, which
    some FastAPI versions parse as JSON) is refused with 403."""
    import re
    from api.auth import iter_route_declarations
    app = create_app(ApiSettings())
    c = TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                   raise_server_exceptions=False)
    checked = 0
    for _route, path, methods, _decl in iter_route_declarations(app):
        for method in sorted(set(methods or ()) & {"POST", "PUT", "PATCH"}):
            url = re.sub(r"\{[^}]+\}", "1", path)
            r = c.request(method, url, content=b"{}")
            assert r.status_code == 403, (method, path, r.status_code)
            checked += 1
    assert checked > 50
