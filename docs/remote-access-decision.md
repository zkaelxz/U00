# Remote access decision (replaces D6: Tailscale)

Decided by the user on 2026-09-29, from a discussion session. No repo changes were made by that session; this file records the outcome.
It replaces D6 (Tailscale + Tailscale Serve) in [`migration-review.md`](migration-review.md) and supersedes [`remote-access-design.md`](remote-access-design.md).
**D5 stays** (admin actions are PC-only, on a separate loopback listener).
Status: decision only, nothing built. Implementation steps are proposed as 133-140 in [`baihe-roadmap-master.md`](baihe-roadmap-master.md) section 5.

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

## Rejected or deferred

Tailscale and other VPNs; Cloudflare Tunnel + Access (revisit if large media isn't needed remotely); Authelia and other self-hosted identity servers; per-person API keys; remote key entry; a portable or dual host; a native mobile app.

## Effect on other docs and slices

- The Discover/Sources/Live spec's open question Q2 (what other devices may trigger) is answered by the permission list above: `media.import_url`, `sources.import`, `engines.paid`, `extension.send` and `media.stream` are off by default; browse, read, review, edit lines, and start and cancel jobs on media already on the PC are on. Slices S-3 to S-6 must not ship to non-local clients before the permission dependency (step 133) exists. Sign-in, proxy, pacing floors and cookies stay local-only.
- Live capture URLs (any public URL) map to `media.import_url`.
- History: option E (built-in logins, publicly exposed) was chosen on 2026-09-28, reversed the same day in favour of Tailscale, and is now chosen again on 2026-09-29 with the design above.
