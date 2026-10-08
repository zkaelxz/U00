# Main server and optional GPU worker (plan)

Status: **deferred, not started** (owner decision 2026-10-04). Build it only when a need comes up. The plan was drafted against `baihe-subtitler` at 5b070bc; line numbers below were correct then. Re-check each one before building.

## Why

The owner has an always-on Windows server with an RTX 3070 (8 GB) and a Windows PC with an RTX 3080 Ti (12 GB) that is often off. Jobs should be able to run on either machine.

## Recommendation

Build it, with three choices:

1. **Keep the in-memory job authority. No persistent queue in v1.** A remote run becomes a third kind of run (after "thread" and "process") inside `background_jobs`. The worker is another executor. The existing `on_done` hook still applies the result on the server, through the same apply functions used today.
2. **Give the worker protocol its own loopback listener behind Caddy**, not the household listener. A third app built like the household one (`create_app(listener="worker")`) mounts only `/api/worker/*`. A bug in the token check then cannot reach a library route, because none exist on that app.
3. **Send the worker a portable job spec.** It holds plain options, never server paths or secrets. The worker uses its own model folder and its own Hugging Face token.

The always-on server is the main node: it holds the library, serves the app and the household sign-in, and owns the job queue. The PC (or any second machine) is an optional worker that claims GPU-heavy jobs while it is online and idle. When no worker is available, the server's own GPU runs the job as today.

Remote access must be in place first: `docs/STATUS.md` says the API must not be exposed beyond loopback until the Caddy and LAN checks are done. The plan also reopens the parked "M8+ job queue" item, which is an owner decision.

## 1. What exists and what is missing

### Job engine (`background_jobs.py`)

- `_jobs` is the in-memory authority (lines 7-8); the docstring says "One machine, one process owning each job: not a distributed job queue" (line 26).
- Best-effort mirror to `job_records` (`_mirror_locked`, 79-111). Heartbeat every 60 s; `STALE_JOB_SECONDS` is 15 minutes (118-123). A restart marks running jobs "Interrupted" with no resume (`INTERRUPTED_MESSAGE` 1695; `jobs_service.sweep_stale_job_records`, `services/jobs_service.py:474`).
- GPU guard: in-process FIFO `_gpu_queue` (187); cross-process `db.gpu_lock` with up to 4 slots (`db.py`: the table, `GPU_LOCK_MAX_SLOTS` and `try_acquire_gpu_lock`; they moved from 824, 4384 and 4427 to 840, 4652 and 4695 after the plan was drafted); free-VRAM rule for parallel jobs (`GPU_PARALLEL_RESERVE_MB`, settle time; 227-266); other programs' GPU load via nvidia-smi (`try_take_gpu_slot` 269-301; `diagnostics.py:1799-1833`).
- `start_process_job` (1010-1119): the child returns a plain result and the parent applies it in `on_done` before marking the job done (1039-1049, watcher 1378-1398). A cancel that arrives after the child finished applies nothing (1384-1388). **This is the seam a remote run plugs into.**
- Cancel: `request_cancel` (1739-1769); a cancel from another process is read from the database (1779-1812); process jobs are killed by the watcher (1320-1335).
- Holds: `acquire_exclusive` (restore) refuses while any job is queued or running (869-879); maintenance count (888-913); `refuse_new_jobs` at shutdown (925-941).
- Job ids are reused per title (`transcribe_{id}`, `diarize_{id}`). There is already code guarding against an old run touching a new run of the same id (`_release_gpu_slot` 519-545). **A lease must carry its own run token, not just the job id.**

### Transcription (`services/transcribe_service.py`)

- `_transcribe_worker` (901-937) writes nothing to the database. `_transcribe_pipeline` (981-1337) touches no database row. `_apply_transcription` (1340-1425) does every library write: history snapshot (1370), full-sync `db.save_lines` (1373), raw transcript and status, MOSS characters, chained diarization (1388), speed record (1397-1405).
- Server-local values mixed into the args: `settings_service.get_whisper_model_path()` (650), `scratch_dir` (640), `use_gpu` (621). The outcome carries `"audio_path"` (1332) and `Line` objects, so it is not JSON as it stands.
- Vocal separation moves `vocals.wav` next to the title's audio (1046, 1082-1084). Besides that stage, `workspace_job_service.py:249` (the standalone separation job) and `cli.py` (`cmd_align`) write it, and nothing under `services/` reads it back. Unknown: whether anything outside `services/` does.
- Inputs built from library state on the server at start: the initial prompt from series glossary names (`build_auto_initial_prompt` 302-316), transcript text, title tuning (613-619). Keys: `hf_token` (607) and the Groq key are read on the server.
- Speed records are keyed `model|gpu|cpu` only (`_speed_key` 166; 245-268), with no machine dimension.
- Hardsub OCR is a thread job that takes `tesseract_cmd` (622-636). PC-only path rules apply. Not offloadable.
- Re-transcribe one line (1596-1716): a thread job that cuts a slice next to the audio (1661) and keeps the result in memory. Apply is a compare-and-set (`apply_retranscribe_line` 1744-1782). Tiny input and output, no library write: the ideal first slice.

### Diarization

- `diarize.diarize_subprocess_worker` (`diarize.py:287-329`) is pure and already falls back from CUDA out-of-memory to CPU (243-269).
- It needs `hf_token` because the pyannote model is gated (`services/diarization_service.py:263`). **The token must never be sent to a worker.**
- Apply writes only `speaker` and `speaker_manual`, saves turns to the title folder and upserts characters (119-150); it is idempotent (136-138).

### Server-only by nature

Dub and TTS (`services/dub_service.py:303-330`: writes into the title folder, reads voice references), live capture (`live_service.py:230`), Scanlate and OCR, translate jobs (glossary, style guide, locale, engine keys), benchmark. Autotune, SenseVoice and re-split are v2 candidates.

### CLI (`cli.py`)

Runs inline, takes the shared GPU slot (113-124) and does not use `background_jobs` (`cmd_align` 438-546). It cannot see the API's in-memory dispatcher, so it stays local in v1.

### Auth and listeners

- `require_permission` is based on the session cookie plus CSRF (`api/auth.py:131-169`); `local_only()` is at 242-258.
- `authenticated()` is already a fourth declaration, limited by test to `/api/auth/` (`api/auth.py:194-209`; `tests/test_api_permissions.py:223-235`). That is the precedent for a worker declaration.
- Tokens are SHA-256 hashed and compared with `compare_digest` (`services/auth_service.py:9-12, 80-81`).
- `create_app` accepts only `admin` or `household` (`api/server.py:179-182`). The two-listener design is in `docs/remote-access-decision.md:150-155`.

### Missing

Worker identity, pairing and tokens; a worker listener and protocol; portable job specs and a JSON outcome codec with validation; an executor dimension in the dispatcher; per-machine speed records; a file-streaming capped download (`services/capped_body.read_capped` buffers in memory, fine for JSON, not for audio); a protocol version (`update_service.current_version()` returns None outside an installed copy).

## 2. Job taxonomy

| Job | v1 | Why |
|---|---|---|
| `retranscribe_` (one line) | Offload | The server cuts the slice; the text proposal goes through the existing compare-and-set apply. |
| `transcribe_`: Whisper, Qwen3-ASR, Qwen3-ASR with speech detection, MOSS, Qwen3 forced align, word realign, vocal separation stage | Offload | Pure pipeline. The server applies the result through `_apply_transcription` unchanged. `vocals.wav` stays in the worker's scratch folder. |
| `diarize_`, including the one chained after transcribe | Offload | Pure. The worker uses its own Hugging Face token or an offline model. |
| Groq transcription | Server | Network and a key, no GPU benefit. |
| Hardsub OCR, novel OCR, Scanlate | Server | `tesseract_cmd` path rules, page files. |
| Dub, voice clone, Live, exports, translate and local LLM, benchmark | Server | Write the title folder, need voice references or keys, have glossary/style/locale parity rules, or are real-time. |
| Autotune, SenseVoice, re-split | Later | Pure-ish; add once the protocol is proven. |

## 3. Queue and lease protocol

- **Dispatch.** A new `start_offloadable_job(job_id, spec, local_target, local_args, on_done, on_finish, route)` queues the job like today. The promoter picks an executor per entry. With no eligible worker claim it falls through to today's `start_process_job` path unchanged. Remote running jobs are excluded from `_running_gpu_job_count_locked` (360-364) and do not take `db.gpu_lock`.
- **States**, shown to users as today's `queued`, `running`, `done`, `error`, `cancelled`: `queued`, `offered` (lease created), `running` (first heartbeat), `uploading`, `applying` (`on_done` on the server), then a final state. Every remote state counts as running for `acquire_exclusive` and `any_job_running_for_drama`, so a restore cannot swap the library under an upload.
- **Lease.** A random 32-byte id stored hashed, bound to (worker, job id, run token, attempt). TTL 90 s, extended by a heartbeat every 20 s. The heartbeat reply carries `cancel: true|false`. A missed TTL revokes the lease and puts the job back at the head of the queue. After 2 remote attempts it is pinned to the server.
- **Idempotency.** A lease accepts exactly one result. A re-upload with the same SHA-256 gets 200 `already_accepted`; a different body gets 409. Any call on a revoked or unknown lease gets 410 and the worker kills its job.
- **Cancel.** `request_cancel` revokes the lease and marks the job cancelled at once; the server holds no GPU slot to wait for. The worker hears it within one heartbeat and kills its process tree. A result after cancel gets 409, plus the existing `is_cancel_requested` check before `on_done` (1384).
- **Progress and ETA.** The worker sends only (stage code, fraction, numbers). The server builds the message from fixed templates, so no worker-written text reaches the UI or `job_records`. Speed records gain a machine dimension (`model|gpu|w<worker_id>`), keeping the existing key for the server. The worker reports work seconds; the server computes audio length from its own file.
- **Result validation and caps.** Line count at most 50,000; at most 2,000 characters per line; finite `0 <= start <= end <= duration + 1 s`; speaker labels match `SPEAKER_\d{1,3}`; embeddings at most 64 x 1,024 floats; re-transcribe text keeps `_RETRANSCRIBE_MAX_CHARS`; JSON body at most 32 MB with no Content-Encoding. The server rebuilds `Line` objects itself and ignores worker-sent `audio_path` and device text. Results go through `jobs_service.RESULT_ALLOWED_KEYS`.
- **Partial results:** none in v1. A worker that dies means the whole job re-runs.

## 4. Data transfer

- **Claim response:** lease id, kind, spec (options only), input size and SHA-256, protocol versions. No URL, path, title or drama id. The worker builds request paths from fixed templates plus the lease id. Private titles never leave the server; the glossary-name prompt and transcript text do, because the job needs them.
- **Input.** The server extracts audio with ffmpeg into the library `tmp` folder (16 kHz mono FLAC for ASR, diarization and line slices; 44.1 kHz stereo FLAC when vocal separation is on). `GET /api/worker/leases/{lease}/input` works only for the lease's worker while the lease is live, supports ranges for resume, and the file is deleted when the lease ends. The worker streams to disk with a byte cap equal to the declared size, a deadline and a SHA-256 check. This needs a file-streaming twin of `read_capped`.
- **Results.** `PUT .../result` (JSON). No stem uploads in v1.
- **Uploads on the server** follow the backup-import pattern: Content-Length checked before reading, chunked uploads refused, the stream counted.
- **CLAUDE.md rules:** the token goes in the `Authorization` header only; every worker HTTP call has `timeout=` and capped reads, and the new worker module is added to the static-analysis list; errors go through `redact_secrets`; tokens get a `bhw_` prefix so the redactor recognises them; no access log on the worker listener.

## 5. Security model

- **Pairing.** (1) At the server, Settings > GPU computers > "Pair a computer" (a `local_only()` route) shows a one-time code: about 40 bits, 10-minute expiry, single use, 5 tries. (2) The worker sends `POST /api/worker/pair` with the code, a name and its capability report. (3) The server lists it as "Waiting for approval" and the owner approves at the server. (4) Only then is the token returned, once (`secrets.token_urlsafe(32)`, stored as a SHA-256 hash). Workers can be revoked and disabled one by one; revoking kills live leases, which are requeued. Because admin is PC-only, pairing needs the owner at the server or on remote desktop.
- **Permission.** A worker is not a user and gets no `PERMISSIONS` entry. A new `worker_token()` declaration is limited by test to `/api/worker/*`, as `authenticated()` is limited to `/api/auth/`, and resolves a worker principal that can only act on its own leases. **This changes CLAUDE.md's "exactly one of three" rule** (already four in practice); the owner must approve it, and the rule text, `tests/test_api_permissions.py:235` and the route table in `docs/remote-access-decision.md` need updating. Rejected alternative: accepting bearer tokens inside `require_permission`, which widens every route's attack surface.
- **Listeners.** A new `BAIHE_API_WORKER_PORT` (loopback, off by default) with its own bind-safety check (it must differ from 8600, 8601, 8756 and the household port). Caddy adds a `handle /api/worker/*` block to that port, with a request-size limit, its own rate-limit zone and timeouts that allow a 25 s long-poll. The household listener does not mount these routes, and the admin listener never accepts worker tokens. `tests/test_caddyfile_template.py` needs extending. Worker routes take no cookies, so no CSRF; bearer only.
- **TLS.** Always go through Caddy with the real certificate for `BAIHE_PUBLIC_URL`. On the LAN the worker may be given the server's LAN IP as its connect address while still verifying the certificate for the public name, which avoids split DNS and router hairpin. Not recommended: a LAN-only listener with a self-signed certificate (it needs a non-loopback bind, which the bind check refuses, and a firewall rule for `python.exe`, which the docs forbid).
- **Replay** is covered by TLS, one result per lease and lease expiry.
- **Malicious worker:** caps and validation as in section 3. It can only replace lines of jobs it leased, and the history snapshot before replacement already exists (1370). It sees only leased audio and prompts.
- **Compromised worker:** revoke it. It is never admin and never reads the library.
- **Compromised server.** The worker runs only a fixed list of job kinds, validates the spec itself (Whisper sizes and backends from allowlists), loads only allowlisted model ids (loading an arbitrary checkpoint can run code), accepts no paths, commands or code updates from the server, and runs as the normal user, never as administrator. Residual risk accepted: parser bugs in ffmpeg or soundfile on malicious audio.

## 6. Worker runtime on Windows

- **Start.** `python -m api worker` as a subparser next to `grant-admin` (`api/__main__.py:292+`), implemented in a new root module (for example `gpu_worker.py`) with a `FILE_ORGANIZATION.md` line and a static-analysis entry. Started from a Start-menu entry or a "start at sign-in" scheduled task in the **user session, not a Windows service**: idle detection (`GetLastInputInfo`) and full-screen detection (`SHQueryUserNotificationState`) do not work from session 0. It can later share the tray shell (`docs/specs/step-141-pc-shell-and-connect.md`).
- **Not stealing the GPU.** The worker claims a job only when: it is not manually paused; the user has been idle at least N minutes (default 10) or "always" is chosen; no full-screen app is running; `diagnostics.external_gpu_is_busy()` is false; and free disk is at least 4 x the input plus a margin. If the user returns mid-job the default is to finish; an option "hand it back" cancels and requeues the job on the server.
- **Models.** The worker keeps its own offline Whisper folder and Hugging Face cache and its own `.env` token for pyannote. Its capability report lists ready kinds as booleans. A "Prepare models" action pre-downloads them.
- **Execution.** The worker runs the job through the existing spawn workers (`_transcribe_worker`, `diarize_subprocess_worker`) with a local result queue and its own scratch folder, so cancel and kill-tree behave as today.
- **Version handshake.** A `WORKER_PROTOCOL` integer plus a schema version per kind must match exactly. A mismatch is refused in plain words ("This computer's Baihe is older than the server's. Update it, then pair again."). An app-version difference is only a warning.
- **Updates** come only through the normal installer; there is no self-update.

## 7. UI

- Settings > "GPU computers" (admin listener only; the household listener sees nothing): pair a computer; a list with status (online, idle, busy, paused, offline since time), GPU name, VRAM and ready job kinds; enable, disable and revoke; routing preference (Auto with faster first, Prefer this server, Prefer a named worker).
- Job rows show "Ran on: This server / worker name", from a new `job_records.ran_on` column and `JobRecord.ran_on` (`api/schemas/system.py:236`).
- Transcribe, Detect speakers and Re-transcribe line get a "Run on" select.
- Plain errors, for example "GAMING-PC went offline; running on this server instead" and "Not enough GPU memory on this server; sent to GAMING-PC".
- Sentence case, 32 px desktop and 44 px touch targets, checked at phone width.

## 8. Failure modes and tests

| Case | Behaviour |
|---|---|
| Worker dies mid-job | The lease times out after 90 s and the job is requeued; after 2 failed attempts it runs on the server. |
| Server restarts | The in-memory job is lost and its row becomes "Interrupted" (today's contract). The worker gets 410 and kills its job. |
| Duplicate upload | Same hash: 200 `already_accepted`; otherwise 409. |
| Stale lease | 410, nothing applied. |
| Cancel during upload | The lease is revoked, 409, and the pre-`on_done` cancel check applies. |
| Version skew | Refused at hello and claim, with a plain message. |
| Out of GPU memory on the 3070 | The worker or server reports `gpu_oom`; retried once on the other machine if eligible, otherwise the existing CPU fallback (diarize 243-269). |
| Worker offline for days | Auto jobs never wait. Jobs pinned to a worker show "Waiting for X (offline since ...)" and can be cancelled; a server restart drops them. |
| Disk full | The server refuses to build the extract; the worker refuses to claim. |

Test strategy (no GPU or network): a fake worker client drives the real `create_app(listener="worker")` through the in-process test client; fake executors return canned outcomes; tests assert the title rows after a remote run equal those after a local run; concurrency tests cover lease expiry vs upload, cancel vs apply, and restore hold vs remote running; the permission, Caddy and ownership static tests are extended; Playwright uses mocked endpoints.

## 9. PR breakdown

All PRs are off by default behind a `workers_enabled` setting.

| # | PR | Exit condition | Size | Review | Owner checks on real Windows |
|---|---|---|---|---|---|
| 1 | Portable specs and outcome codec for transcribe, diarize and re-transcribe (split server-local values: model path, scratch folder, use_gpu; JSON codec and validators). No behaviour change. | Full suite green; local runs give identical results. | ~400 lines | Sonnet | Transcribe and diarize one title on the 3070 as before. |
| 2 | Executor-aware dispatcher in `background_jobs` (remote run kind, lease state machine, GPU count excludes remote, cancel, requeue, fallback) plus a `job_records.ran_on` column (listed in `_INIT_DB_MIGRATED_COLUMNS`). | Fake-executor tests pass; the local path is unchanged. | ~500 lines | Opus (concurrency, data integrity) | None. |
| 3 | Worker registry and pairing: tables, hashed tokens, `local_only()` admin routes, audit rows, route-table rows. | Permission and ownership tests pass. | ~350 lines | Opus (auth) | The pairing code shows at the server. |
| 4 | Worker listener and protocol routes (`worker_token()`, claim long-poll, heartbeat, input download, result upload, caps, rate limits), Caddy template and its test, bind safety; CLAUDE.md rule update after owner approval. | An in-process fake worker completes a re-transcribe-line job end to end. | ~600 lines | Opus (security) | Caddy validate; LAN checklist and the allow and refuse checks on `/api/worker/*`. |
| 5 | Worker runtime (`python -m api worker`: pair, capability, claim loop, capped streamed download with SHA-256, local spawn run, heartbeat and cancel, upload, handshake, idle and pause policy). | Mocked client tests; the static timeout test covers the module. | ~600 lines | Sonnet; Opus pass on HTTP and token handling | Pair the 3080 Ti PC; pause when a game is full screen; resume when idle. |
| 6 | Routing and kinds: re-transcribe line, then Whisper transcribe and diarize (chained diarize routed too); VRAM table; per-machine speed records; `run_on` request field. | Remote and local database state equal in tests. | ~450 lines | Opus (applies to the library) | The same title on each machine: lines and speakers comparable, ETA sensible. |
| 7 | UI: Settings > GPU computers, "Ran on", "Run on" select, plain errors; vitest and Playwright. | `tsc`, vitest and e2e green; phone width fine. | ~500 lines | Sonnet | Use it at the server and from a phone (rows read-only there). |
| 8 | Remaining offload kinds: Qwen3-ASR, speech detection, forced align, MOSS, separation stage; out-of-memory handoff to the other machine. | Mocked out-of-memory handoff test. | ~350 lines | Sonnet plus Opus review | Qwen3 on the 3070 vs the 3080 Ti; force an out-of-memory once. |
| 9 | Windows packaging and a worker docs page: Start-menu entry or sign-in task, model prepare, rollback. | Installer build test. | ~250 lines | Sonnet | Fresh install on the PC; PC off means jobs run on the server; PC on and idle means they go to the PC. |

Order: 1, 2, 3, 4, 5, 6, 7, 8, 9. PR 4 also needs the byte-capped reader consolidation (#677) and the remote-access checklist done. Run PRs 1 and 3 one after the other: both touch `api/schemas` and the tests.

## 10. Open decisions for the owner (with a recommendation)

1. **Worker runs translate or LLM jobs?** No in v1: glossary, style and locale parity and keys would have to leave the server.
2. **Auto-apply heavy results?** Yes, like local today, relying on the history snapshot. Re-transcribe line stays propose-then-apply.
3. **Idle policy.** Default: idle 10 minutes, no full-screen app, GPU not busy, finish the current job when the user returns. Offer "always" and "hand back".
4. **Internet or LAN-only workers.** Through Caddy either way. Allow internet workers (the token plus TLS is the same bar); default the UI to a LAN connect address.
5. **Can household-started jobs go to the owner's PC?** A per-worker toggle, off by default.
6. **Persistent queue across server restarts.** Defer. It needs serialisable `on_done` hooks and breaks the current "no resume" contract.
7. **The new `worker_token()` declaration and the CLAUDE.md rule change.** Recommend approving.
8. **`vocals.wav` no longer saved beside the audio for remote runs.** Accept, unless something consumes it.

## 11. Risks and what not to build in v1

Risks: dispatcher races (lease expiry vs upload vs cancel vs restore hold); a wrong result applied as a full line sync (mitigated by validation and the history snapshot); long-poll behaviour through Caddy is unverified; full-screen detection may miss borderless games; per-model VRAM numbers are estimates to measure on the 3070 and 3080 Ti; upload bandwidth if a worker is outside the LAN; result drift when the two machines run different library versions.

Not in v1: a persistent or distributed queue; partial or resumable results; stem uploads; dub, TTS, voice clone, OCR, Live or LLM on the worker; several workers sharing one job; worker self-update; a LAN-only listener with a self-signed certificate; CLI offload (the CLI has no access to the API dispatcher; its inputs match the app's specs, so outputs stay equivalent and "CLI and app behave the same" holds).

## Unknowns to settle before building

- Per-machine speed records are not in the code: `_speed_key` is model and device only.
- Whether anything outside `services/` reads `vocals.wav`.
- A version identifier: `update_service.current_version()` returns None on git checkouts, so it cannot be the handshake version.
- Whether the owner will hold a Hugging Face token on the PC or wants an offline pyannote model.
- Whether the domain and Caddy certificate will be set up first. Without them there is no TLS path; the fallback would be a Caddy `tls internal` LAN site with a CA pinned at pairing, which is a separate design.
