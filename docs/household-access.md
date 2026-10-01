# Expose Baihe to my household

How household members' phones and computers reach Baihe on the PC over the internet: Caddy on the PC terminates HTTPS and forwards to Baihe's household listener. Design: [`remote-access-decision.md`](remote-access-decision.md) ("D5: two listeners"). This page starts with the installed-service path (Setup with "Run Baihe Studio in the background" ticked, which bundles Caddy); the manual flow with your own Caddy is at the end, under "Without the installer".

**Nothing in Baihe or its installer opens a router port or adds a firewall rule.** Turning remote access on starts Caddy and the household listener on this PC and prints the firewall command. Forwarding a port on the router is your decision, taken last.

```
phone / laptop --https--> router :443 --> Caddy on the PC --> 127.0.0.1:<household port> (sign-in always on)
the PC's own window -----------------------------------------> 127.0.0.1:8600 (admin, never proxied)
```

Baihe enforces the household rules itself: the household listener refuses every PC-only route, and an admin signed in there holds the household permissions plus viewing the user list and the audit log, never an admin write (admin changes stay at the PC). The Caddy template's refusals are defence in depth on top of that; the only admin routes it passes are `GET`/`HEAD` `/api/admin/users` and `/api/admin/audit`.

## Set up remote access (installed service)

Do the steps in this order. Steps 1 to 4 change nothing on the internet side; step 5 starts Caddy; steps 6 and 7 are what actually let traffic in.

1. **Domain and dynamic DNS.** Pick a name such as `baihe.<your-domain>`. Create an A record (and an AAAA record if your ISP gives the PC a public IPv6 address) pointing at your home IP, and set up a dynamic DNS updater (the router's built-in client or your DNS provider's) so the record follows IP changes. Use the public record only: don't add a LAN DNS entry pointing the name at the PC's private address (split DNS). `BAIHE_PUBLIC_URL` must be an ASCII DNS name: not an IP address, not `localhost` (a non-ASCII name in its `xn--` form).
2. **Google OAuth client.** In Google Cloud Console: set up the OAuth consent screen (External, Testing; add each household member as a test user), then Credentials > Create OAuth client ID > Web application, with the authorised redirect URI `https://<domain>/api/auth/callback`. Never paste the client secret into a chat, an issue or a PR.
3. **Put the sign-in settings in the data folder's `.env`.** The installed copy's data folder is the one you chose in Setup (default `%LOCALAPPDATA%\Baihe Studio`); `.env` is the file directly in it. Add:
   ```
   BAIHE_GOOGLE_CLIENT_ID=<client id>
   BAIHE_GOOGLE_CLIENT_SECRET=<client secret>
   BAIHE_PUBLIC_URL=https://<domain>
   ```
   `BAIHE_PUBLIC_URL` is just `https://` and the name: no port, path or user name. These three are the only settings "turn remote access on" reads from `.env`. Baihe itself reads the same three from `.env` first and the environment second, but the service sets every `BAIHE_API_*` value (the household port, the PC's port, auth off) itself, so `setx` of those has no effect on it.
4. **Allowlist people, at the PC.** Only allowlisted emails can sign in. In a command prompt, run the install's own Python (in the program folder, `%LOCALAPPDATA%\Programs\Baihe Studio` by default, or wherever Setup put it; written `python` below, meaning `"<program folder>\python\python.exe"`): `python -m api grant-admin <your-email> for yourself, then `python -m api add-user <email> [--name "Display name"]` for each member. `python -m api list-users` shows the list; `python -m api deactivate <email>` blocks someone and ends their sessions.
5. **Turn remote access on.** Start menu > "Baihe Studio service" (it asks for administrator rights) > 3, "Turn remote access on". It asks for a household port (Enter for 8610). The command-line equivalent, from an administrator prompt, is `"%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" enable-remote [--household-port N]`; the menu exists so nobody has to type that. It checks first and changes nothing (exit code 2) when:
   - `BAIHE_GOOGLE_CLIENT_ID`, `BAIHE_GOOGLE_CLIENT_SECRET` or `BAIHE_PUBLIC_URL` is missing from the data folder's `.env`;
   - `BAIHE_PUBLIC_URL` isn't `https://` plus a DNS name (a port, path, query or user name, an IP address or `localhost`): Caddy answers on 443 and gets the certificate for that name;
   - the household port isn't a whole number from 1024 to 65535, is one of Baihe's own ports (8501, 8600, 8601, 8756) or the service's own port, or another program already uses it;
   - Baihe isn't installed as a service, or the install is missing Caddy or its template (run Setup again).

   Otherwise it restarts Baihe's service with the household listener on 127.0.0.1, writes Caddy's config for your domain and household port, and starts the `BaiheCaddy` service (set to start at boot). If the household listener or Caddy doesn't come up, it undoes its changes. It then prints your next two steps.
6. **Add the Windows Firewall rule it prints** (it never adds it): in an administrator prompt, the printed `netsh advfirewall firewall add rule name="Baihe Studio remote access - Caddy HTTPS" dir=in action=allow protocol=TCP localport=443 program="<caddy.exe>" profile=private,domain enable=yes`. That is TCP 443, for Caddy's program only, on private and domain networks. It applies only if Windows classes the PC's network as Private (or Domain). Add no rule for `python.exe` or for 8600, the household port or 8756. Menu item 1 (status) shows whether the rule exists.
7. **Forward TCP 443 on the router** to the PC's LAN address; reserve that address for the PC in the router's DHCP settings so it doesn't change. Forward only 443 (see the table below). Also keep the PC awake (Windows power settings, sleep "Never").
8. **Check it.** Once the name points at your home and 443 reaches the PC, Caddy asks for its certificate by itself and renews it by itself. Run the "Outside checklist" below from a phone on mobile data. In Baihe, a banner and Diagnostics show remote-access health: the certificate's days left (warns under 14, critical under 5, expired or untrusted), whether the household listener answers, and, if you set a public-address check URL in Settings > Remote access, whether the DNS record matches your public IP. Turn it off with the Start-menu item (4, "Turn remote access off") or `disable-remote`; see "Rollback".

## Which ports, and which are opened

| Port | What it is | Who can reach it | Open or forward |
|---|---|---|---|
| 443 | Caddy's HTTPS, the only public entrance | the internet, once you add the firewall rule and the router forward | firewall rule (step 6) and router forward (step 7). Fixed: `BAIHE_PUBLIC_URL` must not name a port, and `enable-remote` refuses one |
| 80 | Caddy also listens here by default, for the http-to-https redirect and the certificate authority's HTTP check | nothing, unless you open it | **Not forwarded, and not opened by the printed rule.** Caddy can also prove the name over 443 (the TLS-based challenge). That is Caddy's and ACME's expected behaviour; it has not been tested on this setup. The only effect of leaving 80 closed should be no `http://` to `https://` redirect |
| 8610 | default household port: Baihe's household listener (sign-in always on) | this PC only (127.0.0.1), behind Caddy | never forward. Change it with `enable-remote --household-port N` or the menu |
| 8600 | default PC port: the PC's own window and API, auth off | this PC only (127.0.0.1) | never forward. Change it with "Change Baihe Studio's port" in the menu (`set-port`) |
| 8601 | the documented alternative PC port | this PC only | never forward; the household port can't be 8601 |
| 8756 | browser-extension bridge | this PC only (127.0.0.1); fixed | never forward |
| 8501 | legacy Streamlit port (Streamlit is gone) | nothing | still reserved, so the household port can't take it |
| 5173 | Vite dev server, developers only | this PC only | never forward |
| 2019 | Caddy's admin API | nothing: `admin off` in the template | never forward |

Changing 443 is not supported today. It would take a Caddy setting for the HTTPS port in the Caddyfile, the same port in `BAIHE_PUBLIC_URL` (which `enable-remote` currently refuses), the firewall command, the router forward and the Google redirect URI. Only consider it if your ISP blocks inbound 443.


## Outside checklist

From outside the LAN (a phone on mobile data, Wi-Fi off), after the router port is open:

- [ ] `https://baihe.<your-domain>` loads with a valid padlock; `http://` redirects to `https://` only if port 80 reaches Caddy, which this guide doesn't open (see the ports table); without it, type `https://`.
- [ ] Google sign-in works for an allowlisted member and is refused for any other account.
- [ ] Job progress updates live; saving Settings says it is PC-only; an admin account signed in here sees Users and Audit log on the Admin page (cogwheel menu) without any buttons ("Account changes are made on the main PC."), and no other admin section works.
- [ ] `http://<your-public-ip>:8600`, `:8610` and `:8756` don't connect.
- [ ] After a sign-in, Caddy's access log (`BAIHE_CADDY_LOG_DIR\baihe-access.log`) holds no `?code=` or `state=`: its filter drops the query string from the logged URI, and Baihe's household listener writes no access log of its own. That covers these two logs only, so read any other log before sharing it.

Certificate renewal:

- [ ] The padlock's certificate details show an expiry date; check again after about two thirds of its lifetime (Caddy renews well before expiry). Caddy's own log records each renewal. Baihe's remote-access health check reads the certificate's days left and warns under 14 days, and raises a critical alert under 5, expired or untrusted.

## If it doesn't work from outside

Check these first; they are the usual causes.

- **The network is "Public" in Windows.** The firewall rule `enable-remote` prints covers Private and Domain networks only. Home networks are sometimes labelled Public: in Windows Settings > Network & internet, open the connection and set the network profile to Private (`status` shows whether the rule exists).
- **The router forward points at the wrong address.** Forward TCP 443 to the PC's LAN address, and reserve that address for the PC in the router so it doesn't change.
- **Your provider shares one public address between customers (carrier-grade NAT).** Port forwarding can never work then. If the router's WAN address differs from what a "what is my IP" site shows (or is in 100.64.0.0 to 100.127.255.255), ask the provider for a public address.
- **The name doesn't point at your current public address.** Check the DNS record against your public IP; the dynamic DNS updater may not be running.
- **Something else uses port 443 on the PC.** `enable-remote` refuses with a message if Caddy can't take it.
- **UDP is not needed.** Caddy also listens on UDP 443 for HTTP/3; browsers fall back to TCP, which is the only rule you add.

## Rollback

1. **Close the router port forward (TCP 443) first.**
2. Turn remote access off: Start menu > "Baihe Studio service" > 4, "Turn remote access off", or `disable-remote` with the long path from step 5 above, from an administrator prompt. It disables the Caddy service (so it doesn't come back at boot), stops it, removes its Caddyfile, and restarts Baihe without the household listener. The PC's window on 8600 is unaffected. Setting or unsetting `BAIHE_API_*` variables with `setx` does nothing on a service install, because the service sets every one of them itself.
3. Remove the firewall rule it names, in an administrator prompt: `netsh advfirewall firewall delete rule name="Baihe Studio remote access - Caddy HTTPS"` (the command is printed if the rule is still there).
4. Sign out every session issued remotely: at the PC, Diagnostics > Users > "Sign out everywhere..." for each user (every session was issued through the household listener; the PC's own window has none).
5. To shut someone out immediately at any time: `python -m api deactivate <email>` (ends their sessions; `python` is the install's own, as in step 4).

Without the installer: stop Caddy, remove your own firewall rule, then unset the household port (`setx BAIHE_API_HOUSEHOLD_PORT ""`, or remove it in System Properties > Environment Variables) and restart Baihe.

## What to watch

- Caddy's access log: floods of 401, 404 or 429 (sign-in is limited to 60 attempts per 10 minutes per address at Caddy, and more tightly by Baihe).
- Baihe's audit log (Diagnostics, at the PC or signed in as an admin from away) for sign-ins you don't recognise.
- Certificate expiry and your public IP versus the DNS record (dynamic DNS drift).
- Sign-in limit: Caddy counts attempts per connecting address. With a router that loops LAN traffic back through its public address, every device on your LAN shares one count, so one device can lock the others out of sign-in for up to 10 minutes.
- Updates: you own patching Baihe. On a service install Caddy is bundled, so its fixes arrive with new Baihe releases. After updating Baihe, restart Caddy so a changed template is loaded.
- Windows restarts: on a service install Baihe and Caddy both start at boot; check that they come back after an update reboot. With your own Caddy, starting it at boot is yours to set up.

## Without the installer

The manual flow, for a copy of Baihe that isn't the installed service, or to try Caddy's template by hand. The checklists above apply to it too. With your own Caddy, Windows sets nothing for you: the variables, the Caddyfile, the service, the firewall rule and the router are all yours.

### Before you start (prerequisites)

1. **Two-port setup** ("Migrating from single-port sign-in" in the decision doc): `BAIHE_API_AUTH=off`, `BAIHE_API_HOUSEHOLD_PORT=8610` (or any free loopback port other than 8600, 8601 and 8756). `BAIHE_API_*` values are read from the environment, not `.env`, so set it with `setx BAIHE_API_HOUSEHOLD_PORT 8610` and restart Baihe.
2. **Sign-in configured:** `BAIHE_GOOGLE_CLIENT_ID`, `BAIHE_GOOGLE_CLIENT_SECRET` and `BAIHE_PUBLIC_URL=https://baihe.<your-domain>` (environment or `.env`). Allowlist people at the PC: `python -m api grant-admin <you>`, then `python -m api add-user <email>` for each member.
3. **Hardening (work packages WP2 and WP3), merged:** the Host allowlist, the app's security headers, refusing to start the household listener without sign-in configured (WP2, #539), per-device sign-out (WP3, #543), and on the household listener an admin keeps viewing but loses every admin write and override (all merged). The certificate, DNS and listener health banner is built (see step 8 of the setup).

### Steps only you can do, before the LAN test

These need your accounts or an administrator prompt on the PC. None of them opens anything to the internet.

1. **Domain and dynamic DNS.** Pick a name such as `baihe.<your-domain>`. Create an A record (and AAAA if your ISP gives the PC a public IPv6 address) pointing at your home IP, and set up a dynamic DNS updater (the router's built-in client or your DNS provider's) so the record follows IP changes. Use the public record only: don't add a LAN DNS entry pointing the name at the PC's private address (split DNS); the planned PC shell refuses a name that resolves to a private address.
2. **Google OAuth client.** In Google Cloud Console: OAuth consent screen (External, Testing; add each household member as a test user), then Credentials > Create OAuth client ID > Web application, with the authorised redirect URI `https://baihe.<your-domain>/api/auth/callback`. Put the client id and secret in `.env` yourself; never paste the secret into a chat, issue or PR.
3. **Caddy with the rate-limit module.** (The Windows installer bundles one, off until you run `enable-remote`, or "Turn remote access on" in the Start-menu item "Baihe Studio service"; see `docs/windows-installer-design.md` section 11. It never adds the firewall rule or touches the router or DNS: it prints the `netsh advfirewall firewall add rule` command for you to run yourself.) Download Caddy for Windows from caddyserver.com/download with the package `github.com/mholt/caddy-ratelimit` added (or build it with `xcaddy build --with github.com/mholt/caddy-ratelimit`). A plain Caddy refuses to load the template. Put it at, for example, `C:\caddy\caddy.exe`. If your DNS provider has a Caddy module, you can add it too and get the real certificate by DNS challenge without forwarding any port (not in the template).
4. **Keep the PC awake:** Windows power settings, sleep "Never".

### LAN test (Claude Code on the PC can do this)

1. Check the prerequisites: `python -m api list-users`, that Baihe starts with both listeners, and that `http://127.0.0.1:8600` still opens the PC's window.
2. Set Caddy's variables (user-level; a Windows service needs them machine-level): `setx BAIHE_DOMAIN baihe.<your-domain>`, `setx BAIHE_API_HOUSEHOLD_PORT 8610`, `setx BAIHE_CADDY_LOG_DIR C:\caddy\logs`.
3. Validate the template as it is: `caddy validate --config deploy\caddy\Caddyfile.template --adapter caddyfile`. The three "Unnecessary header_up" warnings are expected: the template sets the forwarding headers explicitly so a reader can see them.
4. Run it without a public certificate. Either copy the template to `C:\caddy\Caddyfile.lan-test`, add `tls internal` as the first line inside the `{$BAIHE_DOMAIN} {` block and `skip_install_trust` inside the first (global) block, and `caddy run --config C:\caddy\Caddyfile.lan-test --adapter caddyfile` (Caddy's own local certificate; delete the copy after the test), or, with a DNS-challenge build, run the template itself with your provider's `tls { dns ... }` settings.
5. Run the checklist below from the PC and report what it saw.
6. Keep the template in step with Baihe: `tests/test_caddyfile_template.py` fails when a `local_only()` route isn't refused by the template.

### LAN checklist

On the PC (Windows `curl`; `-o NUL` discards the body). Below, `CURL` stands for
`curl --resolve baihe.<your-domain>:443:127.0.0.1 --cacert "%APPDATA%\Caddy\pki\authorities\local\root.crt"`,
which sends the request to Caddy on this PC and checks it against Caddy's local certificate (with a DNS-challenge certificate, leave out `--cacert ...`).

- [ ] `CURL -sI https://baihe.<your-domain>/` answers 200 with `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` and `Content-Security-Policy: frame-ancestors 'none'`, and no `Server` or `Via` header.
- [ ] Admin and PC-only routes are refused: `CURL -s -o NUL -w "%{http_code}\n"` for `/api/settings`, `/api/diagnostics`, `/api/library/admin/storage` and `/api/docs`, and with `-X POST` for `/api/system/shutdown` and `/api/admin/users/1/deactivate`: every one 404. `/api/admin/users` and `/api/admin/audit` without signing in answer 401 (the admin views pass the proxy).
- [ ] Uncleaned paths are refused: the same command with `--path-as-is` for `/api/../api/settings`, `/api//settings` and `/api/%2e%2e/settings`: every one 400.
- [ ] `/api/health` answers; `/api/library/dramas` without signing in answers 401.
- [ ] `http://127.0.0.1:2019/config/` doesn't connect (Caddy's admin API is off).

### Going live (only after WP2 has landed and the LAN checklist passes)

1. **Windows firewall rule**, in an administrator PowerShell, for Caddy only:
   `New-NetFirewallRule -DisplayName "Caddy for Baihe" -Direction Inbound -Program "C:\caddy\caddy.exe" -Protocol TCP -LocalPort 443 -Action Allow`
   Add no rule for `python.exe` or for 8600, 8610 or 8756. This opens 443 only: Caddy's http-to-https redirect on port 80 then doesn't work (see the ports table).
2. **Router port forward:** TCP 443 to the PC's LAN address (reserve that address for the PC in the router's DHCP settings). Never forward 8600 (or your `BAIHE_API_PORT`), 8601, the household port, 8756 or 8501. Only Caddy's 443 is forwarded.
3. **Certificate:** stop the LAN-test Caddy and run the template itself (`caddy run --config deploy\caddy\Caddyfile.template --adapter caddyfile`). Caddy gets the certificate for `BAIHE_DOMAIN` through the forward above (the TLS-based challenge over 443, expected but untested here, or a DNS challenge) and renews it by itself. There is no separate, earlier forward for the first issuance.
4. Run the outside checklist.
