# engine_backends/

Translation engines by provider plus the helpers they share. Callers import
through `translate_engines.py`, which re-exports every name here.

## Start here
- `__init__.py`: one line per module.
- `shared.py`: `request_translations_with_retry`, `parse_id_keyed_json`, `redact_secrets`, retry/backoff.
- `engine_registry.py`: `ENGINES`, capability tags, `get_engine`, model overrides.
- `translate_pipeline.py`: the per-run translate loop and Reflect mode.
- Provider modules: `claude.py`, `openai_compat.py`, `gemini.py`, `local.py`.

## Rules
- Match LLM results to lines by explicit id, never by list position: use
  `request_translations_with_retry` / `parse_id_keyed_json`.
- API keys go in headers, never in URLs, logs or stored errors; run error text through `redact_secrets`.
- Every HTTP call and SDK client has `timeout=` (enforced for this folder).
- Tests patch the module that uses a name (e.g. `engine_backends.llm_tasks.call_llm_json`),
  not `translate_engines`.

## Tests
- `tests/test_translate_engines.py`, `tests/test_secret_redaction.py`, `tests/test_line_ids.py`
- `tests/test_openai_engine.py`, `tests/test_translate_fallback_chain.py`, `tests/test_engine_key_lists.py`
- `tests/fake_engine.py` is the shared fake engine
- `tests/test_static_analysis.py` (`test_translate_engines` timeouts, SDK client timeouts)

## Do not touch
- `translate_engines.py` re-export list unless you add or rename a public name.
