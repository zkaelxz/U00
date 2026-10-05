# Navigation proposal: left menu, Ctrl+K palette and Jobs table

Status: proposal for the owner to approve. Docs only; no code changed and no tests run. Nothing here is built until the owner answers section 5.
Written 2026-10-05 against `baihe-subtitler` at 611e69e. Every code claim below was read in `frontend/src`, `services/` or `api/` on that commit; where a doc and the code disagree, the code wins and the doc is named.

## 0. What was recovered, and what was not

The owner's brief refers to an earlier "all-tabs" information-architecture (IA) audit with a safe set S1-S17, eight approved decisions and PRs labelled C to J. **The written list is not in the repo, in the merged PR descriptions, or in the docs I could reach.** I rebuilt the inventory from code instead of from that list.

**Searched:**
- `git log` on the one remote branch (`origin/baihe-subtitler`, 50 commits in this clone) with `--grep` for IA, navigation, all-tabs, sidebar, palette, audit and cleanup.
- Every file under `docs/` and `docs/archive/`, plus a grep of `frontend/src` and `frontend/e2e` for sidebar, left menu, command palette, ctrl+k, jobs table, jobs page, all-tabs and information architecture.
- GitHub PRs #656-#743 (titles and head branches), PR search for "audit", and an issue search for the topic (no issues).
- The bodies of #690, #696, #704 and #715.

**Recovered and relied on (decided before this proposal):**

| Source | What it settles |
|---|---|
| `docs/react-ui-guidelines.md` rules 1-22, `docs/design/ui-refresh-spec.md` | The UI rules this proposal follows (single primary, `ButtonLink` for navigation, humanized labels, disabled primary names its fix). |
| `docs/specs/ux-workspace-shell-and-review.md`, "Decisions (user, 2026-09-29)" | Three owner decisions: the API badge only shows when the API is down (built); delete is two-step; stage progress comes from `GET /api/workflow/dramas/{id}/progress`, with no interim mapping (built). |
| #150 (`docs/archive/ux-click-through-audit.md`) | The 8 questions asked of every workflow: where am I, what's next, is the primary obvious, anything unnecessary, scrolling, related controls together, can I get back, is the current project obvious. I reuse them as the test for each PR in section 4. |
| #690 `ui-shell-sticky-next` | Sticky title and stage strip (`--bar-h`), a `Next:` button, creating a drama opens its workspace. |
| #696 `ia-review-folds` and #715 `ia-source-stage` | Two IA clean-ups already merged: Review's lower tools became four folds and flag tools moved from Export to Review; Source got one "Fill in details" fold. Both branches are named `ia-*`, so they were probably part of the lost series. That is my inference, not a recorded fact. |
| #704, #705, #706, #697 | Sentence case, phone spacing, stacked cards on phone, the indigo type scale. |

**Could not recover:**
- The S1-S17 list itself, and which of its items are done.
- The eight approved decisions.
- What PRs C to J were (only two `ia-*` PRs exist in the history I can see, so the labels cannot be mapped).
- Any earlier design for the left menu, the palette or the Jobs table. No document or code mentions them.

**Owner action:** if the original audit text survives anywhere (a chat, a notes file, an older session), paste it into the PR. I will then reconcile it against section 2. Until then, decisions D1-D12 in section 5 stand in for "the eight approved decisions", and none of them should be treated as already approved.

**Stale claims found while checking** (rule: re-check docs against code):
- `ui-refresh-spec.md` §3.3 says the workspace stage defaults to Source and that nothing calls the progress endpoint. Both are out of date: `router.ts` has `stage: string | null`, `WorkspaceShell.tsx` calls `getWorkflowProgress` and opens `progress.stage` (#150's bug is long fixed).
- The same spec's §3.1 Library redesign is built (`Library.tsx` has `ContinueShelf`, a `Sheet` for New drama, grid cards).
- `docs/react-ui-guidelines.md` still contains an unresolved merge conflict (lines 3 and 5 and 46, `<<<<<<< HEAD`, `=======`, `>>>>>>> origin/baihe-subtitler`). I did not touch it; it belongs in a separate fix.

## 1. Inventory of screens and routes

Routes come from `frontend/src/router.ts` (hash routes, `#/...`). The shell is `App.tsx`. "Header" means `.app-header`; "gear" is `GearMenu`.

### 1.1 Routes

| Screen | Route | How you reach it today | Notes |
|---|---|---|---|
| Library | `#/library` (also the fallback for any unknown hash) | Header nav "Library"; brand is not a link | Header buttons: Saved manga, Library tools, New drama. Continue shelf, filters, grid or list. |
| Library tools | `#/library-tools` | One button in the Library page header | Series, Cost by drama, Reading history, Presets, Voice bank, Backup and storage, Disk usage. Highlights "Library" in the nav. |
| Title workspace | `#/drama/<id>` (opens `progress.stage`) and `#/drama/<id>/<stage>` | Library card or row title, Continue "Resume", Sources and Discover results, Series list "Open", Cost list, reading history, Create | Stages `source`, `translate`, `review`, `dub`, `export`. Unknown stage falls back to `source`. |
| Reader | `#/read/<id>[?page=N]` | "Read" on a card or row, "Read" in the workspace header, Continue shelf | Text, audio and video titles. Comics use the next row. |
| Comic viewer | `#/comic/<id>[?page=N]` | `readHref` for comic types, Sources comic import, workspace "Read" | Has a "Translate" panel (Scanlate). |
| Saved manga | `#/manga` | Library header button; Sources "Read saved manga" | Series list; PC shows the save-folder card. |
| Saved manga series | `#/manga/<source>/<series>` | From Saved manga | Chapter list. |
| Saved manga chapter | `#/manga/<source>/<series>/<chapter>[?page=N]` | From a series | Own reader. |
| Quick translate | `#/translate` | Header nav "Quick translate" | Paste or file translate, history. Not tied to a title. |
| Sources | `#/sources` | Header nav; Get started card; Saved manga empty state | Search or paste a link, import chapters, tracked series, source settings (PC). |
| Discover | `#/discover` | Header nav; Get started card | Tabs: Catalogue, Find a title, Add titles. |
| Live | `#/live` | Header nav "Live" | One session at a time; start needs `media.import_url`. |
| Settings | `#/settings` | Gear menu; about nine inline "Open Settings" links (Translate, Review tools, Research, Get started) | Folds: Jobs, Engines and keys, Defaults, Alerts, Sharing, Integrations, Advanced, Experimental. A member (no `admin.settings`) sees only Sharing and devices. |
| Admin | `#/admin` | Gear menu, only when `canViewUsers` | Remote access, Users, Audit log. A deep link by anyone else shows "Only an admin can see this page." |
| Diagnostics | `#/diagnostics[?install=transcription]` | Gear menu; Jobs panel "All jobs"; error boundary; Source stage install link | Setup, Model health, Jobs fold, Packages, Ports, Job history, Log, Danger zone. |
| Benchmark Lab | `#/benchmark[?compare=...]` | Only a button on Diagnostics, and Model health's "Compare in Benchmark Lab" | Nav highlight stays on the gear. |
| Assistant | `#/assistant` | Gear menu only in Developer Mode, on the PC | A deep link with the mode off shows only the Developer Mode switch; remote shows "PC only". |
| Sign-in | (gate, not a route) | `App.tsx` swaps in `LoginPage` when auth is on and signed out | |

### 1.2 What the chrome exposes

- **Header (always on):** brand "Baihe Studio" (not a link), main nav (Library, Quick translate, Sources, Discover, Live), then `JobsMenu`, `NotificationBell`, `ReportProblemButton`, `ThemeMenu`, gear, `ApiStatus` (only when the API is down), and the account menu when auth is on. The Library nav item counts as active for library, library-tools, drama, read, comic and all manga routes, so it cannot tell you which of those you are on.
- **Header Jobs button (`JobsMenu`):** a count badge and a 24rem popover with every active job, then the newest five finished, each with status, elapsed time and Cancel (where `offersCancel`). Job names are plain text, not links. The footer link "All jobs" goes to `#/diagnostics` and opens the Jobs fold there. It hides itself on 401, 403 and 404. Updates come from the push stream (`useEventStream`), with polling only while the stream is down.
- **Sticky stage strip (`WorkspaceShell`, `.ws-strip`):** back "‹ Library", title, status and type badges, line count, "Read", a `Next:` primary on desktop, and the five stage tabs with state marks and counts. On phone the `Next:` button is a sticky bottom bar (not on Review). It publishes its height as `--bar-h`.
- **Library tools:** see the routes table. It is a page, not a fold, and is reachable only from the Library header.
- **Diagnostics Jobs fold (`JobsBlock`):** the only existing table of jobs. Columns Job, Status, Time, actions; cards at 640 px and below; Cancel, plus PC-only delete and "Delete all finished". No title or stage link, no filters, no search.

### 1.3 Reachable only by deep link, or by a single path

- **Benchmark Lab:** one path (Diagnostics button, or the Model health link).
- **Library tools:** one path (Library header button).
- **Saved manga:** Library header button, and Sources text. Nothing in the main nav.
- **Assistant:** one path (gear, Developer Mode on, PC only).
- **Admin:** one path (gear, admins only).
- **`#/drama/<id>` without a stage, `#/read/<id>?page=N`, `#/manga/...`:** shareable only as raw hashes; there is no copy-link control.
- **Any individual job:** no page or link exists. Job ids are `<prefix><dramaId>` (for example `translate_12`, `dub_12`); `background_jobs.DRAMA_JOB_PREFIXES` lists the prefixes.

## 2. Problems the inventory shows

Each is tagged with the audit question it fails (section 0, from #150).

**Duplicate entry points**
1. Adding a title has four front doors: Library "New drama", Sources (paste a link or search), Discover "Add titles", and the Get started card. Nothing tells you which to use (fails "what's next").
2. Jobs live in three places: the header popover, the Diagnostics Jobs fold and, separately, Diagnostics "Job history". Only the popover is live; only the fold can delete.
3. Backup and storage sits in Library tools while automatic backups sit in Settings "Alerts", and Disk usage is in Library tools while Diagnostics holds Model cache and Ports.
4. Reading history appears in Library tools and the Continue shelf.

**Hidden features**
5. Benchmark Lab, Library tools, Saved manga, Admin and Assistant each have exactly one buried path (section 1.3). New users and phone users will not find them.
6. A title's own jobs, cost and history are spread over Library tools (Cost by drama), Diagnostics (jobs) and the workspace (stage panels). There is no per-title overview.

**Inconsistent naming**
7. "Quick translate" (nav) vs "Translate" (page title and route `#/translate`) vs the workspace "Translate" stage: three meanings of one word.
8. "Library tools", "Admin", "Diagnostics" and "Settings" overlap in purpose (backups, storage, who can sign in, what is wrong); the gear's aria-label is "Settings and tools".
9. "Sources" and "Discover" both find titles; "Read", "Reader" and "Comic" are the same action for different media types; "Jobs" is a button, a fold and a settings card.

**Dead ends and lost context**
10. The workspace back link always goes to Library, even when you arrived from Sources, Discover, the Jobs list or the Cost list (fails "can I get back").
11. Job names in the popover and the Diagnostics table are not links, so a failed job cannot take you to its title or stage.
12. Benchmark Lab highlights "Diagnostics" via the gear; the header gives no breadcrumb for it, Library tools or Saved manga.
13. A member who deep-links to Admin or Settings sees a partial or empty page rather than being told the item is not for them (Admin says so in one line; Settings silently shows only Sharing).

**Phone**
14. The header takes a brand row, a 3 by 2 grid of nav links and an icon row before any content, and `ui-refresh-spec.md` §3.3 already set the target "stage content starts at y ≤ 300 on 390×844". Whether the sticky strip now meets it was not measured here.
15. The Jobs popover is 24rem wide and flips its alignment (`panelFitsLeftwards`); on a 360 px screen it is nearly full width with no clear close control other than a tap outside.
16. Reaching Benchmark Lab takes three taps (gear, Diagnostics, button); Library tools takes two.
17. There is no search anywhere on phone except inside Library and Review.

## 3. Proposal

Design rules applied throughout: 32 px controls on desktop and 44 px on touch (`.btn-sm` still gets 44 px on touch); sentence-case copy; every layout works at 360 px with no sideways scroll; navigation items are `a` links (`ButtonLink` where they act like buttons); colour is never the only signal.

### 3.1 Left menu

**Shape.** A persistent rail on wide screens, a drawer elsewhere. It replaces the header's main nav and the gear; the header keeps only quick-glance controls.

| Width | Behaviour |
|---|---|
| ≥ 1024 px | Rail, 15rem wide, with a collapse control that shrinks it to an icon rail (`title` plus visible tooltip text on focus). Remembered per viewer (`usePersistedState`, guideline 12). Content keeps the existing 1200 px cap, so the rail adds width rather than squeezing pages. With no saved choice the rail starts collapsed below 1280 px and expanded from 1280 px up, following the viewport across that width; a saved choice wins at every width. |
| 641-1023 px | Icon rail (collapsed form), expanding as an overlay on tap. |
| ≤ 640 px | No rail. A "Menu" button (44 px) in the header opens the same list in the existing `Sheet` as a full-height drawer. Closes on choosing an item, on Esc and on the backdrop. The 3 by 2 nav grid in the header goes away. |

**Items, groups and order.** Order is by how often a household member uses them, with admin items last. Labels use the owner's current words unless D4 says otherwise.

| Group | Item | Route | Shown when |
|---|---|---|---|
| (none) | Library | `#/library` | Always. Active on library, drama, read, comic. |
| (none) | Jobs | `#/jobs` (new, section 3.3) | Always; a running-count badge. |
| Find and add | Sources | `#/sources` | Always (see "Permissions" for fallbacks). |
| | Discover | `#/discover` | Always. |
| Tools | Quick translate | `#/translate` | Always. |
| | Live | `#/live` | Always. |
| Library | Saved manga | `#/manga` | Always. Active on all manga routes. |
| | Library tools | `#/library-tools` | Always (PC-only actions inside are hidden remotely, as today). |
| System | Settings | `#/settings` | Always (a member sees Sharing and devices only, as today). |
| | Diagnostics | `#/diagnostics` | `admin.diagnostics`, or, until D6 is settled, always with today's behaviour. |
| | Benchmark Lab | `#/benchmark` | Same as Diagnostics. Active on benchmark only. |
| | Admin | `#/admin` | `canViewUsers`. |
| | Assistant | `#/assistant` | PC only and Developer Mode on (the existing `useDeveloperMode`). |

The brand "Baihe Studio" becomes a link to Library.

**Current title.** When on a drama, reader or comic route, a context row sits under Library: the title (one line, ellipsis) as a link to its workspace. **No stage sub-items:** the sticky stage strip stays the only stage navigation, so there is one place to look (fails "unnecessary on screen" otherwise).

**Relationship to the sticky strip.** The strip lives inside the content column, so it sticks to the top of the column, not the viewport edge; the rail does not scroll with it. On phone the drawer is closed by default, so the strip is the first thing under the slim header. `--bar-h` is unchanged and Review's toolbar offsets keep working. The strip's "‹ Library" stays but becomes a real back (D5).

**Relationship to the header.** Header after the change, left to right: Menu button (phone), brand link, a search button that opens the palette (section 3.2), then the icon group: Jobs (kept, section 3.3), notifications bell, report a problem, theme, account. The gear is removed because its four pages are now menu items. `ApiStatus` stays and is shown only when the API is down.

**Header Jobs button vs the Jobs menu item.** Both stay, with different jobs. The header button is the glance: a badge and the popover with the active jobs and Cancel. The menu item is the page. The popover's "All jobs" link changes from `#/diagnostics` to `#/jobs`, and each job name in the popover becomes a link to its title or stage (needs the `drama_id` addition, section 3.3). The two share one data source (extract the list and push-stream state from `JobsMenu` into a shared hook, so there is one subscription).

**Permissions and PC-only.**
- The menu is a hint, never the authority: the server still enforces every route (`tests/test_api_permissions.py`, `docs/remote-access-decision.md`).
- Items are described in one registry (`navItems.ts`) with `requires: permission[]` and `pcOnly: boolean`. The registry reads `useSession().me.permissions` (a missing permission hides the item) and `usePcOnly()`.
- `usePcOnly` is `unknown` until `/api/meta` answers. The existing rule applies: render optimistically, except Assistant, which waits for `local` (as `useDeveloperMode` does).
- Hidden is better than disabled for items a person can never use (Admin, Assistant, Diagnostics for a member). The server's "Only an admin can see this page." line stays for deep links; fix problem 13 by giving Settings the same sentence when `getSettings` answers 403.
- Which household permissions gate Sources, Discover and Live (`sources.import`, `media.import_url`, `engines.paid` are off by default) is **not decided from this reading**: Sources and Discover have read-only uses, so my default is to keep them visible and let the page explain what is missing. The route table in `docs/remote-access-decision.md` is the place to confirm per route (D6).
- Remote admins on the household listener keep their view-only admin: Admin stays visible (`canViewUsers`), its write controls remain PC-only as today.

**Keyboard and accessibility.** The rail is a `<nav aria-label="Main">` of links; the current item has `aria-current="page"`; groups are headings, not roles. Tab order: skip link ("Skip to content"), brand, search, header icons, the rail, then the page. The collapse control is a button with `aria-expanded`. In the drawer, focus moves in on open and returns to the Menu button on close (`Sheet` already does this).

### 3.2 Ctrl+K command palette

**What it is.** One dialog with a search box that finds places and actions. Open with Ctrl+K (Windows, Linux) or Cmd+K (macOS), or the header search button. It does not replace the Library search box or Review's search.

**What it searches, in this group order.**
1. **Actions** (context first): inside a title: "Go to Source", "Go to Translate", "Go to Review", "Go to Dub", "Go to Export", "Read", "Start transcription", "Translate lines", "Export subtitles". Anywhere: "New drama", "Open jobs".
2. **Titles:** the person's own visible titles by `title_en` and `title_zh`, from `GET /api/library/dramas` (already filtered by the server to what they may see). Fetched once when the palette first opens and cached for the session. Chinese, Japanese and Korean titles match on substring, since there is no word boundary to match on.
3. **Screens:** the registry above (each also matches a few synonyms: "backup", "keys", "users", "disk").
4. **Settings:** the Settings folds as deep links ("Engines and keys", "Notifications", "Sharing and devices"), reusing the `settings-<id>` anchors that `jump()` already uses. A deep link needs a small router addition (D9).
5. **Lines in the open title:** only when the route is a drama, reader or comic. Typed after `#` (or after two characters in the scope chip "This title"): `GET /api/review/dramas/{id}/search?term=&limit=200` (existing, `library.read`), capped to the first five in the palette. Choosing one opens Review for that title with the term pre-filled in Review's own search (there is no line-jump URL today, so v1 cannot scroll to the exact line; a `?line=` parameter is a follow-up, D9).
6. **Lines in all titles:** one row at the bottom, "Search every title for ‘term’", which runs the existing `GET /api/library/search?q=` and shows up to five hits inline. It waits for two or more characters and 250 ms of no typing, and never runs on an empty box.

**Actions never start a job.** "Start transcription" and "Translate lines" navigate to the stage and focus its primary button; the stage's own estimate, paid-engine and disabled-reason rules (guidelines 2, 15 and 22) stay the only gate. Reason: a keystroke must not spend money or take the GPU (D7).

**Ranking.** Within a group: exact match, then word-prefix, then substring, then in-order letters (fuzzy); ties go to more recently opened titles (`getRecent`) and then alphabetically. Empty box: the current title's stages and actions, up to four recent titles, then the screens. Cap at six per group and twenty overall, with a "more" row only for Titles. Prefix shortcuts: `>` actions only, `#` lines in this title.

**Keyboard behaviour.**
- Ctrl/Cmd+K toggles it, from anywhere including text fields (it calls `preventDefault` so Firefox's search shortcut does not fire). It does nothing while an IME is composing. `hooks/useShortcut.ts` deliberately ignores Ctrl and Meta combos, so the palette needs its own listener.
- Up and Down move, Home and End jump, Enter opens, Ctrl/Cmd+Enter opens in a new tab (every result is a real `a href`), Esc closes and returns focus to where it was, Tab stays inside the dialog.
- Opening it never discards a draft (a Review line edit stays in the page underneath).
- Pattern: `role="dialog"` `aria-modal`, an input with `role="combobox"` and `aria-activedescendant` over a `role="listbox"`; a polite live region says "5 results". Matches are marked with `<mark>`; each row has text, never colour alone.

**Permissions and PC-only.** Every entry comes from the same registry as the menu, so a hidden item is never offered. Title and line results come from endpoints that already filter by `visible_to`, so a private title another person owns never appears. PC-only entries (Assistant, PC-only settings folds) are omitted when `usePcOnly()` is `remote`. The palette adds no routes and calls only existing read endpoints.

**Phone.** There is no keyboard shortcut to rely on, so the header search button (44 px) opens the same component as a full-screen `Sheet`: the search box on top (`type="search"`, `enterkeyhint="go"`), results as 48 px rows, scope chips below the box (All, Titles, Screens, Actions, This title) replacing the `>` and `#` prefixes. A hardware keyboard on a tablet or phone still gets Ctrl/Cmd+K.

**Empty and error states.** No matches: "Nothing matches ‘term’. Try a title or a screen name." A failed title load: the other groups still work, with one line "Couldn't load titles." and a retry button. While titles load: skeleton rows, not a spinner.

### 3.3 Jobs table

**Where.** A new page at `#/jobs` (route name `jobs`), reached from the menu, from the popover's "All jobs", and from the palette. It becomes the one place to see and manage jobs. The Diagnostics Jobs fold shrinks to a summary line and a link, keeping the "urgent" behaviour (open while one is running or failed) as a one-line banner with a link.

**Data.** Uses what exists: `GET /api/jobs` (`library.read`, visibility already applied by `jobs_service.list_jobs`), `GET /api/jobs/{id}/stages` for the detail row, `POST /api/jobs/{id}/cancel` (`jobs.cancel`), and the PC-only `POST /api/jobs/{id}/delete` and `POST /api/jobs/clear-finished`. Live updates reuse `useEventStream` (`job` and `job_gone` events), exactly as `JobsMenu` does. Types come from `types/jobs.ts`.

**Columns (desktop, a real `<table>`).**

| Column | Content |
|---|---|
| Job | `description`, falling back to a readable name; one-line message under it. |
| Title | Title name as a link to its workspace, resolved from the job's drama id and the cached library list. Blank for jobs not tied to a title (see below). |
| Stage | "Source", "Translate", "Review", "Dub" or "Export" as a link to that stage; for non-title jobs, the page named by the server's `page` field (Sources, Discover, Live, Settings, Diagnostics), blank when it names none. |
| Status | `Badge` (existing `statusTone`) plus the outcome text (`jobOutcomeText`); `stale` and `stalled` shown as words. |
| Progress | A thin bar with the percent as text for running jobs; blank otherwise. |
| Started | Relative time in a `<time>` element, with the full date and time shown as visible text on expand (not only in `title=`, per guideline 21). |
| Duration | `formatDuration`; live for running jobs. |
| Who | "You" or "Someone else" from `owned_by_me`; shown only when auth is on. No names are available from the API and none is added. |
| Actions | Cancel (if allowed), "Open title", "Details" (expands the row), and PC-only Delete. |

**Phone (≤ 640 px):** the same fields as stacked cards (the existing `JobCards` pattern), title first, status badge and progress under it, actions in a 44 px row. No sideways scroll and no hidden column.

**Filters and sort.** Status chips with counts: Active (default when anything is active), Failed, Finished, All. A search box over description and title. A kind select (Transcribe, Translate, Review checks, Dub, Export, Sources, Other). A "Mine" toggle when auth is on. A time range (Today, 7 days, All). Filters are remembered per viewer. Default order: active first, then newest started; Started and Duration are sortable. Filtering is client-side in v1 (the endpoint returns all visible rows); see the risk in section 6.

**Actions.**
- **Cancel** for queued and running jobs where `offersCancel(job, remoteAdmin)` is true. A remote household admin may cancel only their own, with the existing `REMOTE_ADMIN_NOTE`; the server stays the authority (a refused cancel shows its message).
- **Open title** and **Open stage** are `ButtonLink`s.
- **Details** expands the row: `jobDetail`, result lines (`outcome_message`, redacted result fields as the API already projects them), per-stage timings and cost (`getJobStages`), and for a failed job the error text already passed through redaction by the service.
- **Delete** and **Delete all finished** are PC-only: hidden when `usePcOnly()` is `remote`, replaced by the existing `PC_ONLY_DELETE_NOTE`, and keep `ConfirmButton` (guideline 15). The popover still never deletes.
- No Retry button in v1: re-running means opening the stage and pressing its primary, which keeps the cost and engine checks in one place (D8).

**States.**
- **Empty (no jobs ever):** "No jobs yet. Jobs appear here when you transcribe, translate or export." with a `ButtonLink` to Library.
- **Empty after filtering:** "No jobs match." plus "Clear filters".
- **Loading:** four skeleton rows; no spinner.
- **Error:** an `ErrorBanner` above the table; the last good list stays visible (the quiet-failure rule `JobsMenu` follows). On 404 (an older server) say "This server doesn't list jobs." instead of hiding the whole page; on 401 the existing sign-in gate takes over; on 403 say "You can't see jobs here."
- **Stream down:** a small "Updating every 10 seconds" note, matching the poll fallback.

**Ownership and permissions.** Everything the page shows already passes through `ownership_service.can_see_job`: admins and the local owner see every job; a household member sees jobs for titles they can see plus ones they started; a title that goes private stops showing a member its jobs (`docs/remote-access-decision.md`, "Jobs"). The page adds no new visibility. Cancel needs `jobs.cancel`; the page hides Cancel when `me.permissions` lacks it (a hint only).

**Links back.** Job ids are `<prefix><dramaId>` and today the client would have to parse them. Parsing in the browser would duplicate `DRAMA_JOB_PREFIXES` (which has about forty entries, and gains more) and drift. So the proposal is to add the fields below on the server.

**API additions needed (small; one existing route, no new route):**
1. `drama_id: int | null` on `JobRecord`: set by `jobs_service` for jobs whose id matches a `DRAMA_JOB_PREFIXES` entry. Satisfies the "which title" link and the popover links. Integers only, so no redaction concern.
2. `kind: string | null` on `JobRecord`: a short stable key (`transcribe`, `translate`, `align`, `dub`, `export`, `review`, `import`, `other`) from the same prefix table. The client maps `kind` to a stage and label; the server owns the mapping.
   `page: string | null` (`title`, `sources`, `discover`, `live`, `settings`, `diagnostics`) says which page a job belongs to, from the same job ids (`jobs_service.job_page`), so a job with no title still links somewhere. A page name only, never an id, path or URL.
3. Optional, only if the list grows (risk R3): `GET /api/jobs?status=&drama_id=&limit=&before=`. Defaults keep today's behaviour. Same `library.read` permission; the route table row does not change, but add a pytest for visibility with the new filters.

Items 1 and 2 touch `api/schemas/system.py`, `services/jobs_service.py` and `frontend/src/types/jobs.ts`. They add no route, so `tests/test_api_permissions.py` is unaffected, and they need no `db.py` change because both fields are derived, not stored.

## 4. PR breakdown

Each PR is small, merges on its own and leaves the app working. One task per branch off the latest `baihe-subtitler` (`step-<id>-<short-name>` if the owner assigns step ids). Screenshots go on the PR as attachments. Structural PRs (the shell and the Jobs page) need before and after screenshots at 390×844 and 1280×800, light and dark. Each exit condition ends with the six #150 questions that apply; a PR is not done until each is a yes. Run `cd frontend && npx tsc --noEmit && npx vitest run` and the named Playwright specs for desktop and the `phone` project; for the two with backend work, also `python -m pytest -q tests/test_api_permissions.py tests/test_jobs_service.py tests/test_api_job_cancel.py`.

| # | PR | Scope | Exit conditions | Tests | Owner checks (phone / PC) |
|---|---|---|---|---|---|
| N1 | Nav registry | New `navItems.ts`: one list of items with route, group, label, permission and PC-only flags; the header nav and gear render from it. No visible change. | Header and gear show exactly today's items for owner, member, remote admin and PC; Assistant still waits for `local`. | vitest on the registry for each persona; existing `chrome.mobile.spec.ts` and jobs-menu specs unchanged. | Phone and PC: header looks identical. |
| N2 | Left menu, wide screens | Rail with groups, collapse, current-title row; header loses the main nav and gear; brand links to Library. Phone keeps the old header for now. | Every route in section 1.1 reachable from the rail (Benchmark Lab and Library tools included); `aria-current` correct on drama, read, comic and manga; collapse remembered; content width unchanged at 1280. | Playwright: each item navigates; collapsed state persists; member persona hides Admin and Diagnostics; vitest for active-route mapping. | PC: click through every item; collapse and reload. Phone: confirm nothing changed. |
| N3 | Left menu, drawer | Phone and tablet: Menu button, drawer in `Sheet`, icon rail at 641-1023 px; remove the 3 by 2 nav grid. | At 360 and 390 px no sideways scroll, 44 px targets, Esc and backdrop close, focus returns to the Menu button; stage content starts higher than before (measure and record the y). | `*.mobile.spec.ts` for open, navigate, close, focus; hit-area helper (`e2e/hitArea.ts`); `document.documentElement.scrollWidth <= innerWidth`. | Phone: open the menu with a thumb, reach Benchmark Lab in two taps, rotate. PC: narrow the window to 700 px. |
| N4 | Jobs fields (API) | `drama_id` and `kind` on `JobRecord`, derived in `jobs_service`; frontend types. | Every prefix in `DRAMA_JOB_PREFIXES` maps to a kind or `other`; unseen jobs still 404 for a member; no result text added. | pytest: prefix to kind table, member visibility unchanged, schema round-trip; permissions test passes. | None (no UI). |
| N5 | Jobs page | `#/jobs` with table, phone cards, filters, row details, Cancel, PC-only Delete; shared jobs hook; menu item and popover "All jobs" point here; popover names become links; Diagnostics fold becomes a summary and link. | Section 3.3 states all reachable; cancel works for owner, refused note for remote admin; delete hidden remotely; both header popover and page update live from one stream. | Playwright with mocked `/api/jobs` (extend `jobsMenuMocks.ts`): filters, empty, error, 404, cancel, delete confirm, remote persona; vitest for filter and sort; update `jobs-menu.spec.ts` for the new link and `diagnostics` specs for the shrunk fold. | Phone: start a translation, open Jobs, tap through to its stage, cancel. PC: same, then delete a finished job. |
| N6 | Palette, screens and actions | Ctrl/Cmd+K dialog, header search button, registry-driven Screens, Settings folds, Actions that navigate and focus the primary. | Keyboard contract in section 3.2 met; a hidden item never appears; opening over a dirty Review edit loses nothing; reduced motion respected. | vitest for ranking and prefix parsing; Playwright for open, type, arrows, Enter, Ctrl+Enter, Esc, focus return, member persona; phone spec for the sheet and chips. | PC: Ctrl+K from Library, Review (mid-edit) and Settings. Phone: open from the header, pick a screen, one-handed. |
| N7 | Palette, titles and lines | Titles from `/api/library/dramas`, lines in the open title, lines in all titles; Review opens with the term pre-filled. | Private titles of others never listed; debounced; failure of one group does not blank the rest; CJK substring match. | vitest for matching with CJK; Playwright with mocked search routes; one test that a 403 or 500 leaves other groups. | PC: search a Chinese title and a line. Phone: same. |
| N8 | Back and breadcrumb polish | "Back" returns to where you came from (referrer-aware), copy-link on title and reader, Settings 403 sentence, naming sweep (D4). | Problems 10, 12 and 13 closed; no route removed. | Router vitest for the return path; Playwright from Sources, Jobs and Library. | Phone: open a title from Jobs and press back. |
| N9 | Docs | Fold the decided items into `docs/react-ui-guidelines.md` (after its conflict is fixed), `docs/STATUS.md`, `FILE_ORGANIZATION.md` for new files, and mark this proposal "built". | Docs match code. | None. | None. |

Order rationale: N1 to N3 give a working menu without touching jobs; N4 then N5 deliver the Jobs page; N6 and N7 add the palette last because it depends on the registry and, for line results, on the Jobs-independent search endpoints. N4 can ship any time after approval since it changes no UI.

## 5. Decisions for the owner

Each has a recommendation. D1-D12 stand in for the eight approved decisions that could not be recovered; if any conflict with the lost list, the lost list wins.

| # | Decision | Recommendation |
|---|---|---|
| D1 | Persistent rail on wide screens, or keep the top nav and add only the drawer on phone. | Rail on ≥ 1024 px, drawer on phone. The nav now has 5 items plus a hidden gear; groups and the missing pages (Jobs, Saved manga, Library tools, Benchmark Lab) will not fit a top bar. |
| D2 | Phone navigation: header Menu button and drawer, or a bottom tab bar. | Menu button and drawer. A bottom bar would collide with the sticky `Next:` bar and Review's edit bar, which already own the bottom edge. |
| D3 | Should the stage tabs also appear in the left menu while a title is open? | No. One stage navigation (the sticky strip), one title row in the menu. |
| D4 | Naming: rename "Quick translate" to "Translate text", "Library tools" to "Library tools" (keep) and the workspace stage stays "Translate". | Rename only "Quick translate" to "Translate text" (frees the word "Translate" for the stage). Keep other labels; revisit "Diagnostics" vs "Admin" after N8. |
| D5 | Workspace back link: always Library, or return to where the person came from. | Return to where they came from, falling back to Library (N8). |
| D6 | Who sees Diagnostics, Benchmark Lab, Sources, Discover and Live in the menu. | Diagnostics and Benchmark Lab: only with `admin.diagnostics` (confirm what a member sees today first). Sources, Discover, Live: visible to everyone, pages explain missing permissions. Confirm each against the route table in `docs/remote-access-decision.md` before N1. |
| D7 | Palette actions: navigate and focus, or start the job directly. | Navigate and focus. The palette never spends money or takes the GPU. |
| D8 | Retry on failed jobs in the Jobs table. | Not in v1. Link to the stage; its primary is the retry. |
| D9 | Router additions for deep links: a Settings fold anchor and a `?find=` or `?line=` on Review. | Add both as optional hash parameters, unknown ones ignored (`parseRoute` already tolerates this). Line-exact scroll waits until a line-index endpoint exists. |
| D10 | Jobs endpoint: add `drama_id` and `kind` to `JobRecord` now, or parse ids in the browser. | Add them on the server (N4). One mapping, no drift. |
| D11 | Should Library tools' Series, Cost and Backup move into the menu groups or stay one page. | Stay one page, linked from the menu. Splitting it is a separate decision after the menu proves out. |
| D12 | Keyboard shortcut: Ctrl/Cmd+K only, or also `/`. | Ctrl/Cmd+K only. `/` is already Review's search shortcut and the review spec reserves single letters for the list. |

### Owner answers (2026-10-05)

- D1-D12: go with the recommendations as written.
- D13 (added): each signed-in person chooses which menu items to hide for themselves ("Customize menu": a switch per item). Hiding is only tidiness, not access control: a hidden page is still reachable by its address, and what a person may do stays with the existing permissions. Library and Settings cannot be hidden. Hidden items are also left out of Ctrl+K. Stored per signed-in person in the existing per-person settings (no schema change); the PC owner has one setting of their own. Built as a step after the drawer (N3), before the palette (N6), so the palette reads the same list.
- Audit decisions (docs/design audit, section d): the Catalogue is not used, so it is hidden behind Advanced and removal is planned later; Scanlate becomes its own destination, separate from a title's stages (design confirmed in the comic and novel stages PR); the other six as recommended (merge Sources and Discover into "Find and add", one "Data and backups" area, model switching moves to Settings, "title" instead of "drama", per-page engine pickers use the global routing default with a picker only under Advanced, Benchmark Lab and Assistant only with Developer Mode).

## 6. Risks

- **R1 Active-link ambiguity.** Library is "active" on eight route names today; a rail makes that more visible. Mitigation: per-route active mapping in the registry, tested (N2).
- **R2 Sticky and fixed bars on phone.** The sticky strip, the `Next:` bar and Review's edit bar already compete for the edges. A drawer is fine; a new bottom bar is not. Mitigation: D2, and a phone check of Review with the menu open and closed.
- **R3 Jobs list size.** `GET /api/jobs` returns every visible record with no limit or cursor, and the table plus the push stream both hold it in memory. Mitigation: client-side cap with "Show more" in v1 (as the Diagnostics fold does today); the server filters in item 3 of the API additions only if rows climb into the thousands. Measure on a real library before building it.
- **R4 Two subscribers.** The popover and the page both watch jobs. If they diverge the user sees a count that disagrees with the table. Mitigation: one shared hook (N5 exit condition).
- **R5 Permission drift.** A registry is a second place that knows who may see what. Mitigation: it only hides; the server stays the authority; add a vitest per persona and a note in `docs/remote-access-decision.md` that new routes need a registry entry.
- **R6 Palette leaks.** Title and line results must never include another person's private items. Mitigation: use only endpoints that already apply `visible_to`; no client-side joining of data across users; a Playwright persona test.
- **R7 Shortcut collisions.** Firefox uses Ctrl+K for its search bar; macOS apps use Cmd+K variously. Mitigation: `preventDefault` on our own handler, a visible header button as the fallback, and no use of the shortcut while an IME composes.
- **R8 e2e churn.** About 13 e2e files reference header links (`getByRole('navigation', { name: 'Main' })`, the gear, the popover's "All jobs"). Mitigation: N1 first, so later PRs change one registry, and the main nav keeps the same accessible name.
- **R9 Unrecovered audit.** The lost S1-S17 and the eight decisions may contradict a choice here. Mitigation: nothing is built until the owner reads section 5, and section 0 asks for the original text.
- **R10 Unverified claims.** I did not run the app, measure y positions, or open a phone. The statements about phone header height (problem 14) and Settings' member view (problem 13) are read from code and CSS and should be confirmed with screenshots during N3.
