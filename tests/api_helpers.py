"""Helpers shared by the API test files: error bodies, auth headers and a loopback client."""
from fastapi.testclient import TestClient

from api import auth as api_auth


def error_of(resp):
    """The `error` object of an error response, asserting the body carries nothing else."""
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


def session_headers(s):
    """Cookie plus CSRF header for a signed-in session dict from auth_service."""
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def loopback_client(app):
    """A client the server sees as a local request from 127.0.0.1."""
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)
