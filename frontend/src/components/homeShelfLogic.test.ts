import { describe, expect, it } from 'vitest'

import type { JobRecord } from '../types/jobs'
import type { LibraryRunningEntry } from '../types/library'
import { formatLength, runningLine, timeLeft } from './homeShelfLogic'

describe('formatLength', () => {
  it('reads hours with padded minutes and never says 0 min', () => {
    expect(formatLength(7500)).toBe('2 h 05 m')
    expect(formatLength(2700)).toBe('45 min')
    expect(formatLength(5)).toBe('1 min')
  })
})

describe('runningLine', () => {
  const entry = (over: Partial<LibraryRunningEntry>): LibraryRunningEntry => ({
    drama_id: 1, title: 'Chime: game night', media_type: 'streamer_vod', content_mode: 'streamer_vod', has_cover_art: false,
    updated_at: null, step: 'transcribe', status: 'running', job_id: 'transcribe_1', started_at: null, ...over,
  })
  it('shows the running step with its progress, time left and how many wait', () => {
    const jobs = [{ job_id: 'transcribe_1', progress: 0.5, started_at: 1000 } as JobRecord]
    const line = runningLine([entry({ status: 'waiting', drama_id: 2, title: 'Other' }), entry({})], 2, jobs, 1600)
    expect(line).toEqual({ text: 'Transcribing Chime: game night', progress: 0.5, left: '10 min left', waiting: '1 more is waiting its turn' })
  })
  it('is null when nothing runs or waits', () => {
    expect(runningLine([], 0, [], 0)).toBeNull()
  })
  it('gives no time left too early to tell', () => {
    expect(timeLeft(0.01, 0, 100)).toBeNull()
  })
})
