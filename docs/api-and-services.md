# API and services: how a request flows

How the HTTP layer is put together and what a new endpoint must do. Rules
for the code itself live in the root `CLAUDE.md`; remote-access design and
the route table live in [`remote-access-decision.md`](remote-access-decision.md);
setting up household access lives in [`household-access.md`](household-access.md).
This page links the pieces; it does not repeat them.

## Layers

```
frontend/ (React)
  -> frontend/src/api/*            the only code that makes HTTP calls
  -> api/routers/*_routes.py       thin: parse, declare permission, call a service
  -> services/*_service.py         UI-free logic; raises services/service_errors.py errors
  -> root domain modules           core, translate_engines, scanlate, ...
  -> db.py                         plain sqlite3
```

- A router function validates input through a Pydantic model, calls one
  service function and returns its result under a `response_model`.
  Business rules belong in the service so the CLI (`cli.py`) and the API
  share them.
- A service never imports `fastapi`. It takes plain values (and a
  `principal` where visibility matters) and raises a `ServiceError`
  subclass for any failure it expects.
- `api/server.py:create_app` includes every router. The built React app is
  mounted last (`api/static_frontend.py:install_frontend`) so `/api` routes
  match first; an unknown `/api/...` stays a JSON 404.

## Request flow

`create_app(settings, frontend_dist, listener)` in `api/server.py` builds
the app. It opens no database and imports no optional package, so it is
safe at import time and in tests. Middleware, outermost first:

| Layer | When | What it does |
|---|---|---|
| `HouseholdGate` | household listener only | 400 unless `Host` is `BAIHE_PUBLIC_URL`'s; adds security headers (HSTS only when the proxy says https) |
| `CORSMiddleware` | `BAIHE_API_ENV=development` with origins set | GET only, no credentials; production sends no CORS headers |
| `LocalOnlyCrossSiteGate` | always | refuses a simple (no-preflight) POST/PUT/PATCH before the body is read: on `local_only()` routes, and on every `/api` write when auth is off |
| `EarlyAuthGate` | auth on | before the body is read, refuses non-public `/api` requests with no session (401), a missing CSRF token (403), or a `local_only()` route from a non-loopback caller (403) |
| `LoopbackOnlyGate` | auth off | 403 for anything that is not a direct loopback request (non-loopback peer, Host or Origin, or any proxy header) |
| `ActingPrincipalMiddleware` | auth on, innermost | binds a per-request holder so `background_jobs` can record which user started a job |

The gates are pure ASGI middlewares so they run before Starlette parses or
spools a body. They repeat only the cheap checks; the per-route dependency
(below) stays the authority for permissions.

Then the route's dependency runs (`api/auth.py`), then the handler:

1. `require_permission(p)`: with auth off the caller is the local owner
   (`local_owner_principal`, every permission). With auth on,
   `_authenticate` resolves the session cookie via
   `auth_service.resolve_session` (permissions are re-read from the
   database on every request), checks CSRF on unsafe methods, then checks
   the permission and the path-parameter ownership.
2. The handler calls the service, usually passing
   `request.state.principal` where the service filters or checks
   visibility.
3. Failures become the JSON error body described next.

`GET /api/events` is the one streaming endpoint; see
[Server-sent events](#server-sent-events).

## Errors

`api/error_handlers.py:install_error_handlers` gives every non-2xx reply
one body: `{"error": {"code", "message", "details"?}}`. `details` is
omitted when `None`. A `ServiceError` carries `code`, `message` and
optional `details`; the handler picks the status from the first matching
class in `_STATUS_BY_ERROR`:

| Service error | HTTP | `code` |
|---|---|---|
| `InvalidInputError` | 422 | `validation_error` |
| `NotFoundError` | 404 | `not_found` |
| `UnsupportedOperationError` | 400 | `unsupported_operation` |
| `UnauthenticatedError` | 401 | `unauthenticated` |
| `ForbiddenError` | 403 | `forbidden` (`CsrfFailedError` in `api/auth.py` is a subclass with code `csrf_failed`) |
| `ConflictError` | 409 | `conflict` |
| `RateLimitedError` | 429 | `rate_limited` |
| `DependencyUnavailableError` | 503 | `dependency_unavailable` |
| other `ServiceError` | 500 | `application_error` |

Other mappings in the same module:

- FastAPI request validation: 422 `validation_error`, with `details` a list
  of `{loc, msg}` only, never the rejected value.
- `SourcesDatabaseBusy`: 503 `sources_db_busy`.
- Starlette `HTTPException`: 404 `not_found`, 405 `unsupported_operation`,
  413 `too_large`, anything else `application_error`. This is how routers
  signal an over-cap body (`StarletteHTTPException(413, ...)`).
- Anything unexpected: 500 `internal_error` with a fixed message. The
  traceback goes to the app log (`applog`), never to the client.

Known messages pass through `translate_engines.redact_secrets` before they
are sent. `details` is not redacted, so a service must not put a secret,
path or URL in it.

## Listeners

`python -m api` (`api/__main__.py`) serves up to two FastAPI apps in one
process, so both share the in-memory job list. A third, separate server is
the extension bridge.

| Listener | Port | Auth | Reached by |
|---|---|---|---|
| Admin | `BAIHE_API_PORT`, 8600 | off by default (everyone is the local owner); `LoopbackOnlyGate` | the PC's own window, `start.bat` |
| Household | `BAIHE_API_HOUSEHOLD_PORT` (unset = off; 8610 suggested) | always on; `create_app(listener="household")` | Caddy on the PC, which terminates HTTPS |
| Extension bridge | 8756 (`page_server.DEFAULT_PORT`) | one shared token in a header | the browser extension, loopback only |

- On the household listener nothing counts as "the PC": `is_local_request`
  is always False and `local_only()` always refuses.
  `listener_principal` also strips every `admin.*` write permission from
  an admin there, keeping `ADMIN_VIEW_PERMISSIONS`.
- The admin listener's lifespan owns the background services; the household
  app's does nothing.
- The bridge (`page_server.py`) is stdlib `http.server`, not FastAPI, and
  none of the gates or declarations on this page apply to it. It has its own
  token check and body caps (`MAX_BODY_BYTES`, `MAX_IMAGE_BYTES`). The API
  only controls it, through the `local_only()` routes in
  `api/routers/extension_routes.py`. Never route it through Caddy.
- Startup rules (bind safety, which prerequisites skip only the household
  listener) are in the `api/api_config.py` docstring and the "D5" section of
  `remote-access-decision.md`. Caddy setup is in `household-access.md`.

## Route declarations

Every route must carry exactly one marker in `dependencies=[...]`, all from
`api/auth.py`:

| Marker | Meaning | Allowed on |
|---|---|---|
| `require_permission("x.y")` | a signed-in user holding permission `x.y`; the name must exist in `auth_service.PERMISSIONS` or `create_app` fails at import | any route |
| `public_route()` | no login | health-style routes (`/api/health`, `/api/meta`), the sign-in start and callback, `/api/auth/me`, and the frontend catch-all |
| `local_only()` | the owner at the PC: a direct loopback request. No session needed. Always refused on the household listener | routes that touch the PC (keys, files, installs, deletes, settings) |
| `authenticated()` | any signed-in user, no permission | only the caller's own sessions under `/api/auth/` (logout, list, revoke) |

With auth off, every marker passes for a loopback caller; that is why the
declaration still matters: the household listener and auth-on mode run the
same route table.

Related helpers:

- Paid LLM work declares `jobs.start`, and the handler also calls
  `require_engines_allowed(request, engine_names...)`, so a caller without
  `engines.paid` can use only `translate_engines.FREE_ENGINES`.
  `require_paid_engines` and `holds_paid_engines` cover cloud calls that are
  not translate engines.
- `is_local_request(request)` is for a handler that must behave
  differently at the PC. Prefer `local_only()` where a route is PC-only
  outright.

### How the tests enforce it

`tests/test_api_permissions.py` (the walker is
`api.auth.iter_route_declarations`):

- fails any route with zero or more than one declaration, in auth off and on
  modes, and on the household app;
- checks the household app has the same route table as the auth-on admin
  app;
- checks `authenticated()` appears only on own-session routes under
  `/api/auth/`;
- `test_doc_route_table_matches_the_app` parses the table at the end of
  `docs/remote-access-decision.md` (`| Declaration | Routes | Paths |`) and
  fails if any row's route count or any listed `METHOD /path` differs from
  what the app declares. A new route therefore needs a row edit in that
  doc, in the same change.

The same file also covers CSRF, local-only, loopback and bypass cases.

## Ownership of path parameters

`api.auth.OWNED_PATH_PARAMS = {"drama_id": "drama", "series_id": "series"}`.
After the permission check, `require_permission` calls
`require_path_visible`, which for every such path parameter calls
`ownership_service.require_visible` on safe methods and `require_editable`
on the rest. So a route named `/dramas/{drama_id}/...` is covered by default.

- A denied item is a 404, never a 403, so a private item's existence is not
  revealed. The one 403 is an admin on the household listener changing
  something only a member-owner could.
- `local_only()` and `public_route()` do not run this check; `local_only()`
  is the owner at the PC, who sees everything.
- Items named in a body or looked up by a service (a job id, a Live session,
  a drama id in a JSON body) are not covered by the path guard. The service
  checks with `ownership_service` (`can_see_drama`, `require_job_changeable`,
  `filter_visible_drama_ids`), given the principal from
  `request.state.principal`. Never pass `principal=None` from a router: None
  means "auth off, everything visible".
- `tests/test_api_ownership.py` fails if a route names an item by some other
  path parameter without an entry in its `OWNERSHIP_EXEMPT_PARAMS` (with a
  reason) or `JOB_ROUTES`, and if an owned route lacks a guarded declaration
  (`require_permission` or `local_only`).

Rules and the sharing model are in `services/ownership_service.py`'s
docstring and "Ownership and sharing" in `remote-access-decision.md`.

## Schemas

`api/schemas/` is the API contract: Pydantic models, one module per domain
(`common`, `system`, `review`, `characters`, `translate`, `library`,
`sources`, `reader`, `voice`, `transcribe`). `api/schemas/__init__.py`
re-exports every name, so a router writes `from api.schemas import X`.

- A shape used by several domains lives in `common` (the error envelope,
  `API_VERSION`, `TranslateEngine`, `LibraryPreset`); the other modules
  import only from `common`.
- `tests/test_schemas_package.py` fails if one name is defined in two
  modules.
- Adding a field is compatible; renaming or removing one is not
  (bump `API_VERSION` in `common`).
- Models are not database rows. Leave out internal columns, or reduce them
  to a boolean.

## Response rules

- No secrets: key and token fields are `*_configured: bool` (for example
  `TranslateEngine.key_configured`). Write endpoints are write-only; the
  one place a token comes back (`POST /api/extension/token`, with
  `confirm=true`) sends `Cache-Control: no-store`.
- No filesystem paths, stored filenames or fetched URLs. A path becomes a
  boolean (`has_audio`) or an opaque id (reference-clip candidates are
  addressed by one); downloads use a generic name, never the stored one.
- API keys go in request headers, never in URLs, logs or stored errors
  (`translate_engines.redact_secrets` on any error text).
- A denied or missing item answers 404 with a generic message.
- Tests assert these per area (for example `tests/test_api_artifacts.py`,
  `tests/test_api_novel_files.py`, `tests/test_api_sources_tools.py`).

## CSRF and `X-Baihe-Local`

Two different headers for two different threats:

- **`X-CSRF-Token`** (`CSRF_HEADER`): auth on only. Sign-in sets a readable
  `__Host-baihe_csrf` cookie next to the HttpOnly `__Host-baihe_session`
  cookie (plain `baihe_*` names only for plain-http loopback dev).
  Every POST/PUT/PATCH/DELETE must echo it as the header; the server checks
  the header against a hash stored with the session, not against the cookie.
  A failure is 403 `csrf_failed`, so the client can tell it from a PC-only
  refusal. With auth off there is no token.
- **`X-Baihe-Local: 1`** (`LOCAL_HEADER`): a page on another loopback port
  passes the Origin check (it ignores the port) and can send a "simple" POST
  with no CORS preflight. So a POST/PUT/PATCH to a `local_only()` route must
  be `application/json` or carry this header (`_cross_site_safe`); a custom
  header forces a preflight that CORS refuses. Multipart is a simple type,
  so uploads need the header (the React upload helper adds it). With auth
  off the same rule covers every `/api` write, since there is no CSRF
  token. GET and DELETE are unaffected.
- Session cookie names, flags and the session lifetimes:
  `api/auth.py` and `api/api_config.py`.

## Uploads and body caps

An over-cap body is refused with 413 `too_large` before it is spooled to
disk. The pattern, where a route takes a raw body or multipart:

- Cap by Content-Length first, then count the stream, so a chunked body is
  cut off too: `api/routers/bug_report_routes.py` (`capped`, `BodyTooLarge`)
  is the helper; `backup_routes.py`, `drama_routes.py` (cover upload),
  `scanlate_routes.py` and `sources_tools_routes.py` use it.
- Media upload size: `BAIHE_MAX_UPLOAD_MB`, read by
  `media_upload_service.max_upload_bytes`; the service also whitelists the
  file extension, discards the client's filename and writes atomically.
- Multipart routes add a small overhead allowance to the cap
  (`_MULTIPART_OVERHEAD`, 1 MB) and limit the file and field counts.
- Upload routes under `/api/media` are `local_only()`, so the early gates
  refuse a remote caller before the body is read. On a household listener
  with auth on, `EarlyAuthGate` does the same for any anonymous caller.
- Pasted-text bodies have model limits (a 422) on top of the byte cap.
- A route that streams back media supports Range requests and HEAD
  (`/api/media/dramas/{drama_id}/audio`, `/video`), under `media.stream`.

## Server-sent events

`GET /api/events?topics=jobs,notifications,live`
(`api/routers/events_routes.py`, `services/event_stream_service.py`),
declared `library.read`. Events: `ready`, `job`, `job_gone`,
`notifications`, `live`, `resync`, and `ping` (about every 15 s, to keep
Caddy open and let the client detect a stall). Each payload is re-read
through the matching GET route's own service call with the stream's
principal; with auth on the session is re-checked every
`AUTH_RECHECK_SECONDS`, and a sign-out or revoke ends the stream promptly. A
per-user and total cap answers 429; the client falls back to polling. The
stream has a maximum length (`MAX_STREAM_SECONDS`), after which the client
reconnects. Everything else is plain request/response; long work runs as a
background job (`background_jobs`), and a route returns its `job_id` for
the client to follow.

## The React client

`frontend/src/api/` is the only place that talks HTTP.

- `client.ts` owns `fetch`: `getJson`, `postJson`, `deleteJson`,
  `postMultipart`, `fetchBody` (text/binary responses). It adds
  `X-CSRF-Token` to mutations, `X-Baihe-Local` where needed, turns every
  error body into an `ApiError` (`status`, `code`, `details`), and a 401
  notifies `onUnauthorized` so the app swaps in the sign-in page.
- One module per area (`library.ts`, `translate.ts`, `review.ts`, ...)
  exports typed functions; pages and components call those and never build
  URLs or parse errors. `pcOnly.ts` marks PC-only calls: a 403 there flips
  the page to a remote-view state.
- `eventStream.ts` is the only `EventSource` user.
- `noRawRequests.test.ts` fails a raw `fetch`, `XMLHttpRequest`,
  `sendBeacon`, `WebSocket`, `EventSource` (outside `eventStream.ts`),
  `<form action>`, `credentials:` override, or a token in web storage
  anywhere else in `frontend/src`. A raw call would skip the CSRF header and
  the 401 handling.
- Types are **hand-written** in `frontend/src/api/types.ts` (and in the
  per-area modules) and mirror `api/schemas/`. Nothing generates them from
  `/api/openapi.json` and no test compares the two, so a schema change
  needs the TypeScript edited in the same change. Where a limit exists on
  both sides the TS comment says so (for example
  `MAX_TRANSLATE_TEXT_CHARS` mirrors a schema constant).
  `npx tsc --noEmit` and the per-module `*.test.ts` files catch a client
  that drifts from its own types, not from the API.

## Adding an endpoint

1. **Service function** in `services/<area>_service.py`: UI-free, takes
   plain values (and `principal` if it reads or writes items), raises
   `service_errors` classes, whitelists any kwargs that reach
   `db.create_drama`/`db.update_drama`, and checks ownership of any item it
   was handed by id rather than by path. A background job writes only the
   fields it owns (`db.save_lines(..., fields=(...))`). Any outbound HTTP has
   `timeout=`.
2. **Schema** in the matching `api/schemas/<domain>.py`: request and response
   models, added to the module's `__all__`. Booleans, not secrets, paths or
   URLs. Put it in `common` only if several domains use it.
3. **Router** function in `api/routers/<area>_routes.py` (a new file needs
   `include_router` in `api/server.py` and a line in `FILE_ORGANIZATION.md`):
   `response_model`, `responses={...: {"model": ErrorResponse}}`, **exactly
   one** declaration. Pick the permission by what the route does: reads
   `library.read`/`lines.read`, edits `lines.edit`, long work `jobs.start`
   (plus `require_engines_allowed` if it can call a paid engine), streaming
   `media.stream`; use `local_only()` for anything touching the PC; reserve
   `public_route()` and `authenticated()` for the cases above. Use
   `{drama_id}`/`{series_id}` in the path for an owned item.
4. **Route-table row** in `docs/remote-access-decision.md`: add
   `METHOD /path` to the declaration's row and bump its count.
5. **Ownership**: an item in the path is guarded automatically. An item in a
   body, or a job id, is checked in the service; list new path parameters in
   `OWNERSHIP_EXEMPT_PARAMS` (with a reason) in `tests/test_api_ownership.py`
   if they are not items.
6. **Tests**: a service test (use `isolated_db`; no network or real keys),
   an API test with `TestClient` covering the happy path, a 404/422 case and,
   for auth-gated routes, 401/403 and a non-owner's 404. A route with a body
   cap needs an over-cap test. Run `tests/test_api_permissions.py` and
   `tests/test_api_ownership.py`. Where the CLI has an equivalent, keep it
   behaving the same.
7. **Frontend client**: a typed function in the right `frontend/src/api/*.ts`
   (through `client.ts`), its types, and a `*.test.ts` beside it. A
   PC-only route goes through `pcOnly.ts`.
8. If the change adds an optional dependency, register it in
   `diagnostics.OPTIONAL_DEPENDENCIES`.
