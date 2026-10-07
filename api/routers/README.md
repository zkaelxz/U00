# api/routers/

One `<area>_routes.py` per feature area. Each is thin: parse the request
(a model from `api/schemas/` or `api/*_schemas.py`), call one service function,
return a response model.

## Start here
- Find the route: `git grep -n '"/the/path' api/routers` or
  `python tools/repo_map.py --find <path word>` (decorators show the path).
- The frontend caller: `python tools/repo_map.py frontend/src/api/<area>` lists each `/api/...` path.

## Rules
- Every route has exactly one of `require_permission("...")`, `authenticated()`,
  `public_route()` or `local_only()` in `dependencies=[...]`.
- A new or moved route needs a row in `docs/route-permissions.md`.
- A new router module is registered in `api/server.py` and listed in `FILE_ORGANIZATION.md`.
- No `import db` here (enforced); ownership and key whitelists live in services.
- Raise nothing HTTP-specific for domain errors: let the service raise
  `services/service_errors.py` errors; `api/error_handlers.py` maps them.

## Tests
- `tests/test_api_permissions.py` (one declaration per route; table matches the app)
- `tests/test_static_analysis.py::TestLayering::test_routers_do_not_import_db`
- `tests/test_api_<area>.py` for the router you change

## Do not touch
- Other routers' permissions while doing an unrelated task.
