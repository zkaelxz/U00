# sources/adapters/

One module per site. Each defines a `SourceAdapter` subclass decorated with
`@register` (from `sources/registry.py`) and is listed in `BUILTIN` in
`__init__.py`, which is what loads it.

## Start here
- The site's module, e.g. `syosetu.py`; the interface is `sources/base.py`.
- `docs/adding-source.md` (steps), `docs/content-sources.md` (terms and posture record).

## Rules
- Use `self.client` (the paced client from `sources/http.py`) for every request.
- Record terms and robots findings in the adapter's capabilities; a crawl delay
  becomes `host_min_interval`.
- Parse from saved fixtures in tests; no network.

## Tests
- `tests/test_sources_<site>.py` per adapter (e.g. `test_sources_syosetu.py`).

## Do not touch
- Other adapters when fixing one site.
