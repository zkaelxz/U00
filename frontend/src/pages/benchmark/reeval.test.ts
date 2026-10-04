import { describe, expect, it } from 'vitest'

import type { BenchmarkEngineOption, BenchmarkEstimate } from '../../api/benchmark'
import type { ModelCandidate, ModelDecision, ReevalOverview, ReevalRow } from '../../api/reeval'
import {
  addOutcome, canPromote, canReopen, candidateBody, candidateStatusLabel, candidateEngines, decisionScores, draftFromSettings, formatCostDelta,
  formatLatencyDelta, formatVramDelta, intervalProblem, limitProblem, lowerIsBetterTone, nextDueText, productionSource,
  promoteEffects, reevalEstimateKey, reevalMissingKeys, reportActive, runCandidates, runNowState, scheduleBody, scheduleDirty,
  scheduleSummary, setChoiceDirty,
} from './reeval'

const decision = (o: Partial<ModelDecision> = {}): ModelDecision => ({
  id: 1, candidate_id: 2, decision: 'rejected', reason: 'worse on names', scores: {}, decided_at: '2026-09-12T10:00:00',
  summary: 'Already evaluated on 2026-09-12, rejected: worse on names', ...o,
})

const cand = (o: Partial<ModelCandidate> = {}): ModelCandidate => ({
  id: 2, capability: 'translation', engine: 'deepseek', model: 'deepseek-v4-flash', note: '', status: 'candidate',
  created_at: '2026-09-10T09:00:00', last_decision: null, ...o,
})

const row = (o: Partial<ReevalRow> = {}): ReevalRow => ({
  candidate: cand(), run_id: 12, status: 'done', aggregate_score: 0.84, quality_delta: 0.032, cost_delta_usd: -0.02,
  latency_delta_seconds: 0.4, vram_delta_mb: null, total_cost_usd: 0.01, avg_latency_seconds: 1.2, ...o,
})

const overview = (o: Partial<ReevalOverview> = {}): ReevalOverview => ({
  capability: 'translation',
  production: { engine: 'claude', model: 'claude-sonnet-5', source: 'settings' },
  settings: { schedule_enabled: false, interval_days: 30, tier: null, set_name: null, max_cost_usd: null },
  next_due_at: null,
  candidates: [cand()],
  report: { production: { engine: 'claude', model: 'claude-sonnet-5', source: 'settings' }, production_run: null, started_at: null, scheduled: false, arena_group: null, error: null, rows: [] },
  ...o,
})

const engine = (name: string, key = true): BenchmarkEngineOption => ({ name, label: name, free: false, models: null, key_configured: key })

const estimate = (o: Partial<BenchmarkEstimate> = {}): BenchmarkEstimate => ({
  stage: 'translation', case_count: 3, configs: [], estimated_cost_usd: 0.02, monthly_cap_usd: 5, month_spend_usd: 1,
  remaining_usd: 4, monthly_refusal: null, estimate_above_cap: false, ...o,
})

describe('delta formatting', () => {
  it('cost: signed dollars, 4 places under a cent, ±$0.00 for nothing', () => {
    expect(formatCostDelta(0.4)).toBe('+$0.40')
    expect(formatCostDelta(-0.0123)).toBe('−$0.01')
    expect(formatCostDelta(-0.0042)).toBe('−$0.0042')
    expect(formatCostDelta(0)).toBe('±$0.00')
    expect(formatCostDelta(null)).toBe('—')
  })

  it('latency: ms under a second, else seconds', () => {
    expect(formatLatencyDelta(0.4)).toBe('+400 ms')
    expect(formatLatencyDelta(-1.25)).toBe('−1.3 s')
    expect(formatLatencyDelta(0.0001)).toBe('±0 ms')
    expect(formatLatencyDelta(undefined)).toBe('—')
  })

  it('VRAM: MB, GB from 1024', () => {
    expect(formatVramDelta(512)).toBe('+512 MB')
    expect(formatVramDelta(-1536)).toBe('−1.5 GB')
    expect(formatVramDelta(0.2)).toBe('±0 MB')
    expect(formatVramDelta(null)).toBe('—')
  })

  it('less is better for cost, time and VRAM', () => {
    expect(lowerIsBetterTone(-0.01)).toBe('ok')
    expect(lowerIsBetterTone(0.3)).toBe('bad')
    expect(lowerIsBetterTone(0.0001, 0.001)).toBe('neutral')
    expect(lowerIsBetterTone(null)).toBe('neutral')
  })
})

describe('Run now', () => {
  const base = { pcRemote: false, running: false, missingKeys: [] as BenchmarkEngineOption[] }

  it('is disabled until an estimate for this exact line-up is shown', () => {
    const o = overview()
    expect(runNowState({ ...base, overview: o, estimate: null, estimateFor: null })).toEqual({ ok: false, reasons: ['Estimate the cost first.'] })
    expect(runNowState({ ...base, overview: o, estimate: estimate(), estimateFor: reevalEstimateKey(o) }).ok).toBe(true)
  })

  it('a new candidate or a changed golden set makes the estimate stale', () => {
    const o = overview()
    const key = reevalEstimateKey(o)
    const more = overview({ candidates: [cand(), cand({ id: 3, engine: 'gemini', model: null })] })
    const otherSet = overview({ settings: { ...o.settings, set_name: 'flores' } })
    expect(reevalEstimateKey(more)).not.toBe(key)
    expect(reevalEstimateKey(otherSet)).not.toBe(key)
    // A rejected candidate is not in the run, so it doesn't change the key.
    expect(reevalEstimateKey(overview({ candidates: [cand(), cand({ id: 9, status: 'rejected' })] }))).toBe(key)
    expect(runNowState({ ...base, overview: more, estimate: estimate(), estimateFor: key }).reasons).toEqual(['Estimate the cost first.'])
  })

  it('a refused or over-cap estimate keeps it disabled with the reason', () => {
    const o = overview()
    const key = reevalEstimateKey(o)
    expect(runNowState({ ...base, overview: o, estimate: estimate({ monthly_refusal: "This month's cap is used up." }), estimateFor: key }).reasons)
      .toEqual(["This month's cap is used up."])
    expect(runNowState({ ...base, overview: o, estimate: estimate({ estimate_above_cap: true }), estimateFor: key }).reasons)
      .toEqual(["The estimate is more than what's left of this month's cap."])
  })

  it('PC only, needs an open candidate, keys and no other run', () => {
    const o = overview({ candidates: [cand({ status: 'rejected' })] })
    const s = runNowState({ pcRemote: true, running: true, missingKeys: [engine('claude', false)], overview: o, estimate: null, estimateFor: null })
    expect(s.ok).toBe(false)
    expect(s.reasons).toEqual([
      'Starting a run is PC only.',
      'Add a candidate model first.',
      'A benchmark run is already going.',
      'Claude has no key. Set one in Settings.',
      'Estimate the cost first.',
    ])
  })

  it('an open candidate that is production now (Settings changed) is left out of the run', () => {
    const o = overview({ candidates: [cand({ engine: 'claude', model: 'claude-sonnet-5' })] })
    expect(runCandidates(o)).toEqual([])
    expect(runNowState({ ...base, overview: o, estimate: estimate(), estimateFor: reevalEstimateKey(o) }).reasons).toEqual(['Add a candidate model first.'])
  })

  it('missing keys: production and open candidates only', () => {
    const o = overview({ candidates: [cand(), cand({ id: 3, engine: 'gemini', status: 'rejected' })] })
    const engines = [engine('claude', false), engine('deepseek', false), engine('gemini', false), engine('nllb')]
    expect(reevalMissingKeys(o, engines).map((e) => e.name)).toEqual(['claude', 'deepseek'])
  })

  it('a report run still queued or running counts as active', () => {
    expect(reportActive(overview())).toBe(false)
    const o = overview()
    expect(reportActive({ report: { ...o.report, rows: [row({ status: 'running' })] } })).toBe(true)
  })
})

describe('schedule', () => {
  const saved = { schedule_enabled: false, interval_days: 30, tier: null, set_name: null, max_cost_usd: null }

  it('the body carries every field (the server replaces the record); blanks are null', () => {
    const d = { ...draftFromSettings(saved), enabled: true, interval: ' 14 ' }
    expect(scheduleBody(d)).toEqual({ schedule_enabled: true, interval_days: 14, tier: null, set_name: null, max_cost_usd: null })
    expect(scheduleDirty(d, saved)).toBe(true)
    expect(scheduleDirty(draftFromSettings(saved), saved)).toBe(false)
    expect(setChoiceDirty(d, saved)).toBe(false)
    expect(setChoiceDirty({ ...d, tier: 'public' }, saved)).toBe(true)
  })

  it('the per-run limit is kept on save, blank means none', () => {
    const withLimit = { ...saved, max_cost_usd: 0.5 }
    const d = draftFromSettings(withLimit)
    expect(d.limit).toBe('0.5')
    // Changing another field still sends the saved limit (the server replaces the whole record).
    expect(scheduleBody({ ...d, enabled: true }).max_cost_usd).toBe(0.5)
    expect(scheduleDirty(d, withLimit)).toBe(false)
    expect(scheduleDirty({ ...d, limit: '' }, withLimit)).toBe(true)
    expect(scheduleBody({ ...d, limit: ' ' }).max_cost_usd).toBeNull()
    expect(limitProblem('')).toBeNull()
    expect(limitProblem('0.25')).toBeNull()
    expect(limitProblem('abc')).toBe('Enter an amount in dollars, e.g. 0.50.')
    expect(limitProblem('-1')).toBe('Enter an amount in dollars, e.g. 0.50.')
    expect(limitProblem('20000')).toBe('At most $10000.')
  })

  it('interval must be a whole number of days, 1 to 365', () => {
    expect(intervalProblem('30')).toBeNull()
    expect(intervalProblem('0')).toBe('Between 1 and 365 days.')
    expect(intervalProblem('400')).toBe('Between 1 and 365 days.')
    expect(intervalProblem('2.5')).toBe('Enter a whole number of days.')
    expect(intervalProblem('')).toBe('Enter a whole number of days.')
  })

  it('summary and next due in words', () => {
    const label = (t: string) => (t === 'public' ? 'Public' : t)
    expect(scheduleSummary(saved, label)).toBe('Off · any set')
    expect(scheduleSummary({ schedule_enabled: true, interval_days: 1, tier: 'public', set_name: 'flores', max_cost_usd: null }, label)).toBe('Every 1 day · Public / flores')
    expect(scheduleSummary({ ...saved, tier: 'public' }, label)).toBe('Off · any public set')
    expect(scheduleSummary({ ...saved, schedule_enabled: true, max_cost_usd: 0.5 }, label)).toBe('Every 30 days · any set · up to $0.50 a run')

    const now = Date.parse('2026-09-30T12:00:00Z')
    expect(nextDueText(overview(), now)).toBe('Schedule off: runs only when you press Run now.')
    const on = { ...saved, schedule_enabled: true }
    expect(nextDueText(overview({ settings: on, candidates: [], next_due_at: '2026-10-30T12:00:00' }), now))
      .toBe('Schedule on: nothing runs until a candidate is added.')
    expect(nextDueText(overview({ settings: on, next_due_at: '2026-10-30T12:00:00' }), now)).toBe('Next run: 2026-10-30 12:00 (UTC).')
    expect(nextDueText(overview({ settings: on, next_due_at: '2026-09-30T11:00:00' }), now)).toBe('Next run: due now (the PC checks about once an hour).')
  })
})

describe('candidates', () => {
  it('the add body leaves out a blank model and note', () => {
    expect(candidateBody({ engine: 'gemini', model: '', note: '  ' })).toEqual({ engine: 'gemini' })
    expect(candidateBody({ engine: 'deepseek', model: 'deepseek-v4-flash', note: ' cheaper ' })).toEqual({
      engine: 'deepseek', model: 'deepseek-v4-flash', note: 'cheaper',
    })
  })

  it('lists usable engines first', () => {
    expect(candidateEngines([engine('claude', false), engine('ollama')]).map((e) => e.name)).toEqual(['ollama', 'claude'])
  })

  it('a model evaluated before shows its recorded decision, not "added"', () => {
    expect(addOutcome({ candidate: cand(), already_registered: false })).toEqual({ kind: 'added', text: 'Added DeepSeek · deepseek-v4-flash as a candidate.' })
    expect(addOutcome({ candidate: cand({ status: 'rejected', last_decision: decision() }), already_registered: true })).toEqual({
      kind: 'known', text: 'DeepSeek · deepseek-v4-flash: Already evaluated on 2026-09-12, rejected: worse on names',
    })
    expect(addOutcome({ candidate: cand(), already_registered: true })).toEqual({
      kind: 'known', text: 'DeepSeek · deepseek-v4-flash is already on the list (open).',
    })
  })

  it('rejected and superseded candidates can be reopened; open and promoted ones cannot', () => {
    expect(canReopen('rejected')).toBe(true)
    expect(canReopen('superseded')).toBe(true)
    expect(canReopen('candidate')).toBe(false)
    expect(canReopen('promoted')).toBe(false)
    expect(candidateStatusLabel('superseded')).toBe('Superseded')
  })

  it('production source: from Settings, or promoted with the decision date', () => {
    expect(productionSource(overview())).toBe('From Settings')
    const promoted = cand({ status: 'promoted', last_decision: decision({ decision: 'promoted', decided_at: '2026-09-20T08:00:00' }) })
    const o = overview({ production: { engine: 'deepseek', model: 'deepseek-v4-flash', source: 'promoted' }, candidates: [promoted] })
    expect(productionSource(o)).toBe('Promoted 2026-09-20')
    expect(productionSource({ ...o, candidates: [] })).toBe('Promoted')
    expect(productionSource({ ...o, production: { ...o.production, promoted_at: '2026-09-25T10:00:00' } })).toBe('Promoted 2026-09-25')
  })

  it('promote is offered for an open candidate with a finished report row', () => {
    expect(canPromote(cand(), row())).toBe(true)
    expect(canPromote(cand(), null)).toBe(false)
    expect(canPromote(cand(), row({ status: 'running' }))).toBe(false)
    expect(canPromote(cand({ status: 'rejected' }), row())).toBe(false)
  })

  it('promote explains the default-engine change and that presets stay', () => {
    const prod = { engine: 'claude', model: 'claude-sonnet-5', source: 'settings' }
    const other = promoteEffects(cand(), prod)
    expect(other[1]).toBe("Settings' default engine changes from Claude to DeepSeek.")
    expect(other[2]).toMatch(/Presets keep their own model/)
    expect(promoteEffects(cand({ engine: 'claude', model: 'claude-opus-5' }), prod)[1]).toBe("Settings' default engine stays Claude.")
  })

  it('decision scores in words', () => {
    expect(decisionScores({})).toBeNull()
    expect(decisionScores({ aggregate_score: 0.8234, production_score: 0.8 })).toBe('Score 82.3% vs production 80.0%')
    expect(decisionScores({ aggregate_score: 0.5 })).toBe('Score 50.0%')
  })
})
