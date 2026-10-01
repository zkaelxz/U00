// Pure helpers for the per-source domain editor. They mirror the server's
// checks so typos show up before Save; the server stays authoritative.
import type { SourceDomainEntry } from '../../types/sourceDomains'

export const MAX_DOMAINS = 10

const LABEL = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/

export type HostResult = { host: string; error?: undefined } | { host?: undefined; error: string }

/** Lowercase, drop a trailing dot and a default :443, and check one host[:port]. */
export function normalizeHost(raw: string): HostResult {
  const text = raw.trim().toLowerCase()
  if (!text) return { error: 'Type a host name first.' }
  if (/\s/.test(text)) return { error: 'A host name has no spaces.' }
  if (/[/?#@\\]/.test(text) || text.includes('://')) return { error: 'Use just the host name, without http:// or a path.' }
  if (text.startsWith('[') || (text.match(/:/g) ?? []).length > 1) return { error: 'IP addresses are not allowed; use a host name.' }
  const [hostRaw, port, ...rest] = text.split(':')
  if (rest.length) return { error: 'Use host or host:port.' }
  const host = hostRaw.replace(/\.$/, '')
  if (port !== undefined) {
    if (!/^\d{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535) return { error: 'The port must be 1 to 65535.' }
  }
  if (!host || host.length > 253) return { error: 'That is not a valid host name.' }
  const labels = host.split('.')
  if (/^\d+$/.test(labels[labels.length - 1])) return { error: 'IP addresses are not allowed; use a host name.' }
  if (!labels.every((l) => LABEL.test(l))) return { error: 'Use letters, digits and hyphens only (plain ASCII).' }
  const p = port === undefined ? null : String(Number(port))
  return { host: p && p !== '443' ? `${host}:${p}` : host }
}

export type AddResult = { list: string[]; error?: string }

/** Add one host to the end; a duplicate or a full list leaves the list as is. */
export function addDomain(list: string[], raw: string): AddResult {
  const r = normalizeHost(raw)
  if (r.error !== undefined) return { list, error: r.error }
  if (list.includes(r.host)) return { list, error: 'That host is already in the list.' }
  if (list.length >= MAX_DOMAINS) return { list, error: `At most ${MAX_DOMAINS} hosts.` }
  return { list: [...list, r.host] }
}

export function removeDomain(list: string[], index: number): string[] {
  return list.filter((_, i) => i !== index)
}

/** Move one entry up (-1) or down (1); out of range leaves the list as is. */
export function moveDomain(list: string[], index: number, dir: -1 | 1): string[] {
  const to = index + dir
  if (index < 0 || index >= list.length || to < 0 || to >= list.length) return list
  const next = [...list]
  ;[next[index], next[to]] = [next[to], next[index]]
  return next
}

export const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i])

export function domainMarker(e: SourceDomainEntry): string {
  return e.customized ? 'Customized' : 'Default'
}

/** The total of pending proposals, for the section badge. */
export function pendingTotal(entries: SourceDomainEntry[]): number {
  return entries.reduce((n, e) => n + e.pending_proposals, 0)
}

export function foundAtText(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function domainsSummary(entries: SourceDomainEntry[]): string {
  if (!entries.length) return 'no sources'
  const custom = entries.filter((e) => e.customized).length
  const pending = pendingTotal(entries)
  return [
    `${entries.length} source${entries.length === 1 ? '' : 's'}`,
    custom ? `${custom} customized` : 'all default',
    pending ? `${pending} possible new` : '',
  ].filter(Boolean).join(' · ')
}
