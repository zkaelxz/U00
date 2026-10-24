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
  local.py              OllamaEngine
  ollama_server.py      Ollama reachability and the models pulled on the server
  thinking.py           the "reply without thinking" switch (DeepSeek, Ollama)
  temperature.py        the sampling temperature a Lab arm may set (DeepSeek, Gemini, Ollama)
  llm_tasks.py          call_llm_json and the single-prompt features (speaker
                        tagging, pacing, consistency, summaries, flagging)
  vision.py             read the text in a picture (Quick translate's screenshot box)
  engine_registry.py    ENGINES, capability tags, notes, model overrides, get_engine
  fallback.py           the translate fallback chain
  standalone.py         standalone text translation
  flag_reasons.py       flag keys and their labels
  reflect_prompts.py    the three Reflect-mode prompts
  translation_guard.py  merged/shifted/untranslated reply checks
  run_limits.py         failed-batch streak and rate-limit wait budget
  paid_calls.py         usage rows for a paid call; stop rules for a job's AI calls
  reflect_pass.py       Reflect mode (three passes over one batch)
  translate_pipeline.py The per-run translate loop
"""
