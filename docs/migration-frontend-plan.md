# Frontend phase plan (React) -- from the migration-architect, 2026-09-28

Source: read-only architect report; facts checked against the repo at `baihe-subtitler`. Roadmap was not readable by the agent.
Backend queue status lives in `docs/migration-handoff.md`. Job statuses (from `background_jobs`): queued, running, done, error, cancelled.

## Where the frontend is today
Read-only Library preview: `frontend/src/App.tsx` (status badge, `LibraryList`, `DramaDetailPanel`, no router/state lib/writes),
`frontend/src/api/client.ts` (health, meta, listDramas, getDrama; GET-only `getJson`; `ApiError` already parses `{error:{code,message,details}}`),
7 hand-written types, React 19 + vite + vitest + playwright, `e2e/library.spec.ts` with `e2e/serve_seeded_api.py`.
The API has ~106 routes over 25 routers; the UI uses 4. CORS allows only GET (`api/server.py`); keep the Vite proxy (`vite.config.ts`) as the only supported dev path.

## Foundations (slice F, must land first, keep minimal)
- Hand-written per-area API modules (`frontend/src/api/<area>.ts`, `types/<area>.ts`), no OpenAPI codegen yet; add `postJson`, `del`, multipart helper, artifact download-link helper on the existing `ApiError`.
- `useJob(jobId)` polling `GET /api/jobs/{id}` until terminal, with `onDone` for refetches (no SSE exists).
- One `ErrorBanner` fed by `ApiError` (404/409/422/400/503 from `services/service_errors.py`).
- Tiny hash router (or react-router): `/library`, `/drama/:id/:stage`, `/settings`, `/diagnostics`; remount by drama id.
- CSS variables/tokens in `index.css`, no UI library. No auth UI (loopback, same-origin by design).
- Shared files (`App.tsx`, `client.ts`, `types.ts`) are edited ONLY by the lead or by F; later slices add their own `api/<area>.ts`, `types/<area>.ts`, `pages/*` or `stages/*`.

## Slices after F (independent unless noted)
| Slice | Scope | Endpoints | Exit condition |
|---|---|---|---|
| A | Library complete | stats, recent, series, search, history, presets, create/delete drama | create, filter, typed-confirm delete in Playwright |
| B | Diagnostics | diagnostics, jobs (list + cancel) | dependency panel + job list with cancel |
| C | Settings | GET/POST settings | toggles round-trip; key writes stay out (D5, slice 24) |
| I | Standalone Translate | translate engines/history/translate | text in, text out |
| D | Workspace shell + Source stage (shell first, then stages parallel) | media, novel, source, transcribe, diarization, metadata | upload, transcribe, poll job, lines refetch |
| E | Translate stage | translate-run config/estimate/run, glossary, characters | estimate, run, done, refetch |
| F2 | Review | review lines/find-replace, line edit, notes, review-jobs, records | edit a line; refetch after each job |
| G | Export | readiness, subtitle, ass, flags, epub, artifacts | downloads and flags only on explicit click |
| H | Dub | config, pacing, run, artifacts, narration | run and download (Step 95 BGM pending) |
A, B, C, I are file-disjoint (parallel after F). D-H each own one `stages/*.tsx` and need D's `WorkspaceShell.tsx` first (D owns it).

## Backend gaps the UI will hit
Line structure ops (add/delete/merge/split/re-segment/version restore; slice 45, Opus); bulk translate/Reflect (slice 41, Opus);
media playback (no audio/video streaming or Range endpoint; artifacts are download-only) -- candidate slice; no discover/live/scanlate APIs (live needs SSE);
no exposed stage index; E0-deferred destructive library actions; API-key writes (slice 24); no SSE/job push;
serving `frontend/dist` from FastAPI and the launcher story are not built.

## Streamlit retirement
Gate: `docs/migration-review.md` (Class S invariants need service-level tests, Class U need React/e2e tests, plus real-device checks). Per tab: every action reachable in React, e2e covers its Class U rows,
user has run real TTS/ffmpeg/GPU/paid-key paths, CLI parity holds. Order: Diagnostics, Library, Settings (after slice 24), Translate, Workspace stage by stage; Sources/Discover/Live/Scanlate wait for APIs.
Risks: `tabs/workspace_tab.py` hidden behavior; `page_server.py` (browser-extension bridge) depends on Streamlit; no prebuilt-dist story yet.

## Checks
Automatable with Playwright + seeded API: routing, drama-switch isolation, job polling with a fake job, error banners, destructive confirms.
Frontend CI is not gating (Actions minutes exhausted): run `npm test`, `npm run build`, `npm run lint`, `npm run e2e` locally.
Structural redesign rule (CLAUDE.md): capture before/after screenshots with Playwright/Chromium for whole-layout changes.
Unknown: Vite proxy behaviour with multipart uploads (verify in slice D); whether reader HTML can be iframed.
