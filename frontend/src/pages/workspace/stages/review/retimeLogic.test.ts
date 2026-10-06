import { describe, expect, it } from 'vitest'

import { retimeOutcome, retimeSelectedProblem, startShift } from './retimeLogic'

describe('retimeSelectedProblem', () => {
  it('needs a tick and respects the cap', () => {
    expect(retimeSelectedProblem(0)).toBe('Tick at least one line.')
    expect(retimeSelectedProblem(200)).toBeNull()
    expect(retimeSelectedProblem(201)).toMatch(/201 lines ticked.*200/)
  })
})

describe('retimeOutcome', () => {
  it('is ready only for a clean done job', () => {
    expect(retimeOutcome({ status: 'done', outcome: 'ok', result: { candidate_count: 2 } })).toEqual({ kind: 'ready' })
  })
  it('explains each failure in plain words', () => {
    expect(retimeOutcome({ status: 'done', result: { failed_reason: 'cancelled' } })).toMatchObject({ text: expect.stringContaining('Cancelled') })
    expect(retimeOutcome({ status: 'done', result: { failed_reason: 'dependency_missing' } })).toMatchObject({ text: expect.stringContaining('qwen-asr') })
    expect(retimeOutcome({ status: 'done', result: { failed_reason: 'model_download' } })).toMatchObject({ text: expect.stringContaining('downloaded') })
    expect(retimeOutcome({ status: 'failed' })).toMatchObject({ text: expect.stringContaining('failed') })
  })
})

describe('startShift', () => {
  it('signs the move', () => {
    expect(startShift({ start: 6, new_start: 5.5 })).toBe('−0.50 s')
    expect(startShift({ start: 6, new_start: 6.25 })).toBe('+0.25 s')
    expect(startShift({ start: 6, new_start: 6 })).toBe('0.00 s')
  })
})
