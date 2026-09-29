import { describe, expect, it } from 'vitest'

import { jobFailed, jobOutcomeText } from './jobs'

describe('job outcome helpers', () => {
  it('treats a done job with a failed or cancelled outcome as not a success', () => {
    expect(jobFailed({ status: 'done', outcome: 'failed' })).toBe(true)
    expect(jobFailed({ status: 'done', outcome: 'cancelled' })).toBe(true)
    expect(jobFailed({ status: 'error', outcome: null })).toBe(true)
    expect(jobFailed({ status: 'done', outcome: 'ok' })).toBe(false)
    expect(jobFailed({ status: 'done', outcome: 'partial' })).toBe(false)
  })

  it('puts the outcome in words', () => {
    expect(jobOutcomeText({ outcome: 'failed', outcome_message: 'The speech model could not be downloaded.' })).toBe(
      'Failed: The speech model could not be downloaded.',
    )
    expect(jobOutcomeText({ outcome: 'kept_existing', outcome_message: null })).toBe('Nothing new; existing lines kept')
    expect(jobOutcomeText({ outcome: null })).toBeNull()
    expect(jobOutcomeText({})).toBeNull()
  })
})
