# Frontend map and design system

How the React app in `frontend/` is built and where each part lives. This is the map; the rules and the reasoning live elsewhere and are not repeated here:

- **`docs/react-ui-guidelines.md`**: the 22 numbered page rules (primary action first, collapse rare options, no raw API values, and so on). Code and reviews cite them by number.
- **`docs/design/ui-refresh-spec.md`**: the visual direction, the full token table and per-page redesign notes.

Everything below was checked against the code on `baihe-subtitler`. Names are files and components, not line numbers.

## 1. Layout of `frontend/src/`

| Path | What lives there |
|---|---|
| `main.tsx`, `App.tsx` | Entry and shell. `main.tsx` applies the saved theme, installs `bootFallback.ts` and `report/capture.ts`, then renders `App` inside a `RouteErrorBoundary`. `App` renders the header (nav, `JobsMenu`, `NotificationBell`, `ReportProblemButton`, `ThemeMenu`, `GearMenu`, user menu) and picks the page from the route. |
| `router.ts` | The hash router: `Route` type, `parseRoute`, `routeHref`, `useRoute`. |
| `pages/*.tsx` | One file per top-level page (`Library`, `Translate`, `Sources`, `Discover`, `Live`, `Reader`, `Comic`, `SavedManga`, `Settings`, `Diagnostics`, `Admin`, `Assistant`, `Benchmark`, `Login`, `LibraryTools`). |
| `pages/<page>/` | A page's own panels, forms and CSS (`settings/`, `diagnostics/`, `sources/`, `reader/`, ...). Pure form and format logic sits beside the panel as `<name>.ts` with a `<name>.test.ts`. |
| `pages/workspace/` | The per-title workspace: `WorkspaceShell.tsx`, `stages.ts`, `stageRegistry.ts`, `StageContext.ts`, `useDrama.ts`, `workspace.css`. |
| `pages/workspace/stages/` | One `<Name>Stage.tsx` per stage (`SourceStage`, `TranslateStage`, `ReviewStage`, `DubStage`, `ExportStage`) plus the panels each stage composes (`GlossaryPanel`, `CharactersPanel`, `TranscribeStage`, `ExportSubtitles`, ...). `stages/review/` holds the Review editor (`LinesPanel`, `LineRow`, `Player`, `ReviewToolbar`, `ShortcutSheet`, ...). |
| `components/` | Shared UI: the design kit (section 4), `ErrorBanner`, `ErrorBoundary`, `Sheet`, `ConfirmButton`, `TypedConfirm`, the header menus, plus their `*State.ts` / `*Model.ts` logic files. |
| `components/labels.ts`, `uiClasses.ts` | Humanized labels and badge tones; the `buttonClass` / `badgeClass` helpers. |
| `api/` | The HTTP layer (section 6). `client.ts` is the only file that calls `fetch`. One `<area>.ts` per API area, with `api/types.ts` for the core types. |
| `types/` | One `<area>.ts` of hand-written interfaces per API area (section 7). |
| `hooks/` | Reusable hooks: `useLoad`, `useJob`, `useReattachJob`, `useEventStream`, `useSession`, `usePcOnly`, `usePersistedState`, `useMediaQuery`, `useShortcut`, `useDetailsMenu`, `usePopOut`. |
| `report/` | "Report a problem": capture of recent errors, the dialog, the support report. |
| `theme.ts`, `index.css` | Theme preference and the global CSS (tokens and the design-kit classes). |

**State.** There is no state library, router library or context provider tree. State is one of:

- Component state, plus `useLoad` for "load once, show a banner on failure".
- A **module store read with `useSyncExternalStore`** when several components need the same thing: `theme.ts` (theme), `hooks/useSession.ts` (who is signed in), `api/pcOnly.ts` with `hooks/usePcOnly.ts` (is this the main PC), `api/eventStream.ts` with `hooks/useEventStream.ts` (the push stream), `report/reportDialogStore.ts`.
- `StageContext` for the open drama (section 2).
- Per-viewer conveniences in `localStorage` (section 5), always in try/catch.

## 2. Routing and how a title opens into the stages

Routes are location hashes, parsed by `parseRoute` in `router.ts`. Anything unknown or malformed falls back to the library.

| Hash | Route |
|---|---|
| `#/library` (or empty) | Library |
| `#/drama/<id>[/<stage>]` | Workspace for one title |
| `#/read/<id>[?page=n]`, `#/comic/<id>[?page=n]` | Reader and comic reader |
| `#/manga`, `#/manga/<source>/<series>[/<chapter>]` | Saved manga |
| `#/translate`, `#/sources`, `#/discover`, `#/live`, `#/library-tools` | Top-level pages |
| `#/settings`, `#/admin`, `#/diagnostics`, `#/assistant`, `#/benchmark[?compare=]` | Behind the cogwheel (`GearMenu`) |

Nav links are plain `<a href={routeHref(...)}>`. The header's `NAV` list in `App.tsx` also names which route names keep a link marked `aria-current="page"`.

**Opening a title.** A Library card links to `workspaceHref(id)` (`components/libraryView.ts`), which is `#/drama/<id>` with no stage. Then:

1. `App` renders `WorkspaceShell` with `id` and `stage` from the route. The shell is keyed by drama id, so no stage state leaks from one title to the next.
2. `useDrama` fetches the drama. `useProgress` (in `WorkspaceShell.tsx`) fetches the workflow progress (`getWorkflowProgress`).
3. With **no stage in the URL**, `startStage()` (`stages.ts`) opens the stage the progress API reports, once; if progress fails or names an unknown stage it opens `DEFAULT_STAGE` (`source`, in `router.ts`). A later reload never moves the person. With a stage in the URL, `parseStage` uses it, and an unknown id falls back to `source`.
4. The shell looks the stage up in `STAGE_COMPONENTS` (`stageRegistry.ts`) and renders it inside `StageContext.Provider`. Stages take no props; they call `useStage()` for `dramaId`, `drama`, `refetchDrama` and `onJobDone` (refetch after a job finishes).
5. The stage strip is the `<nav aria-label="Stages">` of five links: Source, Translate, Review, Dub, Export (`STAGE_IDS`). Transcript and Diarize live inside Source. Each link carries its state from the progress API (done, next step, optional, blocked) and a count ("32 left", "12 flagged"). Once the open stage is done, `nextAction` offers one "Next: ..." button.

Adding a stage means an id and label in `stages.ts`, a `stages/<Name>Stage.tsx`, and one line in `stageRegistry.ts`.

## 3. Design tokens, themes and where they are defined

All tokens are CSS custom properties at the top of **`frontend/src/index.css`**. Per-page CSS files (`pages/**/*.css`, `components/*.css`) use them and define no colours of their own. The full table and values are in `ui-refresh-spec.md` section 2.1; this is where to look and what is enforced.

**Colour.** An indigo palette: `--accent` `#3d4fa8` on light, `#9aa7ee` on dark, with `--on-accent`, `--accent-soft`, and `--bg` / `--surface` / `--surface-2` / `--border` / `--text` / `--muted` / `--highlight`. Status colours are `--ok`, `--warn`, `--bad`, `--info`, each with a `-soft` mix. `--speaker-1..4-bg/-fg` colour speaker chips. `--ink`, `--line`, `--accent-ink` and `--err` are aliases kept for older CSS. Accent is for the one primary button, the active nav or stage, focus and "on" toggles.

**Themes** are applied by `theme.ts` as `<html data-theme="light|dark|sepia|oled">`; "Match this device" removes the attribute so the `prefers-color-scheme` block applies.

- The dark tokens are written **twice** in `index.css`, once under `@media (prefers-color-scheme: dark)` and once under `:root:is([data-theme="dark"], [data-theme="oled"])`. A media query cannot be combined with an attribute selector, and an explicit choice must beat the OS. Edit both together.
- `:root[data-theme="oled"]` overrides on top of dark: true black `--bg`, near-black surfaces, no popup shadow.
- `:root[data-theme="sepia"]` is the light accent on warm paper, with darker `--warn` and `--ok` for contrast.
- `index.html` sets the saved theme before first paint; `main.tsx` re-applies it for dev and HMR. The theme choice is in `localStorage` under `baihe.theme`.
- `themeContrast.test.ts` reads the CSS and checks text, muted, accent and status contrast for each of the four looks. Run it after touching a colour.

**Type.** The font is the self-hosted Atkinson Hyperlegible (regular and bold Latin subsets in `public/fonts/`, licence alongside, loaded by two `@font-face` rules with `font-display: swap`). CJK text uses each language's system stack through `:lang(zh|ja|ko)`, so mark subtitle text with `lang`, as `LineRow` does. The root is 15px and every size is `rem`, so browser zoom scales it. The scale is `--text-xs` (badges only) `-sm` `-md` `-lg` `-xl` `-2xl`, plus `--text-edit` for subtitle text people edit. `typeScale.test.ts` fails a literal `px` or `rem` `font-size` in any CSS file, a use of the removed `--font-sm` / `--font-md`, and a missing font file.

**Spacing and shape.** Use only `--space-1..8` (4, 8, 12, 16, 24, 32, 48, 64 px). One radius, `--radius` (8px; `-md`, `-lg` are aliases, `-sm` 6px, `-pill`). Motion tokens are `--dur-1..3` and `--ease`; the global `prefers-reduced-motion` rule turns them off.

**Target sizes.**

- Desktop: the default `.btn` is 40px tall, and dense `.btn-sm` is 32px.
- Touch (`@media (pointer: coarse), (max-width: 640px)`): buttons, selects, inputs, textareas, summaries and tab links are at least 44px. A `.btn-sm` and a `Toggle` stay visually small but get a 44px hit area from a pseudo-element, so keep at least `--space-3` between neighbouring dense buttons.
- So the working rule is "32px looks, 44px hits on touch". The e2e helper `hitHeight` measures the hit area, not the box.

**Copy.** Sentence case: visible text starts with a capital letter and has no title case. `copyCase.test.ts` scans the label tables (`*_LABELS`, `*_OPTIONS`, `*_WORDS`, `*_TEXT`, `*_NOTE`, `*_HELP`, `*_MESSAGE` exports) and literal `label`, `title`, `summary` and `help` props. Product names written in lower case (ffmpeg, yt-dlp) are allowed through `capFirst` in `labels.ts`. Labels are 1 to 4 words with the unit in them ("Batch (lines)"), per guideline rules 6 and 7.

## 4. Shared components and when to use each

Kit components are in `frontend/src/components/`; classes are in the design-kit block of `index.css`.

| Component | Use it for |
|---|---|
| `Card` | Always-open block with optional title, one-line meta and actions. What people use on most visits (rule 16). |
| `Section` | A `<details>` fold for rare, advanced or destructive content. Props include `summary` (current values while closed), `count`, `storageKey` (remembers open or closed), `openSignal`, `group` (accordion). Do not render an empty one (rule 8). |
| `Field` | Label + exactly one control + optional unit, `help` (tap-to-open) and `error`. It wires `id`, `aria-describedby` and `aria-invalid`; do not also wrap the control in a `<label>`. |
| `Toggle` | Every boolean setting (rule 19). A `<button role="switch">`. Plain checkboxes stay only for selecting rows and for acknowledgements. |
| `Button` classes, `ButtonLink` | `buttonClass('primary' \| 'secondary' \| 'ghost' \| 'danger', 'md' \| 'sm')` for `<button>`. `ButtonLink` for navigation that acts like a button (rule 18). One primary per Card (rule 17). |
| `Badge` | Status, type or language pill. Use `kind` + `value` so the label goes through `humanize` and status gets its tone. Text always shows; never rely on colour or `title` alone (rule 21). |
| `labels.ts` | `humanize(kind, raw)`, `statusTone`, `tidy`, `humanizeValue`, `capFirst`. Never print a raw API value (rule 20). |
| `ErrorBanner` | A failed call, as a `role="alert"` banner (section 8). |
| `ErrorBoundary` (`RouteErrorBoundary`) | A render crash. Wraps the whole app and, inside `App`, each page, so the header stays usable. |
| `ConfirmButton` | Two-step delete with no typed word. |
| `TypedConfirm` | Destructive action that needs a typed word (restore, resegment). |
| `Sheet` | Modal `<dialog>`: a bottom sheet on phones, a centred dialog above 640px. |
| `JobsMenu`, `NotificationBell`, `GearMenu`, `ThemeMenu`, `RemoteHealthBanner` | Header chrome. Reuse `useDetailsMenu` for a new header menu. |
| `SharingControl` | The private or household switch on a title. |

Related hooks: `useShortcut` (list shortcuts; skips text fields and IME composition), `usePcOnly` (hide or replace PC-only controls when the viewer is remote, with the shared wording constants), `usePersistedState` (section 5).

## 5. Per-viewer memory

Choices that belong to a browser (last engine and model, which `Section`s are open, theme) go in `localStorage`, never in the API (rule 12). Three helpers, each wrapping access in try/catch and falling back when storage is missing: `usePersistedState` (keys under `baihe.pref.`), `Section` `storageKey` (`baihe.section.`), and `theme.ts` (`baihe.theme`). Sign-in tokens never go in web storage; `api/noRawRequests.test.ts` enforces that.

## 6. The `api/` client and loading

- **`api/client.ts` is the only code that calls `fetch`.** It adds the CSRF header on POST, PUT, PATCH and DELETE (with sign-in on), turns a network failure into `ApiError(0, network_error)`, turns any error body into `ApiError(status, {code, message})`, and notifies `useSession` on 401 so the Login page appears. `noRawRequests.test.ts` fails a raw `fetch`, `XMLHttpRequest`, `sendBeacon`, `WebSocket`, `<form action>` or request `method:` literal outside `api/`, and an `EventSource` outside `api/eventStream.ts`.
- **One `api/<area>.ts` per API area**, exporting typed functions; pages never build URLs. A `<area>.test.ts` beside it checks the URL, method and body with a fake `fetch`.
- **Push and polling.** `api/eventStream.ts` is the single server-sent stream per tab. `useEventStream` gives `mode: 'push' | 'connecting' | 'poll'`; fall back to the polling loop (`startJobPolling` in `hooks/useJob.ts`) only in `poll`. `useJob` and `useReattachJob` follow a background job, retrying network and 5xx failures with backoff and stopping at once on 4xx. Job state is the server's; a page reload re-attaches to a running job.
- **Remote or PC-only calls** use `pcOnlyFetch` from `api/pcOnly.ts`.

## 7. How types stay in step with the API

There is **no code generation.** `api/types.ts` and `types/<area>.ts` are hand-written mirrors of `api/schemas/` (one module per domain; the header comment of `api/types.ts` says to change both sides together). So:

1. Change the Pydantic model in `api/schemas/<domain>.py` and the TypeScript interface in the matching `types/<area>.ts` in the same change. Most types files open with a comment naming the schema module they mirror; keep that line true.
2. Mocked specs hide drift, because they return whatever the spec writes. The e2e suite runs the real FastAPI app on a seeded library (section 9), so unmocked specs catch a renamed field.
3. A few numbers that mirror server limits are pinned by `tests/test_frontend_limit_parity.py` (translate text cap, novel EPUB cap). If you add a client-side limit that mirrors a server cap, add a case there.
4. Run `cd frontend && npx tsc --noEmit` after any type change.

## 8. Errors, loading and empty states

- **Failure:** show `<ErrorBanner error={e} />`. It maps the stable API error `code` to plain text (`components/errorMessages.ts`) and shows the server's message only for codes where it is written for people, with paths and key-like strings dropped. Opt in with `describe={{ pcOnly: true }}` for PC-only calls or `{ serverText: true }` for admin and restore calls. Never print `err.message` yourself.
- **One panel fails, the page stays.** `useLoad(load, reloadKey)` returns `{ data, error }`; render the banner in that panel only. `useDrama` keeps the last good drama and tags state with the id, so a slow response for another title is ignored.
- **Loading:** a `.skeleton-block` with `role="status"`, `aria-busy="true"` and a `visually-hidden` "Loading..." (see `WorkspaceShell`, `LibraryList`). Reserve the final size so the page does not jump; the stage strip reserves the width of its count for the same reason.
- **Busy buttons:** disable and change the label ("Signing out..."), as `UserMenu` does.
- **A disabled primary says why** with a one-tap fix (rules 2 and 22); the reason logic is in pure files such as `stages/stageBlockers.ts`, so it is unit-tested.
- **Empty:** render nothing for an empty list or `Section` (rule 8).
- **Crash:** `ErrorBoundary` and `bootFallback.ts` show a message and a way out; `error-boundary*.spec.ts` covers it.
- **Errors you did not catch** are kept (method, path, status and code only, never bodies) by `report/capture.ts` for "Report a problem".

## 9. Phone and responsive rules

The phone breakpoint is `max-width: 640px`. CSS uses it directly; code that must branch uses `useMediaQuery('(max-width: 640px)')`. Touch-only rules use `(pointer: coarse)`.

- **No sideways scroll.** A page never scrolls horizontally. Wide tables sit in `.table-scroll` (`overflow-x: auto`) on desktop. Layouts reflow rather than scroll.
- **Header:** the nav becomes a three-per-row grid; "Report a problem" shrinks to a 44px icon button (its text stays the accessible name).
- **Sticky stage strip:** in the workspace the title row and the stage tabs are one `.ws-strip` (`position: sticky`). `WorkspaceShell` measures it with a `ResizeObserver` into the CSS variable `--bar-h`, and Review's own sticky toolbar and side card offset from that. On phones the tabs become a five-column grid, marker above the label, with counts hidden, and the title row drops to a 44px back arrow, the title and the status badge. The "Next: ..." button moves from the header into a sticky bottom bar, except on Review, where the fixed edit bar owns the bottom edge.
- **Tables as cards:** Characters and Glossary use `<table className="card-table">`; below 640px each row becomes a stacked card whose cell labels come from `td[data-label]` (see `stages/translate.css`). A new table that holds editable fields should do the same.
- **Sheets:** use `Sheet` for per-row actions on a phone (Review's `LineActionsSheet`), not a row of tiny buttons.
- **Tap targets:** see section 3. Do not shrink a control's hit area to fit.

## 10. Testing

Three layers; all of them run in CI (`frontend` job in `.github/workflows/tests.yml`).

| Layer | Command (from `frontend/`) | What it covers |
|---|---|---|
| Types and build | `npx tsc --noEmit` (CI: `npm run build`) | TypeScript, then the bundle. |
| Unit | `npx vitest run` | `src/**/*.test.ts`, run in a **node** environment (no DOM). So components are tested mainly through their logic files (`*Logic.ts`, `*Form.ts`, `*Model.ts`, `*State.ts`), by reading CSS or source text, and (for the kit, `components/uiKit.test.ts`) by rendering to markup; interaction is Playwright's job. |
| Lint | `npm run lint` (oxlint) | |
| Browser | `npm run e2e` | Playwright specs in `e2e/`. |

**Static guards in vitest** worth knowing before you write a screen: `noRawRequests` (HTTP only through `api/`), `copyCase` (sentence case), `typeScale` (type tokens and fonts), `themeContrast` (contrast in all four looks), `uiKit` (kit class contracts), `router.test.ts`.

**Playwright.**

- `playwright.config.ts` starts the real API on a seeded throwaway library (`e2e/serve_seeded_api.py`, port 8611) and the built app via `vite preview` (port 4174, proxying `/api`). `E2E_API_PORT` and `E2E_WEB_PORT` move them. One worker, because specs share the seeded library.
- Two projects. **`desktop`** runs every spec except those ending `mobile.spec.ts`. **`phone`** (390x844, touch, mobile) runs only the specs matching `mobile.spec.ts`, so a phone check for a screen is a file named `<screen>.mobile.spec.ts`. Run one with `--project=phone`.
- **Browser:** use the preinstalled Chromium. Set `PLAYWRIGHT_CHROMIUM_PATH=/opt/pw-browsers/chromium` (the config passes it as `executablePath`). **Never run `playwright install`** here; the CI job installs its own.
- **Mocked-API pattern.** A spec that needs data the seed lacks (a running job, an error, a long name) calls `page.route('**/api/...', route => route.fulfill({ json }))` in a `<screen>Mocks.ts` helper (`phoneTablesMocks.ts`, `liveMocks.ts`, `diskUsageMocks.ts`, `authMocks.ts`, ...). To tweak a real response, `route.fetch()` then `route.fulfill({ response, json: {...} })`. Sign-in specs fulfil every `/api/*` call and assert an `unmocked` list stays empty. Import `test` from `e2e/fixtures.ts` or call `page.unrouteAll({ behavior: 'ignoreErrors' })` in `afterEach`, so an in-flight `route.fetch()` does not fail the test at teardown.
- **Settings groups** start folded; the config pre-opens them through `localStorage` keys `baihe.section.settings.<id>`. Specs about jump links reset that.
- **Phone specs assert** no horizontal overflow (`documentElement.scrollWidth <= clientWidth`), 44px hit height using `installHitArea` and `hitHeight` from `e2e/hitArea.ts` (not the bounding box, which reads 32 for a `.btn-sm`), and that controls are the topmost element at their centre. Run key phone specs at 390 and 360 wide (`phone-tables.mobile.spec.ts`).

## 11. Accessibility conventions

- **Names:** every control has an accessible name. `Field` supplies the label; icon-only buttons get `aria-label`; the stage links keep a plain name and put state in `title` and a `visually-hidden` suffix ("(blocked)").
- **Colour is never the only signal.** Badges and stage marks carry text or a glyph, and status text is never `title`-only (touch has no hover). Help lives in `Field help` (focusable, tap to open) or visible text.
- **Current location:** `aria-current="page"` on the active nav link and stage tab.
- **Announcements:** errors use `role="alert"`; loading and progress use `role="status"` or `aria-live`; `ConfirmButton` announces its second step.
- **Focus:** a visible ring on `:focus-visible` (a 4px glow plus a transparent outline that shows as a system outline in forced-colours mode). Never `outline: none` without a replacement. `Sheet` and `TypedConfirm` use `<dialog>` so the browser traps and returns focus; Escape closes menus and sheets.
- **Keyboard:** everything reachable and operable without a mouse; shortcuts go through `useShortcut`, are listed in `ShortcutSheet`, and do not fire in a text field or during IME composition.
- **Language:** set `lang` on zh, ja and ko text so the right font and line height apply and screen readers pronounce it.
- **Motion:** use the duration tokens; `prefers-reduced-motion` is handled globally.

## 12. Checklist: adding a screen or control

1. **Page or stage?** A page: `pages/<Name>.tsx`, a `Route` variant and a `parseRoute` / `routeHref` branch in `router.ts` (with a case in `router.test.ts`), a render line in `App.tsx`, and a `NAV` or `gearItems` entry if it needs a link. A stage: section 2.
2. **API first.** The route must exist, with its permission. Add a typed function in `api/<area>.ts` and its interface in `types/<area>.ts` (section 7), and a `<area>.test.ts`. Do not call `fetch` anywhere else.
3. **Build from the kit.** `Card` or `Section`, `Field`, `Toggle`, `buttonClass`, `ButtonLink`, `Badge`, `ErrorBanner`. One primary action per Card; collapse advanced options; show a one-line summary; no raw API values; no empty panels (rules 1 to 22).
4. **Style with tokens only.** Colours from variables, sizes from `--text-*` and `--space-*`, one radius. Put page CSS in a `.css` file beside the page; no inline layout styles. Check it in light, dark, sepia and OLED.
5. **Copy.** Sentence case, 1 to 4 word labels, units in the label, help in `Field help`.
6. **States.** Loading skeleton, `ErrorBanner` for a failure, a disabled primary that says why, an empty state that renders nothing, a PC-only fallback if it is remote-restricted (`usePcOnly`).
7. **Remember** only per-viewer choices, with `usePersistedState` or `Section storageKey`.
8. **Phone.** Check at 390 and 360px: no sideways scroll, 44px targets, editable tables as `card-table`, row actions in a `Sheet`. Account for the sticky strip with `--bar-h`.
9. **Accessibility** (section 11): names, focus, `lang`, announcements.
10. **Tests.** Put the logic in a pure `.ts` file with a vitest spec. Add a Playwright spec (`<screen>.spec.ts`) and a `<screen>.mobile.spec.ts`, mocking only what the seed cannot give you.
11. **Run before pushing:** `cd frontend && npx tsc --noEmit && npx vitest run && npm run lint`, then the new specs with `PLAYWRIGHT_CHROMIUM_PATH` set.
12. **Docs.** A new top-level module, `services/*.py` or `api/routers/*.py` file needs a line in `FILE_ORGANIZATION.md`; a new page or stage is worth a mention in `docs/STATUS.md` if it changes what is next.
