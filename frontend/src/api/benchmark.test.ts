import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  arenaExportUrl, checkBenchmarkClips, deleteBenchmarkRun, editCaseReference, getCaseEdits, importBenchmarkClips, runExportUrl, setCaseExcluded,
  addRegressionCase, buildBenchmarkSet, casesQuery, createBenchmarkCase, deleteBenchmarkCase, estimateBenchmark, getBenchmarkArena, getBenchmarkCases,
  getBenchmarkOptions, getBenchmarkRun, getBenchmarkSets, importGoldenSet, listBenchmarkRuns, startBenchmarkRun,
} from './benchmark'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')
const bodyOf = (init: RequestInit) => JSON.parse(String(init.body))

afterEach(() => resetPcModeForTests())

describe('benchmark api: build a set and judge', () => {
  it('builds a set from a title as a PC-only post, and carries the judge on estimate and start', async () => {
    const { mock, f } = reply(200, {})
    const body = { drama_id: 3, set_name: 'moon', include: 'reviewed' as const, lines_per_case: 4, dry_run: true }
    await buildBenchmarkSet(body, f)
    await estimateBenchmark({ stage: 'translation', configs: [{ engine: 'claude' }], judge: { engine: 'deepseek' } }, f)
    await startBenchmarkRun({ stage: 'translation', configs: [{ engine: 'claude' }], judge: { engine: 'deepseek', allow_same_model: true } }, f)
    const calls = mock.mock.calls as [string, RequestInit][]
    expect(calls.map((c) => c[0])).toEqual(['/api/benchmark/sets/from-title', '/api/benchmark/estimate', '/api/benchmark/runs'])
    expect(localHeader(calls[0][1])).toBe('1')
    expect(bodyOf(calls[0][1])).toEqual(body)
    expect(bodyOf(calls[1][1]).judge).toEqual({ engine: 'deepseek' })
    expect(bodyOf(calls[2][1])).toMatchObject({ confirm: true, judge: { engine: 'deepseek', allow_same_model: true } })
  })
})

describe('benchmark api', () => {
  it('reads options, sets, runs, one run and the arena from their paths', async () => {
    const { mock, f } = reply(200, {})
    await getBenchmarkOptions(f)
    await getBenchmarkSets(f)
    await listBenchmarkRuns({}, f)
    await listBenchmarkRuns({ stage: 'ocr', limit: 999 }, f)
    await getBenchmarkRun(7, f)
    await getBenchmarkArena([3, 1, 2], f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/api/benchmark/options',
      '/api/benchmark/sets',
      '/api/benchmark/runs',
      '/api/benchmark/runs?stage=ocr&limit=200',
      '/api/benchmark/runs/7',
      '/api/benchmark/arena?run_ids=3&run_ids=1&run_ids=2',
    ])
  })

  it('cases query sends only the filters that are set (an empty set name means any set)', async () => {
    expect(casesQuery()).toBe('')
    expect(casesQuery({ stage: 'translation', tier: null, set_name: '' })).toBe('?stage=translation')
    expect(casesQuery({ stage: 'translation', tier: 'public', set_name: 'flores zh' })).toBe(
      '?stage=translation&tier=public&set_name=flores+zh',
    )
    const { mock, f } = reply(200, { cases: [] })
    await getBenchmarkCases({ tier: 'regression' }, f)
    expect(mock.mock.calls[0][0]).toBe('/api/benchmark/cases?tier=regression')
  })

  it('estimate is a plain POST with the selection and no confirm (it spends nothing)', async () => {
    const { mock, f } = reply(200, { configs: [] })
    await estimateBenchmark({ stage: 'translation', configs: [{ engine: 'ollama' }] }, f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/benchmark/estimate')
    expect(init.method).toBe('POST')
    expect(bodyOf(init)).toEqual({ stage: 'translation', configs: [{ engine: 'ollama' }] })
  })

  it('start, import, add and delete are PC-only posts; start and delete send confirm', async () => {
    const { mock, f } = reply(200, {})
    await startBenchmarkRun({ stage: 'translation', configs: [{ engine: 'claude', model: 'haiku' }], set_name: 's' }, f)
    await importGoldenSet({ set_name: 's', text: 'a\tb', format: 'tsv', tier: 'public', source_language: 'zh' }, f)
    await createBenchmarkCase({ label: 'L', source_text: '你好', source_language: 'zh', tier: 'application' }, f)
    await deleteBenchmarkCase(4, f)
    const calls = mock.mock.calls as [string, RequestInit][]
    expect(calls.map((c) => c[0])).toEqual([
      '/api/benchmark/runs',
      '/api/benchmark/import',
      '/api/benchmark/cases',
      '/api/benchmark/cases/4/delete',
    ])
    for (const [, init] of calls) {
      expect(init.method).toBe('POST')
      expect(localHeader(init)).toBe('1')
    }
    expect(bodyOf(calls[0][1])).toEqual({
      stage: 'translation', configs: [{ engine: 'claude', model: 'haiku' }], set_name: 's', confirm: true,
    })
    expect(bodyOf(calls[1][1])).toEqual({ set_name: 's', text: 'a\tb', format: 'tsv', tier: 'public', source_language: 'zh' })
    expect(bodyOf(calls[3][1])).toEqual({ confirm: true })
  })

  it('a 403 on a PC-only call marks the tab remote and surfaces the error', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(deleteBenchmarkCase(1, f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })

  it('adds a regression case from a line through the PC-only path', async () => {
    const { mock, f } = reply(200, { case: {}, replaced: false })
    await addRegressionCase(3, 42, f)
    const [url, init] = mock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/benchmark/dramas/3/lines/42/regression')
    expect(init.method).toBe('POST')
    expect(localHeader(init)).toBe('1')
  })
})

describe('benchmark api: clips, case clean-up, run delete and export', () => {
  it('uploads the audio as multipart with the rows beside it, PC only', async () => {
    const { mock, f } = reply(200, { warnings: [] })
    const file = new File(['x'], 'scene one.wav')
    const clips = [{ filename: 'scene one.wav', language: 'ja' as const, transcript: 'はい' }]
    await importBenchmarkClips({ set_name: 'mine', clips, files: [file] }, f)
    const [url, init] = (mock.mock.calls as [string, RequestInit][])[0]
    expect(url).toBe('/api/benchmark/clips/import')
    expect(localHeader(init)).toBe('1')
    const form = init.body as FormData
    expect(form.get('set_name')).toBe('mine')
    expect(JSON.parse(String(form.get('clips')))).toEqual(clips)
    expect((form.getAll('files')[0] as File).name).toBe('scene one.wav')
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
  })

  it('checks rows without the PC-only header and posts case changes with it', async () => {
    const { mock, f } = reply(200, {})
    await checkBenchmarkClips([], f)
    await setCaseExcluded(4, true, f)
    await editCaseReference(4, 'fixed', f)
    await getCaseEdits(4, f)
    await deleteBenchmarkRun(9, f)
    const calls = mock.mock.calls as [string, RequestInit][]
    expect(calls.map((c) => c[0])).toEqual([
      '/api/benchmark/clips/check', '/api/benchmark/cases/4/excluded', '/api/benchmark/cases/4/reference',
      '/api/benchmark/cases/4/edits', '/api/benchmark/runs/9/delete'])
    expect(bodyOf(calls[1][1])).toEqual({ excluded: true })
    expect(bodyOf(calls[2][1])).toEqual({ reference_text: 'fixed' })
    expect(bodyOf(calls[4][1])).toEqual({ confirm: true })
    expect([1, 2, 4].map((i) => localHeader(calls[i][1]))).toEqual(['1', '1', '1'])
  })

  it('links to the export of a run and of a comparison', () => {
    expect(runExportUrl(3, 'csv')).toBe('/api/benchmark/runs/3/export?format=csv')
    expect(arenaExportUrl([3, 5], 'json')).toBe('/api/benchmark/arena/export?run_ids=3&run_ids=5&format=json')
  })
})
