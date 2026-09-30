# Expose Baihe to my household

How household members' phones and computers reach Baihe on the PC over the internet: Caddy on the PC terminates HTTPS and forwards to Baihe's household listener. Design: [`remote-access-decision.md`](remote-access-decision.md) ("D5: two listeners").

**Nothing in this repo opens a router port.** Forwarding a port on the router is a user decision, taken last, after every check below passes on the LAN.

```
phone / laptop --https--> router :443 --> Caddy on the PC --> 127.0.0.1:<household port> (sign-in always on)
the PC's own window -----------------------------------------> 127.0.0.1:8600 (admin, never proxied)
```

Caddy never forwards to 8600 (the PC's admin listener) or 8756 (the extension bridge), and the router never forwards those ports or the household port.

## Before you start (prerequisites)

1. **Two-port setup** ("Migrating from single-port sign-in" in the decision doc): `BAIHE_API_AUTH=off`, `BAIHE_API_HOUSEHOLD_PORT=8610` (or any free loopback port other than 8600, 8601 and 8756). `BAIHE_API_*` values are read from the environment, not `.env`, so set it with `setx BAIHE_API_HOUSEHOLD_PORT 8610` and restart Baihe.
2. **Sign-in configured:** `BAIHE_GOOGLE_CLIENT_ID`, `BAIHE_GOOGLE_CLIENT_SECRET` and `BAIHE_PUBLIC_URL=https://baihe.<your-domain>` (environment or `.env`). Allowlist people at the PC: `python -m api grant-admin <you>`, then `python -m api add-user <email>` for each member.
3. **Hardening merged (work package WP2):** the Host allowlist, the app's security headers, and refusing the household listener without sign-in. Not on `baihe-subtitler` yet (2026-09-30). Don't open the router port before it lands. Per-device sign-out (WP3) and monitoring (WP5) should land before anyone relies on this over mobile data.

## Steps only you can do

These need your accounts, your router or an administrator prompt on the PC.

1. **Domain and dynamic DNS.** Pick a name such as `baihe.<your-domain>`. Create an A record (and AAAA if your ISP gives the PC a public IPv6 address) pointing at your home IP, and set up a dynamic DNS updater (the router's built-in client or your DNS provider's) so the record follows IP changes.
2. **Google OAuth client.** In Google Cloud Console: OAuth consent screen (External, Testing; add each household member as a test user), then Credentials > Create OAuth client ID > Web application, with the authorised redirect URI `https://baihe.<your-domain>/api/auth/callback`. Put the client id and secret in `.env` yourself; never paste the secret into a chat, issue or PR.
3. **Caddy with the rate-limit module.** Download Caddy for Windows from caddyserver.com/download with the package `github.com/mholt/caddy-ratelimit` added (or build it with `xcaddy build --with github.com/mholt/caddy-ratelimit`). A plain Caddy refuses to load the template. Put it at, for example, `C:\caddy\caddy.exe`.
4. **Certificate: automatic.** Caddy gets and renews the certificate for `BAIHE_DOMAIN` by itself. The certificate authority must reach the PC on port 80 or 443 for that, so either forward them briefly for the first issuance during the LAN test, or use a Caddy build with your DNS provider's module and a DNS challenge (not in the template).
5. **Windows firewall rule**, in an administrator PowerShell, for Caddy only:
   `New-NetFirewallRule -DisplayName "Caddy for Baihe" -Direction Inbound -Program "C:\caddy\caddy.exe" -Protocol TCP -LocalPort 80,443 -Action Allow`
   Add no rule for `python.exe` or for 8600, 8610 or 8756.
6. **Router port forward, last:** TCP 443 and 80 to the PC's LAN address (reserve that address for the PC in the router's DHCP settings). Never forward 8600, 8601, the household port, 8756 or 8501. Only after the checklist below passes on the LAN.
7. **Keep the PC awake:** Windows power settings, sleep "Never".

## Steps Claude Code on the PC can do

1. Check the prerequisites: `python -m api list-users`, that Baihe starts with both listeners, and that `http://127.0.0.1:8600` still opens the PC's window.
2. Set Caddy's variables (user-level; a Windows service needs them machine-level): `setx BAIHE_DOMAIN baihe.<your-domain>`, `setx BAIHE_API_HOUSEHOLD_PORT 8610`, `setx BAIHE_CADDY_LOG_DIR C:\caddy\logs`.
3. Validate and run the template as it is, without copying or editing it:
   `caddy validate --config deploy\caddy\Caddyfile.template --adapter caddyfile`, then `caddy run` with the same arguments. The three "Unnecessary header_up" warnings are expected: the template sets the forwarding headers explicitly so a reader can see them.
4. Run the checklist below from the PC and report what it saw.
5. Keep the template in step with Baihe: `tests/test_caddyfile_template.py` fails when a `local_only()` route isn't refused by the template.

## Verification checklist

On the PC (Windows `curl`; `-o NUL` discards the body):

- [ ] `curl -sI https://baihe.<your-domain>/` answers 200 with `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` and `Content-Security-Policy: frame-ancestors 'none'`, and no `Server` or `Via` header.
- [ ] Admin and PC-only routes are refused: `curl -s -o NUL -w "%{http_code}\n"` for `/api/settings`, `/api/diagnostics`, `/api/library/admin/storage`, `/api/admin/users` and `/api/docs`, and with `-X POST` for `/api/system/shutdown`: every one 404.
- [ ] `/api/health` answers; `/api/library/dramas` without signing in answers 401.
- [ ] The access log (`BAIHE_CADDY_LOG_DIR\baihe-access.log`) holds no `?code=` or `state=` after a sign-in.

From outside the LAN (a phone on mobile data, Wi-Fi off), after the router port is open:

- [ ] `https://baihe.<your-domain>` loads with a valid padlock; `http://` redirects to `https://`.
- [ ] Google sign-in works for an allowlisted member and is refused for any other account.
- [ ] Job progress updates live; saving Settings says it is PC-only.
- [ ] `http://<your-public-ip>:8600`, `:8610` and `:8756` don't connect.

Certificate renewal:

- [ ] The padlock's certificate details show an expiry date; check again after about two thirds of its lifetime (Caddy renews well before expiry). Caddy's own log records each renewal. An alert below 14 days is planned (WP5).

## Rollback

1. **Close the router port forward (443 and 80) first.**
2. Stop Caddy.
3. Unset the household port (`setx BAIHE_API_HOUSEHOLD_PORT ""`, or remove it in System Properties > Environment Variables) and restart Baihe. The PC's window on 8600 is unaffected.
4. To shut someone out immediately at any time: `python -m api deactivate <email>` (ends their sessions).

## What to watch

- Caddy's access log: floods of 401, 404 or 429 (sign-in is limited to 60 attempts per 10 minutes per address at Caddy, and more tightly by Baihe).
- Baihe's audit log (at the PC) for sign-ins you don't recognise.
- Certificate expiry and your public IP versus the DNS record (dynamic DNS drift).
- Updates: you own patching Caddy and Baihe. After updating Baihe, restart Caddy so a changed template is loaded.
- Windows restarts: Caddy and Baihe must come back after an update reboot (running both on boot is WP5).
