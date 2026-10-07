# Agent notes (OpenCode and other local-model agents)

OpenCode reads this file, not CLAUDE.md. Read `CLAUDE.md` and
`docs/small-model-checklist.md` before changing anything; they are the rules.

- Your context is small. Do not read many files. Run `python tools/repo_map.py`
  (or `--find "<word>"`, or `python tools/repo_map.py <module>`) on demand.
- Work in small tasks: one task, a handful of files. If it needs more than about
  four files, stop and split it.
- While iterating run only the matching file: `python -m pytest -q tests/test_<area>.py`.
  Run the full suite once at the end (command in CLAUDE.md).
- Never skip or weaken a test.

Rules from CLAUDE.md that bite most:
- Match LLM results back to lines by explicit id, never by list position.
- No secrets in logs, URLs or stored errors: keys go in headers; pass error text
  through `translate_engines.redact_secrets`.
- Every outbound HTTP call has `timeout=`.
- Every API route declares exactly one of `require_permission(...)`,
  `public_route()` or `local_only()`.
