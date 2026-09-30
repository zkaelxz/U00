# Remote access design: M8-H (Tailscale + Tailscale Serve)

> **SUPERSEDED 2026-09-29** by [`remote-access-decision.md`](remote-access-decision.md) (Caddy plus a real Baihe login). Kept for history and for the parts D5 still uses (admin listener, permissions ideas).

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
| Who | The user and other household members, including from phones. Each installs the Tailscale app once and signs in (free Personal plan: up to 6 users, unlimited devices per user, [pricing](https://tailscale.com/pricing)). |
| Threat model | The Baihe PC and its logged-in account's processes are trusted (§3). |
| Permission scope | Global; no private libraries. Everyone granted access is trusted with full access (§4). Progress/preferences per user. |
| Admin actions | Admin permission + confirmation + only from the Baihe PC (§6). |
| What they can do | Start, cancel, read and review, controlled by **deny-by-default permissions** in Baihe. |
| API process | FastAPI runs as its own process (D1), with the four fixes in `migration-review.md` §6 D1. |

## 2. Topology

```
 phone / laptop (Tailscale app, signed in)
        │  encrypted Tailscale connection (works through CGNAT, no open ports)
        ▼
 Baihe PC: tailscaled ── Tailscale Serve (HTTPS, automatic ts.net certificate)
        │   https://baihe.<tailnet>.ts.net/        ──▶ FastAPI 127.0.0.1:8600 (+ built React app)
        ▼
   services ──▶ library/

 PC only (never published): Streamlit :8501 until it's removed
 Loopback only: page_server :8756 (browser extension bridge, unchanged)
```

Setup is one-time commands on the Baihe PC (exact flags to be confirmed
against the installed Tailscale version when it's built):

```
tailscale serve --bg --https=443  http://127.0.0.1:8600
```

Serve terminates HTTPS with a certificate Tailscale issues and renews for
the machine's `ts.net` name. There's no certificate tooling and no
dynamic DNS to run.

**One origin for the new app.** FastAPI serves the built React app
(`frontend/dist`) *and* handles `/api/*`, so Serve proxies to a single
local service on 443. There are no cross-origin requests and no CORS in
production, and the session never spans two hosts. This isn't built yet:
the foundation serves React from `vite preview` in development only. It's
on the checklist (§8). Streamlit is never published through Serve
(§6), and it's removed at the end of the migration.

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
  3. **Localhost doesn't prove a request came through Serve.** Serve's
     proxied requests arrive from `127.0.0.1`, and so does any other
     process on the PC. A local process can bypass Serve and send a
     forged `Tailscale-User-Login` directly. Binding to loopback stops
     *network* clients from doing that, not *local* ones. That's accepted
     only under the threat model below, and nothing in Baihe may treat
     "from localhost" as more than that. Even the owner, sitting at the
     PC, uses the `ts.net` URL (the PC is on the tailnet).
  4. Local development (`npm run dev` with no Tailscale) uses an explicit
     `BAIHE_API_DEV_USER` override, honoured only with
     `BAIHE_API_ENV=development` on a loopback bind.

### Threat model (stated explicitly)

**The Baihe PC, its logged-in Windows account, and the processes running
under that account are trusted.** Serve's identity headers authenticate
*remote tailnet users*. They don't defend against software already
running on the PC.

Why this is the right model here, not a shortcut: any process running as
that Windows user can already read `library/library.db`, the media
folders and `.env` (the API keys) directly from disk. An authenticated
boundary between Serve and FastAPI wouldn't protect any of that data from
such a process; it would only protect the API's *actions*. Real
protection against untrusted local software needs OS-level separation
first (Baihe running under its own service account, with file
permissions locking its data away from the everyday account). That's a
different, much larger project.

**Revisit if** the Baihe PC becomes shared by people who shouldn't have
full access, or routinely runs software you don't trust. Then do both:
(a) a dedicated service account with locked-down file permissions, and
(b) an authenticated hop between Serve and FastAPI (e.g. a secret only
the proxy holds, or a socket only the service account can open; which
mechanism is available on Windows must be verified at that time). A
required identity header alone does not meet that stronger requirement,
consistent with Tailscale's own guidance, which assumes the host is
trusted ([Serve identity headers](https://tailscale.com/docs/features/tailscale-serve)).

## 4. Permissions: deny by default (Baihe's part)

- `users` table in `library.db`, keyed by Tailscale login. A tailnet
  member Baihe doesn't know yet gets **no permissions** and sees "Ask
  the owner for access". They're recorded, so an admin can grant access
  with one click.
- **The first admin is granted locally:** `python -m api grant-admin
  you@example.com`, run on the PC. There's no network bootstrap.
- **Roles** are saved permission bundles ("Reader", "Reviewer",
  "Operator", "Admin"), plus per-user grants on top.
- **Scope: global to start** (decided 2026-09-28). A permission applies
  to the whole library. Reading progress, notes and preferences stay
  **per user**. Per-series/per-drama access is **not** built unless
  household members need private libraries or different content access.
  **Answered 2026-09-28: they don't.** "No one will have access that
  shouldn't have full access." Every person granted access is trusted
  with the whole library. Per-series/per-drama scoping is therefore
  dropped, not deferred. Permissions remain as a guard against accidents
  (e.g. spending on paid engines) and against unknown tailnet members,
  who still get nothing by default.
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
   all, and only on the specific ports. Anyone not on your tailnet can't
   even connect. **Tailscale's default policy lets every member reach
   every device and port**, so tailnet membership alone must not grant
   broad access: replace the default with rules restricted to the Baihe
   service ([access controls](https://tailscale.com/docs/features/access-control)).
   Shape of the policy (field names to be checked against the current
   policy syntax when it's actually set up):

   ```json
   {
     "groups":    { "group:household": ["alice@example.com", "bob@example.com"] },
     "tagOwners": { "tag:baihe": ["autogroup:admin"] },
     "grants": [
       { "src": ["group:household"], "dst": ["tag:baihe"], "ip": ["tcp:443"] }
     ]
   }
   ```

   Nothing else is granted: no access to other household devices, no other
   ports on the Baihe PC, and never 8756 or 8600 directly.
2. **Application (Baihe permissions, §4):** what each of those people can
   do once connected.

**Streamlit is not part of remote access at all** (§6, decision (a)).
Remote access, for everyone including the owner, is the React app on
443, where Baihe's permissions apply. As screens move to React, they
become available remotely according to each person's permissions.

## 6. Admin actions (D5)

Installing packages runs code; reset and restore can replace the whole
server's software or its data. **Decided 2026-09-28:** admin permission,
an explicit confirmation step, and **initially only from the Baihe PC
itself.** Not from a phone.

**Correction to the earlier draft:** it said Tailscale identifies the
*device*. Serve's standard identity headers identify the **user**, not
which device they used, so `Tailscale-User-Login` can't enforce "only
from this device". The mechanism instead:

- **Admin endpoints are not on the Serve-published service at all.** They
  live on a separate listener (e.g. `127.0.0.1:8601`) that Tailscale
  Serve never publishes. Under the threat model (§3), reaching it means
  being on the PC. It still requires `admin.system` and the confirmation
  step (identity comes from the local owner configuration, not a header).
- **Later, if remote admin is ever wanted:** Serve can forward
  *application-capability* headers granted through the access policy
  (e.g. only to one tagged device) with extra configuration
  ([Serve app capabilities](https://tailscale.com/docs/features/tailscale-serve)).
  That has to be designed and tested explicitly; it isn't assumed here.

**Streamlit during the transition. Decided 2026-09-28: (a), never
published through Tailscale.** Streamlit is being removed completely (the
migration's end state), so it gets no remote access, access rules or
exceptions in the meantime. It stays usable only at the Baihe PC.
Remote access, for everyone including the owner, is the React app only,
and grows as screens are migrated. This fully honours "admin only from
the PC", since Diagnostics (install, reset) and Library (restore) stay in
Streamlit until they move. Considered and rejected: (b) owner-only
publishing with an admin exception, and (c) household publishing with
admin sections hidden for Tailscale requests.

**Existing exposure to fix:** `start.bat` binds
Streamlit to all interfaces (Step 10e), so *any device on the home
Wi-Fi*, guests included, can already open Streamlit and its danger zone.
With Tailscale providing remote and household access, Streamlit could
go back to `127.0.0.1` only. That's a separate step touching `start.bat`
(currently owned by Step 79), so it's flagged, not changed here.

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
   Admin endpoints on their own unpublished listener (§6).
6. Tailscale access policy replaced (the default allows everything): port 443
   for the household group, nothing else. Streamlit (8501), 8756, 8600
   and 8601 are never reachable over Tailscale.
7. The PC's sleep settings are adjusted so it stays reachable.
8. A check from a phone on mobile data: the React app works for a
   granted user; an ungranted tailnet user sees "ask for access";
   Streamlit is unreachable for non-owners; nothing is reachable with
   Tailscale off.

## 9. Effect on the migration order

Remote use, for everyone including the owner, is the React screens
only (§6, decision (a)), so migrate what people will use away from home
first:

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
