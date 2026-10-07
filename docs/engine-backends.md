# Engines: translation, transcription and dubbing

How the engine code fits together and the rules it follows. Verified against
the code on `baihe-subtitler`; module and function names are cited instead of
line numbers so the page survives edits. Measured ASR results live in
`asr-experiments.md`, not here.

## 1. Translation engines

### The front door and the package

`translate_engines.py` holds no logic. It re-exports every public and private
name from the `engine_backends/` package so `import translate_engines` and
`translate_engines.X` keep working. The package is split by provider and role
(its `__init__.py` docstring has the same list):

| Module | Holds |
|---|---|
| `pricing.py` | `CLAUDE_MODELS`, `GEMINI_MODELS`, `OPENAI_MODELS`, `PRICING_PER_MILLION_TOKENS`, `estimate_cost*`, `resolve_cost_cap` |
| `shared.py` | usage totals, `call_with_backoff`, secret redaction, id-keyed request and parsing, moderation detection |
| `prompts.py` | prompt builders shared by the LLM engines |
| `claude.py` | `ClaudeEngine` (Anthropic SDK) |
| `openai_compat.py` | `DeepSeekEngine` (OpenAI SDK pointed at `api.deepseek.com`), `OpenAIEngine` (plain `requests` call to Chat Completions) |
| `gemini.py` | `GeminiEngine` (plain `requests` call to `generateContent`), free-tier pacing and rate status |
| `local.py` | `OllamaEngine` (REST to a local server), `NLLBEngine` (offline, `transformers` pipeline), `check_ollama_reachable` |
| `llm_tasks.py` | `call_llm_json` and the single-prompt helpers (speaker tagging, pacing rewrite, consistency check, episode summary, flagging) |
| `engine_registry.py` | `ENGINES`, capability tags, notes, model overrides, `get_engine` |
| `fallback.py` | `FallbackEngine`, the translate fallback chain |
| `standalone.py` | the standalone (non-drama) text translator |
| `translate_pipeline.py` | Reflect mode and the per-run translate loop (`translate_lines_with_engine`) |

Engine map today:

| Engine id | Class | Transport | Needs |
|---|---|---|---|
| `claude` | `ClaudeEngine` | `anthropic` SDK | key |
| `deepseek` | `DeepSeekEngine` | `openai` SDK | key |
| `openai` | `OpenAIEngine` | `requests` | key |
| `gemini` | `GeminiEngine` | `requests` | key (free-tier keys are paced client-side) |
| `ollama` | `OllamaEngine` | `requests` to the local server | none; a running Ollama |
| `nllb` | `NLLBEngine` | local `transformers` pipeline | none; model downloads once |

The `deepseek` engine needs the `openai` package, which is why
`diagnostics.OPTIONAL_DEPENDENCIES["openai"]` is described as "DeepSeek
translation engine".

**No other engines exist.** DeepL, Google Cloud Translation and LibreTranslate
were removed. `engine_registry.REMOVED_ENGINES` keeps their ids so a saved
preset, routing rule or fallback chain that still names one is refused with
`unknown_engine_message()` ("The X engine was removed") instead of a bare
KeyError. The only trace left is a DeepL key pattern in
`shared._SECRET_PATTERNS`, which is harmless. There is also no "test mode"
engine in the app: the key-free `FakeEngine` lives in `tests/fake_engine.py`
(id `fake`, output `[TEST] <text>`), is installed by `tests/conftest.py` and by
`frontend/e2e/serve_seeded_api.py`, and is never in the shipped `ENGINES`.

### What `shared.py` provides

- **Id-keyed requests.** `request_translations_with_retry(zh_lines,
  speaker_names, call_model_fn, ..., line_ids=, engine_name=)` is the one loop
  behind every engine's `translate_batch`. It numbers lines
  (`build_numbered_lines`), asks the model for a JSON object `{"<id>": "<text>"}`,
  and re-requests only the ids that came back missing (`max_retries`, default
  1). Ids are the lines' permanent ids when every line has a unique int id,
  else `1..n`. A line still missing after the retry stays blank; it never
  borrows another line's text.
- **`parse_id_keyed_json(text, expected_ids)`.** Returns `{id: text}` for ids
  that came back as strings. A bare JSON array is treated as malformed (`{}`)
  because positional matching is exactly the bug this exists to prevent.
  `extract_first_json_value` tolerates prose around the JSON (small local
  models add it).
- **`_id_keyed_batch_request`.** The same loop with a caller-built prompt, used
  by Reflect mode's three passes (`reflect_translate_batch`) and `llm_tasks`.
- **`call_with_backoff(fn, max_retries=5, ...)`.** Rate-limit errors
  (`_is_rate_limit_error`: status 429, a `RateLimit` class name, or "429" in
  the text when no status was given) back off exponentially; any other error
  gets one quick retry, then raises. Sleeps go through `_cancellable_sleep`, so
  a cancelled job stops waiting (`TranslationCancelled`).
  `FreeTierDailyLimitReached` is never retried.
- **`redact_secrets(text)`, `redact_for_storage`, `safe_url`,
  `strip_url_queries`.** Pattern-based scrubbing of keys (`sk-`, `AIza`,
  `key=`, `Authorization`/`Bearer`, DeepL, URL userinfo) from any error text.
- **Moderation.** `ContentModerationBlocked(engine, reason)` is raised from each
  engine's own `translate_batch` on a structural signal (Claude
  `stop_reason == "refusal"`, OpenAI-style `refusal`/`content_filter`, Gemini
  `blockReason`/`SAFETY`). `_detect_soft_refusal_text` is a lower-confidence
  fallback used only when a non-empty reply parsed to zero ids.
- **`SDK_REQUEST_TIMEOUT` (300 s).** Per-request timeout for the Anthropic and
  OpenAI clients and the OpenAI REST call; non-streaming replies send nothing
  until complete, so a shorter bound would re-bill a reply still in flight.

### Response size caps

Every `requests`-based provider call (OpenAI, Gemini, Ollama, the `llm_tasks`
Gemini path, bulk batch polling, Groq transcription, `qa.py`) is made with
`stream=True` and read through `engine_backends.shared.read_json_capped`. It
streams the body through `services/capped_body.read_capped` (default 16 MB,
`PROVIDER_RESPONSE_MAX_BYTES`, plus a total deadline) and raises
`ProviderResponseTooLarge` with no URL or header in the message; a non-2xx
status raises `requests.HTTPError` without reading the body. The Anthropic and
OpenAI SDK clients are bounded by `SDK_REQUEST_TIMEOUT` only. A new engine that
uses `requests` should call `read_json_capped` rather than `resp.json()`.

### Engine ids, lists and what the UI sees

- `engine_registry.ENGINES` is the one list; every picker is built from it
  (`services/translate_service.list_engines` adds label, `free`, models,
  `key_configured`). The Settings key list is the one place this is copied by
  hand: `ENV_NAMES` in `services/settings_service.py` against `SECRET_ENGINES`
  in `frontend/src/pages/settingsKeys.ts`, and `tests/test_engine_key_lists.py`
  fails if they drift.
- **Free vs paid.** `FREE_ENGINES = {"ollama", "nllb"}` are free every time.
  Gemini is not in it because free or paid depends on the key; the per-run
  "Gemini free tier" setting decides, and `engine_picker_label` swaps in
  `GEMINI_FREE_TIER_NOTE`. `KEYLESS_ENGINES` (the same two) skip key checks.
  Callers without `engines.paid` are limited to `FREE_ENGINES`
  (`translate_run_service`, `discover_lookup_service`).
- **Capabilities.** `ENGINE_CAPABILITIES` tags (`translate`, `instructions`,
  `long_context`, `local`, `cheap`, `grounded_search`) feed
  `services/engine_routing_service`, which resolves a task such as "episode
  summary" to an engine. Nothing switches engine on its own.
- **"Can't do this" messages.** `TRANSLATION_ONLY_ENGINES = {"nllb"}` has no
  instruction following. Services that need it refuse with
  "`<engine>` is a translation-only engine and can't do this." (see
  `line_ai_service`, `reader_service`, `restructure_service`,
  `review_extras_service`); Reflect is refused with "`<engine>` can't run
  Reflect mode." (`translate_run_service`). Line helpers fall back to the
  `llm.instructions` capability engine. Bulk mode refuses anything but
  Claude, paid Gemini or DeepSeek; bulk Reflect refuses DeepSeek.
- **Model choice.** Built-in defaults come from each constructor's `model`
  default (`builtin_default_model`). A user override
  (`model_overrides.defaults` / `.tiers` app settings, written only from
  Diagnostics) wins via `effective_default_model`; `WORKFLOW_TIERS` (draft,
  standard, release) are fixed starting points. `OpenAIEngine` additionally
  refuses any model that is not in `OPENAI_MODELS` or on OpenAI's own listed
  GPT-5+ extras, because an unpriced model would slip past the cost caps.

### Cost caps and bulk batches

- `pricing.estimate_translation_cost` and `estimate_reflect_mode_cost` (3x a
  normal run) are shown before a run starts. `resolve_cost_cap(job_cap,
  monthly_cap, month_spend)` returns the tighter of the per-job cap and what is
  left of the monthly cap, or a refusal when the month is already spent.
- A cap applies only to `translate_run_service._CAP_ENGINES` (`claude`,
  `deepseek`, `gemini`, `openai`) and not to free-tier Gemini
  (`engine_cap_applies`). With a `FallbackEngine`, each engine has its own cap
  and spend.
- `bulk_translate.py` submits whole-drama batches: Claude Message Batches and
  Gemini Batch API at `BATCH_PRICE_FACTOR = 0.5`, and DeepSeek scheduled into
  its off-peak window (no batch API). Results are applied by line id, and only
  to a line that still exists, whose source hash is unchanged and whose English
  was not edited in the meantime.
- `FallbackEngine` (max `MAX_FALLBACK_ENGINES = 2` extras) wraps engines of the
  same class (all instruction-following, or all translation-only). It retries
  transient errors, switches on auth or connection failures for the rest of the
  run, and records each switch in `events`.

## 2. Rules learned from bugs

1. **Match results by id, never by position.** Use
   `request_translations_with_retry` / `parse_id_keyed_json`. A short or
   reordered model reply once shifted every later translation onto the wrong
   line.
2. **Keys go in headers.** Gemini sends `x-goog-api-key`, OpenAI and Groq send
   `Authorization: Bearer`, the SDKs take the key in the client. Never put a
   key in a URL, log line or stored error. Wrap any provider error text in
   `redact_secrets` (or `redact_for_storage` for something saved) before it is
   shown, stored or logged. API responses carry booleans for "key set", not the
   key, a path or a fetched URL.
3. **Every outbound call has `timeout=`.** `tests/test_static_analysis.py`
   enforces it for `translate_engines.py`, everything under `engine_backends/`,
   `services/`, `api/` and a list of other modules; a new HTTP-calling module
   outside those goes on that list. Bodies of unknown size are read with
   `read_json_capped` (see above).
4. **Patch the module that uses a name, not the front door.** A test patches
   `engine_backends.llm_tasks.call_llm_json`, `engine_backends.local._nllb_pipeline_cache`,
   and so on. Patching `translate_engines.X` only affects code that reads `X`
   from `translate_engines` at call time, so it silently does nothing for
   engine-internal callers.
5. **Moderation is its own error.** Raise `ContentModerationBlocked` from the
   structural signal in `translate_batch` instead of returning empty text, so
   the caller can flag the line with the provider's real reason.
6. **Ollama context.** `estimate_ollama_num_ctx` sizes `num_ctx` per request
   (floor `OLLAMA_MIN_NUM_CTX = 16384`) because an undersized window truncates
   the prompt from the start, silently dropping instructions and glossary.

## 3. Transcription (ASR)

Picked per drama by `asr_backend_choice` (validated in
`services/transcribe_service`); the job plumbing lives there, the models in the
root modules below.

| Choice | Code | What it does |
|---|---|---|
| `whisper` (default) | `asr_backend.WhisperBackend` -> `core.transcribe_for_timing` | local faster-whisper with VAD segmentation; `core.load_whisper_model` falls back from GPU to CPU |
| `qwen3_asr` | `asr_backend.Qwen3ASRBackend` | re-transcribes Whisper's segments and replaces only the text, keeping Whisper's timing; runs on transformers 5.15+ through `qwen3_native.py` (the `-hf` checkpoints; no `qwen-asr` package); batching (`qwen_asr_batch_size`) is honoured whenever it can run (`effective_qwen_batch_size`). `qwen3_asr_vad` and `qwen3_asr_long` are the speech-detection variants; none is picked unless the title saves it |
| `moss_td` | `asr_backend.MossTranscribeDiarizeBackend` | experimental one-pass transcript with speaker labels; refused unless `moss_experimental` is on; downloads pinned remote code (see `asr-experiments.md`) |
| Groq (`use_groq` flag, not a backend choice) | `core.transcribe_with_groq` | uploads the whole file to Groq's hosted Whisper; needs a Groq key; one blocking call with `timeout=600` |

`BACKENDS` / `get_backend` and `EXPERIMENTAL_BACKENDS` in `asr_backend.py` are
the registry. Groq is not in it: it is a flag on the Whisper path in
`transcribe_service`.

Alignment is a separate choice (`alignment_method`): `whisper_diff` (the
default, `core.align_transcript_to_timing`) or `qwen3_forced_align`
(`forced_align.align_with_qwen3`). The forced aligner reuses the coarse pass to
bucket lines into chunks of at most `MAX_CHUNK_SECONDS` (60 s), refuses segments
past `HARD_CAP_SECONDS` (300 s, the aligner's own limit), and falls back to the
coarse timing with `TIMING_FALLBACK_NOTE` / `TIMING_REPAIRED_NOTE` when the
aligner returns zero-length or out-of-order spans.

`vad_segments.py` (`speech_spans`, `merge_close`, `cap_spans`) builds Silero
speech spans and cuts long ones at the quietest point. `Qwen3ASRVadBackend`
(`asr_backend_choice` `qwen3_asr_vad`, opt-in) uses it to feed Qwen3 spans of
at most about 15 s instead of Whisper's segments. `Qwen3ASRLongBackend`
(`qwen3_asr_long`, the default for Chinese and Japanese titles that never chose
a backend, when transformers 5.15+, torch and faster-whisper are installed) runs the same stages with gentler speech
detection (threshold 0.35, no minimum span, 300 ms padding), spans packed into
windows of up to 30 s, one line per sentence (`asr_backend.SENTENCE_SPLIT_RULES`) and
the forced aligner always on, so line length comes from the text and aligned
timings rather than from where the detector found a pause.

The per-title "Split lines by sentences" option (`split_by_sentences`) does the
same for Whisper and Qwen3 ASR: Whisper's speech detection splits only at 2 s
pauses (`asr_backend.SENTENCE_SPLIT_MIN_SILENCE_MS`, faster-whisper's default) and the
lines are cut by `asr_backend.SENTENCE_SPLIT_RULES` using Whisper's word timings.

`mixed_language.py` backs the "mixed languages" option (`mixed_languages` in
the ASR options): language is detected per speech span, the text's script is
checked against it, and a span that disagrees is retried once in the title's
language. A line is given a `lang` only when it differs from the title's, and
one that still can't be confirmed gets the `language_uncertain` flag.

**Model folders and offline use.** Settings > "Offline Whisper model folder"
(`whisper_model_path`, read by `settings_service.get_whisper_model_path`) is
passed as `local_model_path` and used instead of a download. Downloads honour an
HF token (`hf_token` key, `HF_TOKEN`). In portable mode `portable.py` redirects
`HF_HOME`, `TORCH_HOME` and `BAIHE_AUDIO_SEP_MODEL_DIR` under one
`model_cache/` folder so copying the app folder carries the models. Qwen3 ASR,
the Qwen3 aligner and MOSS download from Hugging Face on first use; a blocked
`huggingface.co` becomes `ModelDownloadError` with a DNS-blocker diagnosis.

## 4. TTS and dubbing at a glance

All in `dub.py`; this page only maps them.

- **Stock voices:** `synthesize_line` (Edge TTS, online, the only engine in
  `PARALLEL_SAFE_ENGINES`) and `synthesize_line_offline` (Piper, local,
  serialized by a lock). Edge TTS falls back to Piper when blocked
  (`EdgeTTSBlockedError`).
- **Cloned voices:** `CLONE_ENGINES` = `f5tts` (default), `omnivoice`,
  `gpt_sovits` (its own local server, `gpt_sovits_url`), `chatterbox`, `tada`.
  All but Edge/Piper are in `LOCAL_MODEL_ENGINES` and run single-threaded on
  one model. `clone_engine_supports_language` gates by language.
- **Timing:** `build_dub_track` fits each clip to its subtitle window with
  `stretch_for_window` (speed-up capped at `DUB_MAX_SPEEDUP`, slow-down at
  `DUB_MAX_SLOWDOWN`) and writes `pacing.json`.
- The optional packages for these (`edge_tts`, `piper-tts`, `f5_tts`,
  `omnivoice`, `chatterbox-tts`, `hume-tada`, `pydub`) are all in
  `diagnostics.OPTIONAL_DEPENDENCIES`; OmniVoice, Chatterbox and TADA cannot
  share one environment.

## 5. App and CLI share these paths

`cli.py` calls the same functions as the API: `translate_engines.get_engine`,
`FallbackEngine`, `redact_secrets`, `estimate_cost_for_engine`,
`settings_service.resolve_key`, `transcribe_service` (including
`stored_whisper_size`, `build_auto_initial_prompt`, `require_qwen3_packages`),
`engine_routing_service`, `translate_service` and `engine_cap_applies`. Keys
resolve through `settings_service.resolve_key` (`ENV_NAMES`: `BAIHE_*_KEY`
first, then the provider's own variable; the Gemini key deliberately does not
fall back to `GOOGLE_API_KEY`). Ollama's URL comes from `ollama_url` /
`BAIHE_OLLAMA_URL`. When you change engine behaviour, check `cli.py` for the
same call (glossary, style guide, locale, character names, caps) so the two do
not diverge.

## 6. Adding an engine: checklist

1. **Class** in the matching `engine_backends/` module (or a new one), with
   `name`, `supports_reference`, `last_usage`, and `translate_batch(zh_lines,
   context)` built on `request_translations_with_retry(..., line_ids=
   context.get("line_ids"), engine_name=...)`. Raise
   `ContentModerationBlocked` on the provider's structural refusal signal.
2. **Registry:** add it to `ENGINES`, `ENGINE_CAPABILITIES`, `ENGINE_NOTES`,
   and, if free or keyless, `FREE_ENGINES` / `KEYLESS_ENGINES`; if it cannot
   follow instructions, `TRANSLATION_ONLY_ENGINES`. Add models and prices to
   `pricing.py` (`PRICING_PER_MILLION_TOKENS`) so cost estimates and caps work,
   and to `_CAP_ENGINES` in `translate_run_service` if it is paid. Re-export
   new public names from `translate_engines.py`. Add a bulk provider only if
   the vendor has a batch API (`bulk_translate.BULK_ENGINES`).
3. **Settings and keys:** an `ENV_NAMES` entry in `settings_service`, the
   matching `SECRET_ENGINES` entry in `frontend/src/pages/settingsKeys.ts` and
   a label in `frontend/src/labels.ts`; `tests/test_engine_key_lists.py` checks
   both sides. Add `ENGINE_MODEL_DICTS` in `translate_service` if there is a
   model picker.
4. **Key handling:** key in a header (or the SDK client), never a URL;
   `timeout=` on every call (use `SDK_REQUEST_TIMEOUT` for slow replies);
   `redact_secrets` on any error text; capped read for unbounded bodies. Add
   the key shape to `shared._SECRET_PATTERNS` if it has a recognisable prefix.
5. **Diagnostics:** register any new optional package in
   `diagnostics.OPTIONAL_DEPENDENCIES` in the same change.
6. **Routes and permissions:** if you add an API route, give it exactly one of
   `require_permission(...)`, `public_route()` or `local_only()` and update the
   route table in `route-permissions.md`.
7. **Tests:** mocked only (no network, GPU or keys). Patch the module that uses
   a name. Cover the id-keyed path (a short reply must blank only its own
   line), the refusal signal, rate-limit backoff, secret redaction, and the
   timeout rule (`test_static_analysis.py` covers the last for files it lists).
   Use `isolated_db` for anything touching the database.
