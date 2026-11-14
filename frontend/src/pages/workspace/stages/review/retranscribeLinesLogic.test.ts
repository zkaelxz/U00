import { describe, expect, it, vi } from 'vitest'

import {
  applyItems, applyNote, failureSummary, fillBlankLines, fillNote, gapBlockedReason, gapLabel, gapsInView,
  retranscribeLinesOutcome, retranscribeLinesProblem, type FillDeps,
} from './retranscribeLinesLogic'
import type { RetranscribeManyApplyItem } from '../../../../types/workspace'

const proposal = (id: number, over = {}) => ({
  line_id: id, number: id, base_zh: `旧${id}`, proposed_zh: `新${id}`, had_english: true, ...over,
})

describe('retranscribeLinesProblem', () => {
  it('needs at least one line and at most the cap', () => {
    expect(retranscribeLinesProblem(0)).toMatch(/Tick at least one/)
    expect(retranscribeLinesProblem(1)).toBeNull()
    expect(retranscribeLinesProblem(200)).toBeNull()
    expect(retranscribeLinesProblem(201)).toMatch(/up to 200/)
  })
})

describe('retranscribeLinesOutcome', () => {
  it('is ready for a done job without a failure reason', () => {
    expect(retranscribeLinesOutcome({ status: 'done', outcome: 'ok', result: { line_count: 3 } })).toEqual({ kind: 'ready' })
  })
  it('explains cancel, download, timeout and anything else', () => {
    const text = (job: object) => (retranscribeLinesOutcome(job as never) as { text: string }).text
    expect(text({ status: 'cancelled', outcome: 'cancelled', result: null })).toMatch(/Cancelled/)
    expect(text({ status: 'done', result: { failed_reason: 'model_download' } })).toMatch(/downloaded/)
    expect(text({ status: 'done', result: { failed_reason: 'timeout' } })).toMatch(/too long/)
    expect(text({ status: 'error', outcome: 'failed', result: null })).toMatch(/failed/)
  })
})

describe('failureSummary', () => {
  it('names each line and why', () => {
    expect(failureSummary([])).toBeNull()
    expect(failureSummary([
      { line_id: 1, number: 3, reason: 'empty' },
      { line_id: 2, number: 5, reason: 'audio_slice' },
      { line_id: 3, number: 9, reason: 'odd' },
    ])).toBe('#3 (no speech heard), #5 (its audio could not be cut), #9 (could not be re-transcribed)')
  })
})

describe('applyItems', () => {
  it('sends exactly the proposals shown for the ticked rows, in list order', () => {
    const rows = [proposal(1), proposal(2), proposal(3)]
    expect(applyItems(rows, new Set([3, 1]))).toEqual([
      { line_id: 1, expected_zh: '旧1', expected_proposed: '新1' },
      { line_id: 3, expected_zh: '旧3', expected_proposed: '新3' },
    ])
    expect(applyItems(rows, new Set())).toEqual([])
  })
})

describe('applyNote', () => {
  it('reports what was replaced, marked source changed and left alone', () => {
    expect(applyNote({ applied: [1, 2], skipped: [3], untranslated_count: 0 }, 2)).toBe(
      'Replaced the source text of 2 lines. Kept the English on 2 and marked them “Source changed”: retranslate or keep them from Review checks. 1 changed since the run, so left alone. Undo from History.',
    )
    expect(applyNote({ applied: [1], skipped: [], untranslated_count: 0 }, 1)).toContain(
      'Kept the English on 1 and marked it “Source changed”: retranslate or keep it from Review checks.',
    )
    expect(applyNote({ applied: [], skipped: [1], untranslated_count: 0 }, 0)).toBe(
      'Replaced the source text of 0 lines. 1 changed since the run, so left alone.',
    )
    expect(applyNote({ applied: [1], skipped: [], untranslated_count: 1 }, 0)).toBe(
      'Replaced the source text of 1 line. Undo from History.',
    )
  })
})

describe('gaps', () => {
  const gap = (start: number, end: number, over = {}) => ({
    start, end, seconds: end - start, pieces: 1, part: 1, parts: 1, after_line_id: 1, before_line_id: 2, speech: null, ...over,
  })
  it('keeps only gaps that touch the view', () => {
    const gaps = [gap(0, 5), gap(10, 15), gap(30, 40)]
    expect(gapsInView(gaps, { start: 12, span: 10 })).toEqual([gaps[1]])
    expect(gapsInView(gaps, { start: 15, span: 15 })).toEqual([])
    expect(gapsInView(gaps, { start: 0, span: 100 })).toHaveLength(3)
  })
  it('labels a gap with its length, pieces and speech', () => {
    expect(gapLabel(gap(6, 20))).toBe('0:06.00 – 0:20.00 (14 s)')
    expect(gapLabel(gap(3, 100, { pieces: 4, speech: true, seconds: 97 }))).toBe('0:03.00 – 1:40.00 (97 s, 4 lines, speech heard)')
  })
  it('says why the button is off', () => {
    const ok = { jobRunning: false, editing: false, canTranscribe: true }
    expect(gapBlockedReason(ok)).toBeNull()
    expect(gapBlockedReason({ ...ok, canTranscribe: false })).toMatch(/audio/)
    expect(gapBlockedReason({ ...ok, editing: true })).toMatch(/open edit/)
    expect(gapBlockedReason({ ...ok, jobRunning: true })).toMatch(/Another job/)
  })
})

describe('fillBlankLines', () => {
  const heard = (line_id: number, base_zh: string, proposed_zh: string) =>
    ({ line_id, number: line_id, base_zh, proposed_zh, had_english: false })
  const deps = (over: Partial<FillDeps> = {}) => {
    const calls: string[] = []
    const d: FillDeps = {
      beforeWrite: vi.fn(async (ids: number[]) => { calls.push(`before ${ids.join(',')}`); return null }),
      apply: vi.fn(async (items: RetranscribeManyApplyItem[]) => {
        calls.push(`apply ${items.map((i) => i.line_id).join(',')}`)
        return { applied: items.map((i) => i.line_id), skipped: [], untranslated_count: 0 }
      }),
      afterWrite: vi.fn((ids: number[]) => { calls.push(`after ${ids.join(',')}`) }),
      ...over,
    }
    return { d, calls }
  }

  it('saves an open edit of the blank lines first, then writes exactly what was heard', async () => {
    const { d, calls } = deps()
    const o = await fillBlankLines([heard(7, '', '听到的'), heard(8, ' ', '第二')], d)
    expect(calls).toEqual(['before 7,8', 'apply 7,8', 'after 7,8'])
    expect(d.apply).toHaveBeenCalledWith([
      { line_id: 7, expected_zh: '', expected_proposed: '听到的' },
      { line_id: 8, expected_zh: ' ', expected_proposed: '第二' },
    ])
    expect(o).toEqual({ kind: 'applied', result: { applied: [7, 8], skipped: [], untranslated_count: 0 } })
  })

  it('never fills a line that had text when the run read it', async () => {
    const { d } = deps()
    const o = await fillBlankLines([heard(7, '原来的', '听到的')], d)
    expect(o).toEqual({ kind: 'nothing' })
    expect(d.beforeWrite).not.toHaveBeenCalled()
    expect(d.apply).not.toHaveBeenCalled()
  })

  it('writes nothing when the open edit could not be saved', async () => {
    const { d } = deps({ beforeWrite: vi.fn(async () => 'Save or discard your edit to #7 first.') })
    const o = await fillBlankLines([heard(7, '', '听到的')], d)
    expect(o).toEqual({ kind: 'blocked', text: 'Save or discard your edit to #7 first.' })
    expect(d.apply).not.toHaveBeenCalled()
  })
})

describe('fillNote', () => {
  it('names the filled lines and the ones left as edited', () => {
    const numbers = new Map([[7, 12], [8, 13]])
    expect(fillNote({ applied: [7], skipped: [8], untranslated_count: 0 }, numbers))
      .toBe('Filled #12 with what it heard. Left #13 as you edited it.')
  })
})
