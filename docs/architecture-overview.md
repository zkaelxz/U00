# Architecture overview

One page on how Baihe Studio fits together. Each section links to the long doc.

## Layers, top to bottom

- `frontend/`: the React app. Vite builds it into `frontend/dist`.
- `api/`: FastAPI routers (`api/routers/*_routes.py`), Pydantic models (`api/schemas/`) and auth (`api/auth.py`).
- `services/`: logic with no UI. It raises the errors in `lib/errors.py`.
- Root modules: the domain code (transcription, translation, export and so on).
- `db/`: plain sqlite3, no ORM. Callers use `db.<name>`.
- `lib/`: small shared helpers with no domain knowledge. Every layer may use it; it imports none of them.

A layer calls only the ones below it. Detail: [`api-and-services.md`](api-and-services.md), [`database.md`](database.md).

## Where a request goes

The browser calls `/api/...`. A router checks the caller's permission, hands off to a service, and the service uses domain modules and `db`. Errors travel back as the typed errors from `lib/errors.py`.

## Background jobs

Long work (transcribe, translate, download) runs as a job. `background_jobs.py` is the front door; starting and running live in `jobs/`. The in-memory job table is the authority, with a best-effort copy in the `job_records` table. Detail: [`background-jobs.md`](background-jobs.md).

## Where data is stored

Titles, lines and settings are in `library.db`. Sources keep their own `sources.db`. Media, backups and the Trash sit in the data folder beside them. Detail: [`database.md`](database.md), [`runbook.md`](runbook.md).

## Three rules to know first

1. Match model results back to lines by explicit id, never by list position.
2. API keys go in headers, never in URLs, log lines or stored errors.
3. Every route declares exactly one of `require_permission(...)`, `public_route()` or `local_only()`.

The full rules are in `CLAUDE.md`; translation engines are in [`engine-backends.md`](engine-backends.md); tests and CI are in [`testing-and-ci.md`](testing-and-ci.md).
