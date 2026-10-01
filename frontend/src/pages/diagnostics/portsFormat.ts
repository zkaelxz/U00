import type { PortEntry } from '../../types/diagnostics'

/** "8600", or "not set" while the listener has no port. */
export function portText(p: PortEntry): string {
  return p.port == null ? 'not set' : String(p.port)
}

export function portStatus(p: PortEntry): string {
  return p.active ? 'in use' : 'off'
}

/** The closed Section's one-line summary: the ports that are in use. */
export function portsSummary(ports: PortEntry[]): string {
  const on = ports.filter((p) => p.active && p.port != null).map((p) => p.port)
  return on.length ? `In use: ${on.join(', ')}` : 'None in use'
}
