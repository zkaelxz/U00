# Split view: two titles, or a title and any other page, side by side

Ask: keep two titles open at once, or watch one while clicking around another (transcribe, translate), and let
the second view also go to Live, Settings, Logs and the other pages. So each view needs its own page, not only
its own title.

## What already works (checked in code)
- The route is the URL hash (`frontend/src/router.ts`, `useRoute` on `hashchange`). Titles, stages, tabs, series,
  readers and every top-level page are addressable, and nav entries are plain `href`s, so Ctrl/Shift-click or
  "open link in new window" gives an independent view. Two windows on two titles, or one on a title and one on
  Live or Settings, already behave independently.
- No "current title" lives on the server or in localStorage. Browser storage holds per-title keys
  (`baihe.run.<key>`, `baihe.draft.*`, translate preset per drama) or per-viewer preferences (nav dock, theme,
  last-used sub-tab, notification last-seen). The theme and nav dock already sync across windows through the
  `storage` event.
- Jobs: one `GET /api/events` stream and one jobs list per window (`api/eventStream.ts`, `JobsProvider`), so the
  job pill and live updates are per window and both stay current.

## What is rough today
1. Nothing offers a second window: no "Open in new window" on a title, and the window is not placed side by side
   (the user snaps it with the OS).
2. Window titles are "<page> · Baihe Studio", so two windows on different titles are told apart only by the
   title's own heading; fine for titles, two Settings windows look the same.
3. The same title open twice: line saves are field-level by id, so the last write wins; there is no live
   refresh between the two windows, and a parked review draft (`sessionStorage`) is per window, which is correct.
4. "Last used sub-tab" memories (Library tools, Benchmark, Discover) are shared by all windows; harmless, the
   URL's `?tab=` wins when present.
5. Each window opens its own event stream (and media streams are capped at 3 per user,
   `api/routers/media_routes.py`). Browsers allow about 6 HTTP/1.1 connections per origin; two or three windows
   are fine, ten are not.

## Options

### (a) Every page URL-addressed, user opens two windows
Already true for routing. Remaining work: an "Open in new window" action on the title header and nav entries
(`window.open(href)`), a one-line distinct window title for pages without a heading that differs, and tests.
Size: S (about 3 files: `App.tsx` or the header, `nav/documentTitle.ts`, one small component, vitest plus a
Playwright spec). Risk: low. Limit: the user arranges windows; on a phone it is useless.

### (b) In-app split pane, two independent contexts
Two panes each with its own route. Two ways:
- b1, iframes of the same app (`#/...?embed=1`): each iframe is a fully independent context for free, so every page works
  in either pane. Blocked today: `api/auth.py` sends `X-Frame-Options: DENY` and `frame-ancestors 'none'`;
  it would have to become `SAMEORIGIN` / `'self'`. That is a security-header change and needs Kae's yes first
  (clickjacking surface is same-origin only). Size: M (shell split layout, `embed` mode that hides the second
  pane's nav and header, pane routes in the URL such as `#/drama/3/review?split=%23%2Flive`, `api/auth.py` header
  and its test, resizer, phone fallback to single pane). Risk: medium (two event streams, scroll and focus,
  the media cap, Playwright coverage).
- b2, render `App` routes twice in one document: needs the route to be a prop instead of a global
  `useRoute`, and several module-level singletons assume one title per document (`linesController` writers map
  keyed by drama id, `useDocumentTitle`, `#main-content` ids, the shared jobs provider, skip link, command
  palette). Size: L, risk high. Not recommended.

### (c) Pop-out window
Same as (a)'s window opening plus a small "pop-out" chrome (no nav, no header) via `?embed=1`, placed with
`window.open(url, name, "popup,width=...")`. Size: S to M, shares the `embed` mode with b1. Low risk; the OS places
the window. Works with every page, since it is the same app.

## Recommendation
Do (a) first, built so (c) comes with it: the "Open in new window" action opens the current page (or any nav
entry) in a second window, and an `embed` flag drops the second window's nav and header when the user wants the
slim variant. It needs no server or security change and covers "watch one title, click around another" and "second
view goes to Live, Settings, Logs", because each window has its own page. If Kae wants the panes inside one window,
b1 is the route: same code plus the frame-header change, which is Kae's call (security). b2 is not worth it.

Files for (a)+(c): `frontend/src/App.tsx` (embed flag hides `SideNav` and header), `frontend/src/router.ts`
(strip `embed` from route parsing), `frontend/src/nav/documentTitle.ts`, a small `OpenInWindowButton` beside the
title and Library entries, `frontend/src/router.test.ts`, one Playwright spec. For b1 add `api/auth.py`
and `tests/test_api_household_listener.py` and `tests/test_caddyfile_template.py` (the Caddy template sets the same header).

## Decisions needed
1. Is "two browser windows, with an Open in new window button" enough, or must both views sit in one window (b1)?
2. If b1: OK to change the framing headers from deny to same-origin only?
3. UI boards (button placement, slim window) need Kae's named OK per page group before any merge.
