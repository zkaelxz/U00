import type { DramaSummary } from '../../api/types'
import { humanizeValue } from '../../components/labels'
import type { CheckResult, SourcesSettings, SourcesSettingsUpdate, SourceSummary } from '../../types/sources'


const CACHE_LABELS: Record<string, string> = {
  none: 'Keep nothing',
  temporary: 'Temporary',
  keep_originals: 'Keep originals',
  keep_translated: 'Keep translated only',
  keep_both: 'Keep both',
}

export const cacheLabel = (mode: string) => CACHE_LABELS[mode] ?? humanizeValue(mode)

const fmtNum = (n: number) => String(Number.isInteger(n) ? n : Number(n.toFixed(2)))

function gapText(s: Pick<SourcesSettings, 'pace_min_delay' | 'pace_max_delay'>): string {
  return `${fmtNum(s.pace_min_delay)}–${fmtNum(s.pace_max_delay)} s gap`
}

export function settingsSummary(s: SourcesSettings | null, sources: SourceSummary[]): string {
  const on = `${sources.filter((x) => x.enabled).length} of ${sources.length} sources on`
  if (!s) return on
  return `${on} · ${gapText(s)} · cache: ${cacheLabel(s.cache_mode)}`
}

export function pacingSummary(s: SourcesSettings): string {
  const breaks =
    s.session_break_min_requests > 0 && s.session_break_max_requests > 0
      ? `breaks every ${s.session_break_min_requests}–${s.session_break_max_requests}`
      : 'breaks off'
  return [
    gapText(s),
    `${s.max_concurrent} at a time`,
    `${s.max_retries} ${s.max_retries === 1 ? 'retry' : 'retries'}`,
    breaks,
  ].join(' · ')
}

export type NumKey =
  | 'pace_min_delay'
  | 'pace_max_delay'
  | 'max_concurrent'
  | 'max_retries'
  | 'session_break_min_requests'
  | 'session_break_max_requests'
  | 'session_break_min_delay'
  | 'session_break_max_delay'
  | 'check_interval_hours'
  | 'cache_max_mb'

export type TimingKey = Exclude<NumKey, 'check_interval_hours'>

// The two developer-only switches; auto-import lives on the New chapters card.
export type BoolKey = 'demo_source_enabled' | 'extraction_diagnostics'

export interface NumField<K extends NumKey = NumKey> {
  key: K
  label: string
  min: number
  max: number
  step?: number
  help?: string
  // The field this one must be at least (a max's min).
  atLeast?: K
}

// Pairs in display order; ranges match services/sources_registry_service.py.
export const PACING_ROWS: NumField<TimingKey>[][] = [
  [
    { key: 'pace_min_delay', label: 'Wait between requests, from (s)', min: 3, max: 60, step: 0.5,
      help: "Seconds between requests to one source. Can't go below 3." },
    { key: 'pace_max_delay', label: 'Wait between requests, to (s)', min: 0, max: 120, step: 0.5, atLeast: 'pace_min_delay' },
  ],
  [
    { key: 'max_concurrent', label: 'Pages at a time', min: 1, max: 4,
      help: '1 is human-paced. A site can still insist on slower.' },
    { key: 'max_retries', label: 'Retry attempts', min: 0, max: 6,
      help: 'For busy or overloaded replies only; never for a browser check.' },
  ],
  [
    { key: 'session_break_min_requests', label: 'Rest after every (requests)', min: 0, max: 200,
      help: '0 = off. Picked at random between the two numbers.' },
    { key: 'session_break_max_requests', label: 'Rest after up to (requests)', min: 0, max: 200, atLeast: 'session_break_min_requests' },
  ],
  [
    { key: 'session_break_min_delay', label: 'Rest length, from (s)', min: 0, max: 600, step: 5 },
    { key: 'session_break_max_delay', label: 'Rest length, to (s)', min: 0, max: 900, step: 5, atLeast: 'session_break_min_delay' },
  ],
]

// Names the rows above for assistive tech; the fields carry their own labels.
export const PACING_GROUPS = ['Wait between requests', 'Pages at a time and retries', 'Rest after', 'Rest length']

export const CHECK_FIELD: NumField = { key: 'check_interval_hours', label: 'Check followed titles every (h)', min: 0, max: 168, help: '0 = off.' }

export const CACHE_MAX_FIELD: NumField<TimingKey> = {
  key: 'cache_max_mb', label: 'Saved pages limit (MB)', min: 0, max: 1_000_000,
  help: '0 = no limit. Oldest-used pages go first.',
}

// The Timing & cache form; the check interval is edited on the New chapters card.
const NUM_FIELDS: NumField<TimingKey>[] = [...PACING_ROWS.flat(), CACHE_MAX_FIELD]

export const DEVELOPER_FIELDS: { key: BoolKey; label: string; help?: string }[] = [
  { key: 'demo_source_enabled', label: 'Show demo source' },
  { key: 'extraction_diagnostics', label: 'Extraction diagnostics' },
]

// The editable form: numbers as typed text so a half-typed value is allowed.
export type PacingDraft = Record<TimingKey, string> & Record<BoolKey, boolean> & { cache_mode: string }

export function draftFrom(s: SourcesSettings): PacingDraft {
  const d = { cache_mode: s.cache_mode } as PacingDraft
  for (const f of NUM_FIELDS) d[f.key] = String(s[f.key])
  for (const b of DEVELOPER_FIELDS) d[b.key] = s[b.key]
  return d
}

const asNumber = (text: string) => (text.trim() === '' ? NaN : Number(text))

/** One field's problem: not a number, out of range or not whole; null when it is fine. */
export function fieldProblem(f: Pick<NumField, 'min' | 'max' | 'step'>, text: string): string | null {
  const v = asNumber(text)
  if (!Number.isFinite(v)) return 'Enter a number.'
  if (v < f.min || v > f.max) return `Must be between ${f.min} and ${f.max}.`
  if (!f.step && !Number.isInteger(v)) return 'Enter a whole number.'
  return null
}

/** Field errors: out of range, not a number, or a max below its min. */
export function pacingErrors(d: PacingDraft): Partial<Record<TimingKey, string>> {
  const out: Partial<Record<TimingKey, string>> = {}
  for (const f of NUM_FIELDS) {
    const problem = fieldProblem(f, d[f.key])
    if (problem) out[f.key] = problem
  }
  for (const f of NUM_FIELDS) {
    if (!f.atLeast || out[f.key] || out[f.atLeast]) continue
    if (asNumber(d[f.key]) < asNumber(d[f.atLeast])) out[f.key] = 'Must be at least the min.'
  }
  return out
}

/** Only the keys that differ from what the server sent. */
export function settingsChanges(original: SourcesSettings, d: PacingDraft): SourcesSettingsUpdate {
  const out: SourcesSettingsUpdate = {}
  for (const f of NUM_FIELDS) {
    const v = asNumber(d[f.key])
    if (Number.isFinite(v) && v !== original[f.key]) out[f.key] = v
  }
  for (const b of DEVELOPER_FIELDS) if (d[b.key] !== original[b.key]) out[b.key] = d[b.key]
  if (d.cache_mode !== original.cache_mode) out.cache_mode = d.cache_mode
  return out
}

export function profileLine(v: {
  version: number | null
  kind: string | null
  status: string | null
  origin: string | null
  failures: number
  last_failure_reason: string | null
}): string {
  const parts = [`v${v.version ?? '?'}`]
  if (v.kind) parts.push(v.kind)
  if (v.status) parts.push(v.status)
  if (v.origin) parts.push(`from ${v.origin}`)
  if (v.failures) {
    parts.push(`${v.failures} failure${v.failures === 1 ? '' : 's'}${v.last_failure_reason ? ` (${v.last_failure_reason})` : ''}`)
  }
  return parts.join(' · ')
}

// "Check now" (sources_chapter_check): one line for the finished run.
export function checkSummary(r: CheckResult): string {
  if (r.skipped) return 'Another check was already running, so this one checked nothing.'
  const parts = [`Checked ${r.checked} series`]
  parts.push(r.new ? `${r.new} new chapter${r.new === 1 ? '' : 's'}` : 'no new chapters')
  if (r.queued.length) parts.push(`importing into ${r.queued.length} title${r.queued.length === 1 ? '' : 's'}`)
  if (r.saved?.length) parts.push(`saved ${r.saved.length} series as CBZ`)
  const failed = Object.keys(r.errors).length
  if (failed) parts.push(`${failed} failed`)
  return parts.join(' · ') + '.'
}

// Which dramas a tracked series can auto-import into (same rule as an import).
export function trackedDramaChoices(dramas: DramaSummary[], source: SourceSummary | undefined): DramaSummary[] {
  if (!source) return []
  const comic = source.supports.get_pages
  const ok = comic ? ['manhua', 'manga', 'manhwa'] : ['novel']
  return dramas.filter((d) => ok.includes((d.media_type ?? '').toLowerCase()))
}

// A pasted page for sign-in / tests: a plain http(s) URL (the server checks the site).
export function pageUrlProblem(text: string): string | null {
  const t = text.trim()
  if (!t) return null
  try {
    const u = new URL(t)
    return u.protocol === 'http:' || u.protocol === 'https:' ? null : 'Use an http:// or https:// address.'
  } catch {
    return 'Paste a full address, starting with https://.'
  }
}

// The proxy field: "" clears it; otherwise http(s)://host[:port], nothing after.
export function proxyProblem(text: string): string | null {
  const t = text.trim()
  if (!t) return null
  try {
    const u = new URL(t)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'HTTP(S) proxies only.'
    if (!u.hostname || (u.pathname !== '/' && u.pathname !== '') || u.search || u.hash) {
      return 'Just the address and port, e.g. http://127.0.0.1:8080.'
    }
    return null
  } catch {
    return 'Use an address like http://127.0.0.1:8080.'
  }
}
