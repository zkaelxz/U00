import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobRecord } from '../../../types/jobs'
import { STALL_NOTE, STUCK_TICK_MS, progressKey, stripStallNote, stuckMinutesFor, stuckText, watchStuck } from './jobStuck'

const job = (over: Partial<JobRecord> = {}): JobRecord => ({
  job_id: 'translate_3', status: 'running', progress: 0.2, message: 'Batch 1 of 5', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...over,
})

const MIN = 60_000

describe('watchStuck', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('warns after 5 minutes without a change and resets when progress moves', () => {
    let current: JobRecord | null = job()
    const seen: (string | null)[] = []
    const w = watchStuck(() => current, (i) => seen.push(i && `${i.minutes}:${i.stuck}`))
    expect(seen).toEqual(['0:false'])
    vi.advanceTimersByTime(4 * MIN + 59_000)
    expect(seen.at(-1)).toBe('4:false')
    vi.advanceTimersByTime(STUCK_TICK_MS)
    expect(seen.at(-1)).toBe('5:true')
    // Progress moved: the count starts again from that moment.
    current = job({ progress: 0.3 })
    w.update()
    expect(seen.at(-1)).toBe('0:false')
    vi.advanceTimersByTime(3 * MIN + STUCK_TICK_MS)
    expect(seen.at(-1)).toBe('3:false')
    w.stop()
    vi.advanceTimersByTime(10 * MIN)
    expect(seen.at(-1)).toBe('3:false')
  })

  it('gives a transcription 10 minutes and ignores the elapsed ticker and the stall note', () => {
    let current: JobRecord | null = job({ job_id: 'transcribe_3', progress: null, message: 'Loading model (elapsed 5s). No progress is available.' })
    const seen: (string | null)[] = []
    watchStuck(() => current, (i) => seen.push(i && `${i.minutes}:${i.stuck}`))
    for (let s = 10; s <= 9 * 60 + 30; s += 5) {
      current = job({ job_id: 'transcribe_3', progress: null, message: `Loading model (elapsed ${s}s). No progress is available.` })
      vi.advanceTimersByTime(5000)
    }
    expect(seen.at(-1)).toBe('9:false')
    current = job({ job_id: 'transcribe_3', progress: null, message: `Loading model (elapsed 600s). No progress is available. ${STALL_NOTE}` })
    vi.advanceTimersByTime(MIN + STUCK_TICK_MS)
    expect(seen.at(-1)).toBe('10:true')
  })

  it('trusts the server flag at once and reports null once the job ends', () => {
    let current: JobRecord | null = job({ stalled: true })
    const seen: (string | null)[] = []
    watchStuck(() => current, (i) => seen.push(i && `${i.minutes}:${i.stuck}`))
    expect(seen).toEqual(['0:true'])
    current = job({ status: 'done' })
    vi.advanceTimersByTime(STUCK_TICK_MS)
    expect(seen.at(-1)).toBeNull()
    current = null
    vi.advanceTimersByTime(STUCK_TICK_MS)
    expect(seen).toHaveLength(2)
  })
})

describe('progress helpers', () => {
  it('keys on percent, message and updated_at', () => {
    expect(progressKey(job())).toBe(progressKey(job({ message: `Batch 1 of 5 ${STALL_NOTE}` })))
    expect(progressKey(job())).not.toBe(progressKey(job({ progress: 0.21 })))
    expect(progressKey(job())).not.toBe(progressKey(job({ updated_at: 2 })))
    expect(progressKey(job({ message: 'Load (elapsed 1m 05s). x' }))).toBe(progressKey(job({ message: 'Load (elapsed 2m 00s). x' })))
  })

  it('names the allowance by job kind and words the warning', () => {
    expect(stuckMinutesFor('transcribe_1')).toBe(10)
    expect(stuckMinutesFor('diarize_1')).toBe(10)
    expect(stuckMinutesFor('translate_1')).toBe(5)
    expect(stuckText({ minutes: 7, stuck: true }, 'translate_1')).toBe('No progress for 7 min. It may be stuck.')
    expect(stuckText({ minutes: 0, stuck: true }, 'translate_1')).toBe('No progress for a while. It may be stuck.')
    expect(stripStallNote(`Working ${STALL_NOTE}`)).toBe('Working')
  })
})
