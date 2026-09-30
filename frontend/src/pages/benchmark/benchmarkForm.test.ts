import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { BenchmarkEstimate, BenchmarkOptions, BenchmarkRun, BenchmarkSet } from '../../api/benchmark'
import {
  arenaGroups, arenaRunNames, casesInSelection, compareProblem, configLabel, defaultConfig, deltaTone, enginesMissingKey, estimateKey,
  formatCost, formatDelta, formatLatency, formatScore, formatWhen, metricName, metricNote, mixedScorerNote, plainError, restoreConfigs,
  runRequestBody, selectionProblems, setOptions, startState, tierLabel, toggleCompare, type RunSelection,
} from './benchmarkForm'

const options: BenchmarkOptions = {
  stages: ['translation', 'transcription', 'ocr'],
  tiers: ['public', 'application', 'regression'],
  source_languages: ['zh', 'ja', 'ko'],
  translation_engines: [
    { name: 'claude', label: 'x', free: false, models: ['sonnet', 'haiku'], key_configured: false },
    { name: 'test_offline', label: 'x', free: true, models: null, key_configured: true },
    { name: 'nllb', label: 'x', free: true, models: ['small'], key_configured: true },
  ],
  whisper_sizes: ['small', 'medium', 'large-v3'],
  ocr_backends: ['tesseract', 'paddle'],
  pass_threshold: 0.8,
  max_configs: 4,
}

const sel = (over: Partial<RunSelection> = {}): RunSelection => ({
  stage: 'translation',
  configs: [{ engine: 'test_offline' }],
  tier: '',
  setName: '',
  label: '',
  promptVersion: '',
  ...over,
})

const estimate = (over: Partial<BenchmarkEstimate> = {}): BenchmarkEstimate => ({
  stage: 'translation', case_count: 3, configs: [], estimated_cost_usd: 0, monthly_cap_usd: 0, month_spend_usd: 0,
  remaining_usd: null, monthly_refusal: null, estimate_above_cap: false, ...over,
})

const run = (id: number, over: Partial<BenchmarkRun> = {}): BenchmarkRun => ({
  id, label: '', stage: 'translation', engine: 'test_offline', model: null, prompt_version: '', arena_group: null,
  status: 'done', case_count: 2, scored_count: 2, passed_count: 1, error_count: 0, aggregate_score: 0.5,
  avg_latency_seconds: 0.1, total_cost_usd: 0, peak_vram_mb: null, note: null, created_at: null, finished_at: null,
  context_settings: {}, case_filter: {}, delta_vs_first: null, ...over,
})

describe('formatting', () => {
  it('scores, deltas, cost, latency and times', () => {
    expect(formatScore(0.8234)).toBe('82.3%')
    expect(formatScore(null)).toBe('—')
    expect(formatDelta(0.032)).toBe('+3.2 pts')
    expect(formatDelta(-0.01)).toBe('−1.0 pts')
    expect(formatDelta(0.00001)).toBe('±0 pts')
    expect(formatDelta(null)).toBe('—')
    expect(deltaTone(0.05)).toBe('ok')
    expect(deltaTone(-0.05)).toBe('bad')
    expect(deltaTone(0)).toBe('neutral')
    expect(formatCost(0)).toBe('$0.00')
    expect(formatCost(0.0012)).toBe('$0.0012')
    expect(formatCost(1.5)).toBe('$1.50')
    expect(formatLatency(0.25)).toBe('250 ms')
    expect(formatLatency(2.345)).toBe('2.3 s')
    expect(formatWhen('2026-09-29T14:03:11.123')).toBe('2026-09-29 14:03')
    expect(formatWhen(null)).toBe('—')
  })

  it('labels engines per stage and tiers in plain words', () => {
    expect(configLabel('translation', 'claude', 'haiku')).toBe('Claude · haiku')
    expect(configLabel('translation', 'test_offline', null)).toBe('Offline test')
    expect(configLabel('transcription', 'whisper', 'large-v3')).toBe('Whisper large-v3')
    expect(configLabel('ocr', 'manga_ocr')).toBe('Manga OCR')
    expect(tierLabel('regression')).toBe('Regression')
  })

  it('never calls a CER/WER score translation quality', () => {
    expect(metricName('cer')).toBe('1 − CER')
    expect(metricName('wer')).toBe('1 − WER')
    expect(metricName('similarity')).toBe('similarity')
    expect(metricNote('translation')).toMatch(/similarity/)
    expect(metricNote('ocr')).toMatch(/CER/)
    expect(metricNote('ocr')).not.toMatch(/translation/i)
    expect(metricNote('transcription')).toMatch(/WER/)
    expect(metricNote('transcription')).not.toMatch(/translation/i)
  })

  it('flags CER/WER scores from different scorers side by side', () => {
    const cell = (metric: string, scorer?: string | null) => ({ metric, scorer })
    expect(mixedScorerNote([{ results: [cell('cer', 'jiwer'), cell('cer', null)] }])).toMatch(/jiwer/)
    expect(mixedScorerNote([{ results: [cell('wer', 'jiwer')] }, { results: [cell('wer', 'builtin')] }])).not.toBe('')
    // Older results (no scorer recorded) were built-in: same scorer, no note.
    expect(mixedScorerNote([{ results: [cell('cer', null), cell('cer', 'builtin'), null] }])).toBe('')
    expect(mixedScorerNote([{ results: [cell('cer', 'jiwer'), cell('cer', 'jiwer')] }])).toBe('')
    // Translation similarity is never compared across scorers.
    expect(mixedScorerNote([{ results: [cell('similarity', 'builtin'), cell('cer', 'jiwer')] }])).toBe('')
  })
})

describe('run request', () => {
  it('leaves out blank filters, models, label and prompt version', () => {
    expect(runRequestBody(sel({ configs: [{ engine: 'claude', model: '' }], label: '  ', promptVersion: '' }))).toEqual({
      stage: 'translation', configs: [{ engine: 'claude' }],
    })
    expect(runRequestBody(sel({ configs: [{ engine: 'claude', model: 'haiku' }], tier: 'public', setName: 'flores', label: ' A ', promptVersion: 'v2 ' }))).toEqual({
      stage: 'translation', configs: [{ engine: 'claude', model: 'haiku' }], tier: 'public', set_name: 'flores', label: 'A', prompt_version: 'v2',
    })
  })

  it('the estimate key ignores label and prompt version but not engines or filters', () => {
    const base = estimateKey(sel())
    expect(estimateKey(sel({ label: 'x', promptVersion: 'v3' }))).toBe(base)
    expect(estimateKey(sel({ tier: 'public' }))).not.toBe(base)
    expect(estimateKey(sel({ configs: [{ engine: 'nllb' }] }))).not.toBe(base)
  })

  it('default configs pick the next unused engine, usable ones first', () => {
    expect(defaultConfig('translation', options)).toEqual({ engine: 'test_offline' })
    expect(defaultConfig('translation', options, [{ engine: 'test_offline' }])).toEqual({ engine: 'nllb' })
    expect(defaultConfig('transcription', options, [{ engine: 'whisper', model: 'small' }])).toEqual({ engine: 'whisper', model: 'medium' })
    expect(defaultConfig('ocr', options, [{ engine: 'tesseract' }, { engine: 'paddle' }])).toBeNull()
  })

  it('restores remembered configs only while the options still offer them', () => {
    expect(restoreConfigs('translation', [{ engine: 'claude', model: 'haiku' }, { engine: 'gone' }], options)).toEqual([
      { engine: 'claude', model: 'haiku' },
    ])
    expect(restoreConfigs('translation', [{ engine: 'claude', model: 'opus-9' }], options)).toEqual([{ engine: 'test_offline' }])
    expect(restoreConfigs('ocr', 'junk', options)).toEqual([{ engine: 'tesseract' }])
    expect(restoreConfigs('transcription', [{ engine: 'whisper', model: 'large-v3' }], options)).toEqual([{ engine: 'whisper', model: 'large-v3' }])
  })

  it('flags an empty or duplicated engine list', () => {
    expect(selectionProblems(sel({ configs: [] }), 4)).toEqual(['Pick at least one engine.'])
    expect(selectionProblems(sel({ configs: [{ engine: 'nllb' }, { engine: 'nllb' }] }), 4)).toEqual([
      'The same engine and model is picked twice.',
    ])
    expect(selectionProblems(sel({ configs: [{ engine: 'nllb' }, { engine: 'nllb', model: 'small' }] }), 4)).toEqual([])
  })

  it('lists picked translation engines with no key', () => {
    expect(enginesMissingKey(sel({ configs: [{ engine: 'claude' }, { engine: 'test_offline' }] }), options.translation_engines).map((e) => e.name)).toEqual(['claude'])
    expect(enginesMissingKey(sel({ stage: 'ocr', configs: [{ engine: 'claude' }] }), options.translation_engines)).toEqual([])
  })
})

describe('whether Start is enabled', () => {
  const base = { maxConfigs: 4, pcRemote: false, running: false, missingKeys: [] }

  it('needs an estimate for exactly this selection', () => {
    const s = sel()
    expect(startState({ ...base, sel: s, estimate: null, estimateFor: null })).toEqual({
      ok: false, reasons: ['Estimate the cost of this selection first.'],
    })
    expect(startState({ ...base, sel: s, estimate: estimate(), estimateFor: estimateKey(s) }).ok).toBe(true)
    // Changing a filter after estimating asks for a new estimate; a new label does not.
    expect(startState({ ...base, sel: sel({ tier: 'public' }), estimate: estimate(), estimateFor: estimateKey(s) }).ok).toBe(false)
    expect(startState({ ...base, sel: sel({ label: 'new' }), estimate: estimate(), estimateFor: estimateKey(s) }).ok).toBe(true)
  })

  it('is refused when the monthly cap is used up or the estimate is above it', () => {
    const s = sel()
    const k = estimateKey(s)
    expect(startState({ ...base, sel: s, estimate: estimate({ monthly_refusal: 'Monthly cap reached.' }), estimateFor: k }).reasons).toEqual([
      'Monthly cap reached.',
    ])
    expect(startState({ ...base, sel: s, estimate: estimate({ estimate_above_cap: true }), estimateFor: k }).reasons[0]).toMatch(/cap/)
  })

  it('is off the PC, while running, or with a missing key', () => {
    const s = sel()
    const k = estimateKey(s)
    expect(startState({ ...base, pcRemote: true, sel: s, estimate: estimate(), estimateFor: k }).reasons).toEqual(['Starting a run is PC only.'])
    expect(startState({ ...base, running: true, sel: s, estimate: estimate(), estimateFor: k }).reasons).toEqual(['A benchmark run is already going.'])
    expect(startState({ ...base, missingKeys: [options.translation_engines[0]], sel: s, estimate: estimate(), estimateFor: k }).reasons).toEqual([
      'Claude has no key. Set one in Settings.',
    ])
  })
})

describe('sets', () => {
  const sets: BenchmarkSet[] = [
    { stage: 'translation', tier: 'public', set_name: 'flores', case_count: 10, with_reference: 10 },
    { stage: 'translation', tier: 'application', set_name: 'mine', case_count: 3, with_reference: 2 },
    { stage: 'translation', tier: 'application', set_name: '', case_count: 2, with_reference: 0 },
    { stage: 'ocr', tier: 'application', set_name: 'pages', case_count: 4, with_reference: 4 },
  ]

  it('offers named sets of the stage and tier', () => {
    expect(setOptions(sets, 'translation', '')).toEqual(['flores', 'mine'])
    expect(setOptions(sets, 'translation', 'public')).toEqual(['flores'])
    expect(setOptions(sets, 'ocr', '')).toEqual(['pages'])
  })

  it('counts the cases a selection covers', () => {
    expect(casesInSelection(sets, 'translation', '', '')).toBe(15)
    expect(casesInSelection(sets, 'translation', 'application', '')).toBe(5)
    expect(casesInSelection(sets, 'translation', '', 'flores')).toBe(10)
    expect(casesInSelection(sets, 'transcription', '', '')).toBe(0)
  })
})

describe('arena', () => {
  it('groups runs started together, in run order, only when 2 or more', () => {
    const runs = [
      run(5, { arena_group: 'g2', label: 'B' }), run(4, { arena_group: 'g2' }), run(3),
      run(2, { arena_group: 'g1', label: 'A' }), run(1, { arena_group: 'solo' }),
    ]
    expect(arenaGroups(runs)).toEqual([{ group: 'g2', runIds: [4, 5], label: 'Translation arena', created: null }])
    expect(arenaGroups([run(2, { arena_group: 'g' , label: 'L' }), run(1, { arena_group: 'g', label: 'L' })])[0]).toMatchObject({ runIds: [1, 2], label: 'L' })
  })

  it('names compared runs, adding the label only when two share an engine', () => {
    expect(arenaRunNames([run(1, { label: 'A' }), run(2, { engine: 'nllb' })])).toEqual(['Offline test', 'NLLB'])
    expect(arenaRunNames([run(1, { label: 'A' }), run(2)])).toEqual(['Offline test · A', 'Offline test · run 2'])
  })

  it('toggles a compare pick, at most 4', () => {
    expect(toggleCompare([], 1)).toEqual([1])
    expect(toggleCompare([1, 2], 1)).toEqual([2])
    expect(toggleCompare([1, 2, 3, 4], 5)).toEqual([1, 2, 3, 4])
  })

  it('explains why Compare is disabled', () => {
    const runs = [run(1), run(2), run(3, { stage: 'ocr' })]
    expect(compareProblem([1], runs)).toMatch(/Tick 2 to 4/)
    expect(compareProblem([1, 3], runs)).toBe('Only runs of the same stage can be compared.')
    expect(compareProblem([1, 2], runs)).toBeNull()
  })
})

describe('plainError', () => {
  it("shows the server's own sentence for plain codes, never a path or key", () => {
    expect(plainError(new ApiError(400, { code: 'unsupported_operation', message: 'No benchmark cases match that selection.' }))).toBe(
      'No benchmark cases match that selection.',
    )
    expect(plainError(new ApiError(400, { code: 'invalid_input', message: 'Line 3 isn\'t valid JSON.' }))).toBe("Line 3 isn't valid JSON.")
    expect(plainError(new ApiError(503, { code: 'dependency_unavailable', message: 'Bad key sk-abcdef123456' }))).toMatch(/tool or package/)
    expect(plainError(new ApiError(500, { code: 'internal_error', message: 'at /home/user/x.py' }))).toMatch(/Something went wrong/)
    expect(plainError(new ApiError(403, { code: 'forbidden', message: 'x' }), { pcOnly: true })).toBe('This only works on the main PC.')
  })
})
