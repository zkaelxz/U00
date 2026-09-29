# Remote access decision (replaces D6: Tailscale)

Decided by the user on 2026-09-29, from a discussion session. No repo changes were made by that session; this file records the outcome.
It replaces D6 (Tailscale + Tailscale Serve) in [`migration-review.md`](migration-review.md) and supersedes [`remote-access-design.md`](remote-access-design.md).
**D5 stays** (admin actions are PC-only, on a separate loopback listener).
Status: step 133 (users, sessions, permissions, static test) is built, see "Step 133" below; the rest is not. Implementation steps are proposed as 133-140 in [`baihe-roadmap-master.md`](baihe-roadmap-master.md) section 5.

## Context

- The user doubted the VPN, so Tailscale is out.
- The PC is always on with a normal home IP. The user owns a domain.
- Household members use their own phones and computers, browser only.
- Today `api/server.py` has no authentication, and the API binds to loopback by default.

## Decided

- **Route:** Caddy on the PC with a real Baihe login. Household devices will stream and download large video; Cloudflare's request cap and its terms on large media are uncertain, and the extension behind Cloudflare Access is unproven.
- **Order of work:** build auth and permissions, test on the LAN through Caddy with a real certificate, and open the router port last.
- **Auth:** Google OIDC sign-in (a vetted library such as Authlib) plus a Baihe users allowlist. Match on Google's `sub`, require `email_verified`, use PKCE, state and nonce. Server-side sessions in a Secure, HttpOnly, SameSite cookie, with CSRF protection. Rate-limit login routes; audit-log sign-ins and admin actions. The first admin is granted locally with `python -m api grant-admin`, which also serves as recovery.
- **Permissions:** deny by default, one declared permission per route, and a static test that fails if a route has none.
  - Household scope: browse, read, review, edit lines, and start and cancel jobs on media already on the PC.
  - Separate permissions, off by default: `media.import_url` (yt-dlp), `sources.import` (manga and novel fetch), `engines.paid`, `extension.send`, `media.stream`.
- **Keys:** unchanged. Entered at the PC, resolved server-side, never sent to a device. Slice 24 stays off by default.
- **Admin:** stays on a separate loopback listener (D5) that Caddy never routes to. Remote admin, if ever wanted, would need identity plus a second factor.
- **Host:** stays on the always-on PC. A movable host was discussed and not chosen.
- **Extension:** desktop Chrome and Edge only, for household members' computers. Each person creates a per-device API token in Baihe's settings (hash stored, shown once, revocable). The bridge moves from `page_server.py` (loopback, one shared token) into the API behind this auth. Never route port 8756.
- **Phones:** an installable PWA first. Capacitor is deferred. Mobile URL capture uses a share target (Android), an iOS Shortcut posting to the API, and pasting a URL into URL import. If a native wrapper is ever built, Google sign-in must open in the system browser.

## Repo work

1. Users table, allowlist, sessions, and the permission dependency plus its static test.
2. Google OIDC login and callback.
3. Per-device extension tokens, then move the extension bridge into the API.
4. URL-import guards: http/https only, block private, loopback and link-local addresses (including after redirects), and caps on size, count and concurrency.
5. Per-user job limits, and per-user bandwidth caps for media.
6. Verify Range and seek behaviour with a multi-GB file (the API uses `FileResponse`), and job progress through Caddy.
7. Set an upload size limit if uploads are ever allowed remotely (there is none today). Uploads, deletes and settings are PC-only unless the user decides otherwise.
8. PWA manifest and a minimal service worker (cache the app shell only), and check session cookies in installed PWAs on real devices.
9. Hardening: no public `/api/docs`, production mode only, Streamlit never exposed.
10. Operations: the PC never sleeps, Caddy and the API run as services on boot, an uptime alert, backups, dynamic DNS and certificate renewal monitoring, and per-device session lists with revocation.

## Accepted trade-offs

- A compromised household Google account gives access to that member's permissions. Sensitive permissions stay off by default.
- A Google outage blocks new logins; existing sessions keep working.
- The 24/7 PC exposes Baihe's own login page to the internet, and the user owns the patching.
- Downloads come from the user's home IP.

## Unverified (not checkable from the session)

- Range support in the installed Starlette.
- Google's app-consent limits for an unverified app.
- The ISP's inbound-port policy and the home upload speed.
- Cloudflare's current terms, if that route is ever revisited.
- Mobile extension support, Web Share Target on iOS, and PWA push limits.
- Whether the app can use a separate capped key for household jobs, and what the monthly cap enforces.
- Sources fetches through a configured or environment proxy (B-25, `fix-b25-redirect-hops`): every hop's target is validated by name only and cannot be pinned, so a DNS answer that changes between the check and the proxy's own lookup is not caught.

## Rejected or deferred

Tailscale and other VPNs; Cloudflare Tunnel + Access (revisit if large media isn't needed remotely); Authelia and other self-hosted identity servers; per-person API keys; remote key entry; a portable or dual host; a native mobile app.

## Effect on other docs and slices

- The Discover/Sources/Live spec's open question Q2 (what other devices may trigger) is answered by the permission list above: `media.import_url`, `sources.import`, `engines.paid`, `extension.send` and `media.stream` are off by default; browse, read, review, edit lines, and start and cancel jobs on media already on the PC are on. Slices S-3 to S-6 must not ship to non-local clients before the permission dependency (step 133) exists. Sign-in, proxy, pacing floors and cookies stay local-only.
- Live capture URLs (any public URL) map to `media.import_url`.
- History: option E (built-in logins, publicly exposed) was chosen on 2026-09-28, reversed the same day in favour of Tailscale, and is now chosen again on 2026-09-29 with the design above.

## Step 133: permission model as built

`BAIHE_API_AUTH=off` (default) keeps today's behaviour for the owner at the PC: every request is the local owner with every permission, no session, no CSRF. Because of that, off mode serves only direct loopback requests: `LoopbackOnlyGate` answers a generic 403 to any request with a non-loopback peer, Host or Origin, or with any proxy/forwarding header (`X-Forwarded-*`, `Forwarded`, `Via`, `X-Real-IP`, ...), on every path including `/api/health` and the frontend (a proxied health check must not report a server it would refuse to serve). A non-loopback `BAIHE_API_HOST` is also refused at startup (`python -m api` and `create_app`); the env check alone could not stop a same-PC reverse proxy or `uvicorn --host`, the request-time gate does. The gate also blocks DNS rebinding (the Host is the attacker's name). `on` enforces the rules below. Catalogue (`services/auth_service.py`): household defaults `library.read`, `lines.read`, `lines.edit`, `jobs.start`, `jobs.cancel`, `review.use`; off by default `media.import_url`, `sources.import`, `engines.paid`, `extension.send`, `media.stream`; admin-only (from the `is_admin` flag, never grantable) `admin.library`, `admin.settings`, `admin.diagnostics`, `admin.users`. `local_only()` means a direct loopback request at the PC (no proxy headers, loopback Host/Origin), with no session needed; it is how "uploads, deletes and settings are PC-only" is enforced until the D5 admin listener exists. In both modes a POST/PUT/PATCH to a `local_only()` route must also be `application/json` or carry `X-Baihe-Local: 1`, so a page on another loopback port cannot reach it with a no-cors "simple" request (text/plain, form-urlencoded or multipart); DELETE and GET are unaffected. The React upload helper (`postMultipart`) sends `X-Baihe-Local: 1`. `LocalOnlyCrossSiteGate` applies this before the request body is read. `tests/test_api_permissions.py` fails if any route lacks exactly one declaration.

Route -> permission (generated from the app; auth on, frontend built):

| Declaration | Routes | Paths |
|---|---|---|
| admin.diagnostics | 7 | `GET /api/diagnostics`<br>`GET /api/diagnostics/setup-checks`<br>`GET /api/diagnostics/model-cache`<br>`GET /api/diagnostics/pyannote`<br>`GET /api/diagnostics/job-history`<br>`GET /api/diagnostics/log`<br>`GET /api/diagnostics/support-report` |
| admin.library | 9 | `POST /api/library/presets/{preset_id}/rename`<br>`POST /api/library/voice-bank/{entry_id}/rename`<br>`POST /api/dramas`<br>`POST /api/dramas/{drama_id}/metadata`<br>`POST /api/metadata/dramas/{drama_id}/autofill/apply`<br>`POST /api/discover/titles`<br>`POST /api/discover/titles/seed`<br>`POST /api/discover/titles/{title_id}/import-to-library`<br>`POST /api/discover/bulk-commit` |
| admin.settings | 9 | `GET /api/settings`<br>`GET /api/sources/settings`<br>`GET /api/sources/profiles`<br>`POST /api/sources/cache/clear`<br>`POST /api/sources/profiles/{domain}/{kind}/rollback`<br>`POST /api/sources/{name}/enabled`<br>`POST /api/sources/{name}/adult`<br>`POST /api/sources/{name}/health/reset`<br>`GET /api/sources/{name}/attempts` |
| engines.paid | 3 | `POST /api/translate`<br>`POST /api/line-ai/dramas/{drama_id}/lines/{line_id}/improve`<br>`POST /api/line-ai/dramas/{drama_id}/lines/{line_id}/explain` |
| jobs.cancel | 3 | `POST /api/jobs/{job_id}/cancel`<br>`POST /api/translate-run/dramas/{drama_id}/bulk/{bulk_job_id}/cancel`<br>`POST /api/live/sessions/{session_id}/stop` |
| jobs.start | 13 | `POST /api/export/dramas/{drama_id}/audiobook`<br>`POST /api/export/dramas/{drama_id}/burned-video`<br>`POST /api/diarization/dramas/{drama_id}/run`<br>`POST /api/transcribe/dramas/{drama_id}/run`<br>`POST /api/dub/dramas/{drama_id}/run`<br>`POST /api/translate-run/dramas/{drama_id}/run`<br>`POST /api/translate-run/dramas/{drama_id}/bulk/resume`<br>`POST /api/narration/dramas/{drama_id}/run`<br>`POST /api/review-jobs/dramas/{drama_id}/consistency`<br>`POST /api/review-jobs/dramas/{drama_id}/emotion`<br>`POST /api/review-jobs/dramas/{drama_id}/notes`<br>`POST /api/review-jobs/dramas/{drama_id}/flag`<br>`POST /api/review-jobs/dramas/{drama_id}/fix-flagged` |
| library.read | 54 | `GET /api/library/dramas`<br>`GET /api/library/dramas/{drama_id}`<br>`GET /api/library/stats`<br>`GET /api/library/recent`<br>`GET /api/library/costs`<br>`GET /api/library/series`<br>`GET /api/library/search`<br>`GET /api/library/history`<br>`GET /api/library/presets`<br>`GET /api/library/voice-bank`<br>`GET /api/reader/dramas/{drama_id}/page`<br>`GET /api/jobs`<br>`GET /api/jobs/{job_id}`<br>`GET /api/translate/engines`<br>`GET /api/translate/history`<br>`GET /api/export/dramas/{drama_id}/readiness`<br>`GET /api/export/ass-style-options`<br>`GET /api/diarization/dramas/{drama_id}/config`<br>`GET /api/source/dramas/{drama_id}/config`<br>`GET /api/transcribe/dramas/{drama_id}/config`<br>`GET /api/dub/dramas/{drama_id}/config`<br>`GET /api/dub/dramas/{drama_id}/pacing`<br>`GET /api/translate-run/dramas/{drama_id}/config`<br>`GET /api/translate-run/dramas/{drama_id}/estimate`<br>`GET /api/translate-run/dramas/{drama_id}/bulk`<br>`GET /api/characters/dramas/{drama_id}`<br>`GET /api/characters/dramas/{drama_id}/clone-engines`<br>`GET /api/characters/series/{series_id}/characters`<br>`GET /api/characters/voice-bank`<br>`GET /api/glossary/catalogues`<br>`GET /api/glossary/dramas/{drama_id}/terms`<br>`GET /api/glossary/dramas/{drama_id}/instructions`<br>`GET /api/artifacts/dramas/{drama_id}/{kind}/info`<br>`GET /api/media/dramas/{drama_id}/status`<br>`GET /api/narration/dramas/{drama_id}/config`<br>`POST /api/metadata/dramas/{drama_id}/analyze-media`<br>`GET /api/novel/dramas/{drama_id}/status`<br>`GET /api/discover/titles`<br>`GET /api/discover/platforms`<br>`GET /api/discover/search-links`<br>`GET /api/sources`<br>`GET /api/sources/tracked`<br>`GET /api/sources/notifications`<br>`GET /api/sources/{name}`<br>`GET /api/workflow/dramas/{drama_id}/progress`<br>`GET /api/live/sessions`<br>`GET /api/live/sessions/{session_id}`<br>`POST /api/discover/translate-query`<br>`POST /api/discover/baihehub-search`<br>`GET /api/discover/bulk-extract/result`<br>`GET /api/discover/navigation-help/result`<br>`POST /api/sources/search`<br>`POST /api/sources/{name}/series`<br>`GET /api/sources/jobs/{job_id}/result` |
| lines.edit | 23 | `POST /api/export/dramas/{drama_id}/flag-overlaps`<br>`POST /api/export/dramas/{drama_id}/flag-dense-lines`<br>`POST /api/export/dramas/{drama_id}/flag-auto-qc`<br>`POST /api/source/dramas/{drama_id}/config`<br>`POST /api/transcribe/dramas/{drama_id}/config`<br>`POST /api/characters/dramas/{drama_id}/character`<br>`POST /api/characters/dramas/{drama_id}/voice-bank/apply`<br>`POST /api/glossary/dramas/{drama_id}/terms`<br>`DELETE /api/glossary/dramas/{drama_id}/terms/{term_id}`<br>`POST /api/glossary/dramas/{drama_id}/instructions/project`<br>`POST /api/glossary/dramas/{drama_id}/instructions/series`<br>`POST /api/lines/dramas/{drama_id}/lines/{line_id}`<br>`POST /api/lines/dramas/{drama_id}/lines/{line_id}/dismiss-flag`<br>`POST /api/lines/dramas/{drama_id}/find-replace/apply`<br>`POST /api/lines/dramas/{drama_id}/lines/{line_id}/accept-tm`<br>`POST /api/lines/dramas/{drama_id}/notes`<br>`DELETE /api/lines/dramas/{drama_id}/notes/{note_id}`<br>`POST /api/restructure/dramas/{drama_id}/lines/add`<br>`POST /api/restructure/dramas/{drama_id}/lines/{line_id}/delete`<br>`POST /api/restructure/dramas/{drama_id}/merge`<br>`POST /api/restructure/dramas/{drama_id}/lines/{line_id}/split`<br>`POST /api/restructure/dramas/{drama_id}/resegment`<br>`POST /api/restructure/dramas/{drama_id}/history/{history_id}/restore` |
| lines.read | 11 | `GET /api/export/dramas/{drama_id}/subtitle`<br>`GET /api/export/dramas/{drama_id}/epub`<br>`POST /api/export/dramas/{drama_id}/ass`<br>`GET /api/review/dramas/{drama_id}/lines`<br>`GET /api/review/dramas/{drama_id}/search`<br>`POST /api/review/dramas/{drama_id}/find-replace/preview`<br>`GET /api/review/dramas/{drama_id}/coverage`<br>`GET /api/review/dramas/{drama_id}/pacing-flags`<br>`GET /api/review/dramas/{drama_id}/lines/{line_id}/provenance`<br>`GET /api/review/dramas/{drama_id}/lines/{line_id}/original-text`<br>`GET /api/restructure/dramas/{drama_id}/resegment/preview` |
| local_only() | 18 | `POST /api/settings`<br>`POST /api/settings/keys/{engine}`<br>`POST /api/settings/keys/{engine}/clear`<br>`DELETE /api/translate/history`<br>`DELETE /api/dramas/{drama_id}`<br>`POST /api/media/dramas/{drama_id}/upload`<br>`POST /api/media/dramas/{drama_id}/upload-and-transcribe`<br>`POST /api/novel/dramas/{drama_id}/attach-text`<br>`POST /api/novel/dramas/{drama_id}/attach-epub`<br>`POST /api/novel/dramas/{drama_id}/ocr-chapter`<br>`POST /api/discover/titles/{title_id}/delete`<br>`POST /api/sources/settings`<br>`POST /api/diagnostics/dependencies/{package}/install`<br>`POST /api/diagnostics/dependencies/{package}/upgrade`<br>`POST /api/diagnostics/reset-library`<br>`GET /api/extension/status`<br>`POST /api/extension/enabled`<br>`POST /api/extension/token` |
| media.import_url | 5 | `POST /api/metadata/dramas/{drama_id}/autofill`<br>`POST /api/live/sessions`<br>`POST /api/discover/import-suggestion`<br>`POST /api/discover/bulk-extract`<br>`POST /api/discover/navigation-help` |
| media.stream | 6 | `GET /api/dub/dramas/{drama_id}/track`<br>`GET /api/artifacts/dramas/{drama_id}/{kind}`<br>`GET /api/media/dramas/{drama_id}/audio`<br>`HEAD /api/media/dramas/{drama_id}/audio`<br>`GET /api/media/dramas/{drama_id}/video`<br>`HEAD /api/media/dramas/{drama_id}/video` |
| public() | 3 | `GET /api/health`<br>`GET /api/meta`<br>`GET /{path:path}` |
| review.use | 11 | `GET /api/review/dramas/{drama_id}/history`<br>`GET /api/review/dramas/{drama_id}/history/{history_id}`<br>`GET /api/review/dramas/{drama_id}/versions`<br>`GET /api/review/dramas/{drama_id}/versions/compare`<br>`GET /api/review/dramas/{drama_id}/notes`<br>`GET /api/review/dramas/{drama_id}/notes/markdown`<br>`GET /api/review/dramas/{drama_id}/consistency`<br>`GET /api/review/dramas/{drama_id}/emotions`<br>`GET /api/review/dramas/{drama_id}/tendencies`<br>`GET /api/review/dramas/{drama_id}/tm-suggestions`<br>`GET /api/restructure/dramas/{drama_id}/history` |
| sources.import | 2 | `POST /api/sources/tracked`<br>`POST /api/sources/notifications/{notification_id}/dismiss` |

Non-routes: `/api/docs`, `/api/openapi.json` and `/docs/oauth2-redirect` exist only with auth off (loopback-only); with auth on they are not registered, and a request for them is a 401 like any other non-public `/api` path. There are no mounts or WebSocket routes; a future one shows up in the static test as undeclared.

Judgement calls:
- **Paid engines.** Routes that can call an LLM (`translate-run/.../run`, the five `review-jobs`, `narration/.../run`, `metadata/.../autofill`, and `restructure/.../resegment` when `use_llm` is true) declare `jobs.start` (or `media.import_url`) and the handler calls `require_engines_allowed` with every engine the request names (including a translate fallback chain). Without `engines.paid` only `translate_engines.FREE_ENGINES` pass; an omitted engine (the configured default) and Gemini (free tier is a per-key setting) count as paid. `POST /api/translate` and `line-ai` improve/explain are ad-hoc LLM calls and declare `engines.paid` outright. Groq cloud transcription is paid too: `POST /api/transcribe/.../config` with `use_groq: true`, and `POST /api/transcribe/.../run` for a drama whose stored config has `use_groq` on, need `engines.paid` (`require_paid_engines`). Audited (2026-09-29): every service that resolves an engine key or builds an engine (`line_ai`, `metadata`, `narration`, `restructure`, `review_jobs`, `translate_run`/`workspace_job`, `translate`, `transcribe`) is behind one of these checks; `translate-run/.../bulk/resume` only polls batches already paid for; dub/audiobook TTS (`edge_tts`, Piper) and voice-clone engines (F5-TTS, OmniVoice, GPT-SoVITS, Chatterbox, TADA) are free/local; diarization uses a free Hugging Face token.
- **Uploads and deletes are `local_only()`** (media upload, upload-and-transcribe, novel attach-text/epub/OCR, `DELETE /api/dramas/{id}`, discover title delete, `DELETE /api/translate/history`), per repo-work item 7. Line-level deletes (glossary term, note, restructure line delete) are ordinary edits: `lines.edit`.
- **Settings writes and source pacing/proxy/cookie config are `local_only()`** (`POST /api/settings`, key writes, `POST /api/sources/settings`); other sources administration (enable, adult toggle, health reset, cache clear, profile rollback, attempts log, settings/profiles read) is `admin.settings`.
- **Drama-level metadata and library catalogue writes** (create drama, drama metadata, autofill apply, preset/voice rename, discover create/seed/import) are `admin.library`; per-drama stage config (source, transcribe) and flags/glossary/characters are `lines.edit`.
- **Media bytes** (`artifacts/.../{kind}`, dub track) are `media.stream`; subtitle/EPUB/ASS text exports are `lines.read`. `/api/jobs` reads are `library.read` (jobs are not yet per-user, step 137).
- **Sources:** tracking a series and dismissing a new-chapter notification are `sources.import`; listing sources, tracked series and notifications is `library.read`; searching the sources and listing one series' chapters (spec S-3, paced jobs that fetch from this PC but write nothing) are `library.read` too, per the spec's "read and search allowed". Importing chapters (S-4, not built) will be `sources.import`.
- **Diagnostics:** reads (overview, setup checks, model cache, pyannote readiness, job history, log, support report) are `admin.diagnostics`; dependency install/upgrade and the library reset are `local_only()` with `confirm=true` (reset also `confirm_text` "RESET") and refuse while a job runs.
- **Extension bridge control** (status, on/off, token) is `local_only()`: the bridge and its token belong to the owner at the PC (user decision 2026-09-29). The status never carries the port or the token; only `POST /api/extension/token` with `confirm=true` returns the token, with `Cache-Control: no-store`. Port 8756 stays loopback-bound.
- **Public:** `/api/health`, `/api/meta` and the frontend shell catch-all (static files only; it never answers an `/api` path with data).

Deployment note (Caddy): use a plain `reverse_proxy 127.0.0.1:8600`. Caddy's defaults set `X-Forwarded-For`/`-Proto`/`-Host` and pass the public Host through; do not strip the forwarding headers or rewrite Host to a loopback name, since those are what mark a request as remote (off mode refuses it; `local_only()` refuses it with auth on). Run the API with `BAIHE_API_AUTH=on` behind Caddy.

Requirements carried to step 134 (login):
- Issue a fresh session from `auth_service.create_session` on every successful login and revoke the client's previous session; never adopt a session id the client sent (session fixation).
- Name the cookie with the `__Host-` prefix (`__Host-baihe_session`: Secure, `Path=/`, no `Domain`) so a sibling subdomain can't plant or overwrite it; keep the loopback-http dev relaxation working (a `__Host-` cookie requires Secure, so dev without TLS needs the plain name).
- Rate-limit the login and callback routes per client address with `SlidingWindowRateLimiter` and audit-log sign-ins.
- Expired sessions are deleted only when presented; add a periodic sweep if the table grows.
