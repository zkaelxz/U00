// Mirrors api/schemas/system.py Extension* (/api/extension, PC only).
import type { TranslateEngine } from './translate'

// No port and no token, ever.
export interface ExtensionStatus {
  enabled: boolean
  running: boolean
}

// restart_needed: turned off, but the endpoint could not be stopped and
// keeps serving until Baihe restarts. Normally false: off stops it at once.
export interface ExtensionEnabledResult extends ExtensionStatus {
  restart_needed: boolean
}

export interface ExtensionToken {
  token: string
}

// The extension's translation engine. `ready`: an engine is chosen and its
// key is configured on the PC. Keys never come back, only key_configured.
export interface ExtensionEngineSettings {
  engine: string | null
  model: string | null
  ready: boolean
  engines: TranslateEngine[]
}
