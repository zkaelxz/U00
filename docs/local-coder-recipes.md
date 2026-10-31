# Local coder: extra rules, skip list and recipe cards

Linked from `AGENTS.md`. For a 64k-context model: read the card for your edit, not the whole file.

## Rules `CLAUDE.md` has that `AGENTS.md` only names
- Tests are mocked: no network, GPU, real models or real keys. Use the `isolated_db` fixture for anything touching the
  database and `pytest.importorskip` for optional libraries. Wait for job threads before asserting.
- Frontend: `cd frontend && npx tsc -b && npx vitest run <changed test files>`; lint one file with `npx oxlint <file>`
  (bare `npx oxlint` prints about 32 KB). UI rules: `docs/react-ui-guidelines.md`, `docs/frontend-design-system.md`.
- `db.create_drama` / `db.update_drama` put kwarg keys into SQL: a service must whitelist any key a client can choose
  (`drama_service` does). Services check drama and series ownership (`services/ownership_service.py`).
- `db.save_lines(drama_id, lines, fields=("en",))` writes only those fields. `fields=None` is a full sync: the list becomes the
  drama's lines (rows updated in place by id, missing rows deleted, a field written when it differs from the Line's `orig`).
  Build Lines with `core.line_from_row` so flags and speaker are not wiped.
- `sources.db` is a separate file: its new columns go in `_ADDED_COLUMNS` in `sources/store.py`, which the `library.db`
  column test does not cover.
- A new optional dependency is registered in `diagnostics.OPTIONAL_DEPENDENCIES` in the same change.
- Comments say WHY (a constraint, an invariant, a workaround), never WHAT, and never a Step, Slice, B- or PR id.
- Keep changes to what the task needs: no new files, settings or abstractions unless asked; report pre-existing problems
  in the summary, not the diff. A new setting needs a sentence on who asked for it. Removing a feature removes all of it
  (routes, their `docs/route-permissions.md` rows, service, domain code, settings, tests, docs, menu entry).
- Finish with a short summary: what changed, commands run with pass counts, what you are unsure about, follow-ups.

## Skip list (do not read unless the task is about them)
`docs/STATUS.md`,
`docs/engineering-standards.md`, `.test_durations` (2.2 MB),
`docs/archive/` (research notes, reports and session summaries included). `opencode.json` denies reads of these.

## Recipe cards

**Add a Settings row**
1. `lib/settings_schema.py`: one `_pref(...)` row (name, type, default, label, help, tab, section).
2. `api/schemas/system.py`: the field on `SettingsPreferences` and on `SettingsUpdateRequest`.
3. `frontend/src/types/settings.ts`: the same field.
4. `services/settings_service.py` (a choices lambda or helper, only if the row needs one) and the code that reads it via
   `settings_service.get(...)`.
5. `tests/test_settings_parity.py::test_preference_defaults` lists every default; add the row there. Editing only
   `lib/settings_schema.py` fails that test.
6. A schema or UI test for the row (`tests/test_settings_schema.py`, `frontend/src/pages/settings/*.test.ts`) and a line in the user guide (`docs/user-guide*.md`).
7. Run: `tests/test_settings_schema.py tests/test_settings_parity.py tests/test_settings_service.py tests/test_api_settings_schema.py`.

**Add an API route**
1. Router in `api/routers/<area>_routes.py`, thin: validate, call `services/<area>_service.py`, return a model from `api/schemas/`.
2. Declare exactly one of `require_permission(...)`, `public_route()`, `local_only()` on it.
3. `python tools/route_table.py --write` (never without `--write`: it prints 36 KB), then `git diff docs/route-permissions.md`.
4. A new router file starts with a one-line docstring.
5. Run `tests/test_api_permissions.py` and the area's API test. The undeclared-route failure there is a bare IndexError:
   it means a route has no declaration.

**Add a language-pack entry**
1. Add `{"source": ..., "en": ..., "category": ..., "note": ...}` to `entries` in `language_pack_data/<id>.json`
   (look at a neighbour; `note` is optional). Bump `version` only if the file says to.
2. Run `tests/test_language_packs.py tests/test_api_language_packs.py`. No code change is needed for an entry.

**Add a library.db column**
1. `db/__init__.py` `init_db`: `ALTER TABLE ... ADD COLUMN` (a change that cannot be an ADD COLUMN is a table rebuild: stop and ask).
2. Add `(table, column)` to `_INIT_DB_MIGRATED_COLUMNS` in `tests/test_db.py`; a guard test fails if you forget.
3. Read and write it with `db.<name>` helpers only. Run `tests/test_db.py`.

**Fix a flaky wall-clock test**
1. Do not lengthen a sleep or loosen a bound. Find what the test waits for.
2. Replace the sleep with `threading.Event`s: the code under test sets "started", the test releases it, and the test asserts
   on state ("the read returned while the probe was still blocked"), with `wait(10)` only as a hang guard.
3. When two events can land between two polls, assert `>=` the count, not `==`. Example: `tests/test_job_force_stop.py::TestGpuProbeOutsideTheLock`.

**Add an optional dependency**
1. Entry in `diagnostics.OPTIONAL_DEPENDENCIES` (key = the real pip name; the Install button runs `pip install <key>`).
2. Import it lazily and guard with a clear "not installed" error; tests use `pytest.importorskip`.
3. Add it to `requirements-optional.txt` like its neighbours. Run `tests/test_diagnostics_service.py`.

**Rename a UI label across specs and docs**
1. `git grep -n -F "<old text>" -- frontend/src frontend/e2e docs ':!docs/archive'` (add `-m 3` per file if long).
2. Change the component, then every `getByText`/`getByRole` in `frontend/e2e/*.spec.ts` and the `*.test.tsx` files, then the docs.
3. `cd frontend && npx tsc -b && npx vitest run <changed tests>`; run the one Playwright spec for that page (`docs/testing-and-ci.md`).
