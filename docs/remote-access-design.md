# Remote access design: M8-H (Tailscale + Tailscale Serve)

> **Status: design only, nothing built.** Written 2026-09-28 on the
> unmerged `migration/react-fastapi-foundation` branch from the user's
> decisions. It's the proposed resolution of the roadmap's deferred
> **M8-H** for the planning session to fold in.
>
> **History:** an earlier version of this file (same day) designed
> option E, logins built into Baihe with a port opened to the internet.
> The user reversed that once it was clear E is the *highest-effort*
> option (all of a self-run public server's upkeep, plus building
> security software into Baihe). The chosen option is **A: Tailscale.**

## 1. Decisions (user, 2026-09-28)

| Question | Decision |
|---|---|
| Access model | **Tailscale** (a private network of the household's devices) with **Tailscale Serve** publishing Baihe to it over HTTPS. Nothing is exposed to the public internet; no router port is opened. |
| Priority | Lowest effort that meets the needs below. |
| Domain | None purchased. Tailscale provides the name and HTTPS certificate (`<machine>.<tailnet>.ts.net`). |
| Who | The user and other household members, including from phones. Each installs the Tailscale app once and signs in. |
| What they can do | Start, cancel, read and review, controlled by **deny-by-default permissions** in Baihe. |
| API process | FastAPI runs as its own process (D1), with the four fixes in `migration-review.md` §6 D1. |

## 2. Topology

```
 phone / laptop (Tailscale app, signed in)
        │  encrypted Tailscale connection (works through CGNAT, no open ports)
        ▼
 Baihe PC: tailscaled ── Tailscale Serve (HTTPS, automatic ts.net certificate)
        │   https://baihe.<tailnet>.ts.net/        ──▶ FastAPI 127.0.0.1:8600 (+ built React app)
        │   https://baihe.<tailnet>.ts.net:8443/   ──▶ Streamlit 127.0.0.1:8501 (while it still exists)
        ▼
   services ──▶ library/

 Loopback only: page_server :8756 (browser extension bridge, unchanged)
```

Setup is one-time commands on the Baihe PC (exact flags to be confirmed
against the installed Tailscale version when it's built):

```
tailscale serve --bg --https=443  http://127.0.0.1:8600
tailscale serve --bg --https=8443 http://127.0.0.1:8501
```

Serve terminates HTTPS with a certificate Tailscale issues and renews for
the machine's `ts.net` name. There's no certificate tooling and no
dynamic DNS to run.

**One origin for the new app.** FastAPI serves the built React app
(`frontend/dist`) *and* handles `/api/*`, so Serve proxies to a single
local service on 443. There are no cross-origin requests and no CORS in
production, and the session never spans two hosts. This isn't built yet:
the foundation serves React from `vite preview` in development only. It's
on the checklist (§8). Streamlit stays a separate service on 8443,
owner-only (§5), until it's retired.

## 3. Identity: who is making the request

- When a request arrives through Serve from a tailnet member, Serve adds
  `Tailscale-User-Login` (e.g. `alice@gmail.com`) and `Tailscale-User-Name`,
  and strips any copies a client tried to send itself
  ([Tailscale Serve docs](https://tailscale.com/docs/features/tailscale-serve)).
  Baihe takes the user's identity from `Tailscale-User-Login`.
- **Logins, passwords and 2FA are Tailscale's (i.e. the person's own
  Google/Apple/Microsoft/GitHub account and its 2FA).** Baihe builds none
  of that: no password storage, no lockout, no password reset, no
  session cookies.
- **Hard rules that make the header trustworthy.** Each needs a test.
  1. The API listens on `127.0.0.1` only. Anyone who could reach it
     directly could type their own header. The server refuses to start
     on a non-loopback address unless it's in explicit local development
     mode.
  2. **Every request without `Tailscale-User-Login` is rejected (401)**,
     except `/api/health`. This also blocks **Tailscale Funnel** (public
     sharing), whose traffic carries no identity headers. Funnel must
     never be turned on for Baihe anyway; the Diagnostics check will warn
     if it is.
  3. "Came from localhost" is **not** trust. Serve's own requests come
     from localhost too. Even the owner, sitting at the PC, uses the
     `ts.net` URL (the PC is on the tailnet).
  4. Local development (`npm run dev` with no Tailscale) uses an explicit
     `BAIHE_API_DEV_USER` override, honoured only with
     `BAIHE_API_ENV=development` on a loopback bind.

## 4. Permissions: deny by default (Baihe's part)

- `users` table in `library.db`, keyed by Tailscale login. A tailnet
  member Baihe doesn't know yet gets **no permissions** and sees "Ask
  the owner for access". They're recorded, so an admin can grant access
  with one click.
- **The first admin is granted locally:** `python -m api grant-admin
  you@example.com`, run on the PC. There's no network bootstrap.
- **Roles** are saved permission bundles ("Reader", "Reviewer",
  "Operator", "Admin"), plus per-user grants on top.
- **Every route declares its required permission**, checked by one
  FastAPI dependency. A static test fails the build if any route lacks
  one, which is what makes deny-by-default real.

| Permission | Allows |
|---|---|
| `library.read` | Library list, details, stats, search |
| `reader.use` | Reader and story tools that don't spend money |
| `lines.review` | Flag, comment, mark reviewed |
| `lines.edit` | Edit lines, find/replace, merge, restore versions |
| `drama.manage` | Create/edit dramas, upload media |
| `drama.delete` | Delete (Step 43 soft-delete when it lands) |
| `jobs.read` | Job list and progress |
| `jobs.start` | Start transcribe/translate/dub/export/OCR jobs |
| `engines.paid` | Use engines that cost money (needed *in addition to* `jobs.start`) |
| `jobs.cancel.own` / `jobs.cancel.any` | Cancel your own jobs / anyone's |
| `settings.manage` | App settings; API keys are write-only, never shown (D2) |
| `users.manage` | Grant/revoke permissions, disable users |
| `admin.system` | Install/upgrade packages, reset library, restore backup (§6) |

- Household profiles (Step 26e/78): each user maps to one profile, so
  reading progress and notes follow the person automatically. This fits
  Step 78's "no passwords in Baihe"; identity comes from Tailscale.
- **Audit log** (user, time, action) for job starts/cancels, deletes,
  permission and settings changes.

## 5. Two layers of deny-by-default

1. **Network (Tailscale access rules, set in the Tailscale admin
   console):** only the people you list can reach the Baihe machine at
   all, and only on ports 443/8443. Everyone else in the world, and
   anyone not on your tailnet, can't even connect.
2. **Application (Baihe permissions, §4):** what each of those people can
   do once connected.

**Streamlit is the gap until it's retired.** It has no permissions of
its own, so anyone who can reach port 8443 can do everything in it. Use
the Tailscale access rules to allow port 8443 **only for you (the
owner)**. Other household members get only the React app on 443, where
Baihe's permissions apply. As screens move to React, they become
available to everyone according to their permissions.

## 6. Admin actions (D5)

Installing packages runs code; reset and restore can destroy the library.
Proposal:
- the `admin.system` permission (admins only),
- a confirmation step showing exactly what will happen, and
- only from devices the admin marks as trusted (Tailscale identifies the
  device too; by default, the Baihe PC itself).

Until that's built, these stay in Streamlit, reachable only by the owner
(§5).

## 7. Costs and limits

- **The home PC is the server.** If it sleeps, shuts down, loses power
  or loses internet, Baihe is unavailable remotely. Tailscale doesn't
  turn it into a cloud service. Practical: set the PC to not sleep while
  on AC power; jobs already survive a browser disconnecting, but not the
  PC going down (durable jobs are Step 41).
- **Relayed connections are slower.** When two devices can't connect
  directly, Tailscale relays the encrypted traffic
  ([connection types](https://tailscale.com/docs/reference/connection-types)).
  That's fine for text, reading progress and job controls, but watching
  large videos over a relay may stutter. The media endpoint (HTTP Range
  support, migration review §2) helps by streaming instead of
  downloading whole files.

- **Free.** Tailscale's own docs list **6 users** on the free Personal
  plan with unlimited devices per user; some third-party summaries say
  3 ([Tailscale free plans](https://tailscale.com/docs/account/manage-plans/free-plans-discounts)).
  Confirm the current number when signing up. A "user" is a person; a
  person's phone, laptop and tablet count once.
- Each person installs the Tailscale app and signs in once. After that it
  runs in the background.
- It depends on a third-party service. If that ever has to change, only
  §3 (how Baihe learns who someone is) changes. §4's permissions stay as
  they are.

## 7b. The browser extension and phones

- **Remote access doesn't make the extension remote-capable.** It sends
  captured page images to `page_server` on `127.0.0.1:8756` on the same
  machine. That local-only design is correct: **never expose 8756**, not
  even to the tailnet (it's a token-guarded local bridge, not built for a
  network).
- **Laptop browser away from home:** the extension needs a new target,
  an authenticated FastAPI endpoint (e.g. `POST /api/extension/pages`)
  reached over Tailscale at the `ts.net` URL. The request then carries
  Tailscale identity like any other, and needs a permission (e.g.
  `extension.submit`). The port keeps page_server's input limits
  (image count/size, types). It's a small, separate step after
  identity/permissions exist. Until then the extension works only on the
  Baihe PC itself.
- **Phones:** the extension targets desktop Chrome/Edge and isn't
  supported on phones. Phone access means the React web app in the
  phone's browser, which is separate from extension support.

## 8. Before sharing access with anyone (checklist)

1. The four separate-process fixes from D1 (job-records table, settings
   from DB/`.env`, migration-race fix; model-cache duplication accepted).
2. Identity middleware with rules 1–4 of §3, each tested (including
   "a spoofed header on a direct request is rejected" and "no header →
   401").
3. Users/roles/permissions, the every-route-declares-a-permission static
   test, and the audit log.
4. FastAPI serving the built React app from the same origin (§2).
5. Tailscale installed on the PC, Serve configured (§2), Funnel **off**.
6. Tailscale access rules: port 443 for household members, port 8443
   (Streamlit) for the owner only. Port 8756 is never served.
7. The PC's sleep settings are adjusted so it stays reachable.
8. A check from a phone on mobile data: the React app works for a
   granted user; an ungranted tailnet user sees "ask for access";
   Streamlit is unreachable for non-owners; nothing is reachable with
   Tailscale off.

## 9. Effect on the migration order

Because the owner can still use Streamlit remotely, the auth work no
longer blocks remote use of the *whole* app for you. For **other
household members**, remote use is the React screens, so migrate what
they'll use first:

1. Identity + permissions (§3–§4).
2. Job monitoring/cancel (needs the job-records table).
3. Reader (served HTML first).
4. Review/line editing.
5. Starting jobs (translate first).
6. The rest, per `migration-review.md` §5.

## 10. Rejected alternatives (for the record)

| Option | Why not |
|---|---|
| E. Logins built into Baihe, port opened | Its one real advantage: household members need no app, just a browser. It would be the right choice only if "no app installation" were a firm requirement. Cost: highest effort: public server upkeep (TLS certificates, updates, exposure) *plus* building auth/2FA/lockout. Every Baihe auth bug becomes internet-reachable. Chosen first, then reversed by the user on effort. |
| C. Own reverse proxy + login portal | High effort: you maintain the exposed server, updates and certificates. |
| B. Cloudflare Tunnel + Access | Needs a domain on Cloudflare; the user doesn't want one. |
| D. Tailscale Funnel | Public, with no identity headers; Baihe would need its own logins (back to E's risk). |
