# api/schemas/

The API contract: Pydantic request/response models, one module per domain
(`characters`, `library`, `reader`, `review`, `sources`, `system`,
`subtitle_import`, `transcribe`, `translate`, `voice`). `common.py` holds shapes several domains
share; the other modules import only from `common`. `__init__.py` re-exports
everything, so `from api.schemas import X` works.

Older schema modules sit beside this package as `api/*_schemas.py` and are
imported directly by their routers. New models go here, not there.

## Start here
- `python tools/repo_map.py api/schemas/<domain>`: classes with their `fields:`.
- `python tools/repo_map.py --find <field_name>` finds the model that carries a field.

## Rules
- Models are not database rows: no stored filenames, paths, secrets or fetched URLs
  (reduce them to booleans).
- Adding a field is compatible; renaming or removing one is not.
- The React types in `frontend/src/types/<area>.ts` mirror these; change both.

## Tests
- `tests/test_schemas_package.py` (package layout, re-exports)
- `tests/test_frontend_limit_parity.py` (frontend limits match `Field` bounds)
- `tests/test_api_<area>.py` for the routes using the model

## Do not touch
- Field names a released frontend reads, unless the task is that change.
