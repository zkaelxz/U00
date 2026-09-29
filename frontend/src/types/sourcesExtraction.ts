// Mirrors api/sources_extraction_schemas.py (Sources parity SO09): the
// pasted-URL AI fallback. Names only; no key ever reaches the browser.

// GET /api/sources/url/ai-engines
export interface AiEngines {
  engines: string[]
  // The saved default engine, when the fallback can use it.
  default: string | null
}

// The opt-in fields a URL import sends (omitted = off).
export interface AiRequestFields {
  use_ai?: boolean
  engine?: string
}
