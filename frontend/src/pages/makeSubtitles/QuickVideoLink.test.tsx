import { describe, expect, it } from 'vitest'

import type { QuickVideoRun } from '../../types/quickVideo'
import { runText } from './QuickVideoLink'

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
})
