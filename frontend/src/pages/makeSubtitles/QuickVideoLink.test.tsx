import { describe, expect, it } from 'vitest'

import type { QuickVideoRun } from '../../types/quickVideo'
import { canDeleteWatched, runText, visibleRuns } from './QuickVideoLink'

const run = (o: Partial<QuickVideoRun>): QuickVideoRun => ({
  drama_id: 1, title: 'T', watch_once: false, state: 'running', step: 'download', reason: null, ...o,
})

describe('Quick video translate run text', () => {
  it('names the step under way', () => {
    expect(runText(run({ step: 'translate' }))).toBe('Translating')
  })
  it('gives the reason a stopped run carries', () => {
    expect(runText(run({ state: 'stopped', step: 'download', reason: 'Skipped: no yt-dlp' }))).toBe('Download stopped: Skipped: no yt-dlp')
  })
  it('says Done when every step finished', () => {
    expect(runText(run({ state: 'done', step: 'export' }))).toBe('Done')
  })
  it('drops exception class names from a stopped run reason', () => {
    expect(runText(run({ state: 'stopped', reason: "RuntimeError: Couldn't download that" }))).toBe("Download stopped: Couldn't download that")
  })
  it('offers the watched-title delete only for finished watch-once runs', () => {
    expect(canDeleteWatched(run({ watch_once: true, state: 'done' }))).toBe(true)
    expect(canDeleteWatched(run({ watch_once: true, state: 'stopped' }))).toBe(false)
    expect(canDeleteWatched(run({ watch_once: true, state: 'running' }))).toBe(false)
    expect(canDeleteWatched(run({ watch_once: false, state: 'done' }))).toBe(false)
  })
  it('caps the list but always keeps a running run', () => {
    const runs = Array.from({ length: 8 }, (_, i) => run({ drama_id: i, state: i === 6 ? 'running' : 'done' }))
    expect(visibleRuns(runs, false).map((r) => r.drama_id)).toEqual([0, 1, 2, 6])
    expect(visibleRuns(runs, true)).toHaveLength(8)
  })
})
