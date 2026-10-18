import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { BenchmarkEstimate, BenchmarkOptions, BenchmarkRun, BenchmarkSet } from '../../api/benchmark'
import { sectionStorageKey, writeSectionOpen } from '../../components/sectionStorage'
import type { JobRecord } from '../../types/jobs'
import {
  arenaGroups, arenaRunNames, benchSectionOpen, benchSectionStorageKey, casesInSelection, compareParam, comparePrefill, compareProblem, configLabel, defaultConfig, deltaTone, enginesMissingKey, engineBlockReason, estimateKey,
  formatCost, formatDelta, formatLatency, formatScore, formatWhen, metricName, metricNote, mixedMetricNote, mixedScorerNote, parseCompareParam, plainError, restoreConfigs,
  formatJudgeScore, leftOutLine, judgeOverlap, judgeRunLine, runRequestBody, runningStatus, selectionProblems, setOptions, startState, tierLabel,
  caseTimingLine, optionValue, runConfigLabel, runExtraLabel, scriptCountsLine, timingLine, toggleCompare, whisperChoiceGroups, withOption, type RunSelection,
} from './benchmarkForm'
import { BENCH_INTRO, BENCH_PAGE_HELP, BENCH_SECTIONS } from './benchmarkHelp'

const options: BenchmarkOptions = {
  stages: ['translation', 'transcription', 'ocr'],
  tiers: ['public', 'application', 'regression'],
  source_languages: ['zh', 'ja', 'ko'],
  translation_engines: [
    { name: 'claude', label: 'x', free: false, models: ['sonnet', 'haiku'], key_configured: false },
    { name: 'ollama', label: 'x', free: true, models: null, key_configured: true },
    { name: 'deepseek', label: 'x', free: true, models: ['small'], key_configured: true },
  ],
  whisper_sizes: ['small', 'medium', 'large-v3', 'ja-1.5b'],
  whisper_exclusions: { medium: { ja: 'No medium for Japanese.' }, 'ja-1.5b': { zh: 'Japanese only.', ko: 'Japanese only.' } },
  transcription_backends: [{ name: 'qwen3_asr_vad', label: 'Qwen3-ASR', problem: null }, { name: 'anime_whisper', label: 'anime-whisper', problem: null }],
  transcribe_defaults: {
    beam_size: 5, min_silence_ms: 300, vad_threshold: 0.5, sensitivity_preset: 'normal', hallucination_silence_sec: 0,
    whisper_repeat_guard: false, difficult_audio: false, separate_vocals_first: false, initial_prompt: '',
    speech_pad_ms: 400, refine_timing: false, split_max_seconds: 8, split_max_chars: 40,
  },
  transcribe_options_ignored: { whisper: [], qwen3_asr_vad: ['beam_size'], anime_whisper: ['beam_size'] },
  translate_choices: {
    style_presets: [{ key: 'audio_drama', label: 'Audio drama' }, { key: 'novel', label: 'Novel' }],
    series: [{ id: 3, name: 'Saga' }],
    defaults: { context_window: 10, context_window_ahead: 6, batch_size: 30, reflect: false, style_preset: 'audio_drama' },
    batch_size_defaults: { claude: { '': 60, haiku: 30 }, ollama: { '': 30 } },
  },
  ocr_backends: ['tesseract', 'paddle'],
  vision_engines: ['claude', 'ollama'],
  pass_threshold: 0.8,
  chrf_pass_threshold: 0.5,
  max_configs: 4,
}

const sel = (over: Partial<RunSelection> = {}): RunSelection => ({
  stage: 'translation',
  configs: [{ engine: 'ollama' }],
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
  id, label: '', stage: 'translation', engine: 'ollama', model: null, prompt_version: '', arena_group: null,
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
    expect(configLabel('translation', 'ollama', null)).toBe('Ollama')
    expect(configLabel('transcription', 'whisper', 'large-v3')).toBe('Whisper large-v3')
    expect(configLabel('ocr', 'manga_ocr')).toBe('Manga OCR')
    expect(tierLabel('regression')).toBe('Regression')
  })

  it('never calls a CER/WER score translation quality', () => {
    expect(metricName('cer')).toBe('1 − CER')
    expect(metricName('wer')).toBe('1 − WER')
    expect(metricName('similarity')).toBe('similarity')
    expect(metricName('chrf')).toBe('chrF')
    expect(metricNote('translation')).toMatch(/chrF/)
    expect(metricNote('ocr')).toMatch(/CER/)
    expect(metricNote('ocr')).not.toMatch(/translation/i)
    expect(metricNote('transcription')).toMatch(/WER/)
    expect(metricNote('transcription')).not.toMatch(/translation/i)
  })

  it('flags CER/WER scores from different scorers side by side', () => {
    const cell = (metric: string, scorer?: string | null) => ({ metric, scorer })
    expect(mixedScorerNote([{ results: [cell('cer', 'jiwer'), cell('cer', null)] }])).toMatch(/jiwer/)
    // One run's per-case fallback is not a mix: each case matches across runs.
    expect(mixedScorerNote([{ results: [cell('wer', 'jiwer'), cell('wer', 'jiwer')] },
      { results: [cell('wer', 'builtin'), cell('wer', 'builtin')] }])).toBe('')
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
    expect(estimateKey(sel({ configs: [{ engine: 'deepseek' }] }))).not.toBe(base)
  })

  it('a bigger case count makes the estimate stale and turns Start off', () => {
    const s = sel()
    const small = estimateKey(s, 5)
    expect(estimateKey(s, 500)).not.toBe(small)
    const args = { sel: s, maxConfigs: 4, estimate: estimate(), estimateFor: small, pcRemote: false, running: false, missingKeys: [] }
    expect(startState({ ...args, caseCount: 5 }).ok).toBe(true)
    expect(startState({ ...args, caseCount: 500 })).toEqual({ ok: false, reasons: ['Estimate the cost of this selection first.'] })
  })

  it('default configs pick the next unused engine, usable ones first', () => {
    expect(defaultConfig('translation', options)).toEqual({ engine: 'ollama' })
    expect(defaultConfig('translation', options, [{ engine: 'ollama' }])).toEqual({ engine: 'deepseek' })
    expect(defaultConfig('transcription', options, [{ engine: 'whisper', model: 'small' }])).toEqual({ engine: 'whisper', model: 'large-v3' })
    expect(defaultConfig('ocr', options, [{ engine: 'tesseract' }, { engine: 'paddle' }])).toBeNull()
  })

  it('restores remembered configs only while the options still offer them', () => {
    expect(restoreConfigs('translation', [{ engine: 'claude', model: 'haiku' }, { engine: 'gone' }], options)).toEqual([
      { engine: 'claude', model: 'haiku' },
    ])
    expect(restoreConfigs('translation', [{ engine: 'claude', model: 'opus-9' }], options)).toEqual([{ engine: 'ollama' }])
    expect(restoreConfigs('ocr', 'junk', options)).toEqual([{ engine: 'tesseract' }])
    expect(restoreConfigs('transcription', [{ engine: 'whisper', model: 'large-v3' }], options)).toEqual([{ engine: 'whisper', model: 'large-v3' }])
  })

  it('flags an empty or duplicated engine list', () => {
    expect(selectionProblems(sel({ configs: [] }), 4)).toEqual(['Pick at least one engine.'])
    expect(selectionProblems(sel({ configs: [{ engine: 'deepseek' }, { engine: 'deepseek' }] }), 4)).toEqual([
      'The same engine and model is picked twice.',
    ])
    expect(selectionProblems(sel({ configs: [{ engine: 'deepseek' }, { engine: 'deepseek', model: 'small' }] }), 4)).toEqual([])
  })

  it('lists picked translation engines with no key', () => {
    expect(enginesMissingKey(sel({ configs: [{ engine: 'claude' }, { engine: 'ollama' }] }), options.translation_engines).map((e) => e.name)).toEqual(['claude'])
    expect(enginesMissingKey(sel({ stage: 'ocr', configs: [{ engine: 'tesseract' }] }), options.translation_engines)).toEqual([])
    expect(enginesMissingKey(sel({ stage: 'ocr', configs: [{ engine: 'claude' }] }), options.translation_engines).map((e) => e.name)).toEqual(['claude'])
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

describe('engines that cannot run', () => {
  const claude = options.translation_engines[0]
  it('names a missing package instead of a key', () => {
    expect(engineBlockReason({ ...claude, key_configured: false, package_installed: false })).toContain('anthropic package')
  })
  it('names Ollama when it is not answering', () => {
    const ollama = { name: 'ollama', label: 'Ollama', free: true, models: null, key_configured: false }
    expect(engineBlockReason(ollama)).toContain("Ollama isn't answering")
  })
  it('keeps the key wording when only the key is missing', () => {
    expect(engineBlockReason({ ...claude, key_configured: false })).toBe('Claude has no key. Set one in Settings.')
  })
})

describe('sets', () => {
  const sets: BenchmarkSet[] = [
    { stage: 'translation', tier: 'public', set_name: 'flores', case_count: 10, with_reference: 10 },
    { stage: 'translation', tier: 'application', set_name: 'mine', case_count: 3, with_reference: 2, excluded: 1 },
    { stage: 'translation', tier: 'application', set_name: '', case_count: 2, with_reference: 0 },
    { stage: 'ocr', tier: 'application', set_name: 'pages', case_count: 4, with_reference: 4 },
  ]

  it('offers named sets of the stage and tier', () => {
    expect(setOptions(sets, 'translation', '')).toEqual(['flores', 'mine'])
    expect(setOptions(sets, 'translation', 'public')).toEqual(['flores'])
    expect(setOptions(sets, 'ocr', '')).toEqual(['pages'])
  })

  it('counts the cases a selection covers', () => {
    // An excluded case is not run, so it is not counted.
    expect(casesInSelection(sets, 'translation', '', '')).toBe(14)
    expect(casesInSelection(sets, 'translation', 'application', '')).toBe(4)
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
    expect(arenaRunNames([run(1, { label: 'A' }), run(2, { engine: 'deepseek' })])).toEqual(['Ollama', 'DeepSeek'])
    expect(arenaRunNames([run(1, { label: 'A' }), run(2)])).toEqual(['Ollama · A', 'Ollama · run 2'])
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

describe('compare links (Model health -> Benchmark Lab)', () => {
  it('builds and reads engine:model pairs, keeping ":" and "/" inside a model', () => {
    const configs = [{ engine: 'ollama', model: 'gemma4:12b' }, { engine: 'deepseek', model: 'vendor/model' }, { engine: 'ollama' }]
    const raw = compareParam(configs)
    expect(raw).toBe('ollama:gemma4%3A12b,deepseek:vendor%2Fmodel,ollama')
    expect(parseCompareParam(raw)).toEqual(configs)
    expect(parseCompareParam('claude:claude-sonnet-4-6,claude:claude-sonnet-5')).toEqual([
      { engine: 'claude', model: 'claude-sonnet-4-6' }, { engine: 'claude', model: 'claude-sonnet-5' },
    ])
  })

  it('skips empty and malformed parts', () => {
    expect(parseCompareParam(null)).toEqual([])
    expect(parseCompareParam('')).toEqual([])
    expect(parseCompareParam(',claude:,:x,%E0%A4%A:y,deepseek:small')).toEqual([{ engine: 'claude' }, { engine: 'deepseek', model: 'small' }])
  })

  it('prefills offered configs and says what it left out', () => {
    const p = comparePrefill(parseCompareParam('claude:gone-model,claude:haiku,nope:x,ollama:some-model,claude:haiku'), options)
    expect(p.configs).toEqual([{ engine: 'claude', model: 'haiku' }, { engine: 'ollama' }])
    expect(p.notes).toEqual([
      "Claude · gone-model isn't offered in this app any more, so it was left out.",
      "Nope · x isn't an engine the Benchmark Lab can run, so it was left out.",
      'Ollama has no model choice here, so it runs its built-in model.',
    ])
  })

  it('stops at the most engines a run may have', () => {
    const p = comparePrefill(parseCompareParam('claude:sonnet,claude:haiku,deepseek:small,ollama,claude'), { ...options, max_configs: 4 })
    expect(p.configs).toHaveLength(4)
    expect(p.notes).toEqual(['Claude was left out: at most 4 engines at once.'])
  })
})

describe('mixedMetricNote', () => {
  const cell = (metric: string) => ({ metric })
  it('warns when chrF and the older similarity ratio meet in one case', () => {
    expect(mixedMetricNote([{ results: [cell('chrf'), cell('similarity')] }])).toMatch(/chrF/)
    expect(mixedMetricNote([{ results: [cell('chrf'), cell('chrf'), null] }])).toBe('')
    expect(mixedMetricNote([{ results: [cell('cer'), cell('similarity')] }])).toBe('')
  })
})

describe('page sections', () => {
  const store = (init: Record<string, string> = {}) => {
    const data = { ...init }
    return { getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => void (data[k] = v) }
  }

  it('opens every section on a first visit: each is the first card of its tab', () => {
    const s = store()
    for (const id of ['sets', 'run', 'sweep', 'reeval', 'runs'] as const) expect(benchSectionOpen(s, id)).toBe(true)
  })

  it('remembers a choice over the default, per section', () => {
    const s = store()
    writeSectionOpen(s, benchSectionStorageKey('sets'), false)
    writeSectionOpen(s, benchSectionStorageKey('run'), false)
    expect(benchSectionOpen(s, 'sets')).toBe(false)
    expect(benchSectionOpen(s, 'run')).toBe(false)
    expect(benchSectionOpen(s, 'runs')).toBe(true)
  })

  it('falls back to the default without storage or with junk in it', () => {
    expect(benchSectionOpen(null, 'sets')).toBe(true)
    expect(benchSectionOpen(store({ [sectionStorageKey(benchSectionStorageKey('run'))]: 'maybe' }), 'run')).toBe(true)
  })

  it('shows the percentage while a job runs', () => {
    expect(runningStatus(null)).toBe('Running…')
    expect(runningStatus({ progress: 0.4 } as JobRecord)).toBe('Running · 40%')
  })

  it('keeps the help copy short', () => {
    const lines = [...BENCH_INTRO, ...BENCH_PAGE_HELP, ...Object.values(BENCH_SECTIONS).flatMap((s) => [s.purpose, ...s.steps])]
    for (const line of lines) expect(line.split(/\s+/).length, line).toBeLessThanOrEqual(14)
  })
})

describe('judge', () => {
  const judged = (over: Partial<RunSelection> = {}) => sel({ judge: { engine: 'deepseek', model: '' }, ...over })

  it('is sent only on translation runs, with the model when one is picked', () => {
    expect(runRequestBody(sel()).judge).toBeUndefined()
    expect(runRequestBody(judged()).judge).toEqual({ engine: 'deepseek' })
    expect(runRequestBody(judged({ judge: { engine: 'deepseek', model: 'small' } })).judge).toEqual({ engine: 'deepseek', model: 'small' })
    expect(runRequestBody(judged({ stage: 'ocr', configs: [{ engine: 'tesseract' }] })).judge).toBeUndefined()
  })

  it('changes the estimate key, so an estimate made without it no longer unlocks Start', () => {
    expect(estimateKey(judged())).not.toBe(estimateKey(sel()))
    expect(estimateKey(judged({ label: 'x' }))).toBe(estimateKey(judged()))
  })

  it('flags a judge that may be a tested model, and the tick is what lets it through', () => {
    const same = judged({ configs: [{ engine: 'deepseek', model: 'small' }], judge: { engine: 'deepseek', model: 'small' } })
    expect(judgeOverlap(same)).toHaveLength(1)
    // Either side on the engine's default may be the same model, so it is flagged too.
    expect(judgeOverlap(judged({ configs: [{ engine: 'deepseek', model: 'small' }] }))).toHaveLength(1)
    expect(judgeOverlap(judged())).toHaveLength(0)
    expect(judgeOverlap(judged({ configs: [{ engine: 'deepseek', model: 'big' }], judge: { engine: 'deepseek', model: 'small' } }))).toHaveLength(0)
    expect(runRequestBody(same).judge?.allow_same_model).toBeUndefined()
    expect(runRequestBody({ ...same, allowSameJudge: true }).judge?.allow_same_model).toBe(true)
    // A tick with nothing to accept sends nothing.
    expect(runRequestBody({ ...judged(), allowSameJudge: true }).judge?.allow_same_model).toBeUndefined()
  })

  it('holds Start back until the same-model warning is accepted', () => {
    const same = judged({ configs: [{ engine: 'deepseek' }] })
    const ready = (s: RunSelection) =>
      startState({ sel: s, maxConfigs: 4, estimate: estimate(), estimateFor: estimateKey(s), pcRemote: false, running: false, missingKeys: [] })
    expect(ready(same).ok).toBe(false)
    expect(ready(same).reasons[0]).toMatch(/judge with a model you are also testing/)
    expect(ready({ ...same, allowSameJudge: true }).ok).toBe(true)
  })

  it('asks for the judge engine key too', () => {
    const missing = enginesMissingKey(judged({ judge: { engine: 'claude', model: '' } }), options.translation_engines)
    expect(missing.map((e) => e.name)).toEqual(['claude'])
  })

  it('shows scores and cost in plain words', () => {
    expect(formatJudgeScore(null)).toBe('')
    expect(formatJudgeScore({ accuracy: 0.9, tone: 0.8, naturalness: 0.7, overall: 0.8 })).toBe('80.0% (accuracy 90 · tone 80 · natural 70)')
    const line = judgeRunLine({
      engine: 'deepseek', model: null, status: 'done', same_as_tested: false, cost_usd: 0.0123, scored: 5, average: { overall: 0.8 }, note: null,
    })
    expect(line).toBe('Judge 80.0% · 5 scored · judge cost $0.01')
    expect(judgeRunLine({ engine: null, model: null, status: 'stopped_cap', same_as_tested: false, cost_usd: 0, scored: 0, average: {}, note: null })).toBe(
      'Judge — · 0 scored · judge cost $0.00 · stopped at cap',
    )
  })
})

describe('whisper models in the Lab picker', () => {
  it('groups models that are not recommended for every language, with their reasons', () => {
    const groups = whisperChoiceGroups(options)
    expect(groups.everywhere).toEqual(['small', 'large-v3'])
    expect(groups.limited).toEqual([
      { size: 'medium', reasons: ['No medium for Japanese.'] },
      { size: 'ja-1.5b', reasons: ['Japanese only.'] },
    ])
  })

  it('never picks a limited model by default', () => {
    const first = defaultConfig('transcription', options)
    expect(first?.model).toBe('small')
    const rest = defaultConfig('transcription', options, [{ engine: 'whisper', model: 'small' }, { engine: 'whisper', model: 'large-v3' }])
    expect(rest).toBeNull()
  })
})

describe('scriptCountsLine', () => {
  const counts = {
    traditional_lines: 3, simplified_lines: 0, mixed_lines: 1, neutral_lines: 2,
    traditional_chars: 41, simplified_chars: 2, neutral_chars: 90,
  }

  it('shows Traditional vs Simplified lines and characters', () => {
    expect(scriptCountsLine(counts)).toBe(
      'Lines: 3 Traditional / 0 Simplified / 1 mixed / 2 neutral · Characters: 41 Traditional, 2 Simplified',
    )
  })

  it('is empty when the run is not a Chinese transcript', () => {
    expect(scriptCountsLine(null)).toBe('')
    expect(scriptCountsLine(undefined)).toBe('')
  })
})

describe('leftOutLine', () => {
  const none = { total: 0, removed: 0, excluded: 0, edited: 0, not_in_every_run: 0 }

  it('says what was compared, and nothing more when no case was left out', () => {
    expect(leftOutLine(3, none)).toBe('Compared on the 3 cases every run scored against the same reference.')
    expect(leftOutLine(1, undefined)).toBe('Compared on the 1 case every run scored against the same reference.')
  })

  it('counts every reason a case was left out', () => {
    expect(leftOutLine(2, { total: 5, removed: 1, excluded: 1, edited: 2, not_in_every_run: 1 })).toBe(
      'Compared on the 2 cases every run scored against the same reference. 5 left out: 2 edited between the runs, 1 excluded, 1 deleted, 1 not scored in every run.')
  })
})

describe('transcription arms at real settings', () => {
  const defaults = options.transcribe_defaults

  it('sends no options for a default arm and only the changed ones otherwise', () => {
    const plain = sel({ stage: 'transcription', configs: [{ engine: 'whisper', model: 'large-v3' }] })
    expect(runRequestBody(plain).configs).toEqual([{ engine: 'whisper', model: 'large-v3' }])
    const changed = withOption(withOption({ engine: 'whisper', model: 'large-v3' }, defaults, 'beam_size', 1), defaults, 'difficult_audio', true)
    expect(runRequestBody(sel({ stage: 'transcription', configs: [changed] })).configs).toEqual([
      { engine: 'whisper', model: 'large-v3', options: { beam_size: 1, difficult_audio: true } },
    ])
  })

  it('drops options on a stage that has none', () => {
    const stray = { engine: 'tesseract', options: { beam_size: 1 } }
    expect(runRequestBody(sel({ stage: 'ocr', configs: [stray] })).configs).toEqual([{ engine: 'tesseract' }])
  })

  it('setting an option back to the default removes it', () => {
    const once = withOption({ engine: 'whisper', model: 'small' }, defaults, 'min_silence_ms', 600)
    expect(optionValue(once, defaults, 'min_silence_ms')).toBe(600)
    const back = withOption(once, defaults, 'min_silence_ms', 300)
    expect(back).toEqual({ engine: 'whisper', model: 'small' })
    expect(optionValue(back, defaults, 'min_silence_ms')).toBe(300)
  })

  it('treats one model at two different settings as two arms, and the same settings twice as a duplicate', () => {
    const a = withOption({ engine: 'whisper', model: 'small' }, defaults, 'beam_size', 1)
    const b = { engine: 'whisper', model: 'small' }
    expect(selectionProblems(sel({ stage: 'transcription', configs: [a, b] }), 4)).toEqual([])
    expect(selectionProblems(sel({ stage: 'transcription', configs: [b, { ...b }] }), 4)).toEqual(['The same engine and model is picked twice.'])
  })

  it('changing the options changes what an estimate is for', () => {
    const base = sel({ stage: 'transcription', configs: [{ engine: 'whisper', model: 'small' }] })
    const tuned = sel({ stage: 'transcription', configs: [withOption({ engine: 'whisper', model: 'small' }, defaults, 'beam_size', 1)] })
    expect(estimateKey(base, 3)).not.toBe(estimateKey(tuned, 3))
  })

  it('restores the other recognisers (picked by hand, without a model) and their options', () => {
    expect(restoreConfigs('transcription', [{ engine: 'anime_whisper', options: { beam_size: 2, junk: 1 } }, { engine: 'gone' }], options)).toEqual([
      { engine: 'anime_whisper', options: { beam_size: 2 } },
    ])
    expect(configLabel('transcription', 'qwen3_asr_vad')).toBe('Qwen3-ASR')
  })

  it('names the options a run changed beside its model, and nothing for a default run', () => {
    const run = (changes?: Record<string, unknown>) => ({ stage: 'transcription', engine: 'whisper', model: 'large-v3', context_settings: changes ? { transcribe_changes: changes } : {} })
    expect(runConfigLabel(run())).toBe('Whisper large-v3')
    expect(runConfigLabel(run({ beam_size: 1, min_silence_ms: 600 }))).toBe('Whisper large-v3 (beam 1, min silence 600 ms)')
    expect(runExtraLabel({ ...run({ beam_size: 1 }), label: 'Whisper large-v3 (beam 1)' })).toBeNull()
  })
})

describe('timingLine', () => {
  it('is empty without timed cases', () => {
    expect(timingLine(undefined)).toBe('')
    expect(timingLine({ cases: 0 })).toBe('')
  })

  it('names the medians, the share within 0.3 s and the counts', () => {
    expect(timingLine({ cases: 4, start_median_ms: 120, end_median_ms: 180, within: 0.75, merged: 1, split: 0, missed: 2, silence: 3 })).toBe(
      'Timing on 4 lines: start 120 ms, end 180 ms off (median) · 75% within 0.3 s · 1 merged · 0 split · 2 missed · 3 in silence',
    )
  })

  it('leaves out the silence count when the detector was not available', () => {
    expect(timingLine({ cases: 1, start_median_ms: 10, end_median_ms: 20, within: 1, merged: 0, split: 0, missed: 0, silence: null })).not.toContain('silence')
  })
})

describe('caseTimingLine', () => {
  it('says a missed line was missed', () => {
    expect(caseTimingLine({ lines: 0, missed: true, split: false, merged: false, start_error_s: null, end_error_s: null, silence: null })).toBe('Timing: line missed')
  })

  it('shows the edge errors in ms with merged and split flags', () => {
    expect(caseTimingLine({ lines: 2, missed: false, split: true, merged: false, start_error_s: 0.12, end_error_s: 0.4, silence: 0 })).toBe(
      'Timing: start 120 ms, end 400 ms off · split',
    )
  })
})

describe('timing options', () => {
  it('are kept when changed and dropped when set back to the default', () => {
    const base = { engine: 'whisper', model: 'large-v3' }
    const changed = withOption(base, options.transcribe_defaults, 'speech_pad_ms', 150)
    expect(changed.options).toEqual({ speech_pad_ms: 150 })
    expect(withOption(changed, options.transcribe_defaults, 'speech_pad_ms', 400).options).toBeUndefined()
  })
})
