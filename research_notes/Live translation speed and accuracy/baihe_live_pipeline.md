# Baihe Live pipeline: how it works today, where latency and inaccuracy come from, and what to change

Scope: codebase analysis of `/home/user/U00` at `origin/baihe-subtitler` HEAD `8231d96d` (2026-10-10). Sources are repo files with line numbers. "Inference" marks anything not read directly from code or a repo measurement. No live run was made; no timing number below comes from a real stream.

## 1. End-to-end entry points and data flow

### Takeaway
Live is one background thread job that runs a serial loop: yt-dlp resolves the page, a Python fetcher pipes HLS or direct bytes into ffmpeg, ffmpeg cuts fixed, non-overlapping 16 kHz mono WAV chunks (default 20 s), and each chunk goes through faster-whisper (with a 3 s audio overlap and the previous text as `initial_prompt`). Then every resulting cue is translated with its own LLM call, on the same thread. Cues reach the browser through a job-result change event on SSE plus a GET from a cursor. The LocalAgreement-2 code (`StreamAgreement`) is NOT wired in, even though `docs/STATUS.md` says it is.

### Cited Findings
**Frontend**
- The Live page is `frontend/src/pages/Live.tsx` (425 lines) plus `frontend/src/pages/live/` (`LiveCaptions.tsx`, `StreamEmbed.tsx`, `captions.ts`, `embedLogic.ts`). Form defaults are `source_language 'zh'`, `whisper_size 'small'`, `segment_seconds 20`, `overlap_seconds 3`, `max_minutes 60`, `use_gpu false` and `reply_without_thinking true`. The default engine is Ollama with model `gemma4:12b`. — [frontend/src/api/live.ts:42-72](frontend/src/api/live.ts)
- The "Fast captions" preset is `segment_seconds 4`, `overlap_seconds 1`, `whisper_size 'small'`. — [frontend/src/api/live.ts:78-83](frontend/src/api/live.ts)
- The Whisper sizes offered are `['tiny','base','small','medium']`. Chunk range is 3-60 s, overlap range 0-8 s. `POLL_MS = 2000`. — [frontend/src/api/live.ts:29-38](frontend/src/api/live.ts)
- Delivery: the page subscribes to `GET /api/events` (topic `live`). A pushed status carries `cues: []`. When `next_index` has grown or a cue is still pending, the page calls `GET /api/live/sessions/{id}?after=<oldest pending cue>` and merges the result by id. Timer polling every `POLL_MS` runs only while the event stream is down. — [frontend/src/pages/Live.tsx:126-197](frontend/src/pages/Live.tsx)
- Caption overlay: a cue is due at `max(origin + (cue.start + delay)*1000, arrivedAt)`. The hold is 3-8 s (`HOLD_MIN_S`/`HOLD_MAX_S`). It shows the translation once `translation === 'done'`, otherwise the transcript. — [frontend/src/pages/live/captions.ts:12-62](frontend/src/pages/live/captions.ts)
- The overlay ticks every 500 ms. — [frontend/src/pages/live/LiveCaptions.tsx:11,30](frontend/src/pages/live/LiveCaptions.tsx)
- The video delay slider is 0-90 s, `DEFAULT_DELAY = 15`. — [frontend/src/pages/live/embedLogic.ts:12-13](frontend/src/pages/live/embedLogic.ts)

**Routes**
- `api/routers/live_routes.py` has these routes:
  - `POST /api/live/sessions` (`media.import_url`, plus the engine and cloud-model gates)
  - `GET /api/live/sessions`
  - `GET /api/live/ollama-check`
  - `GET /api/live/sessions/{id}?after=`
  - `POST /api/live/sessions/{id}/stop` (`jobs.cancel`)

  Saved cookies are used only for local requests. — [api/routers/live_routes.py:31-74](api/routers/live_routes.py)
- SSE loop: it coalesces bursts with `MIN_BATCH_SECONDS = 0.25`, sweeps jobs every 5 s and sends a heartbeat every 15 s. — [services/event_stream_service.py:39-41](services/event_stream_service.py); [api/routers/events_routes.py:108-150](api/routers/events_routes.py)
- `background_jobs.set_result` calls `_emit_change(job_id)`, which is what pushes the `live` event. — [background_jobs.py:1575-1590](background_jobs.py)

**Service**
- `services/live_service.start_session` validates its inputs:
  - `WHISPER_SIZES = ("tiny","base","small","medium")`, `SEGMENT_RANGE (3,60)`, `OVERLAP_RANGE (0,8)`
  - the default engine is `ollama`
  - one session at a time (409)
  - a start is refused while an abandoned Whisper call from an earlier session is still alive

  It builds the engine and starts the job `live_<uuid>` with `gpu_touching=bool(use_gpu)`. — [services/live_service.py:53-60,257-366](services/live_service.py)
- The job target wraps `live_translate.run_live_job` in an `egress_proxy.GuardedProxy`. — [services/live_service.py:227-240](services/live_service.py)
- `get_session` returns cues from `after`, with secrets and paths redacted. — [services/live_service.py:407-440](services/live_service.py)

**Capture**
- `resolve_stream_url` calls yt-dlp `extract_info(download=False)` with `bestaudio/best`, then `best`, then the player clients `android`, `tv` and `web_safari`. It names JS runtimes for EJS. — [live_translate.py:116-192](live_translate.py)
- `start_segment_capture` runs ffmpeg with `-protocol_whitelist pipe -format_whitelist mpegts,mov,aac,mp3,flv,matroska,ogg,wav -i pipe:0 -vn -ac 1 -ar 16000 -f segment -segment_time <S> -reset_timestamps 1 chunk_%05d.wav`. A `live_fetch.StreamPump` feeds its stdin. — [live_translate.py:200-256](live_translate.py)
- `live_fetch` follows HLS itself:
  - It picks the master's lowest-bandwidth variant or audio rendition.
  - It joins a live playlist `LIVE_EDGE_SEGMENTS = 3` segments from the end.
  - It reloads after one target duration, or half of one when nothing new arrived.
  - It decrypts AES-128.
  - `STALL_TIMEOUT = 60 s`, `CONNECT_TIMEOUT 10`, `READ_TIMEOUT 20`, `QUEUE_CHUNKS = 32` (backpressure).

  — [live_fetch.py:32-52,528-561](live_fetch.py)
- There is no DASH handling: ffmpeg's format whitelist excludes `dash`/`hls` on purpose. — [live_translate.py:195-200](live_translate.py)
- The browser-extension and caption-overlay paths do not feed Live audio. The extension is a Sources capture path (STATUS line 16). The "caption overlay" is the page's own `LiveCaptions` drawn over `StreamEmbed`. — [docs/STATUS.md:15-16](docs/STATUS.md); [frontend/src/pages/Live.tsx:386-387](frontend/src/pages/Live.tsx)

**Chunking and windowing**
- A chunk is processed only once its successor file exists, so ffmpeg has finished it. — [live_translate.py:290-306](live_translate.py)
- The loop sleeps `poll_interval = 2.0` s when no chunk is waiting. — [live_translate.py:557,843-846](live_translate.py)
- The overlap is implemented as follows:
  - The last `overlap_seconds` of chunk N's PCM is prepended to chunk N+1 (`window_for_chunk`, `write_padded_chunk`).
  - Overlap is capped at half a chunk.
  - A chunk longer than `S + 2 s` (`OVERSIZE_SLACK_SECONDS`) is trimmed to its newest S seconds.

  — [live_audio.py:61-132](live_audio.py); [live_translate.py:610](live_translate.py)
- Dedup of the re-heard overlap is `dedup_overlap`. It looks for the longest exact token suffix/prefix match between the previous chunk's tail text and the new head, with up to 2 garbled leading tokens skipped. If nothing matches, it drops only the segments that end inside the overlap. There is no fuzzy matching. — [live_translate.py:326-403](live_translate.py)
- Tokenizer: one token per CJK/kana character, one per run of other letters. Punctuation is ignored. — [live_tokens.py](live_tokens.py)

**ASR call**
- `process_chunk` calls `core.transcribe_for_timing(chunk, model_size=whisper_size, language=source_language, use_gpu, initial_prompt=context_prompt, progress_cb)` through `WhisperRunner.run`. — [live_translate.py:479-481](live_translate.py)
- Every other argument keeps its default: `beam_size=5`, `min_silence_duration_ms=2000`, `vad_threshold=0.5`, `filter_hallucination_repeats=4`, `fast_mode=False`, `hallucination_silence_sec=DEFAULT_HALLUCINATION_SILENCE_SEC (0.0)`, `sensitivity_preset="normal"`. `vad_filter=True` and `word_timestamps=True` are always on. `condition_on_previous_text=False` comes from `WHISPER_ANTI_LOOP_KWARGS`. — [core.py:305-314,459-466,539-550](core.py)
- Model load: CPU uses `int8`, GPU uses `float16`, and models are cached per size and device. — [whisper_models.py:213-235](whisper_models.py)
- `context_prompt` is the last 200 characters of the previous chunk's emitted cue text (`_CONTEXT_CARRY_CHARS = 200`). It is reset to empty after a backlog drop. — [live_translate.py:406-411,756-761,808-814](live_translate.py)
- Before capture, a warm start loads the model and decodes 2 s of low-level noise with VAD off, so the CUDA init does not land on chunk 1. — [live_whisper.py:355-370,444-450](live_whisper.py); [live_translate.py:645-660](live_translate.py)
- There is a single Whisper worker thread per process. A call that is abandoned (Stop or time limit) blocks any later call: those chunks are skipped as `WhisperBusy`. Chunk timeout is `max(30, 6 × S)`. — [live_whisper.py:22-31,56-57,186-269](live_whisper.py)

**Translation call**
- `live_cue_feed.translate_cues` publishes the untranslated cues first. It then translates them in order, calling `translator.translate(cue["text"])` once per cue and republishing after each. — [live_cue_feed.py:39-59](live_cue_feed.py)
- `CueTranslator.translate` calls `engine.translate_batch([text], context)` with one line per call. The context is `build_live_context`:
  - `drama_meta={"source_language", "content_mode":"streamer_vod", "genre":"livestream"}`
  - `recent_context` = the last `RECENT_CUES = 4` (source, translation) pairs, each capped at 200 chars
  - `recent_as_data=True`, `line_ids=[n]`, `reply_without_thinking`

  — [live_cue_translation.py:12-61](live_cue_translation.py)
- No glossary, style guide, locale, character or speaker names, language pack, title or `upcoming_lines` is sent. Live does not call `translate_engines.build_translation_context`. Its only callers are compare_transcription, translate_run, stronger_engine, workspace_job, scanlate and translate_pipeline. — grep of `build_translation_context`; [engine_backends/translate_pipeline.py:230-255](engine_backends/translate_pipeline.py)
  - The `build_translation_context` docstring says it is "shared by live translation and bulk submission so both send the same prompt". That is stale: Live has its own builder.
- The prompt actually sent (built locally with `build_stable_system_text` / `build_batch_user_message` for one cue plus 4 recent pairs) is a 1,246-character system prompt and a 442-character user message. The prompt says "Write in American English" because `locale` defaults to `en-US`. — run of `engine_backends/prompts.py:378-441` with `live_cue_translation.build_live_context`
- Ollama request: `"stream": False`, `format` = an id-keyed JSON schema, `options.num_ctx = max(16384, estimate)` (`OLLAMA_MIN_NUM_CTX = 16384`), request timeout 300 s (900 s for gemma4:26b/31b). — [engine_backends/local.py:25-45,100-112,330-375](engine_backends/local.py)
- Results are matched by id (`request_translations_with_retry`, `max_retries=1`). — [engine_backends/shared.py:529-560](engine_backends/shared.py)

**Output and stability**
- Each cue is `{id, start, end, text, translated, translation: pending|done|failed|cancelled}`. A cue's id is its position in a list that only grows, and cues are never revised. — [live_cue_feed.py:1-30](live_cue_feed.py)
- There is no partial-hypothesis display. A cue appears only after a whole chunk is decoded, and it is final at once. The "transcript first" step means the transcript is shown before its translation, not before the chunk is finished.

**Errors, stalls and catch-up**
- Backlog: if more than `MAX_BACKLOG_CHUNKS = 2` completed chunks are waiting, the older ones are deleted unprocessed and a counted note is shown ("Skipped N chunks (~X s) to catch up"). — [live_translate.py:418-421,756-761](live_translate.py); [live_whisper.py:303-314,347-352,386-420](live_whisper.py)
- Chunk timeout leads to `ChunkTimeout`, which skips the chunk. A busy abandoned call leads to `WhisperBusy`, which skips the chunk. Stop bumps a generation counter so an in-flight chunk's result is discarded. — [live_translate.py:492-516,800-834](live_translate.py)
- An abandoned GPU decode keeps a `gpu_lock` claim (`live-whisper:<pid>`) until its thread exits. — [live_whisper.py:69-183](live_whisper.py)
- When ffmpeg exits normally the loop breaks ("Capture stopped (stream likely ended)") before it processes any chunk still waiting. The final chunk has no successor file, so it is never listed. — [live_translate.py:745-757,290-306](live_translate.py)
- Per-chunk timing is logged as `chunk i: audio, waited (file age), Whisper, translate`. GPU free VRAM is added only when a GPU chunk was slow. A chunk taking more than 3× its audio gets a status note. — [live_whisper.py:423-481](live_whisper.py)
- Tests: `tests/test_live_stalls.py` has 49 tests (hung chunk skipped, one thread and one model load, backlog drop, warm start, Ollama unload per job, notes). `tests/test_live_fetch.py` has 26 tests (edge join, rejoin on sequence restart, AES, stalls, prompt stop, deadlines). — test name listing via grep

### Inferences
- The test `test_a_failing_or_slow_translator_never_blocks_the_next_chunks_whisper` ([tests/test_live_stalls.py:280-295](tests/test_live_stalls.py)) uses a translator that fails after 0.05 s. It proves Whisper keeps being called, but it does not prove that translation runs concurrently. In code, translation is inline in `process_chunk` ([live_translate.py:488](live_translate.py)) and the loop waits for it. A slow translator therefore delays the next chunk's Whisper, and if the delay is long enough it causes catch-up drops.
- The STATUS claim "LocalAgreement-2 streaming recognition" ([docs/STATUS.md:15](docs/STATUS.md)) does not match the code. `StreamAgreement` is referenced only in `live_agreement.py` and `tests/test_live_agreement.py`, which matches `docs/local-agent-backlog.md:23`.

### Gaps
- How long yt-dlp resolution takes per platform is not measured in the repo.
- Which platforms deliver HLS vs a direct progressive stream to Live is not recorded.
- `#EXT-X-PROGRAM-DATE-TIME` is not parsed (`grep` finds nothing in `live_fetch.py`), so the code cannot measure how far behind the true live edge the capture is.

## 2. Latency budget (glass-to-caption)

### Takeaway
The structural minimum is set by the chunk length. Speech waits on average S/2 (10 s at the default S = 20) for its chunk to close, then Whisper on S + overlap seconds of audio, then a serial per-cue LLM call, plus the HLS lag the fetcher starts with (3 segments behind the playlist end). Using the repo's own CPU benchmark for Whisper small, a mid-chunk word at default settings shows up about 18 s plus translation time after it reaches the fetcher, before any platform or HLS delay. The largest contributors are (1) chunk length, (2) Whisper time, which is serialized with (3) per-cue translation, and (4) the 3-segment HLS join.

### Cited Findings (each stage and its configured size)
| # | Stage | Constant / default | Source |
|---|---|---|---|
| 0 | yt-dlp resolve (once per session) | up to 5 attempts (format x client); no timeout of its own, abortable via `_run_abortable` 0.25 s poll | [live_translate.py:155-170,519-542](live_translate.py) |
| 0 | Model warm start (once, before capture) | `WARM_UP_TIMEOUT = 900` s cap; 2 s noise decode | [live_whisper.py:27,32-33](live_whisper.py) |
| 1 | HLS join point | `LIVE_EDGE_SEGMENTS = 3` from playlist end | [live_fetch.py:48-49,541](live_fetch.py) |
| 2 | HLS playlist reload | every target duration T (T/2 if nothing new); thread poll `_POLL = 0.2` s | [live_fetch.py:52,553-556](live_fetch.py) |
| 3 | Chunk accumulation | `segment_seconds` S = 20 default (3-60), Fast captions 4 | [services/live_service.py:54,257](services/live_service.py); [frontend/src/api/live.ts:61-83](frontend/src/api/live.ts) |
| 3b | Chunk "done" proof | successor file must exist | [live_translate.py:290-306](live_translate.py) |
| 4 | Loop poll | `poll_interval = 2.0` s (not overridable from the service) | [live_translate.py:557,846](live_translate.py) |
| 5 | Overlap re-decode | `DEFAULT_OVERLAP_SECONDS = 3` (Fast: 1), capped at S/2; Whisper gets S + overlap s | [live_translate.py:416,610](live_translate.py); [live_audio.py:106-132](live_audio.py) |
| 6 | Whisper decode | beam 5, VAD on (2000 ms min silence), word timestamps; time limit `max(30, 6×S)` = 120 s at S = 20 | [core.py:459-550](core.py); [live_whisper.py:24-25,56-57](live_whisper.py) |
| 7 | Translation | one LLM call per cue, serial; Ollama non-streaming, 300 s timeout; `num_ctx >= 16384` | [live_cue_feed.py](live_cue_feed.py); [engine_backends/local.py:28,101-112](engine_backends/local.py) |
| 8 | Push to browser | `set_result` → `_emit_change`; SSE coalesce 0.25 s; then one GET | [background_jobs.py:1575-1590](background_jobs.py); [services/event_stream_service.py:41](services/event_stream_service.py) |
| 9 | Overlay | 500 ms tick; shown at `max(origin+start+delay, arrival)`; default video delay 15 s | [LiveCaptions.tsx:11](frontend/src/pages/live/LiveCaptions.tsx); [captions.ts:37-42](frontend/src/pages/live/captions.ts); [embedLogic.ts:13](frontend/src/pages/live/embedLogic.ts) |
| — | Backlog | more than 2 waiting chunks → oldest dropped (audio lost, not delayed) | [live_translate.py:421](live_translate.py) |

- The repo's only Whisper speed data for stream audio is CPU-only (4 cores, int8, faster-whisper 1.2.1) on a 15-minute Chinese streamer VOD. Wall time for the first 5 min (300 s), including model load, was:

  | Model | Wall time | CER |
  |---|---|---|
  | small | 92 s | 23.4 |
  | large-v3-turbo | 145 s | 16.4 |
  | medium | 236 s | 17.5 |

  Beam 1 took 127 s vs 145 s for turbo. — [docs/asr-experiments.md:329-362](docs/asr-experiments.md)
- Clean-read-speech RTF on CPU: turbo 0.36/0.56/0.69 (ko/ja/zh), medium 0.53/0.84/0.94. — [docs/asr-experiments.md:234-237](docs/asr-experiments.md)
- No GPU speed or memory figures exist in the repo ("CPU only; no GPU speed or memory figures"). — [docs/asr-experiments.md:326](docs/asr-experiments.md)

### Inferences (computed from the constants above; not measured)
- **Whisper time per default chunk (CPU, small):** 23 s of audio × an RTF of about 0.31 (92/300, load included, so an upper estimate) ≈ 7 s. With turbo, RTF ≈ 0.48 gives ≈ 11 s; with medium, ≈ 0.79 gives ≈ 18 s.
- **Budget at default settings, excluding the platform/HLS part:**

  | Case | Chunk wait | Poll | Whisper | Push | Total before translation |
  |---|---|---|---|---|---|
  | Best (word at chunk end) | ≈ 0 | 0 | ≈ 7 | < 1 | ≈ 7-8 s |
  | Typical (mid-chunk) | 10 | ≈ 1 (avg) | ≈ 7 | < 1 | ≈ 18-19 s |
  | Worst (word at chunk start) | 20 | 2 | ≈ 7 | < 1 | ≈ 29-30 s |

  On top of each total:
  - Translation of that cue, plus all earlier cues of the same chunk, because they are translated in order.
  - The HLS offset: about 3-4 target durations behind the newest segment the platform has published (the 3-segment join plus up to one reload interval). T is platform-dependent and not in the repo.
- **Fast captions (S = 4, overlap 1):** the chunk wait falls to about 2 s on average. However, faster-whisper pads every call's mel input to a 30 s window (a property of Whisper's encoder, not of this repo), so a 5 s call likely costs a large share of what a 23 s call costs. On CPU, Fast captions may therefore run slower than real time, and catch-up drops (audio loss) would replace delay. This needs measuring (see "Measure first").
- **Serial pipeline:** the job needs Whisper(S+o) + Σ translate(cues) ≤ S to keep up. Otherwise a growing backlog is cut to 2 chunks and the oldest audio is dropped ([live_translate.py:756-761](live_translate.py)). With a local 12B model and roughly 3-8 cues per 20 s chunk (cue count not measured), translation can plausibly rival Whisper.
- **Video delay vs chunk length:** the default video delay (15 s) is shorter than the default pipeline's typical lag (≈ 18 s + translation). Captions for most speech will therefore appear late against the delayed picture: they are shown at arrival, which is later than `origin+start+delay`.
- **Ranked contributors:** (1) chunk length S (avg S/2, worst S); (2) Whisper time per chunk; (3) per-cue serial translation, which also blocks the next Whisper call; (4) the 3-segment HLS join and reload; (5) the 2 s loop poll (avg ~1 s). SSE delivery (≤ 0.25 s plus one local GET) and the overlay tick (≤ 0.5 s) are minor.

### Gaps
- No per-chunk timing log from a real session is in the repo. `log_chunk` writes one to the app log, but none is recorded in docs.
- No GPU RTF numbers exist. Per-cue translation latency for `gemma4:12b` or the hosted engines is not recorded.
- No measurement exists of the HLS target duration on the owner's platforms.

## 3. Accuracy levers present or missing

### Takeaway
Present: a 3 s audio overlap with exact-match dedup, a 200-character previous-text `initial_prompt`, VAD, `condition_on_previous_text=False`, within-chunk repeat-loop and stock-phrase filters, and 4 recent translated pairs as translator context. Missing: LocalAgreement (written but unwired), any name or glossary priming for Whisper or the translator, cross-chunk hallucination suppression, retroactive re-translation of a cut sentence, and the models that the repo's own benchmark says are better (turbo is not offered).

### Cited Findings
- **`StreamAgreement`** is defined in `live_agreement.py`:
  - `MIN_WINDOW_SECONDS 2`, `MAX_WINDOW_SECONDS 15`
  - `FORCE_COMMIT_AGE_SECONDS 6`, edge guard 1 s
  - `LINE_CLOSE_SILENCE_SECONDS 0.7`, `LINE_MAX_SECONDS 7`, `LINE_MAX_CJK_CHARS 40`
  - a stock-hallucination check through `core.filter_hallucinated_segments`

  — [live_agreement.py:16-187](live_agreement.py)
- It is imported only by `tests/test_live_agreement.py` (grep across *.py, *.ts, *.tsx and *.md). The backlog says it "exists in `live_agreement.py` but nothing in Live uses it yet; `live_audio.py` has chunk windows but no ring buffer; the streaming orchestration module does not exist yet". — [docs/local-agent-backlog.md:23,57-63](docs/local-agent-backlog.md)
- The agreement work was merged as #990 (`0a684a86`). Branch `origin/step-live-streaming-a-agreement` exists. — `git log`
- **Context carry-over:** the previous chunk's emitted text tail (200 chars) is used as `initial_prompt`. It is cleared after a backlog drop. — [live_translate.py:411,761,808-814](live_translate.py)
- Batch transcription instead uses `build_auto_initial_prompt(drama_id)`, made of the series glossary names and extra names (capped at 900 chars). — [services/transcribe_service.py:228-261](services/transcribe_service.py)
- Repo guidance: "Use only names that occur in the series and keep it short. A prompt of the utterance's own names ... gave -0.7 points on clean audio and -3.8 on very bad audio". — [docs/asr-experiments.md:139](docs/asr-experiments.md)
- **Dedup:** exact token match only. The docstring says plainly that a line straddling the boundary "can still repeat a few words". An earlier line is never retracted or re-translated. — [live_translate.py:41-58,335-361](live_translate.py)
- **Hallucination guards in the live path:**
  - VAD on.
  - Within-chunk collapse of 4+ identical consecutive segments, and stock credit/outro phrases dropped (`filter_hallucinated_segments`).
  - `hallucination_silence_threshold` off (0.0).
  - Repeat guard off. — [core.py:305-314,392-422,539-570](core.py)
  - Segments with no letter, digit or CJK character are dropped. — [core.py:553-558](core.py)
- **Translator context:** 4 recent pairs, delimited as data (`<recent_lines>`). There are no upcoming lines. — [live_cue_translation.py](live_cue_translation.py); [engine_backends/prompts.py:242-284](engine_backends/prompts.py)
- **Model choice:** Live allows tiny/base/small/medium only. — [services/live_service.py:53](services/live_service.py)
  - Batch defaults to `large-v3-turbo`, described as "close to large-v3 in our tests, about 2x faster". — [whisper_models.py:11-21](whisper_models.py)
  - On the streamer VOD, small's CER was 23.4 vs turbo's 16.4, and medium (17.5) was slower than turbo (236 vs 145 s). — [docs/asr-experiments.md:346-362](docs/asr-experiments.md)
  - "Medium wrote Traditional characters far more often" on Chinese. — [docs/asr-experiments.md:123,152](docs/asr-experiments.md)
- **VAD silence:** Live uses the function default of 2000 ms; batch uses 300 ms. — [core.py:462](core.py); [services/transcribe_service.py:72](services/transcribe_service.py)
  - On the streamer VOD, 2000 ms scored 16.2 vs 16.4 at 300 ms, i.e. no measurable harm. — [docs/asr-experiments.md:355](docs/asr-experiments.md)
- **Beam:** beam 1 on the streamer VOD gave CER 18.3 vs 16.4 and wall time 127 vs 145 s (turbo). — [docs/asr-experiments.md:351](docs/asr-experiments.md)
- **Locale:** the Live context sets none, so the prompt is always "American English". Other services read `settings_service.get_preference("default_locale")`. — [services/compare_transcription_service.py:281](services/compare_transcription_service.py); [engine_backends/prompts.py:92-96](engine_backends/prompts.py)

### Inferences
- **Hallucination carry-over:** the hallucination filter runs per chunk ([core.py:567-568](core.py)), so a phrase hallucinated once per 20 s chunk over music is never a run of 4 and passes through.
- Because the emitted text becomes the next chunk's `initial_prompt`, a hallucinated phrase can seed the next chunk's decode. This is a known Whisper failure pattern, and the code has no guard against it.
- **Prompt and overlap together:** the carried prompt contains the text of the very overlap audio being re-decoded. This may bias Whisper to repeat or skip that text in the overlap. Not measured.
- **Name consistency** depends only on 4 recent pairs. A name not said in the last 4 cues has nothing anchoring its spelling or translation, either in ASR or in MT.

### Gaps
- No accuracy measurement of Live chunking vs whole-file decoding exists in the repo. `asr-experiments.md` measures batch only.
- No measurement of dedup failure rates exists.

## 4. Resource contention (GPU, LLM slots, VRAM)

### Takeaway
Live takes a normal GPU job slot only when "Use GPU" is on (`gpu_touching`). With the default `gpu_max_parallel = 1`, a Live session and a GPU transcription job queue behind each other for up to the session's `max_minutes`. Live never uses `api/llm_slots` (those cap synchronous request-handler LLM calls). On GPU with local Ollama, Whisper and the translation model share VRAM. The code frees Ollama once per job, and the test that reproduces the original chunk-1 stall describes it as "Ollama's translation model is resident, the next Whisper call crawls".

### Cited Findings
- The job starts with `gpu_touching=bool(use_gpu)`. — [services/live_service.py:350](services/live_service.py)
- GPU admission requires `_running_gpu_job_count_locked < gpu_max_parallel` and `try_take_gpu_slot` (cross-process `gpu_lock`, an external-load check for the first holder). A job that cannot get a slot queues. — [background_jobs.py:189-260,420-450](background_jobs.py)
- `gpu_max_parallel` defaults to 1. — [background_jobs.py:192](background_jobs.py)
- A parallel join needs `GPU_PARALLEL_RESERVE_MB` (2048) free and a 30 s settle. — [docs/background-jobs.md:183-192](docs/background-jobs.md)
- `GPU_LOCK_MAX_SLOTS = 4`, `GPU_LOCK_STALE_SECONDS = 600`. — [db.py:4883-4885](db.py)
- An abandoned Live GPU decode claims a `gpu_lock` row up to `GPU_LOCK_MAX_SLOTS`, so a queued GPU job is not promoted into its VRAM. Queued jobs show "Waiting for the GPU (a Live Whisper call is still finishing)". — [live_whisper.py:95-183](live_whisper.py)
- `api/llm_slots.py`: `LLM_MAX_IN_FLIGHT = 2`, for synchronous request-handler work only (Reader, translate route, line-ai and so on). Live's translator runs in the job thread and is not listed. — [api/llm_slots.py:1-25](api/llm_slots.py)
- **Ollama/VRAM:**
  - `ollama_unload` exists because Ollama keeps the model loaded for 5 minutes and "a Whisper / Qwen3 load that starts inside the window can run out of VRAM and fall back to the CPU". — [ollama_unload.py:1-12](ollama_unload.py)
  - The Live `JobScope` frees a local Ollama once per job. It frees it again only while no Whisper call has succeeded and free VRAM is below the model's estimate (`small` 2000 MB, `medium` 3500, large 5500). — [ollama_unload.py:47-80](ollama_unload.py); [live_whisper.py:324-330,373-383](live_whisper.py)
- The test reproducing the original stall shape: "Ollama's translation model is resident, the next Whisper call crawls, and the loader hook runs on every call. Ollama is freed once, the slow chunk is given up on". — [tests/test_live_stalls.py:241-245](tests/test_live_stalls.py)
- PR #1007 (`d203080f`): "A abandoned Whisper call is no longer followed by a second thread and a second model load ... Ollama is freed once per Live job ... per-chunk timings are logged and a chunk over 3x its audio is called out". — `git show d203080f`
- Ollama requests ask for `num_ctx ≥ 16384` regardless of prompt size, and Live's prompt is about 1.7k characters. — [engine_backends/local.py:28,31-38](engine_backends/local.py)

### Inferences
- **The VRAM-oversubscription hypothesis is consistent with the code but unproven.** Whisper is loaded first (warm start), then the first translation reloads `gemma4:12b` with a 16k context next to it. On a card where both do not fit, either Ollama spills layers to CPU (slow translation, which then blocks the next Whisper call because the pipeline is serial) or Whisper's CUDA allocations contend. `log_chunk` already records GPU free MB on slow GPU chunks, which is the data needed.
- **Live with Use GPU off** (the form default) holds no GPU slot. A GPU transcription job started meanwhile runs `ollama_unload` for its own load, which can evict Live's translation model, and Live's next cue then pays a model reload. The two also compete for CPU when Live's Whisper runs on CPU. Not measured.
- **Live with Use GPU on** while a transcription job runs: the Live session sits `queued` until the transcription ends. Conversely, a transcription started during Live waits up to `max_minutes` (60 by default).

### Gaps
- There is no recorded VRAM reading from a real Live session with a local Ollama. The owner's GPU model and VRAM size are not in the repo.

## 5. CLI/app parity

### Takeaway
There is no Live path in the CLI. `cli.py` subcommands are narrate-prep, list, inspect-line, set-language, clean-en, align, diarize, dub, transcribe, qc, glossary (list/add/remove), import, export, run, export-video, doctor; translate is in `cli_translate.py`. Parity is therefore between Live and the app's own batch path. Live skips everything `workspace_job_service.build_run_style_context` provides.

### Cited Findings
- `grep -iln "live_translate|live_service|run_live_job" cli*.py` finds nothing. — repo grep
- `build_run_style_context(drama_id, drama, lines, style_preset, ...)` returns `(glossary_terms, style_guidelines, character_names)`. It covers the series glossary, style profile, emotion guidance, gender hints, speaker labels and the title's language packs, and is "shared by the app's runs and `cli.py translate`". — [services/workspace_job_service.py:55-83](services/workspace_job_service.py)
- Language packs are selected by scanning the source text of the run's lines (`block_for(drama, lines, ...)`). The block is chosen "from every line the run translates so all of its batches share one identical (cacheable) prompt". — [services/language_pack_service.py:119-127](services/language_pack_service.py)
- Live sends none of glossary, style guide, locale, characters or language pack (see section 1). Whisper gets no name prompt (section 3).
- Owner decision (2026-10-10, from the brief): Live should get an optional title picker whose glossary, language packs and characters apply. Nothing in the repo implements it yet: `LiveSessionStart` has no title field ([api/routers/live_routes.py:38-43](api/routers/live_routes.py)).

### Inferences
- **Title picker design:** `start_session(drama_id=...)` should check ownership, then call `build_run_style_context(drama_id, drama, [], style_preset)` once at start. That gives one stable system prompt per session, which suits Ollama/DeepSeek/Gemini prefix reuse and Claude caching. Whisper should get `transcribe_service.build_auto_initial_prompt(drama_id)` combined with the carried tail text.
- **Language packs:** `block_for` needs lines. Passing `[]` yields no pack, so Live needs either the whole pack or a per-session selection made from the glossary alone. A per-chunk reselection would change the system prompt every chunk and break prefix caching.
- **CLI parity:** if a CLI Live command is not wanted, the PR should state that Live is app-only, per the CLAUDE.md parity rule.

### Gaps
- Whether the owner wants a CLI Live command is not recorded.

## 6. Other most-used flows: cheap wins with code evidence

### Takeaway
Bulk translation already uses large id-keyed batches (30 by default) with a stable, cacheable system prefix and per-batch context in the user message. The "cache breakpoint" fix is on an unmerged branch. Transcription defaults agree with the repo's benchmarks, and the measured speed lever (beam 1) costs accuracy on stream audio. No code-backed export win was found.

### Cited Findings
- Bulk/translate defaults are `context_window 10`, `context_window_ahead 6`, `batch_size 30`; `MAX_BATCH_SIZE = 60`. — [services/translate_run_service.py:88,99-102](services/translate_run_service.py)
- Batches start at scene breaks (gap ≥ 3 s, in the last 25% of the batch). — [engine_backends/translate_pipeline.py:258-290](engine_backends/translate_pipeline.py)
- Cache layout:
  - The stable instructions (style guide and glossary) and the novel reference go in system blocks. Recent and upcoming lines go in the user message "never inside them, where it would break the cached prefix". — [engine_backends/prompts.py:242-260,378-441](engine_backends/prompts.py)
  - Claude puts `cache_control` on the last system block. — [engine_backends/prompts.py:421-431](engine_backends/prompts.py)
- **The cache-breakpoint change is not merged.** Commit `efc62304` ("... cache breakpoint ...", 2026-10-10) puts the breakpoint on the instructions block when the novel excerpt is cut per batch, because "a breakpoint after it would pay the cache write each time and never read it back". `git merge-base --is-ancestor efc62304 HEAD` says it is not merged. It is contained only in `origin/fix-caps-privacy-glossary`. — `git show efc62304`
- **Transcription defaults:** beam 5, `min_silence_ms 300`, `vad_threshold 0.5`, fast mode off. — [services/transcribe_service.py:69-82](services/transcribe_service.py)
- On the streamer VOD, fast mode (batched) gave CER 24.0 with 44 lines vs 16.4 with 150 lines at a similar wall time. The repo therefore has evidence against fast mode on stream audio. — [docs/asr-experiments.md:357](docs/asr-experiments.md)
- Beam 1 saved 7-35% of time on read speech "with no clear accuracy cost in most cells". — [docs/asr-experiments.md:138](docs/asr-experiments.md)
- Auto-tune (`services/autotune_service.py`) sweeps the `min_silence` candidates using the title's own beam, model and fast mode. — [services/autotune_service.py:47-118](services/autotune_service.py)
- **Export:** `services/export_service.py` (25 KB) builds SRT/ASS/EPUB from `db.load_lines`. No hot loop or repeated DB read stood out in a skim. — [services/export_service.py:118-362](services/export_service.py)

### Inferences
- **Live and Claude caching:** a Live prompt of about 1.2k characters (~300-400 tokens) is below Claude's minimum cacheable prefix, so Live on Claude gets no caching. A title picker that adds a glossary and style guide may push it above the minimum. This rests on Claude's published minimum (≥ 1024 tokens on current models), which was not re-verified here.
- **The cheapest bulk win is merging the existing branch commit `efc62304`.** Its effect applies only to Claude runs with a novel reference longer than the excerpt budget.

### Gaps
- No measured cache-hit rates for bulk runs are in the repo (the pricing code tracks `cache_read_tokens`, but no report is in docs).
- Subtitle export was not profiled.

## 7. Ranked improvements (at most 12)

Owning PRs: open PRs were listed via the GitHub REST API, and their files filtered for the touched paths.
- #1073 (`jobs-db-first-gpu-slots`) touches `background_jobs.py`, `live_whisper.py`, `services/workspace_job_service.py` and `tests/test_live_stalls.py`.
- #1068 (`settings-schema-one-reader`) touches `background_jobs.py`, `ollama_unload.py`, `services/live_service.py`, `services/workspace_job_service.py` and `tests/test_live_thinking_and_context.py`.
- #1070 (`shrink-http-wave4-sessions`, whose title includes live capture) shows `engine_backends/local.py` in the filtered file list. Its `live_fetch.py` change could not be confirmed by the filter.
- #883 (`translate-by-sentence`) touches `engine_backends/local.py`.
- #1099 touches `core.py`.
- #1115 touches `services/workspace_job_service.py`.
- #1126 touches `whisper_models.py`.
- Recent commit history per file is listed in the notes below each item.

1. **Run translation on its own worker so Whisper never waits for the LLM.**
   - Change: in `live_translate.run_live_job` / `process_chunk`, hand cues to a translation queue thread; keep `translate_cues` ordering and the generation and cancel checks.
   - Effect: latency (the next chunk's Whisper starts right away) and fewer catch-up drops, because the chunk budget becomes max(Whisper, translate) instead of the sum.
   - Size: M. Risk: medium. Concurrency touches cancel and Stop, the `_cancel_check_var`/`abort_check_var` context vars (which are per thread), GPU sharing with local Ollama, and the ordering of `set_result` snapshots.
   - Files: `live_translate.py`, `live_cue_feed.py`, `live_cue_translation.py`, `tests/test_live_stalls.py`, `tests/test_live_translate.py`.
   - PRs: #1073 edits `tests/test_live_stalls.py`. Last touched in #1007, #995, #989.
   - Proof: a test with a fake translator that sleeps 2 s and a fake Whisper asserts that chunk N+1's Whisper call starts before chunk N's translations finish. Measure the `waited` column of `log_chunk` before and after.

2. **Offer `large-v3-turbo` in Live and drop or demote `medium`.**
   - Change: `WHISPER_SIZES` in `services/live_service.py:53` and `frontend/src/api/live.ts:29`.
   - Effect: accuracy. On the repo's streamer VOD, turbo's CER was 16.4 vs small's 23.4, and medium was slower and worse than turbo. CPU cost is about 1.6× small (145 vs 92 s per 300 s).
   - Size: S. Risk: low to medium. A slower chunk on weak CPUs causes more catch-up drops; `vram_needed_mb` already maps unknown sizes to 5500 MB.
   - Files: `services/live_service.py`, `frontend/src/api/live.ts`, `frontend/src/pages/Live.tsx` (help text), `tests/test_live_service.py`, `frontend/src/api/live.test.ts`.
   - PRs: #1068 (`live_service.py`).
   - Proof: an API test that turbo is accepted. Measure the per-chunk Whisper RTF from `log_chunk` with small vs turbo on the same stream.

3. **Title picker: glossary, characters, language packs and locale for Live (owner decision 2026-10-10).**
   - Change:
     - Add an optional `drama_id` to `LiveSessionStart`.
     - In `live_service.start_session`, check ownership (`ownership_service`), then call `workspace_job_service.build_run_style_context` once.
     - Pass `glossary_terms`, `style_guidelines`, `speaker` names and `locale` into `build_live_context`.
     - Pass `transcribe_service.build_auto_initial_prompt(drama_id)` combined with the carried tail as Whisper's prompt.
     - Read `default_locale` even with no title.
   - Effect: accuracy (name spelling in ASR and consistent name and term translation).
   - Size: M. Risk: medium. Ownership and permissions (a household user picking someone else's title); the prompt grows on every per-cue call; the language pack needs lines, so decide between the whole pack and glossary-driven selection; parity note for the CLI.
   - Files: `api/schemas/` (live model), `api/routers/live_routes.py`, `services/live_service.py`, `live_cue_translation.py`, `live_translate.py`, `frontend/src/pages/Live.tsx`, `frontend/src/api/live.ts`, `frontend/src/types/live.ts`, `docs/route-permissions.md` if a route changes.
   - PRs: #1068 (`live_service.py`, `workspace_job_service.py`), #1115 and #1073 (`workspace_job_service.py`).
   - Proof: a fake engine captures `context` and asserts the glossary, style and locale are present. A fake `transcribe_for_timing` asserts glossary names are in `initial_prompt`. On a real stream with a known cast, count name-spelling variants per session.

4. **Batch each chunk's cues into one id-keyed call (or two).**
   - Change: replace N per-cue `translate_batch([text])` calls with `translate_batch(texts, context with line_ids=[cue ids])`, keeping the recent pairs.
   - Effect: latency and cost. N round-trips and prefills per chunk become one. Trade-off: the first cue's translation waits for the whole chunk, so combine with #1 or keep per-cue calls for chunks of 1-2 cues.
   - Size: S-M. Risk: low to medium. Matching is already by id (`request_translations_with_retry`).
   - Files: `live_cue_feed.py`, `live_cue_translation.py`, `tests/test_live_translate.py`, `tests/test_live_thinking_and_context.py`.
   - PRs: #1068 (`test_live_thinking_and_context.py`).
   - Proof: the `translate` seconds per chunk in `log_chunk` before and after on the same stream; a unit test that ids map back correctly when the model reorders its reply.

5. **Make the default video delay follow the pipeline.**
   - Change: set the default delay from `segment_seconds` plus the last measured chunk processing time (the status already carries `speed_text`), or at least default it to S plus a margin. Today it is 15 s against a default S of 20.
   - Effect: perceived sync (captions land on the right picture instead of late).
   - Size: S. Risk: low (frontend only).
   - Files: `frontend/src/pages/live/embedLogic.ts`, `frontend/src/pages/Live.tsx`, and their vitest files.
   - PRs: none open found. Last touched in #995, #989, #982.
   - Proof: a vitest that the suggested delay is ≥ S. Manual check that caption `from` equals `origin+start+delay`, not `arrivedAt`, for most cues (log both in dev).

6. **Shorten the loop poll.**
   - Change: `poll_interval` from 2.0 s to about 0.25-0.5 s, or wake on the fetcher or ffmpeg writing a new file.
   - Effect: latency (−0.75 to −1 s average).
   - Size: S. Risk: low (a cheap `listdir` on a small directory).
   - Files: `live_translate.py`, `tests/test_live_translate.py`.
   - Proof: a fake-clock test measures the time from successor-file creation to `process_chunk` start.

7. **Join HLS closer to the live edge.**
   - Change: `LIVE_EDGE_SEGMENTS` 3 to 1 or 2 for Live.
   - Effect: latency (−1 to −2 target durations).
   - Size: S. Risk: medium (more stalls and refetches if the newest segment is not yet fully available; ffmpeg's own default is 3 for that reason).
   - Files: `live_fetch.py`, `tests/test_live_fetch.py` (`test_a_live_playlist_starts_at_the_edge_and_tracks_new_segments_once`).
   - PRs: possibly #1070 (live capture sessions). `live_fetch.py` was last touched via the HTTP-front-door waves.
   - Proof: parse `#EXT-X-PROGRAM-DATE-TIME` (not parsed today) and log wall-clock minus segment PDT at emit, before and after.

8. **Wire LocalAgreement-2 (`StreamAgreement`) with a ring buffer as a low-latency mode, ideally GPU-only.**
   - Change: the planned B-F work.
   - Effect: latency down to about the window step plus decode (seconds instead of S/2+), with stable, non-revised cues and better boundary handling than exact-match dedup.
   - Size: L. Risk: high (threads, audio buffer, re-decoding a ≤ 15 s window each step multiplies Whisper calls, GPU slot).
   - Files: `live_audio.py` (ring buffer), a new orchestration module, `live_translate.py`, `services/live_service.py`, the Live page, `FILE_ORGANIZATION.md`.
   - PRs: #1073 and #1068 overlap the touched files. Backlog item B2.
   - Proof: decide first with the measurement below (Whisper wall time per 2-15 s window on the owner's GPU). Unit tests with a scripted recogniser already exist.

9. **Process the remaining chunks when the stream ends.**
   - Change: on a normal ffmpeg exit, process the waiting completed chunks and the final chunk (which has no successor) before breaking.
   - Effect: accuracy and completeness. The last S seconds or more of a stream are currently never transcribed.
   - Size: S. Risk: low.
   - Files: `live_translate.py` (loop at lines 745-757), `tests/test_live_translate.py`.
   - Proof: a test where the fake ffmpeg exits 0 with 2 chunks on disk asserts both are transcribed.

10. **Cross-chunk hallucination guard and a cleaner prompt carry.**
    - Change: keep the last K emitted cue texts across chunks and drop an identical cue repeated within a short window during low-speech stretches. Do not carry a cue flagged as a likely hallucination (stock phrase, repeat) into `context_prompt`.
    - Effect: accuracy on music, BGM or idle streams.
    - Size: S. Risk: low to medium (real repeated lines such as greetings could be dropped; use a high K or time threshold).
    - Files: `live_translate.py`, `live_cue_feed.py`, `core.filter_hallucinated_segments` (reuse only).
    - PRs: #1099 touches `core.py`.
    - Proof: count identical consecutive cues across chunks in a music-heavy test session log, before and after. Unit test with scripted segments.

11. **Live-specific Ollama context size, to free VRAM.**
    - Change: for Live, request a smaller `num_ctx` (the prompt is about 1.7k characters; today it is floored at 16384). The override can only raise it today, so add a Live floor.
    - Effect: latency on GPU with local Ollama (less VRAM pressure, fewer Ollama CPU spills or Whisper slowdowns). This is the hypothesis.
    - Size: S-M. Risk: medium. Ollama reloads a model when `num_ctx` changes, so a concurrent bulk job with 16k would ping-pong. Truncation risk if the title picker adds a large glossary, so size it from the estimate.
    - Files: `engine_backends/local.py`, `live_cue_translation.py`.
    - PRs: #1070, #883 (`engine_backends/local.py`).
    - Proof: `ollama ps` (GPU/CPU split) and `nvidia-smi` free VRAM during a Live GPU session at 16k vs the smaller value, plus the `translate` and `Whisper` columns of `log_chunk`.

12. **Whisper decode options for Fast captions only.**
    - Change: beam 1 and/or `word_timestamps` off. Live uses word timings only to tighten segment edges.
    - Effect: latency, at an accuracy cost. Beam 1 cost 1.9 CER points on the streamer VOD; word timestamps' cost is unmeasured here.
    - Size: S. Risk: medium (accuracy).
    - Files: `live_translate.process_chunk` (pass `beam_size`), `core.transcribe_for_timing` (already takes `beam_size`).
    - PRs: #1099 (`core.py`).
    - Proof: per-chunk Whisper seconds and CER of a recorded stream clip run through `process_chunk`, offline, with and without the options.

## 8. What to MEASURE first (one small instrumented run)

### Takeaway
Most of the decisions above depend on four numbers the repo does not have: Whisper seconds per chunk at the chunk sizes in use, translate seconds per cue and per chunk, the number of cues per chunk, and VRAM headroom with local Ollama. `live_whisper.log_chunk` already logs most of them per chunk.

### Cited Findings
- `log_chunk` writes one app-log line per chunk: `chunk i: <audio> s of audio; waited <q> s, Whisper <w> s (model was not loaded yet), translate <t> s[, GPU free X of Y MB]`. GPU free is read only on slow GPU chunks. — [live_whisper.py:453-481](live_whisper.py)
- `ChunkTiming.queued` is the chunk file's age when processing starts, which is the queue wait. — [live_whisper.py:423-441](live_whisper.py)

### Inferences (proposed protocol)
- **Run matrix.** One public HLS stream (or a local looping HLS source; the module docstring says the pipeline was verified against one) for 10 minutes per cell, over:
  - Fast (4/1) vs default (20/3) chunks
  - small vs turbo
  - CPU vs GPU
  - Ollama `gemma4:12b` vs one hosted engine
- **Record from `log_chunk`:** `audio`, `waited`, `Whisper`, `translate`, the cue count per chunk (add one field), and the catch-up and skip notes.
- **Add three timestamps, behind Developer Mode logging:**
  - when the fetcher emits each HLS segment (and its `PROGRAM-DATE-TIME` if present)
  - when ffmpeg's successor file appears
  - when `set_result` publishes each cue's transcript and translation

  With the browser `arrivedAt`, this gives a true glass-to-caption split per stage.
- **Run once with Use GPU and local Ollama** and force the VRAM reading every chunk (not only slow ones), alongside `ollama ps`, to confirm or refute the oversubscription hypothesis.
- **Decision rules from the data:**
  - If the translate time is a large share of S, do #1 and #4 first.
  - If Whisper per call at S = 4 is close to Whisper at S = 20 (the 30 s padding effect), Fast captions should not be offered on CPU, and #8 (LocalAgreement) needs GPU.
  - If `waited` > 2 s typically, do #6.

### Gaps
- No harness for this exists in the repo. The streamer-VOD harness behind `asr-experiments.md` "is not in the repo". — [docs/asr-experiments.md:333](docs/asr-experiments.md)
