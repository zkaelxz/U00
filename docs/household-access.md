# Expose Baihe to my household

How household members' phones and computers reach Baihe on the PC over the internet: Caddy on the PC terminates HTTPS and forwards to Baihe's household listener. Design: [`remote-access-decision.md`](remote-access-decision.md) ("D5: two listeners").

**Nothing in this repo opens a router port.** Forwarding a port on the router, including a brief forward for the first certificate, is a user decision, taken last: after hardening WP2 has landed and every LAN check below passes.

```
phone / laptop --https--> router :443 --> Caddy on the PC --> 127.0.0.1:<household port> (sign-in always on)
the PC's own window -----------------------------------------> 127.0.0.1:8600 (admin, never proxied)
```

Caddy never forwards to 8600 (the PC's admin listener, or whatever `BAIHE_API_PORT` is set to) or 8756 (the extension bridge), and the router never forwards those ports or the household port. Baihe enforces the household rules itself: the household listener refuses every PC-only route, and an admin signed in there holds the household permissions plus viewing the user list and the audit log, never an admin write (admin changes stay at the PC). The template's refusals are defence in depth on top of that; the only admin routes it passes are `GET`/`HEAD` `/api/admin/users` and `/api/admin/audit`.

## Before you start (prerequisites)

1. **Two-port setup** ("Migrating from single-port sign-in" in the decision doc): `BAIHE_API_AUTH=off`, `BAIHE_API_HOUSEHOLD_PORT=8610` (or any free loopback port other than 8600, 8601 and 8756). `BAIHE_API_*` values are read from the environment, not `.env`, so set it with `setx BAIHE_API_HOUSEHOLD_PORT 8610` and restart Baihe.
2. **Sign-in configured:** `BAIHE_GOOGLE_CLIENT_ID`, `BAIHE_GOOGLE_CLIENT_SECRET` and `BAIHE_PUBLIC_URL=https://baihe.<your-domain>` (environment or `.env`). Allowlist people at the PC: `python -m api grant-admin <you>`, then `python -m api add-user <email>` for each member.
3. **Hardening (work packages WP2 and WP3), merged:** the Host allowlist, the app's security headers, refusing to start the household listener without sign-in configured (WP2, #539), per-device sign-out (WP3, #543), and on the household listener an admin keeps viewing but loses every admin write and override (this branch). Monitoring (WP5) is not built yet and should land before anyone relies on this over mobile data.

## Steps only you can do, before the LAN test

These need your accounts or an administrator prompt on the PC. None of them opens anything to the internet.

1. **Domain and dynamic DNS.** Pick a name such as `baihe.<your-domain>`. Create an A record (and AAAA if your ISP gives the PC a public IPv6 address) pointing at your home IP, and set up a dynamic DNS updater (the router's built-in client or your DNS provider's) so the record follows IP changes. Use the public record only: don't add a LAN DNS entry pointing the name at the PC's private address (split DNS); the planned PC shell refuses a name that resolves to a private address.
2. **Google OAuth client.** In Google Cloud Console: OAuth consent screen (External, Testing; add each household member as a test user), then Credentials > Create OAuth client ID > Web application, with the authorised redirect URI `https://baihe.<your-domain>/api/auth/callback`. Put the client id and secret in `.env` yourself; never paste the secret into a chat, issue or PR.
3. **Caddy with the rate-limit module.** (The Windows installer bundles one, off until you run `enable-remote`; see `docs/windows-installer-design.md` section 11. It never adds the firewall rule or touches the router or DNS: it prints the `netsh advfirewall firewall add rule` command for you to run yourself.) Download Caddy for Windows from caddyserver.com/download with the package `github.com/mholt/caddy-ratelimit` added (or build it with `xcaddy build --with github.com/mholt/caddy-ratelimit`). A plain Caddy refuses to load the template. Put it at, for example, `C:\caddy\caddy.exe`. If your DNS provider has a Caddy module, you can add it too and get the real certificate by DNS challenge without forwarding any port (not in the template).
4. **Keep the PC awake:** Windows power settings, sleep "Never".

## LAN test (Claude Code on the PC can do this)

1. Check the prerequisites: `python -m api list-users`, that Baihe starts with both listeners, and that `http://127.0.0.1:8600` still opens the PC's window.
2. Set Caddy's variables (user-level; a Windows service needs them machine-level): `setx BAIHE_DOMAIN baihe.<your-domain>`, `setx BAIHE_API_HOUSEHOLD_PORT 8610`, `setx BAIHE_CADDY_LOG_DIR C:\caddy\logs`.
3. Validate the template as it is: `caddy validate --config deploy\caddy\Caddyfile.template --adapter caddyfile`. The three "Unnecessary header_up" warnings are expected: the template sets the forwarding headers explicitly so a reader can see them.
4. Run it without a public certificate. Either copy the template to `C:\caddy\Caddyfile.lan-test`, add `tls internal` as the first line inside the `{$BAIHE_DOMAIN} {` block and `skip_install_trust` inside the first (global) block, and `caddy run --config C:\caddy\Caddyfile.lan-test --adapter caddyfile` (Caddy's own local certificate; delete the copy after the test), or, with a DNS-challenge build, run the template itself with your provider's `tls { dns ... }` settings.
5. Run the checklist below from the PC and report what it saw.
6. Keep the template in step with Baihe: `tests/test_caddyfile_template.py` fails when a `local_only()` route isn't refused by the template.

## LAN checklist

On the PC (Windows `curl`; `-o NUL` discards the body). Below, `CURL` stands for
`curl --resolve baihe.<your-domain>:443:127.0.0.1 --cacert "%APPDATA%\Caddy\pki\authorities\local\root.crt"`,
which sends the request to Caddy on this PC and checks it against Caddy's local certificate (with a DNS-challenge certificate, leave out `--cacert ...`).

- [ ] `CURL -sI https://baihe.<your-domain>/` answers 200 with `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` and `Content-Security-Policy: frame-ancestors 'none'`, and no `Server` or `Via` header.
- [ ] Admin and PC-only routes are refused: `CURL -s -o NUL -w "%{http_code}\n"` for `/api/settings`, `/api/diagnostics`, `/api/library/admin/storage` and `/api/docs`, and with `-X POST` for `/api/system/shutdown` and `/api/admin/users/1/deactivate`: every one 404. `/api/admin/users` and `/api/admin/audit` without signing in answer 401 (the admin views pass the proxy).
- [ ] Uncleaned paths are refused: the same command with `--path-as-is` for `/api/../api/settings`, `/api//settings` and `/api/%2e%2e/settings`: every one 400.
- [ ] `/api/health` answers; `/api/library/dramas` without signing in answers 401.
- [ ] `http://127.0.0.1:2019/config/` doesn't connect (Caddy's admin API is off).

## Going live (only after WP2 has landed and the LAN checklist passes)

1. **Windows firewall rule**, in an administrator PowerShell, for Caddy only:
   `New-NetFirewallRule -DisplayName "Caddy for Baihe" -Direction Inbound -Program "C:\caddy\caddy.exe" -Protocol TCP -LocalPort 80,443 -Action Allow`
   Add no rule for `python.exe` or for 8600, 8610 or 8756.
2. **Router port forward:** TCP 443 and 80 to the PC's LAN address (reserve that address for the PC in the router's DHCP settings). Never forward 8600 (or your `BAIHE_API_PORT`), 8601, the household port, 8756 or 8501. Only Caddy's 443 and 80 are ever forwarded. With a DNS-challenge build you may forward 443 only.
3. **Certificate:** stop the LAN-test Caddy and run the template itself (`caddy run --config deploy\caddy\Caddyfile.template --adapter caddyfile`). Caddy gets the certificate for `BAIHE_DOMAIN` through the forward above (or by DNS challenge) and renews it by itself. There is no separate, earlier forward for the first issuance.
4. Run the outside checklist.

## Outside checklist

From outside the LAN (a phone on mobile data, Wi-Fi off), after the router port is open:

- [ ] `https://baihe.<your-domain>` loads with a valid padlock; `http://` redirects to `https://`.
- [ ] Google sign-in works for an allowlisted member and is refused for any other account.
- [ ] Job progress updates live; saving Settings says it is PC-only; an admin account signed in here sees Users and Audit log in Diagnostics without any buttons ("Account changes are made on the main PC."), and no other admin section works.
- [ ] `http://<your-public-ip>:8600`, `:8610` and `:8756` don't connect.
- [ ] After a sign-in, Caddy's access log (`BAIHE_CADDY_LOG_DIR\baihe-access.log`) holds no `?code=` or `state=`: its filter drops the query string from the logged URI, and Baihe's household listener writes no access log of its own. That covers these two logs only, so read any other log before sharing it.

Certificate renewal:

- [ ] The padlock's certificate details show an expiry date; check again after about two thirds of its lifetime (Caddy renews well before expiry). Caddy's own log records each renewal. An alert below 14 days is planned (WP5).

## Rollback

1. **Close the router port forward (443 and 80) first.**
2. Stop Caddy.
3. Remove the firewall rule, in an administrator PowerShell: `Remove-NetFirewallRule -DisplayName "Caddy for Baihe"`.
4. Sign out every session issued remotely: at the PC, Diagnostics > Users > "Sign out everywhere..." for each user (every session was issued through the household listener; the PC's own window has none).
5. Unset the household port (`setx BAIHE_API_HOUSEHOLD_PORT ""`, or remove it in System Properties > Environment Variables) and restart Baihe. The PC's window on 8600 is unaffected.
6. To shut someone out immediately at any time: `python -m api deactivate <email>` (ends their sessions).

## What to watch

- Caddy's access log: floods of 401, 404 or 429 (sign-in is limited to 60 attempts per 10 minutes per address at Caddy, and more tightly by Baihe).
- Baihe's audit log (Diagnostics, at the PC or signed in as an admin from away) for sign-ins you don't recognise.
- Certificate expiry and your public IP versus the DNS record (dynamic DNS drift).
- Updates: you own patching Caddy and Baihe. After updating Baihe, restart Caddy so a changed template is loaded.
- Windows restarts: Caddy and Baihe must come back after an update reboot (running both on boot is WP5).
