# Step 141 spec: the PC shell and "This PC" / "Connect to my PC"

Status: spec only, nothing built (2026-09-30). Needs Step 140 (Caddy, LAN test, router port) for "Connect"; "This PC" does not.

## What Step 141 is (reading of the roadmap)

The roadmap row (`docs/archive/baihe-roadmap-master.md`, row 141) asks for a spec of:

- a **standalone PC shell**: a desktop window (pywebview or Tauri) with an icon, a tray entry and the installer, running the local API;
- a **connection toggle** in the shell: "This PC" (local, no login) or "Connect to my PC" (server URL plus Google sign-in through Caddy);
- **phones** stay a PWA client (Step 138) with only "Connect"; a Capacitor wrapper can come later; offline or standalone phone use is deferred.

The remote-access plan (2026-09-30) adds: "This PC" talks to the admin listener `127.0.0.1:8600`; "Connect" stores an https origin only and loads it top-level; Google sign-in runs in the system browser with a one-time code handed back to the shell (to be designed and reviewed on Opus); a Connect shell never talks to the admin port.

## Modes

| | This PC | Connect to my PC |
|---|---|---|
| Loads | `http://127.0.0.1:<admin port>/` top-level (8600, or the port the launcher chose) | the stored `https://<host>` origin, top-level |
| Starts the local API | yes, exactly as `installer/launcher.py` does (health probe, start, stop on exit via its clean-stop path) | never; it doesn't probe or open any loopback Baihe port |
| Sign-in | none (admin listener, off mode, local owner) | Google, in the system browser, then a one-time code handoff |
| Who uses it | the owner at the PC | a household member's own computer (and the owner away from home) |

The mode is a shell setting in the shell's own per-user config file, not in Baihe's database or `.env`. Each mode has its own web storage folder (WebView2 user data folder), so cookies and local storage never cross between them.

## Security requirements (must hold)

1. **No bridge into remote content.** In Connect mode the page is remote: expose no JavaScript API to it (pywebview `js_api` unset; Tauri: no capabilities for the remote origin). This PC mode exposes none either unless a later task needs one, reviewed on its own.
2. **Origin rules for Connect.** Store only `scheme://host[:port]`: https only; no path, query, fragment or userinfo; a DNS name, not an IP literal; never `localhost`, `*.localhost` or a name that resolves to a loopback, private or link-local address at save time. Navigation outside the stored origin opens in the system browser, except the sign-in hop below.
3. **Connect never reaches the admin listener.** A Connect-mode shell starts no API and blocks navigation and requests to `127.0.0.1`, `::1` and `localhost` on any port other than its own one-shot handoff listener (below). The admin listener already refuses proxied or non-loopback requests (`LoopbackOnlyGate`); this is the shell-side half.
4. **Top-level, never framed.** Both modes load the app as the top-level document (framing is refused: the Caddy template sends `X-Frame-Options: DENY` and `frame-ancestors 'none'`, and hardening WP2 adds them in the app), so its cookies are first-party and `__Host-` cookies work.
5. **Sign-in never in the embedded webview.** Google refuses embedded user agents and an embedded login would expose the Google password to the shell. The shell opens the system browser.

## Sign-in handoff (proposal; design and review on Opus before building)

Based on RFC 8252 (native apps, loopback redirect, PKCE):

1. The shell makes a random verifier `V` and `C = BASE64URL(SHA256(V))`, and opens a one-shot listener on `127.0.0.1:<random port>` (the shell's own, never Baihe's).
2. It opens the system browser at `https://<origin>/api/auth/login?handoff=<C>&return_port=<port>`. The server accepts only a return of exactly `http://127.0.0.1:<port>/baihe-handoff` (no host or path from the client, so no open redirect).
3. After the normal Google callback succeeds, the server sets no session in the browser. It creates a one-time code (random, single use, 60 s, bound to `C` and the user) and redirects the browser to the loopback return with `?code=`.
4. The shell takes the code, closes its listener, and navigates its webview top-level to a new public route, `GET https://<origin>/api/auth/handoff?code=&verifier=V`. The server checks `SHA256(V) == C`, burns the code, issues a fresh session with the usual `__Host-` cookies in the webview and redirects to `/`.
5. The handoff route is `public_route()`, rate-limited and audited like login and callback, and gets a row in the route table. A code without `V` is useless; `V` never leaves the shell except in that one TLS request. The Caddy template already logs no query strings.

Open for the review: GET versus a POST form for step 4; how the SameSite=Strict CSRF cookie behaves after the redirect in WebView2; a custom URL scheme (`baihe://`) instead of loopback (rejected for now: any app can claim a scheme on Windows).

## Shell choice

- **pywebview** (WebView2 on Windows): Python, fits the bundled Python and `installer/` (one more wheel); small change to the launcher. Tray needs another package (for example `pystray`). Recommended for a first build.
- **Tauri**: a separate Rust binary with Python as a sidecar; a stricter capability model and auto-update, but a Rust toolchain in CI and a larger installer change.

Either way, a new optional dependency is registered in `diagnostics.OPTIONAL_DEPENDENCIES` in the same change, and the browser-window fallback in `launcher.py` stays for machines without WebView2.

## Acceptance criteria

- This PC: the shell opens the app from the installer's shortcut, starts and stops the API as the launcher does today, shows a tray icon (open, stop), and never needs sign-in.
- Connect: saving an origin refuses http, IP literals, loopback, private names, paths and credentials; the app loads top-level; sign-in completes through the system browser; the admin port is never contacted (tested with a fake server on the admin port that fails the test if hit).
- The handoff: a replayed, expired or wrong-verifier code is refused and audited; a return URL other than the loopback form is refused; `tests/test_api_permissions.py` passes with the new route.
- Switching modes keeps each mode's cookies separate; signing out in Connect doesn't touch This PC.

## Work packages (proposed)

1. Shell for This PC (pywebview window, tray, installer shortcut). No server change.
2. Connect mode without sign-in changes (origin rules, separate storage, navigation allowlist, no JS API).
3. Handoff server routes and shell listener (Opus design, then Opus security review).
4. Installer and docs (the installer design doc, `household-access.md` for members' computers).

## Out of scope

A native phone app (Capacitor is deferred), offline or standalone phone use, remote admin (stays PC-only per D5), and moving a library between machines (Steps 142-143).
