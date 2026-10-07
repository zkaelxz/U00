"""
The translation engines behind translate_engines.py, split by provider and role.
Import from translate_engines to use these names: that module re-exports every one.
A test must patch the module that uses the name (engine_backends.<module>.<name>);
a patch on translate_engines only affects code that reads the name from
translate_engines at call time.

  pricing.py            model lists, per-million-token prices, cost estimates
  shared.py             usage totals, retry/backoff, secret redaction, id-keyed
                        request and parsing, content-moderation detection
  prompts.py            prompt builders shared by the LLM engines
  claude.py             ClaudeEngine
  openai_compat.py      DeepSeekEngine, OpenAIEngine
  gemini.py             GeminiEngine, rate-limit status, free-tier limits
  local.py              OllamaEngine, Ollama reachability
  llm_tasks.py          call_llm_json and the single-prompt features (speaker
                        tagging, pacing, consistency, summaries, flagging)
  engine_registry.py    ENGINES, capability tags, notes, model overrides, get_engine
  fallback.py           the translate fallback chain
  standalone.py         standalone text translation
  translate_pipeline.py Reflect mode and the per-run translate loop
"""
