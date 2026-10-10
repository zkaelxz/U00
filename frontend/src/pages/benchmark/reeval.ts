/*
 * Pure helpers for the Model re-evaluation card (pages/benchmark/ReevalCard.tsx): labels, signed deltas, whether "Run now" may be pressed, the
 * schedule and candidate request bodies, and the wording shown for a
 * candidate that was already evaluated. No React here (reeval.test.ts).
 */
import type { BenchmarkEngineOption, BenchmarkEstimate } from '../../api/benchmark'
import type {
  CandidateAddRequest, CandidateAddResult, ModelCandidate, ProductionModel, ReevalOverview, ReevalRow,
  ReevalSettings, ReevalSettingsRequest,
} from '../../api/reeval'
import { humanize, humanizeValue, type BadgeTone } from '../../components/labels'
import { configLabel, formatCost, formatScore, formatWhen, isRunActive } from './benchmarkForm'

const DEFAULT_INTERVAL_DAYS = 30
const MAX_INTERVAL_DAYS = 365
export const MAX_REASON_CHARS = 300
export const MAX_NOTE_CHARS = 200

// superseded: promoted once, then replaced by a later promotion (reopenable like rejected).
const STATUS_LABELS: Record<string, string> = { candidate: 'Open', promoted: 'Promoted', rejected: 'Rejected', superseded: 'Superseded' }
const STATUS_TONES: Record<string, BadgeTone> = { candidate: 'info', promoted: 'ok', rejected: 'neutral', superseded: 'neutral' }

/** A rejected or superseded candidate can be put back in the running. */
export const canReopen = (status: string) => status === 'rejected' || status === 'superseded'

export const candidateStatusLabel = (s: string) => STATUS_LABELS[s] ?? humanizeValue(s)
export const candidateStatusTone = (s: string): BadgeTone => STATUS_TONES[s] ?? 'neutral'

/** "Claude · claude-sonnet-5". */
export const modelLabel = (m: Pick<ProductionModel, 'engine' | 'model'>) => configLabel('translation', m.engine, m.model)

/** Two models are the same pick (engine and model). */
export const sameModel = (a: Pick<ProductionModel, 'engine' | 'model'>, b: Pick<ProductionModel, 'engine' | 'model'>) =>
  a.engine === b.engine && (a.model ?? null) === (b.model ?? null)

/** Where the production model comes from: "From Settings" or "Promoted 2026-09-30". */
export function productionSource(overview: Pick<ReevalOverview, 'production' | 'candidates'>): string {
  const { production, candidates } = overview
  if (production.source !== 'promoted') return 'From Settings'
  if (production.promoted_at) return `Promoted ${production.promoted_at.slice(0, 10)}`
  // Older records have no date: the promote decision carries one.
  const promoted = candidates
    .filter((c) => c.status === 'promoted' && sameModel(c, production) && c.last_decision?.decided_at)
    .map((c) => c.last_decision!.decided_at as string)
    .sort()
  const when = promoted.length ? promoted[promoted.length - 1].slice(0, 10) : null
  return when ? `Promoted ${when}` : 'Promoted'
}

// ---- signed deltas (candidate minus production) ----

const MINUS = '−'

/** A cost difference: "+$0.0123", "−$0.40", "±$0.00". Lower is cheaper. */
export function formatCostDelta(usd: number | null | undefined): string {
  if (usd == null || !Number.isFinite(usd)) return '—'
  const abs = Math.abs(usd)
  if (abs < 0.00005) return '±$0.00'
  const text = abs < 0.01 ? abs.toFixed(4) : abs.toFixed(2)
  return `${usd > 0 ? '+' : MINUS}$${text}`
}

/** An average-time difference: "+0.4 s", "−120 ms", "±0 ms". */
export function formatLatencyDelta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const abs = Math.abs(seconds)
  const sign = seconds > 0 ? '+' : MINUS
  if (abs < 1) {
    const ms = Math.round(abs * 1000)
    return ms === 0 ? '±0 ms' : `${sign}${ms} ms`
  }
  return `${sign}${abs.toFixed(1)} s`
}

/** A peak-VRAM difference: "+512 MB", "−1.5 GB", "±0 MB". */
export function formatVramDelta(mb: number | null | undefined): string {
  if (mb == null || !Number.isFinite(mb)) return '—'
  const abs = Math.abs(mb)
  if (Math.round(abs) === 0) return '±0 MB'
  const sign = mb > 0 ? '+' : MINUS
  return abs >= 1024 ? `${sign}${(abs / 1024).toFixed(1)} GB` : `${sign}${Math.round(abs)} MB`
}

/** Tone for a delta where less is better (cost, time, VRAM). */
export function lowerIsBetterTone(delta: number | null | undefined, tolerance = 0): BadgeTone {
  if (delta == null || !Number.isFinite(delta) || Math.abs(delta) <= tolerance) return 'neutral'
  return delta < 0 ? 'ok' : 'bad'
}

// ---- estimate and "Run now" ----

export const openCandidates = (candidates: ModelCandidate[]) => candidates.filter((c) => c.status === 'candidate')

/** The open candidates a run includes: one that has since become production (Settings changed) is left out. */
export const runCandidates = (overview: Pick<ReevalOverview, 'production' | 'candidates'>) =>
  openCandidates(overview.candidates).filter((c) => !sameModel(c, overview.production))

/** What an estimate depends on: production, the saved golden set and the open candidates. */
export function reevalEstimateKey(overview: Pick<ReevalOverview, 'production' | 'settings' | 'candidates'>): string {
  return JSON.stringify({
    p: [overview.production.engine, overview.production.model ?? null],
    t: overview.settings.tier ?? null,
    s: overview.settings.set_name ?? null,
    c: runCandidates(overview).map((c) => c.id).sort((a, b) => a - b),
  })
}

/** Engines in the run (production + open candidates) that have no key yet; the server refuses to start them. */
export function reevalMissingKeys(
  overview: Pick<ReevalOverview, 'production' | 'candidates'>,
  engines: BenchmarkEngineOption[],
): BenchmarkEngineOption[] {
  const used = new Set([overview.production.engine, ...runCandidates(overview).map((c) => c.engine)])
  return engines.filter((e) => used.has(e.name) && !e.key_configured)
}

/** A run of the last report is still queued or running. */
export function reportActive(overview: Pick<ReevalOverview, 'report'>): boolean {
  const { report } = overview
  return (!!report.production_run && isRunActive(report.production_run)) || report.rows.some(isRunActive)
}

interface RunNowState {
  ok: boolean
  // Plain-English reasons "Run now" is disabled; the first is the most useful.
  reasons: string[]
}

/** "Run now" needs: the PC, an open candidate, keys, no run going, and a shown estimate the cap allows. */
export function runNowState(args: {
  overview: Pick<ReevalOverview, 'production' | 'settings' | 'candidates'>
  estimate: BenchmarkEstimate | null
  estimateFor: string | null
  pcRemote: boolean
  running: boolean
  missingKeys: BenchmarkEngineOption[]
}): RunNowState {
  const { overview, estimate, estimateFor, pcRemote, running, missingKeys } = args
  const reasons: string[] = []
  if (pcRemote) reasons.push('Starting a run is PC only.')
  if (!runCandidates(overview).length) reasons.push('Add a candidate model first.')
  if (running) reasons.push('A benchmark run is already going.')
  for (const e of missingKeys) reasons.push(`${humanize('engine', e.name)} has no key. Set one in Settings.`)
  if (!estimate || estimateFor !== reevalEstimateKey(overview)) reasons.push('Estimate the cost first.')
  else if (estimate.monthly_refusal) reasons.push(estimate.monthly_refusal)
  else if (estimate.estimate_above_cap) reasons.push("The estimate is more than what's left of this month's cap.")
  return { ok: reasons.length === 0, reasons }
}

// ---- schedule ----

export interface ScheduleDraft {
  enabled: boolean
  interval: string
  tier: string
  setName: string
  // Dollars; blank = no limit of its own (then a monthly cap must be set in Settings to turn the schedule on).
  limit: string
}

export const draftFromSettings = (s: ReevalSettings): ScheduleDraft => ({
  enabled: s.schedule_enabled,
  interval: String(s.interval_days || DEFAULT_INTERVAL_DAYS),
  tier: s.tier ?? '',
  setName: s.set_name ?? '',
  limit: s.max_cost_usd == null ? '' : String(s.max_cost_usd),
})

/** Why the interval can't be saved, or null. */
export function intervalProblem(text: string): string | null {
  const t = text.trim()
  if (!/^\d+$/.test(t)) return 'Enter a whole number of days.'
  const n = Number(t)
  if (n < 1 || n > MAX_INTERVAL_DAYS) return `Between 1 and ${MAX_INTERVAL_DAYS} days.`
  return null
}

const MAX_LIMIT_USD = 10000

/** Why the per-run limit can't be saved, or null (blank is fine: no limit). */
export function limitProblem(text: string): string | null {
  const t = text.trim()
  if (!t) return null
  const n = Number(t)
  if (!/^\d*\.?\d+$|^\d+\.$/.test(t) || !Number.isFinite(n)) return 'Enter an amount in dollars, e.g. 0.50.'
  if (n > MAX_LIMIT_USD) return `At most $${MAX_LIMIT_USD}.`
  return null
}

/**
 * The POST /settings body. The server replaces the whole record (a left-out
 * tier or set means "any"), so every field is sent, blank ones as null.
 */
export function scheduleBody(d: ScheduleDraft): ReevalSettingsRequest {
  return {
    schedule_enabled: d.enabled,
    interval_days: Number(d.interval.trim()),
    tier: d.tier || null,
    set_name: d.setName || null,
    max_cost_usd: d.limit.trim() ? Number(d.limit.trim()) : null,
  }
}

export function scheduleDirty(d: ScheduleDraft, s: ReevalSettings): boolean {
  const b = scheduleBody(d)
  return b.schedule_enabled !== s.schedule_enabled || b.interval_days !== s.interval_days
    || b.tier !== (s.tier ?? null) || b.set_name !== (s.set_name ?? null)
    || (b.max_cost_usd ?? null) !== (s.max_cost_usd ?? null)
}

/** The golden-set choice differs from the saved one (runs and estimates use the saved one). */
export const setChoiceDirty = (d: ScheduleDraft, s: ReevalSettings) =>
  (d.tier || null) !== (s.tier ?? null) || (d.setName || null) !== (s.set_name ?? null)

/** "Every 30 days · Public / flores-zh · up to $0.50 a run" or "Off · any set". */
export function scheduleSummary(s: ReevalSettings, tierLabel: (t: string) => string): string {
  const set = s.set_name ? s.set_name : s.tier ? `any ${tierLabel(s.tier).toLowerCase()} set` : 'any set'
  const on = s.schedule_enabled ? `Every ${s.interval_days} ${s.interval_days === 1 ? 'day' : 'days'}` : 'Off'
  const limit = s.schedule_enabled && s.max_cost_usd != null ? ` · up to ${formatCost(s.max_cost_usd)} a run` : ''
  return `${on} · ${s.tier && s.set_name ? `${tierLabel(s.tier)} / ` : ''}${set}${limit}`
}

/** Stored times are UTC without a zone: read them as UTC. */
function parseUtc(iso: string): number {
  return Date.parse(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`)
}

/** The next scheduled run in words. */
export function nextDueText(overview: Pick<ReevalOverview, 'settings' | 'next_due_at' | 'candidates' | 'production'>, now: number = Date.now()): string {
  if (!overview.settings.schedule_enabled) return 'Schedule off: runs only when you press Run now.'
  if (!runCandidates(overview).length) return 'Schedule on: nothing runs until a candidate is added.'
  const due = overview.next_due_at
  if (!due) return 'Schedule on.'
  const t = parseUtc(due)
  if (Number.isFinite(t) && t <= now) return 'Next run: due now (the PC checks about once an hour).'
  return `Next run: ${formatWhen(due)} (UTC).`
}

// ---- candidates ----

interface CandidateForm {
  engine: string
  model: string
  note: string
}

/** The POST /candidates body: a blank model or note is left out. */
export function candidateBody(f: CandidateForm): CandidateAddRequest {
  const body: CandidateAddRequest = { engine: f.engine }
  if (f.model) body.model = f.model
  const note = f.note.trim()
  if (note) body.note = note.slice(0, MAX_NOTE_CHARS)
  return body
}

/** Engines a candidate can use: every translation engine, usable ones first. */
export function candidateEngines(engines: BenchmarkEngineOption[]): BenchmarkEngineOption[] {
  return [...engines.filter((e) => e.key_configured), ...engines.filter((e) => !e.key_configured)]
}

export interface AddOutcome {
  // 'known': it was registered before; the text says what was decided then.
  kind: 'added' | 'known'
  text: string
}

/** What to say after adding: a model evaluated before shows its recorded decision, never "added". */
export function addOutcome(res: CandidateAddResult): AddOutcome {
  const c = res.candidate
  const name = modelLabel(c)
  if (!res.already_registered) return { kind: 'added', text: `Added ${name} as a candidate.` }
  if (c.last_decision) return { kind: 'known', text: `${name}: ${c.last_decision.summary}` }
  return { kind: 'known', text: `${name} is already on the list (${candidateStatusLabel(c.status).toLowerCase()}).` }
}

/** The report row for a candidate in the latest re-evaluation, if it was in it. */
export const rowFor = (rows: ReevalRow[], candidateId: number) => rows.find((r) => r.candidate.id === candidateId) ?? null

/** Promote is offered for an open candidate that has a finished report row. */
export const canPromote = (c: ModelCandidate, row: ReevalRow | null) =>
  c.status === 'candidate' && !!row && !isRunActive(row)

/** What promoting changes, in plain words. */
export function promoteEffects(c: Pick<ModelCandidate, 'engine' | 'model'>, production: ProductionModel): string[] {
  const out = [`${modelLabel(c)} becomes the production model that later re-evaluations compare against.`]
  out.push(c.engine !== production.engine
    ? `Settings' default engine changes from ${humanize('engine', production.engine)} to ${humanize('engine', c.engine)}.`
    : `Settings' default engine stays ${humanize('engine', production.engine)}.`)
  out.push("Presets keep their own model; switch those with Model health on Diagnostics.")
  return out
}

/** A decision's recorded scores: "Score 82.3% vs production 80.1%". */
export function decisionScores(scores: Record<string, unknown>): string | null {
  const num = (k: string) => (typeof scores[k] === 'number' ? (scores[k] as number) : null)
  const score = num('aggregate_score')
  const prod = num('production_score')
  if (score == null && prod == null) return null
  return prod == null ? `Score ${formatScore(score)}` : `Score ${formatScore(score)} vs production ${formatScore(prod)}`
}

export const decisionLabel = (d: string) => (d === 'promoted' ? 'Promoted' : d === 'rejected' ? 'Rejected' : humanizeValue(d))
