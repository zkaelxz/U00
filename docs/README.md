# Documentation index

A map of `docs/`. Repo rules are in the root `CLAUDE.md` (and `AGENTS.md` for local models); the module map is `python tools/repo_map.py` (built from each module's docstring). The numbered roadmap, `docs/baihe-roadmap.md`, is not in this repo: it lives on the planning branch `claude/baihe-subtitle-planning-95qyvq`.

## Start here

- **`STATUS.md`**: what works, known limits, what is next.
- **`user-guide.md`**: start here and the contents of the guide, which continues in `user-guide-setup.md` (install, dev setup, ports), `user-guide-subtitles.md`, `user-guide-library.md` and `user-guide-operations.md` (troubleshooting, Diagnostics, Benchmark Lab).
- **`engineering-standards.md`**: precedence, scope, review policy, verification, git and safety.
- **`testing-and-ci.md`**: test commands, the guard set, the CI jobs and which a change runs.
- **`runbook.md`**: maintainer steps (installer lock, restore, certificate, benchmark, break-glass admin).

## How the code fits together

- **`architecture-overview.md`**: one page on the layers, where a request goes, where jobs and data live, and the three rules to know first.
- **`api-and-services.md`**: request flow, errors, route declarations, ownership, schemas, an adding-an-endpoint checklist.
- **`database.md`**: the `db` package and `sources.db`, migrations and their guard test, a table map, rules learned from bugs, backups, what sits on disk beside the database.
- **`background-jobs.md`**: the `jobs/` modules, thread and process jobs, the GPU guard, cancel, holds, the Jobs API, an add-a-job checklist.
- **`engine-backends.md`**: translation engines, transcription and dubbing engines, rules learned from bugs, an adding-an-engine checklist.
- **`asr-experiments.md`**: transcription options and a dated benchmark snapshot.
- **`technical-notes.md`**: an engineering changelog of real bugs and their fixes.
- **`windows-installer-design.md`** and **`RELEASE.md`**: the installer and boot service, and building the installer.

## Frontend

- **`frontend-design-system.md`**: layout, routing, tokens and themes, shared components, phone rules, testing, an add-a-screen checklist.
- **`react-ui-guidelines.md`**: concise-UI rules for the React app.
- **`design/`**: `ui-refresh-spec.md` (the original 2026-09 UI refresh spec, history), `navigation-proposal.md` (left menu, Ctrl+K, Jobs page, with its build status), `screens/after/` (screenshots taken after the refresh).

## Remote access

- **`remote-access-decision.md`**: the remote-access decision and the map of its as-built detail: `remote-access-permissions-core.md`, `remote-access-permissions-sources-ops.md` and `remote-access-permissions-accounts.md` (permissions), `remote-access-listeners.md` (the two listeners), `remote-access-ownership.md` (ownership and sharing).
- **`threat-model.md`**: what the app defends against and what it leaves out.
- **`route-permissions.md`**: the route table `tests/test_api_permissions.py` enforces; regenerate with `python tools/route_table.py --write`.
- **`household-access.md`**: step-by-step setup through Caddy.

## Sources

- **`adding-source.md`**: writing a `sources/adapters/*.py` adapter, with the pre-coding site checklist.
- **`content-sources.md`**: every source and what was checked. **`known-working-sources.md`**: the status board (data in `source-status.json`, built by `scripts/source_status.py`).
- **`sources-credential-audit.md`**: how adapters handle credentials and cookies. **`browser-extension.md`**: the browser extension and its bridge.

## Working with a small or local model

- **`small-model-checklist.md`**, **`local-model-card.md`**, **`local-coder-recipes.md`**, **`local-agent-backlog.md`** (open follow-ups sorted by who can do them), **`lead-troubleshooting.md`** (checks for lead and fix sessions).

## Plans, research and history

- **`specs/`**: `gpu-worker-plan.md` (deferred), `step-141-pc-shell-and-connect.md` (spec only).
- **`research/`**: dated surveys (`oss-gap-report.md`, `timing-constants-inventory.md`, `twmanga-vetting.md`, `vtuber-terminology-zh-ja.md`). Each states the commit it was checked against; the code wins over them.
- **`archive/`**: historical records, not sources of truth: `research/` (the syntheses in `reports/` and the raw `research_notes/` behind them), the migration review, handoff and phase log, the roadmap master tracker, the superseded remote-access design, installer research and cut design history, the Step 19 click-through audit (`ux-click-through-audit.md`, of the removed Streamlit app), the unbuilt Jellyfin/Plex metadata design, the full ASR experiment write-up, the built Scanlate, Discover/Sources/Live and workspace-shell specs, and `file-organization-full.md` (the old long per-file descriptions).
