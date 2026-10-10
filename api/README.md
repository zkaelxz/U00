# api/

FastAPI app: HTTP in, service call, JSON out. No business logic here.

## Start here
- `server.py`: `create_app()`; every router is registered with `app.include_router(...)`.
- `auth.py`: `require_permission`, `authenticated`, `public_route`, `local_only`, CSRF, local-only gate.
- `error_handlers.py`: maps `lib/errors.py` exceptions to one JSON error shape.
- `routers/` (see its README), `schemas/` (see its README), `api/*_schemas.py` (older schema modules).
- `__main__.py`: `python -m api`; settings in `api_config.py` (`BAIHE_API_*`).

## Rules
- Routers call `services/`, never `db` (enforced). Services never import `api` (enforced).
- Every route declares exactly one permission (see `routers/README.md`).
- Responses never carry secrets, filesystem paths or fetched URLs (booleans only).
- Error text passes through redaction before it is returned (`error_handlers._redact`).
- Every outbound HTTP call under `api/` has `timeout=` (enforced).

## Tests
- `tests/test_api_permissions.py` (route declarations + `docs/route-permissions.md` table)
- `tests/test_static_analysis.py` (`TestLayering`, timeouts, module size)
- `tests/test_api_<area>.py` per feature, e.g. `test_api_dramas.py`, `test_api_lines.py`

## Do not touch
- `auth.py`, `api_config.py`, `__main__.py` unless the task is auth or remote access.
- Do not add a router without adding it to `server.py` and the route table.
