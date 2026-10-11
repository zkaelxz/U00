# Package README template

Copy this into `<package>/README.md` when a package lands (for example the
`db/`, reading/scanlate and video packages after their splits). Aim for
under 2 KB: a small-context model reads it before any code. Verify every
fact against the code, not older docs, and name only test files that exist.

```markdown
# <path>/

One or two sentences: what this package does and which layer it sits in
(frontend -> api/routers -> services -> root domain modules -> db.py; `lib/` sits below them all).

## Start here
- `<module>.py`: the entry point most tasks begin from, and what it holds.
- `python tools/repo_map.py <path>` lists the modules; `--find <word>` searches them.

## Rules
- The CLAUDE.md rules that apply here, as short imperative lines
  (permissions per route, id-keyed LLM matching, redaction, timeouts,
  jobs write only their own fields, schema migration rule).
- Which of them a test enforces, and which only review catches.

## Tests
- `tests/test_<area>.py`: what it covers.
- The guard tests a change here can trip (`tests/test_static_analysis.py`, ...).

## Do not touch
- Files or areas a task here should leave alone, and why.
```

Keep it current: when a rule, entry point or test file named in a README
changes, update the README in the same PR.
