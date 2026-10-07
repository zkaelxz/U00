// "Which engine does what" (api/engine_routing_schemas.py).
// Engine names and short redacted text only; never a key or a URL.

export interface CapabilityRoute {
  id: string
  label: string
  help: string
  requires: string
  engine: string
  default_engine: string
  is_default: boolean
  // Set when unset means "off" (the stronger engine): the unset option's label.
  unset_label?: string | null
  engine_supported: boolean
  choices: string[]
}

export type EngineStatus = 'not_configured' | 'untested' | 'working' | 'failed'

export interface EngineTestOutcome {
  ok: boolean
  tested_at: string // ISO 8601, UTC
  error: string | null
}

export interface EngineRouteStatus {
  engine: string
  tags: string[]
  test_blocked?: string | null // why Test isn't offered (e.g. a large first-use download)
  needs_key: boolean
  key_configured: boolean
  status: EngineStatus
  last_test: EngineTestOutcome | null
}

export interface EngineRouting {
  capabilities: CapabilityRoute[]
  engines: EngineRouteStatus[]
}
