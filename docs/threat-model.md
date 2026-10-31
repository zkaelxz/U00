# Threat model

One page: what Baihe exposes, which layer stops what, where the PC-only boundary sits, and what is deliberately left undefended. The detail and the reasons live in [`remote-access-decision.md`](remote-access-decision.md) and the `remote-access-*.md` files it lists (auth, listeners, sharing), [`route-permissions.md`](route-permissions.md) (every route and its declaration) and [`browser-extension.md`](browser-extension.md) (the extension bridge). When this page and the code disagree, the code wins; fix the page.

## What is exposed

| Surface | Bound to | Who can reach it | Default |
|---|---|---|---|
| Admin listener (FastAPI + the React app) | `127.0.0.1:8600` (`BAIHE_API_PORT`) | Direct loopback requests on the PC only | On |
| Household listener | `127.0.0.1:BAIHE_API_HOUSEHOLD_PORT` (suggested 8610), reached through the owner's reverse proxy (Caddy) | Signed-in, allowlisted household members | Off (port unset) |
| Extension bridge (`page_server.py`) | `127.0.0.1:8756` | The browser extension on the PC, with the bridge token | Off (Settings toggle) |
| Extension routes (`/api/ext-bridge/*`) | The household listener, through Caddy | A member's extension, with a per-device bearer token and `extension.send` | On with the household listener |
| Outbound fetches | The home IP | Sites, engine APIs, model hubs, Google sign-in | Per feature |
| Files on disk | The library folder and `.env` | Anyone with the PC's user account | n/a |

A non-loopback `BAIHE_API_HOST` is refused at startup, and nothing in the repo opens a router port; forwarding one to the proxy is a step only the owner takes.

## Layers, outside in

1. **Bind address.** Every listener binds to loopback. `page_server.py` checks the peer address again on every request.
2. **Request gates on the admin listener.** `LoopbackOnlyGate` answers 403 to a non-loopback peer, Host or Origin and to any proxy or forwarding header, on every path. That also stops DNS rebinding (the Host is the attacker's name). With sign-in off every `/api` POST/PUT/PATCH must be JSON or carry `X-Baihe-Local: 1`, so a page on another loopback port can't send a no-CORS "simple" request.
3. **Host check on the household listener.** Only the `BAIHE_PUBLIC_URL` host is answered; any other Host is a 400 before routing.
4. **Sign-in.** Google OIDC with PKCE, state and nonce, `email_verified` required, matched by `sub` against an allowlist (`services/oidc_service.py`). Server-side sessions in a `__Host-` Secure HttpOnly cookie, a fresh session on every login, CSRF header on every non-GET, login routes rate-limited, sign-ins and admin actions audit-logged.
5. **Permissions.** Deny by default: every route declares exactly one of `require_permission(...)`, `public_route()` or `local_only()` (`tests/test_api_permissions.py`). Risky permissions (`media.import_url`, `sources.import`, `engines.paid`, `extension.send`, `media.stream`) are off for household members; admin permissions come only from the admin flag.
6. **Ownership.** Services check that a title or series belongs to the caller or was shared with them (`services/ownership_service.py`).
7. **Bounds.** URL fetches go through the public-address check on every redirect hop (`lib/url_guard.py`, `lib/http.py`); bodies, uploads, restores and images are size-capped; every outbound call has a timeout; per-user caps on running and queued jobs and open media streams.
8. **Secrets.** Engine keys are entered at the PC, written to `.env` and never returned; they travel in headers, never URLs; error text passes through `redact_secrets` before it is shown, stored or logged. API responses carry no keys, filesystem paths or fetched URLs. Device tokens and session ids are stored hashed; a member's own Ollama key is stored encrypted.

## The PC-only boundary

`local_only()` routes (deletes, settings, key writes, installs, shutdown, running a program on the PC) need a direct loopback request with no proxy headers and a loopback Host and Origin. On the household listener they refuse every request: nothing there is ever "the PC". This is why the two-port setup is the supported way to go remote: with sign-in on a single port behind a proxy, the PC is told from other devices by request headers alone, and the server warns about it at every start.

The one opening in that boundary is admin uploads (`media.upload`, `api/remote_uploads.py`): while the PC's "Allow uploads from away" switch is on (Developer Mode, 7 days at most), an admin session from away may upload files into titles it may change. A stolen admin cookie in that window can fill the disk with repeated uploads (each held to the Settings upload limit) and replace a title's media (the old file is kept); it cannot delete anything or make the PC fetch or run anything.

The other opening is managing member accounts from away (`admin.users` on the household listener, owner decision 2026-10-30): a stolen admin cookie can give or take a member's rights (paid engines and the browser extension included), sign members out and deactivate them, and remove a member's own Ollama key. It cannot change an admin account (each such change is 403 away from the PC, so it can't lock the owner out), add people, or reach Settings, keys, Diagnostics or backups; every change is in the activity log under that admin.

## Deliberately not defended

- **Anything already running as the PC's user.** Any local program can call the admin listener (with sign-in off, the default, every loopback request is the owner) and read `.env`, `library.db`, the bridge token and saved site sign-ins. The OS account is the boundary.
- **A household member's compromised Google account.** It gets that member's permissions, which is why the risky ones are off by default.
- **Hostile media and pages.** ffmpeg, OCR, PDF and image decoders parse what sites send. Live capture limits ffmpeg to piped input and a demuxer whitelist; the decoders themselves are trusted.
- **Denial of service.** Only sign-in is rate-limited. There is no bandwidth cap per user, and the extension bridge, on the PC and through `/api/ext-bridge/*` for members, has no concurrency cap.
- **Egress from the home IP.** Imports and live capture fetch any public host from the owner's address, including the home's own WAN address on a router with hairpin NAT. A DNS answer that changes between the check and a configured proxy's own lookup is not caught.
- **The owner's own choices at the PC.** Program paths, installed tools (PhantomJS, lightnovel-crawler), models and Python packages the owner installs run with the owner's rights.
- **Physical access and backups.** Backup zips leave out sign-ins, tokens and keys, but hold the whole library; where the owner keeps them is up to the owner.
- **Google and the proxy.** A Google outage blocks new sign-ins; the reverse proxy's certificate and patching are the owner's.
