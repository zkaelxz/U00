// Pure display and state logic for Settings > "Which engine does what"
// (EngineRoutingCard.tsx). Kept free of React so it is unit-testable.
import type { BadgeTone } from '../../components/labels'
import { humanize } from '../../components/labels'
import { keyRows } from '../settingsKeys'
import type { CapabilityRoute, EngineRouteStatus, EngineRouting, EngineStatus } from '../../types/engineRouting'
import type { SetupReadiness } from '../../types/setup'
import type { Need } from '../readiness/needs'

const STATUS_BADGES: Record<EngineStatus, { label: string; tone: BadgeTone }> = {
  working: { label: 'Working', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
  untested: { label: 'Not tested', tone: 'neutral' },
  not_configured: { label: 'No key', tone: 'warn' },
}

export function statusBadge(status: string): { label: string; tone: BadgeTone } {
  return STATUS_BADGES[status as EngineStatus] ?? STATUS_BADGES.untested
}

// translate_engines.CAP_* tags in plain words.
const TAG_LABELS: Record<string, string> = {
  translate: 'Translates',
  instructions: 'Follows instructions',
  long_context: 'Long context',
  local: 'Runs on this PC',
  cheap: 'Low cost',
  grounded_search: 'Web search',
}

export function tagsText(tags: string[]): string {
  return tags.map((t) => TAG_LABELS[t] ?? t.replace(/[_-]+/g, ' ')).join(' · ')
}

/** "Tested just now" / "Tested 5 min ago" / "Tested 3 h ago" / "Tested <local date and time>"; '' if unreadable. */
export function testedText(iso: string | null | undefined, now: number = Date.now()): string {
  const at = iso ? Date.parse(iso) : NaN
  if (Number.isNaN(at)) return ''
  const secs = Math.max(0, Math.round((now - at) / 1000))
  if (secs < 60) return 'Tested just now'
  if (secs < 3600) return `Tested ${Math.floor(secs / 60)} min ago`
  if (secs < 86400) return `Tested ${Math.floor(secs / 3600)} h ago`
  return `Tested ${new Date(at).toLocaleString()}`
}

/** Why Test can't run for this engine right now, or null when it can. */
export function testBlockedReason(e: EngineRouteStatus): string | null {
  if (e.test_blocked) return e.test_blocked
  return e.status === 'not_configured' ? 'Set its key first.' : null
}

// The <select> value: '' is the "Use default" option (sent as null).
export const selectValue = (c: CapabilityRoute): string => (c.is_default ? '' : c.engine)
export const choiceFromSelect = (value: string): string | null => (value === '' ? null : value)

/** The capability as it will look once `engine` is saved (the optimistic update). */
export function withChoice(c: CapabilityRoute, engine: string | null): CapabilityRoute {
  const next = engine ?? c.default_engine
  return {
    ...c,
    engine: next,
    // With unset_label only "unset" is off; picking the default engine is a real choice.
    is_default: engine === null || (!c.unset_label && engine === c.default_engine),
    engine_supported: c.choices.includes(next),
  }
}

export function replaceCapability(r: EngineRouting, c: CapabilityRoute): EngineRouting {
  return { ...r, capabilities: r.capabilities.map((x) => (x.id === c.id ? c : x)) }
}

export function replaceEngine(r: EngineRouting, e: EngineRouteStatus): EngineRouting {
  return { ...r, engines: r.engines.map((x) => (x.engine === e.engine ? e : x)) }
}

export function workingCount(r: EngineRouting): number {
  return r.engines.filter((e) => e.status === 'working').length
}

// The unset option's text: "Off (no suggestions)" where unset means off,
// else "Use default (<engine>)".
export const unsetOptionLabel = (c: CapabilityRoute, engineLabel: (e: string) => string): string =>
  c.unset_label ?? `Use default (${engineLabel(c.default_engine)})`

export const unsetBadge = (c: CapabilityRoute): string => (c.unset_label ? 'Off' : 'Default')

export type KeyState = 'set' | 'missing' | 'none'

/** One row of the merged "Engines and keys" list. `status` is null for a key that no routed engine uses (Groq, Hugging Face) or while routing is still loading. */
export interface EngineRow {
  engine: string
  label: string
  status: EngineRouteStatus | null
  keyState: KeyState
  writable: boolean
  cost: string
}

// Keys with no routed engine behind them.
const KEY_ONLY_COST: Record<string, string> = { groq: 'Paid', hf_token: 'Free' }

export function costText(e: EngineRouteStatus): string {
  if (e.tags.includes('local')) return 'Free · runs on this PC'
  return e.needs_key ? 'Paid' : 'Free'
}

/**
 * Every routed engine in routing order, then the keys no routed engine uses.
 * Key state comes from the settings overview (it updates the moment a key is
 * saved); before routing loads, only the key rows show.
 */
export function engineRows(routing: EngineRouting | null, engineKeys: Record<string, boolean>): EngineRow[] {
  const keyed = keyRows(engineKeys)
  const routed: EngineRow[] = (routing?.engines ?? []).map((e) => {
    const configured = engineKeys[e.engine] ?? e.key_configured
    return {
      engine: e.engine,
      label: humanize('engine', e.engine),
      status: e,
      keyState: e.needs_key ? (configured ? 'set' : 'missing') : 'none',
      writable: e.needs_key && keyed.some((k) => k.engine === e.engine && k.writable),
      cost: costText(e),
    }
  })
  const seen = new Set(routed.map((r) => r.engine))
  const extra: EngineRow[] = keyed
    .filter((k) => !seen.has(k.engine))
    .map((k) => ({
      engine: k.engine,
      label: k.label,
      status: null,
      keyState: engineKeys[k.engine] ? 'set' : 'missing',
      writable: k.writable,
      cost: KEY_ONLY_COST[k.engine] ?? '',
    }))
  return [...routed, ...extra]
}

export function keysSetCount(rows: EngineRow[]): { set: number; total: number } {
  const keyed = rows.filter((r) => r.keyState !== 'none')
  return { set: keyed.filter((r) => r.keyState === 'set').length, total: keyed.length }
}

// The SDK each hosted engine imports; Diagnostics > Packages names it.
const TASK_ENGINE_PACKAGES: Record<string, string> = { claude: 'anthropic', deepseek: 'openai', openai: 'openai' }

/**
 * Why the engine a task is set to cannot run now, as a Need. Unknown readiness counts as ready. A task that is
 * "Off" (unset with a stronger engine to switch on) calls nothing, so it needs nothing.
 */
export function taskEngineNeed(cap: CapabilityRoute, rows: EngineRow[], readiness: SetupReadiness | null): Need | null {
  if (cap.is_default && cap.unset_label) return null
  const row = rows.find((r) => r.engine === cap.engine)
  if (!row) return null
  const pkg = TASK_ENGINE_PACKAGES[cap.engine]
  if (pkg && readiness && readiness[pkg as 'openai' | 'anthropic'] === false) {
    return {
      id: 'task_package', label: row.label, action: 'diagnostics', packages: [pkg],
      reason: `${row.label} can't run: the ${pkg} package it needs isn't installed.`,
    }
  }
  if (cap.engine === 'ollama') {
    if (readiness?.ollama_reachable !== false) return null
    return {
      id: 'task_ollama', label: 'Ollama', action: 'recheck', packages: [],
      reason: "Ollama isn't answering on this PC. Start it, then check again.",
      link: { href: 'https://ollama.com/download', label: 'Get Ollama' },
    }
  }
  if (row.keyState === 'missing') {
    return {
      id: 'task_key', label: row.label, action: 'external', packages: [],
      reason: `${row.label} has no API key yet, so this task can't run. Add one under Engines and keys below.`,
    }
  }
  return null
}
