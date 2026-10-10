# Background jobs

How long-running work (transcribe, translate, dub, export, imports, backups) runs
outside the HTTP request, and how the GPU guard, cancel, holds, the Jobs API and the
CLI fit around it. The code is the authority: this page cites modules and functions,
so re-check against them before relying on a detail.

Modules: `background_jobs.py` (the runner), `services/jobs_service.py` (what a client
sees), `db.py` (`job_records`, `gpu_lock`), `services/shutdown_service.py`,
`api/background.py`, `services/job_timing_service.py`, `cli.py`.

One machine, one process owning each job. This is not a distributed queue.

## The job model

A job is an entry in the module-level `_jobs` dict in `background_jobs.py`, keyed by
job id. Two kinds, recorded as `job["kind"]`:

| Kind | Started with | Runs in | Cancel |
|---|---|---|---|
| thread | `start_job(job_id, target, *args, gpu_touching=, description=)` | a daemon thread (`_spawn`) | cooperative: the job must check for it |
| process | `start_process_job(job_id, target, args=, gpu_touching=, description=, on_done=, on_finish=, kill_whole_tree=, start_method=)` | a `multiprocessing.Process`, watched by a thread in the server (`_process_watcher`) | the watcher kills the process |

Use a process job for work with no safe point to stop at (one opaque model call), so
cancel can really stop it, and for CUDA work (`start_method="spawn"`: a forked child of
a process that already initialised CUDA cannot use the GPU).

Both return `False` without starting anything when the id is already `queued` or
`running` (a double click never starts a duplicate), and when an exclusive hold is
active (see Holds). `start_job` runs `target(*args, **kwargs)`. A process `target`
must be a top-level picklable function whose last parameter is a
`multiprocessing.Queue`; `start_process_job` appends the queue itself.

### Authority and the mirror

- The in-memory `_jobs` dict is the authority for jobs this process owns. The API polls
  it through `get_status()`.
- Every status change is also written to the `job_records` table by `_mirror_locked`
  (`db.save_job_record`). The mirror is best-effort: a failed write is logged and never
  breaks the job. Other processes and a restarted server see the row; they never see
  the dict.
- Only status transitions are mirrored. `update_progress` changes the dict only, so
  `jobs_service._with_live_progress` overlays the live progress and message from
  `background_jobs.get_status` while the job runs in this process.
- Text going into `job_records` (it lands in backups) passes `_storage_text`
  (`translate_engines.redact_for_storage`); the result is stored as the allow-listed
  projection (`jobs_service.project_result_json`).
- A mirror write also notifies change listeners (`add_change_listener`), which is how
  the SSE stream (`services/event_stream_service.py`) learns a job changed.

### States

`queued`, `running`, `done`, `error`, `cancelled`. There is no `interrupted` state:

- **Interrupted after a restart** is a `job_records` row left `queued`/`running` by a
  dead process. `jobs_service.sweep_stale_job_records` closes it as `cancelled` with
  `error = background_jobs.INTERRUPTED_MESSAGE`. It runs at startup (`api/server.py`)
  and on every `jobs_service.list_jobs`. The in-memory dict is empty after a restart,
  so nothing resumes: records have no resume.
- A worker that died without reporting back (thread gone, status still `running`) is
  marked `error` with `WORKER_LOST_MESSAGE` by `reconcile_dead_workers`.
- A queued job that is cancelled ends `cancelled` and never starts.

A job only records its outcome over `running` (`_still_running_locked`). A job already
cancelled, cleared or marked lost keeps that state when a late worker returns.

### Job ids, reuse and the run token

Ids are strings the service chooses, and they are the identity of a job:

- Per drama: `<prefix><drama_id>`, e.g. `transcribe_12`, `translate_12`, `diarize_12`,
  `dub_12`, `resegment_12`. The prefixes are listed in
  `background_jobs.DRAMA_JOB_PREFIXES`; `any_job_running_for_drama` checks them before a
  whole-title action such as delete. A new per-drama prefix must be added there.
- Fixed: one job of that kind in the app, e.g. `library_backup`, `library_export_zip`,
  `bulk_series_translate`, `sources_search`, `deno_install` (constants named
  `*_JOB_ID` in the services).
- A job id is reused: starting `transcribe_12` again replaces the finished entry in
  `_jobs` (and its `job_records` row). Only the latest run per id is kept in memory.

Because ids are reused, an old run can still be winding down when a new one with the
same id starts. The protections:

- `run=`: `_spawn` and `_process_watcher` carry the run's own job dict. A run's cleanup
  (`_release_gpu_slot`) leaves the GPU lock row alone when the id's current entry is a
  different, running run. `reconcile_dead_workers` and `_workers` key on the dict, not
  the id.
- `job_timing_service.start_run` returns a token (the run's start time) and
  `finish_run(token=)` only closes that run.
- `db.save_job_record` hands the owner to the new run when `started_at` changes, and
  resets `cancel_requested` when the row is not active, so a stale flag does not cancel
  the next run.
- Services that need to tell runs apart add their own id: the glossary extraction jobs
  carry a `run_id` (`services/glossary_service.py`), so an apply names the run it is
  applying.

### Heartbeat and the stale sweep

- While this process has queued/running jobs, the daemon `job-heartbeat` thread
  (`_ensure_heartbeat`, started by the first mirror write) calls
  `db.touch_job_records` every `HEARTBEAT_INTERVAL` (60 s) and also runs
  `reconcile_dead_workers`.
- A GPU job also refreshes its `gpu_lock` row on every `update_progress`
  (`db.heartbeat_gpu_lock`).
- A `queued`/`running` row is stale when its owner pid is gone (`owner_pid`, via
  `owner_process_alive`) or `updated_at` is older than `STALE_JOB_SECONDS` (15 min).
  `jobs_service.is_stale` and the sweep skip any job live in this process. Each close
  is one conditional `UPDATE` (`close_orphaned_job_record`, `close_stale_job_record`),
  so a live owner's heartbeat, `done` or new run always wins.

### Threads that outlive the status

A job's status turns final before its thread finishes: the thread still writes the
notification, the timing row and the GPU-lock release. `wait_for_job_threads(timeout,
job_ids=None)` joins those threads. Callers that replace the database file or stop the
server use it (`library_admin_service`, `shutdown_service.wait_for_jobs`).

## The GPU guard

Applies to jobs started with `gpu_touching=True`, and only while the GPU limit setting
is on (`get_gpu_limit_enabled`, `app_settings.gpu_limit_enabled`, default on). It is a
soft guard against two jobs fighting for VRAM, not a model manager.

A GPU job is queued instead of started when an earlier GPU job is already waiting
(`_gpu_queue_waiting_locked`: first come, first served) or no slot is free
(`_gpu_slot_available_locked`). Three layers:

1. **In-process FIFO queue.** `_gpu_queue` holds the waiting entries (target, args,
   `on_done`/`on_finish`, kind). When any GPU job ends (`_release_gpu_slot` then
   `_promote_next_queued_gpu_job`), the head is started if a slot is free. Entries whose
   job was cleared or cancelled are dropped (their `on_finish` still runs). The queue
   is per process on purpose: each process manages only the jobs it started.
2. **Cross-process slots.** `try_take_gpu_slot(holder, description)` claims a row in
   `db.gpu_lock` via `db.try_acquire_gpu_lock` (`BEGIN IMMEDIATE`, so the read-then-write
   is atomic across processes). Holders are `ui:<job_id>` and `cli:<pid>`. A row not
   heartbeated for `db.GPU_LOCK_STALE_SECONDS` (10 min) is abandoned and ignored. At
   startup a `ui:` row is also released at once when its job record was written after the
   row was taken and names a dead owner pid (`gpu_lock_recovery_service`); a record older
   than the row is a previous run's, so the row is left to expire. The
   table has at most `GPU_LOCK_MAX_SLOTS` (4) rows.
3. **External load.** For the first holder (`check_external_load=True`, which UI jobs
   pass), `diagnostics.external_gpu_is_busy` reads nvidia-smi totals. A program Baihe
   did not start (a game, a transcoder) keeps the job queued, with a message that says
   so (`_external_gpu_wait_message`: numbers only, never process names). No nvidia-smi,
   or a failing check, is ignored for the first job.

The "GPU jobs at once" setting (`get_gpu_max_parallel`, `app_settings.gpu_max_parallel`,
1 to `GPU_MAX_PARALLEL_LIMIT` = 4) is the parallel rule. With a cap above 1, a job joins
a running one only if all of these hold:

- fewer than the cap holders (live `gpu_lock` rows, any process);
- at least `GPU_PARALLEL_RESERVE_MB` (2048) free VRAM per nvidia-smi
  (`_vram_room_for_another_gpu_job`; no reading means no join);
- the newest holder has held its slot `GPU_PARALLEL_SETTLE_SECONDS` (30 s), checked
  inside the same transaction so two processes cannot both join on one reading.

A job also respects `_running_gpu_job_count_locked`, the count of GPU jobs running in
this process. A failing `gpu_lock` read fails closed: the job queues.

What a waiting job shows: always the plain `GPU_WAIT_MESSAGE` ("Waiting for the GPU
(another job is running)"), with its place in line from the second entry on
(`_queue_message`), or the external-load text for the first. It never names the job
holding the GPU: the message is visible to anyone who can see the waiting job and is
mirrored to `job_records`.

Nothing polls by itself; a queue is re-checked when a GPU job ends, and by
`recheck_gpu_queue` on a timer from `api/background.start_gpu_queue_poller` (every
`GPU_QUEUE_POLL_SECONDS` = 20 s, only while background services are on). A job queued by a
CLI run's lock therefore resumes within about that interval, not instantly.

### CPU fallback notices

The guard decides when a job may start, not what device it uses. When a model cannot
load on the GPU, the job falls back to CPU, finishes `done`, and says so in its result:
`gpu_fallback` (a short redacted reason) and `device_notice`
(`core.gpu_fallback_notice`: "<Task> ran on the CPU because the GPU couldn't be used
(...)"). `jobs_service.derive_outcome` reports such a job as `partial`, not `ok`. The
CLI prints the same sentence.

## Cancel

`background_jobs.request_cancel(job_id)` sets `cancel_requested` and then:

- **Queued:** the job ends `cancelled` immediately, leaves `_gpu_queue`, and a process
  job's `on_finish` runs before the call returns. Nothing was started, so nothing to stop.
- **Running thread job:** the message becomes `CANCELLING_MESSAGE`. The job must notice
  and raise `JobCancelled`, which `_spawn` records as `cancelled`. Check with
  `is_cancel_requested(job_id)` between units of work; external commands go through
  `run_cancellable` (ffmpeg and friends, killed as a process tree).
- **Running process job:** `_process_watcher` sees the flag, stops the child
  (`_stop_process`: terminate, then kill; or `kill_tree` for `kill_whole_tree=True`),
  and marks the job `cancelled` ("Cancelled."). `on_done` is not called. A cancel that
  arrives after the child already returned its result also ends `cancelled` and applies
  nothing.
- **Another process:** `jobs_service.cancel_job` calls `request_cancel` and also sets
  `job_records.cancel_requested` (`db.request_job_record_cancel`). The owning process
  reads the flag in `is_cancel_requested` (and the watcher), at most every
  `_DB_CANCEL_CHECK_INTERVAL` (2 s) per job, so a cross-process cancel takes up to
  about that long. If no live owner exists (pid gone or no heartbeat for
  `STALE_JOB_SECONDS`), `cancel_job` closes the row itself as `cancelled`.

Related: `cancel_queued` clears a still-queued job (re-checking under the lock that it
was not just promoted); `clear_job` removes a finished record, and for a live process job
also stops the process; `cancel_line_jobs(drama_id)` cancels the line-writing jobs
(`LINE_WRITING_JOB_PREFIXES`) before something replaces all of a title's lines. The CLI
does the same plus the `job_records` flag for jobs run by the API.

A GPU job's worker is started through `services/gpu_process_job.py`: `run_in_child` for a thread job whose GPU stage must be killable and whose next stage needs the parent (`comparetx_`, `fixflag_`); the worker may send each finished unit as `("item", x)` so a cancel or timeout keeps what was done. It runs the body under `run_worker` (own process group, a deadline watchdog that closes the result queue before `os._exit`, scratch folder as the temp dir). The child never reads the database: the parent resolves settings and passes plain values.

A worker whose server dies is not left running: a process job started with
`start_own_process_group()` ends itself when its parent is gone
(`exit_if_parent_gone`, from each progress report and a watchdog thread).

## Holds

Three controls stop new jobs from starting. All are in `background_jobs.py`.

| Hold | Taken with | Blocks | Used by |
|---|---|---|---|
| exclusive | `acquire_exclusive(label)` / `release_exclusive()` | `start_job` and `start_process_job` return `False` | restore, library reset, dependency install, model cache delete, disk-usage clear/move |
| maintenance | `enter_maintenance()` / `exit_maintenance()` | an exclusive hold (and refuses while one is active) | short non-job library work: bulk delete, storage cleanup (`library_admin_service`) |
| stopping | `refuse_new_jobs()`, once, never undone | `start_job` and `start_process_job` raise `ConflictError` (`STOPPING_MESSAGE`) | `shutdown_service` at the start of a clean stop |

- `acquire_exclusive` is atomic: it refuses (returns `False`) if any job in this process
  is queued or running, if another exclusive hold is active, or if the maintenance count
  is above zero. Callers then wait for job threads (`wait_for_job_threads`) before
  touching the database file.
- Exclusive and maintenance are mutually exclusive, so a restore never swaps the library
  under a bulk delete.
- `refuse_new_jobs` is set under the same lock as the start checks. A start either
  finished before it (so `active_job_ids()` sees it and the stop cancels it) or is
  refused, so nothing started during the grace wait is killed mid-write. The clean stop
  then cancels every active job through `jobs_service.cancel_job`, queued ones first
  (`cancel_queued`), and `wait_for_jobs` waits up to `JOB_GRACE_SECONDS`.

None of the holds applies to the CLI, which starts no background jobs.

## How services start jobs

Business logic stays in a `services/*_service.py` function that validates, builds the
arguments, and calls `start_job` / `start_process_job`. It returns `{"job_id": ...}`, or
raises `ConflictError` when the start returned `False`. The router only calls the service.

### The pure worker plus `on_done`

For a process job the child writes nothing. It computes and puts plain Python on the
queue; the parent applies the result:

```
service.start_xxx()                      # validate, build args, start_process_job(...)
  -> worker(*args, result_queue)         # child: no DB, no files outside scratch, no torch objects out
       report_progress(q, frac, msg)     #   ("progress", frac, msg), optional
       report_stage(q, msg)              #   ("stage", frac, msg) for a stage with no progress
       q.put(("ok", result)) | q.put(("error", type_name, message))
  -> _process_watcher (parent thread)    # drains the queue while the child runs
       on_done(job_id, result)           #   parent applies: _apply_*; return value, if not None, becomes the result
  -> status "done"                       # set only after on_done returns, so a poller never sees
                                         # "done" while the result is still being applied
  -> on_finish(job_id)                   # last, once, whatever the outcome: remove scratch files
```

Rules that follow from the code:

- `on_done` runs in the watcher thread, outside `_lock`. If it raises, the job ends
  `error` ("Completion hook failed: ..."). It is not called on error or cancel.
- `on_finish` is for cleanup a killed child cannot do. It runs once for every call that
  returned `True` (also for a job cancelled or cleared while still queued) and never for
  one that returned `False` or raised; the caller still owns that cleanup.
- The watcher drains the queue while the child is alive. A child that `put()`s more than
  an OS pipe buffer holds cannot exit until the parent reads, so waiting for exit first
  deadlocks on a large result.
- The apply step is where field-scoped writes happen. Examples:
  `services/transcribe_service._apply_on_done` -> `_apply_transcription`,
  `services/diarization_service.make_apply_on_done` -> `apply_diarization_result`,
  `services/restructure_service._make_on_done` -> `_apply_resegmented`,
  `services/dub_service.apply_dub_result`.
- Thread jobs follow the same shape in one function: a pure core computes, then the job
  applies at the end (`transcribe_service._run_transcribe_and_apply_job`,
  `restructure_service._run_resegment_job`).

### Field-scoped writes

A job writes only the fields it owns so it cannot overwrite the user's edits or another
job's work: `db.save_lines(drama_id, lines, fields=("en",))`. Lines are matched back by
permanent line id, never by list position. `fields=None` is a full replace (the list
becomes the drama's lines) and is reserved for jobs that replace everything, such as a new
transcription. Jobs that write only their own fields can run beside each other
(`LINE_WRITING_JOB_PREFIXES`); a wholesale replace cancels them first (`cancel_line_jobs`).
Build lines with `core.line_from_row` so flags and speaker are not wiped.

## Progress, ETA and the speed record

- Report progress from inside the job with `update_progress(job_id, frac, message)`; a
  process worker uses `report_progress(result_queue, frac, message)`, which the watcher
  applies. Messages from a worker are redacted (`_apply_progress_item`). While cancel is
  pending the message stays `CANCELLING_MESSAGE`. A running job should not report 1.0:
  completion sets `progress = 1.0` (transcribe caps at `RUNNING_MAX` = 0.99).
- A stage that cannot report progress (a model download or load) uses `stage_ticker` (a
  context manager) or `report_stage` from a worker. It shows elapsed time and a "no
  progress is available" note, and does not count as progress.
- `job_may_be_stalled` is advisory only: a running job with no progress for
  `JOB_STALL_SECONDS` (5 min), or `JOB_STALL_NO_PROGRESS_SECONDS` (15 min) in a no-progress
  stage. `jobs_service` surfaces it as `stalled` and appends a note to the message.
- `set_result(job_id, result, mirror=False)` stores the result payload. `mirror=True` also
  writes it to `job_records` at once, for a result other pollers need while the job runs.
- Per-stage durations and spend per run: `services/job_timing_service`.
  `background_jobs` starts and finishes each run; a job may call `mark_stage`. A job that
  does not gets one "Whole job" row. Last `RUNS_KEPT_PER_JOB` (10) runs are kept. Exposed
  by `GET /api/jobs/{id}/stages` (`jobs_service.get_job_stages`).
- The speed record feeds the estimates shown before a run. `transcribe_service.
  record_transcribe_speed` and `record_diarize_speed` store audio-seconds per work-second
  (and per-stage seconds) per (model, device) in `app_settings.transcribe_speed`, keeping
  the last `_SPEED_RUNS_KEPT` (5) runs. `measured_transcribe_speed`,
  `measured_stage_seconds` and `measured_diarize_speed` read the median. Runs under 5 s of
  work or outside the sane range are ignored; recording never raises, so bookkeeping
  cannot fail a finished job. Speaker detection needs `_DIARIZE_MIN_RUNS` (3) runs before
  its estimate is used; until then the caption is a range scaled from the audio length
  (`diarization_estimate_caption`).

## Job types today

A representative list, not a full registry (the service that starts a job is the source).
Thread unless marked process.

| Id | Service | Kind | GPU |
|---|---|---|---|
| `transcribe_<id>` | `transcribe_service` | process; thread for hardsub OCR | yes |
| `diarize_<id>` | `diarization_service` (also chained from transcribe) | process | yes |
| `autotune_<id>` | `transcribe_service` | process | yes |
| `retranscribe_<id>` | `transcribe_service` (one line), `retranscribe_many_service` (the ticked lines or a gap's added lines, one model load) | process | yes |
| `dub_<id>` | `dub_service` | process | when a local clone/TTS model is used |
| `resegment_<id>`, `resegpreview_<id>` | `restructure_service` | process with Ollama, otherwise thread | with Ollama |
| `resplit_<id>` | `restructure_service` | thread | yes |
| `translate_<id>`, `bulk_translate_<id>` | `translate_run_service`, `workspace_job_service` | thread | with Ollama |
| `flag_<id>`, `fixflag_<id>`, `consistency_`, `emotion_`, `notes_` and their `bulk_*` | `review_jobs_service` and others | thread | no |
| `sensevoice_<id>` | `review_extras_service` | thread | yes |
| `narration_<id>`, `audiobook_<id>`, `burned_video_<id>`, `softsub_video_<id>`, `dubbed_video_<id>` | `narration_service`, `media_export_service` | thread | no |
| `voiceref_<id>` | `voice_clone_service` | thread | no |
| `ocrchapter_<id>`, `scanlate_<id>` | `novel_attach_service`, `scanlate_pages_service` | thread | yes |
| `novel_glossary_<id>`, `lines_glossary_<id>` | `glossary_service` | thread | with Ollama |
| `extract_audio_<id>`, `urlmedia_<id>`, `lncrawl_<id>` | media upload, URL media and lncrawl services | thread | no |
| Sources: `sources_search`, `sources_save`, `sourceimport_<id>`, `sources_series_*`, `sources_signin_*` | `sources_*_service` | thread | no |
| Library: `library_backup`, `library_db_backup`, `library_user_backup`, `library_auto_backup`, `library_export_zip`, `bulk_series_translate` | `library_admin_service`, `auto_backup_service` | thread | no |
| `deno_install`, `upgrade_check`, `discover_*` | diagnostics and discover services | thread | no |
| Live capture | `live_service` | thread | with local Whisper |
| Benchmark runs | `benchmark_lab_service` | thread | for local-model stages |

## The Jobs API and what a client sees

Routes in `api/routers/jobs_routes.py`, logic in `services/jobs_service.py`:

| Route | Permission | Does |
|---|---|---|
| `GET /api/jobs` | `library.read` | every visible `job_records` row, newest started first (sweeps stale rows first) |
| `GET /api/jobs/{id}` | `library.read` | one record |
| `POST /api/jobs/{id}/cancel` | `jobs.cancel` | `cancel_job`: 404 unknown or not visible, 409 already finished, else `{cancel_requested, status}`; asynchronous |
| `POST /api/jobs/{id}/force-stop` | `jobs.cancel` | `force_stop_job`: 404 unknown or not visible, 409 unless a thread job has been Cancelling for over a minute, else closes the record as `cancelled` and returns `{force_stopped, status, worker_still_running}`; the thread itself cannot be killed |
| `POST /api/jobs/{id}/delete` | `local_only()` | removes one finished record (needs `confirm=true`; 409 if still active) |
| `POST /api/jobs/clear-finished` | `local_only()` | removes every finished record (needs `confirm=true`) |

`GET /api/events` (SSE) carries the same records, re-read through the GET route's own
service call with the stream's principal.

The client reads the `job_records` row, never the in-memory dict, with these changes
(`jobs_service._redact`, `_for_caller`):

- **Result allow-list.** `RESULT_ALLOWED_KEYS` are the only result keys stored or
  returned. Values are scalars or short lists of scalars: strings are capped
  (`_MAX_STR` 500) and URLs replaced with `[URL]`; lists are capped (`_MAX_LIST` 20); the
  JSON is capped (`_MAX_JSON` 8000 bytes). A key not on the list is dropped, also from
  older stored rows (re-projected on read). A new result key a client needs must be added
  to `RESULT_ALLOWED_KEYS`.
- **Redaction.** `message`, `error` and the outcome text go through
  `diagnostics.redact_for_support` (secrets removed, absolute paths collapsed to
  `.../name`). `owner_pid` is dropped, and the response carries `owned_by_me`, never an
  owner id.
- **Normalised outcome.** `derive_outcome` adds `outcome` (`ok`, `failed`, `cancelled`,
  `partial`, `kept_existing`) and `outcome_message`, so a client does not need each job's
  result shape. A `done` job with warnings (CPU fallback, capped spend, partial work)
  reads `partial`.
- **Flags.** `stale` (an active row with no live owner, judged on the server's clock) and
  `stalled` (no progress for a while).
- **Visibility.** `ownership_service.can_see_job`: the PC owner, admins and auth-off see
  every job. Otherwise a `<prefix><drama_id>` job follows its title's visibility and any
  other job is visible only to its starter. A job you may not see is a 404.

A cancelled job after a restart reads `cancelled` with an `Interrupted: ...` error, and
its outcome message says so.

## The CLI

`cli.py` is a headless batch runner. It does not use `start_job`: each command runs its
work inline in the CLI process (for example `translate_engines.translate_lines_with_engine`
in `cmd_translate`) and writes no `job_records` rows. Differences that matter:

- **GPU slot.** A GPU step wraps its work in `_gpu_lock`, which loops on
  `background_jobs.try_take_gpu_slot("cli:<pid>", ...)` (waiting, printing what it is
  busy with) and releases the row on exit. So the "GPU jobs at once" cap and the free-VRAM
  check apply to the CLI like app jobs, and a CLI GPU run blocks a UI job (and the reverse).
  The CLI does not pass `check_external_load`, so it skips the nvidia-smi external-load
  check. It refreshes its row with `db.heartbeat_gpu_lock`.
- **Not seen by the Jobs API.** A CLI run is visible to other code only while it holds the
  GPU lock (`db.gpu_lock_status`); a CLI run of non-GPU steps leaves no trace.
- **Replacing lines.** Before writing a wholesale result it calls `cancel_line_jobs` and
  sets `job_records` cancel flags for the API's line-writing jobs.
- CLI and app must behave the same for glossary, style guide, locale and character names:
  when you change the job path, check the CLI path too.

## Rules learned from bugs

- Jobs write only the fields they own (`fields=...`); match results to lines by id.
- Pass any error text through `translate_engines.redact_secrets` before it is stored,
  shown or logged. `job_records` and every response are redacted again on the way out.
- Do not hold `_lock` while doing slow work (database, files, `join`, hooks); `on_done`
  and `on_finish` run outside it.
- A final status does not mean the thread is done (see Threads that outlive the status).
- In tests, wait for job threads before asserting (`background_jobs.wait_for_job_threads`,
  or poll the status). Tests are mocked: no network, GPU or real models.
- Poll a background process with `kill -0 <pid>`. Never use `pgrep -f <pattern>` when
  the pattern also appears in your own command line: it matches itself and never ends
  (`docs/testing-and-ci.md`).

## Adding a new job type

1. Pick the id: `<prefix><drama_id>` for a title's job (add the prefix to
   `DRAMA_JOB_PREFIXES`, and to `LINE_WRITING_JOB_PREFIXES` only if it writes its own
   line fields and is safe beside other jobs) or a fixed `*_JOB_ID` constant.
2. Put the logic in a `services/*_service.py` function: check ownership
   (`services/ownership_service.py`), validate, raise the `service_errors` types, and
   turn a `False` from the start into `ConflictError`.
3. Choose thread or process. Process if there is no cancel checkpoint or it needs CUDA
   (then `start_method="spawn"`, and `kill_whole_tree=True` plus
   `start_own_process_group()` if it starts ffmpeg or similar).
4. Set `gpu_touching=True` when it loads a local model, and a short `description`
   (never another job's name).
5. Keep the worker pure: plain Python in and out. Apply the result in `on_done` (process)
   or at the end of the job (thread), writing only your fields. Use `on_finish` to remove
   scratch files.
6. Report progress (`update_progress` / `report_progress`; `stage_ticker` / `report_stage`
   for stages without progress). Check `is_cancel_requested` between units of work, and
   use `run_cancellable` for external commands.
7. If a client needs result fields, put them in `set_result` and add the keys to
   `jobs_service.RESULT_ALLOWED_KEYS`; add a `derive_outcome` case if the result means
   `partial` or `failed`.
8. Redact anything stored or shown (`redact_secrets`); never put keys, paths or URLs in
   results.
9. If it uses the CLI too, make the CLI path match (including the GPU slot).
10. Tests: mock the worker, use the `isolated_db` fixture, wait for job threads before
    asserting. Register new routes with a permission (`tests/test_api_permissions.py`).
