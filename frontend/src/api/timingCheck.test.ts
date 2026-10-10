import { describe, expect, it } from 'vitest'

import { checkSummary, snapSummary, suggestionsByLine } from '../pages/workspace/stages/review/timingCheckLogic'
import { getTimingCheck, snapToSpeech, startTimingCheck } from './timingCheck'

function fakeFetch(body: unknown, calls: { url: string; init?: RequestInit }[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status: 200 })
  }) as typeof fetch
}

describe('timing check api', () => {
  it('reads status, starts the run and snaps', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await getTimingCheck(3, fakeFetch({}, calls))
    await startTimingCheck(3, fakeFetch({ job_id: 'timingchk_3' }, calls))
    await snapToSpeech(3, [4, 5], fakeFetch({}, calls))
    await snapToSpeech(3, undefined, fakeFetch({}, calls))
    expect(calls.map((c) => c.url)).toEqual([
      '/api/timing-check/dramas/3', '/api/timing-check/dramas/3/run',
      '/api/timing-check/dramas/3/snap', '/api/timing-check/dramas/3/snap',
    ])
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ line_ids: [4, 5] })
    expect(JSON.parse(String(calls[3].init?.body))).toEqual({})
  })
})

describe('timing check summaries', () => {
  const last = (flagged: number, notice: string | null = null) => ({ last_check: { checked_at: 'x', flagged, notice } })
  it('describes the last check', () => {
    expect(checkSummary({ last_check: null })).toBeNull()
    expect(checkSummary(last(0))).toBe('No line timing disagrees with the audio.')
    expect(checkSummary(last(1))).toBe('1 line flagged for timing.')
    expect(checkSummary(last(4))).toBe('4 lines flagged for timing.')
  })
  it('shows the title-level notice instead of a count', () => {
    expect(checkSummary(last(0, 'No speech was found.'))).toBe('No speech was found.')
  })
  it('reports lines left alone because they changed', () => {
    expect(snapSummary({ snapped: 2, stale_ids: [], history_id: 1 })).toBe('Snapped 2 lines to speech. Undo from Versions and history.')
    expect(snapSummary({ snapped: 0, stale_ids: [7], history_id: 1 })).toContain('1 line changed since the check')
  })
  it('indexes suggestions by line', () => {
    const s = { line_id: 2, start: 1, end: 2, new_start: 1.5, new_end: 2 }
    expect(suggestionsByLine([s]).get(2)).toBe(s)
  })
})
