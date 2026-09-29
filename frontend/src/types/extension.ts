// Mirrors api/schemas.py Extension* (/api/extension, PC only).

// No port and no token, ever.
export interface ExtensionStatus {
  enabled: boolean
  running: boolean
}

// restart_needed: turned off, but the running endpoint keeps serving
// until Baihe restarts.
export interface ExtensionEnabledResult extends ExtensionStatus {
  restart_needed: boolean
}

export interface ExtensionToken {
  token: string
}
