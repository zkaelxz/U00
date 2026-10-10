// Mirrors api/schemas/pending_install.py.
export interface PendingInstallChange {
  name: string
  from_version: string | null
  to_version: string
  kind: 'install' | 'upgrade' | 'downgrade'
}

export interface PendingInstallPlan {
  packages: string[]
  available: boolean
  // `now` installs immediately; `restart` waits for the next start.
  mode: 'now' | 'restart'
  changes: PendingInstallChange[]
  summary: string[]
  loaded: string[]
  blocked: string[]
  needs_confirm: string[]
  note: string | null
}

export interface PendingInstallQueued {
  queued: boolean
  install_now: boolean
  plan: PendingInstallPlan
}

export interface PendingInstallOutcome {
  status: 'ok' | 'failed' | 'timed_out' | 'refused' | 'running' | 'interrupted'
  packages: string[]
  message: string
  tail: string[]
  restored: string[]
  restore_failed: string[]
  finished: number | null
}

export interface PendingInstallStatus {
  packages: string[]
  created: number | null
  before: Record<string, string>
  problem: string | null
  result: PendingInstallOutcome | null
  applying: boolean
}
