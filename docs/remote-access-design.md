# Remote access design: M8-H (built-in logins, exposed directly)

> **Status: design only, nothing built.** Written 2026-09-28 on the
> unmerged `migration/react-fastapi-foundation` branch from the user's
> decisions below. It's the proposed resolution of the roadmap's deferred
> **M8-H** for the planning session to fold in. **No port may be opened to
> the internet until everything in §8 "Before opening a port" is done.**

## 1. Decisions (from the user, 2026-09-28)

| Question | Decision |
|---|---|
| Access model | **Option E: logins built into Baihe (FastAPI), exposed directly** by opening a port. No VPN, no third-party gateway. |
| Domain | **No purchased domain.** |
| Who | The user and other household members, including from phones. |
| What remote users can do | Start, cancel, read and review, controlled by **deny-by-default permissions** (nothing is allowed until granted). |
| API process | FastAPI runs as **its own process** (D1), with the four fixes in `migration-review.md` §6 D1. |
| Admin actions (install/reset/restore) | Were waiting on M8-H (D5). The proposal is in §6 and needs confirmation. |

## 2. What gets exposed, and what never does

```
 Internet ──▶ router :443 ──▶ Caddy (TLS, limits, headers) ──▶ FastAPI 127.0.0.1:8600 ──▶ services ──▶ library/
                                                                 │
                                                                 └─ also serves the built React app (same origin)

 LAN only:   Streamlit :8501   (no login of its own -- never port-forwarded)
 Loopback:   page_server :8756 (browser extension bridge -- unchanged)
```

- **Only the FastAPI app is exposed.** Streamlit has no authentication and
  can't safely be put on the internet, so it stays LAN-only until it's
  retired. The earlier "two services to front" concern goes away: remote
  users get the React app, local users can still use Streamlit.
- **Consequence:** remote users only see screens already migrated to
  React. That makes the migration order matter more (§9).
- FastAPI keeps binding `127.0.0.1`. Caddy is the only thing listening
  on the public port. Caddy handles TLS termination, request-size limits
  (large uploads), timeouts and security headers, which are things to
  keep out of application code. Caddy runs on Windows as a single binary.

## 3. HTTPS without buying a domain

**HTTPS is not optional.** Without it, every password and session cookie
crosses the internet (and café Wi-Fi) in plain text. There are two ways
to do it with no purchase:

| | A. Free dynamic-DNS name (recommended) | B. Certificate for the bare IP address |
|---|---|---|
| What | A free subdomain from a dynamic-DNS service (e.g. `something.duckdns.org`) that follows your home IP | A Let's Encrypt certificate issued directly to your public IP address. Generally available since January 2026 ([Let's Encrypt](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)) |
| Cost | Free, nothing bought | Free |
| When your ISP changes your IP | The name updates automatically; bookmarks and phones keep working | The address everyone bookmarked stops working; a new certificate is needed for the new IP |
| Certificate life | 90 days, auto-renewed | ~6 days (IP certificates must use the short-lived profile), auto-renewed often |
| Tooling | Caddy handles it natively, including DNS updates via a plugin | Caddy's native support is uncertain ([caddy#7399](https://github.com/caddyserver/caddy/issues/7399) shows it refusing an IP certificate). A working pattern is `acme.sh` with the `shortlived` profile feeding Caddy static cert files, renewed every few days |
| Phone experience | `https://something.duckdns.org` | `https://203.0.113.7` |

**Recommendation: A.** It isn't buying a domain, and it's the only
option that survives a home IP change, which most residential
connections have. B is viable if your IP is static.

**Never:** a self-signed certificate. Phones show a scary warning every
time, and teaching household members to click through it defeats the
point of the certificate.

**Check first:** does the router get a real public IPv4 address? Many ISPs
use CGNAT (carrier-grade NAT, where many customers share one public
address), and then no port forwarding is possible at all. Test: compare
the router's WAN IP with what a "what is my IP" site shows. If they
differ, option E can't work over IPv4 without the ISP's help (or IPv6,
if every device's network supports it).

## 4. Authentication (who you are)

- **Accounts:** `users` table in `library.db`: username, Argon2id password
  hash (`argon2-cffi`, a new core dependency), disabled flag, created and
  last-login timestamps.
- **The first admin is created locally only:** `python -m api create-admin`
  on the Baihe PC. There's no "sign up" page and no bootstrap over the
  network. That prevents the classic "whoever reaches it first becomes
  admin" hole.
- **Sessions, not JWTs:** a random 256-bit token in an `HttpOnly`,
  `Secure`, `SameSite=Strict` cookie. The DB stores only its hash, plus
  expiry, idle timeout, device label, last-seen time and IP. Server-side
  sessions can be listed and revoked ("log out my lost phone"), which
  JWTs can't do cleanly.
- **Brute-force protection:** per-account and per-IP failure counters
  with exponential backoff and temporary lockout. Login errors never say
  whether the username exists.
- **Two-factor (TOTP authenticator app):** strongly recommended for
  anything on the open internet. At minimum required for admins
  (question Q2).
- **Password resets:** by an admin, locally. There's no email system, and
  "forgot password" flows are a common weak point.
- **CSRF:** SameSite=Strict cookies, plus every state-changing request must
  carry a custom header and a matching `Origin`. No CORS in production
  (already how the API behaves).
- **Household profiles (Step 26e/78):** each user is linked to one
  profile, so reading progress and notes follow the login automatically.
  **This reverses Step 78's "profiles carry no password"** for remote
  access. Flagged for the planning session.

## 5. Authorization (what you can do): deny by default

- **Permissions are named capabilities.** A user has none until granted.
  **Roles** are just saved bundles to grant quickly ("Reader", "Editor",
  "Operator", "Admin"). Individual grants and denies on top.
- **Every route must declare its required permission.** A FastAPI
  dependency checks it. A static test (the same style as the existing
  `test_static_analysis.py` timeout check) fails the build if any route
  lacks one, so a forgotten check can't ship silently. That's what makes
  "deny by default" real rather than a convention.

| Permission | Allows |
|---|---|
| `library.read` | Library list, details, stats, search |
| `reader.use` | Reader, story tools that don't spend money |
| `lines.review` | Flag, comment, mark reviewed |
| `lines.edit` | Edit lines, find/replace, merge, restore versions |
| `drama.manage` | Create and edit dramas, upload media |
| `drama.delete` | Delete (Step 43 soft-delete when it lands) |
| `jobs.start` | Start transcribe/translate/dub/export/OCR jobs |
| `engines.paid` | Use engines that cost money (a job using a paid engine needs this *and* `jobs.start`) |
| `jobs.cancel.own` | Cancel jobs you started |
| `jobs.cancel.any` | Cancel anyone's job |
| `jobs.read` | See the job list and progress |
| `settings.manage` | App settings, API keys (write-only: the value is never shown) |
| `users.manage` | Create users, grant permissions, reset passwords, revoke sessions |
| `admin.system` | Install/upgrade packages, reset library, restore backup (see §6) |

- **Spending:** paid-engine use is its own permission because a
  compromised or careless account can spend real money through your
  keys. The existing monthly cap still applies globally; a per-user cap
  is optional later.
- **Scope:** permissions start global. Per-series or per-drama scoping
  ("this person can only see these titles") is possible later, since the
  check lives in one dependency (question Q4).
- **Audit log:** logins, failures, permission changes, job
  starts/cancels, deletes and settings changes, recorded with user, IP and
  time, viewable by admins.

## 6. Admin actions (D5, now that M8-H is decided)

Installing packages runs code, and reset/restore can destroy the library.
**Proposal:** require `admin.system` **and** a recent re-entry of the
password/2FA code **and** a request from the home network (the LAN or the
PC itself), refused from the internet even for admins. It keeps the power
where the risk is lowest. Needs your confirmation (Q3).

## 7. What stays the same

- Engine API keys stay server-side only (D2). Remote users can *use*
  engines if granted `engines.paid`, but can never read a key.
- The browser extension keeps its loopback-only token bridge.
- The API error handling already never leaks tracebacks, paths or keys.

## 8. Before opening a port (hard checklist)

1. Accounts, sessions, lockout, TOTP, CSRF and audit log implemented and
   tested.
2. The every-route-has-a-permission static test passes.
3. The server refuses to start on a non-loopback address unless auth is
   enabled, so it can't be exposed accidentally without logins.
4. Caddy with a real certificate (§3), security headers (HSTS, CSP for
   the React build), upload size and timeout limits.
5. Only port 443 forwarded, to Caddy. Streamlit (8501) and 8600 never
   forwarded. The Windows firewall matches.
6. An external check from a phone on mobile data: login works; Streamlit
   and 8600 are unreachable; plain `http` redirects to `https`.
7. A written "how to revoke a lost phone / disable a user" note for the
   household.

## 9. How this changes the migration order

Remote users only get React screens, so migrate what they'll actually
use away from home first:

1. Auth, users, permissions, sessions (the prerequisite for anything remote).
2. Library (done: read) and **job monitoring/cancel** (needs the job-records table, D1 fix 1).
3. Reader (served HTML first).
4. Review and line editing.
5. Starting jobs (translate first; it's the most common remote action).
6. Everything else per `migration-review.md` §5.

## 10. Honest risk statement

Option E makes Baihe internet-facing security software. A bug in its
login or permission code would expose the whole library and the ability
to spend your API keys to anyone on the internet, and automated scanners
find new open ports within hours. The design above is built to make that
bug unlikely, and bounded when it does happen (deny-by-default,
per-route checks, 2FA, no bootstrap over the network, admin actions
LAN-only, audit log). It still means keeping Baihe, Caddy and Python up
to date becomes a real responsibility. None of this blocks adding a
gateway later (Cloudflare Access or a VPN in front): the same logins keep
working behind one.

## 11. Open questions

- **Q1.** A free dynamic-DNS subdomain (recommended) or a bare-IP
  certificate? And does the router have a real public IP (the CGNAT
  check)?
- **Q2.** 2FA for everyone, or admins only?
- **Q3.** Admin actions: LAN-only even for admins, as proposed?
- **Q4.** Global permissions to start, or per-series/per-drama from
  day one?
- **Q5.** Should people on the home network also log in to the React app
  (recommended: yes, one model everywhere), with Streamlit staying
  login-free on the LAN until it's retired?
