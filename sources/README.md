# sources/

Content sources: site adapters, the paced HTTP client, the access ladder,
and their own store (`sources.db`).

## Start here
- `registry.py`: `register`, `get_adapter`, `find_for_url`, `multi_search`.
- `front_door.py`: the "paste any URL" entry point.
- `base.py`: `SourceAdapter`, the interface every adapter implements.
- `http.py`: the one paced client (pacing, retries, challenge hand-off, timeouts).
- `store.py`: `sources.db`; its own `_ADDED_COLUMNS` migration list.
- `adapters/`: one module per site (see its README). How to add one: `docs/adding-source.md`.

## Rules
- Fetch through `http.py`'s client, never raw `requests`. Every call has a timeout;
  `tests/test_static_analysis.py` checks `http.py` and the URL-import modules.
- A schema change to `sources.db` goes in `store.py` `_ADDED_COLUMNS`
  (the `tests/test_db.py` guard does not cover it).
- A challenge page is never retried or automated: it raises `ChallengeDetected`.
- Never read browser cookies or profiles; responses expose no fetched URLs.

## Tests
- `tests/test_sources_core.py`, `tests/test_sources_http_limits.py`, `tests/test_sources_preflight.py`
- `tests/test_sources_store_locking.py`, `tests/test_sources_redact_at_rest.py`,
  `tests/test_sources_credential_audit.py`
- `tests/test_api_sources_<area>.py` for the routes; helpers in `tests/sources_helpers.py`

## Do not touch
- `http.py`, `adaptive.py`, `ai_extract.py` are on the size allowlist: shrink only.
