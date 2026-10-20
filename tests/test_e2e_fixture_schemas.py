"""E2E mock bodies must still fit the API response schemas they stand in for.

Playwright mocks are hand-written, so a renamed or retyped response field leaves a green spec
testing a shape the API no longer returns. This loads the shared auth mocks (the app shell's
bodies, used by every mocked spec) straight from TypeScript and validates each against the
OpenAPI response schema of its route. It can be retired once the mocks are generated from the
OpenAPI spec.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from api.api_config import ApiSettings
from api.server import create_app

E2E = Path(__file__).resolve().parent.parent / "frontend" / "e2e"

CHECKED_GET_PATHS = (
    "/api/health", "/api/meta", "/api/library/dramas", "/api/library/stats", "/api/library/recent",
    "/api/diagnostics/remote-health", "/api/system/update",
)

_DUMP = """
const m = await import(process.argv[1])
process.stdout.write(JSON.stringify({ get: m.GET_FIXTURES, me: m.ME }))
"""


@pytest.fixture(scope="module")
def mocks():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    # Node strips the TypeScript types itself; authMocks.ts only imports types, which are erased.
    run = subprocess.run([node, "--input-type=module", "-e", _DUMP, (E2E / "authMocks.ts").as_uri()],
                         capture_output=True, text=True, timeout=60)
    if run.returncode != 0:
        pytest.skip(f"node cannot load the TypeScript mocks: {run.stderr[-300:]}")
    return json.loads(run.stdout)


@pytest.fixture(scope="module")
def spec():
    return create_app(ApiSettings()).openapi()


def _check(spec, path, body):
    # A route that loses its response model has no schema here, so the lookup fails loudly
    # instead of letting the mock pass unchecked.
    schema = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    errors = list(Draft202012Validator({**schema, "components": spec["components"]}).iter_errors(body))
    assert not errors, [f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors]


@pytest.mark.parametrize("path", CHECKED_GET_PATHS)
def test_get_mock_fits_response_schema(mocks, spec, path):
    _check(spec, path, mocks["get"][path])


@pytest.mark.parametrize("name", ("authOff", "signedOut", "notConfigured", "signedIn"))
def test_auth_me_mock_fits_response_schema(mocks, spec, name):
    _check(spec, "/api/auth/me", mocks["me"][name])
