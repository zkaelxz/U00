import { describe, expect, it } from 'vitest'

import { jobFailed, jobOutcomeText, jobSucceeded, offersCancel } from './jobs'

describe('job outcome helpers', () => {
  it('treats a done job with a failed or cancelled outcome as not a success', () => {
    expect(jobFailed({ status: 'done', outcome: 'failed' })).toBe(true)
    expect(jobFailed({ status: 'done', outcome: 'cancelled' })).toBe(true)
    expect(jobFailed({ status: 'error', outcome: null })).toBe(true)
    expect(jobFailed({ status: 'done', outcome: 'ok' })).toBe(false)
    expect(jobFailed({ status: 'done', outcome: 'partial' })).toBe(false)
  })

  it('only treats a done, non-failed job as ready (Dub track, export download)', () => {
    expect(jobSucceeded({ status: 'done', outcome: 'ok' })).toBe(true)
    expect(jobSucceeded({ status: 'done', outcome: 'partial' })).toBe(true)
    expect(jobSucceeded({ status: 'done' })).toBe(true)
    expect(jobSucceeded({ status: 'done', outcome: 'failed' })).toBe(false)
    expect(jobSucceeded({ status: 'done', outcome: 'cancelled' })).toBe(false)
    expect(jobSucceeded({ status: 'running' })).toBe(false)
    expect(jobSucceeded(null)).toBe(false)
  })

  it('puts the outcome in words', () => {
    expect(jobOutcomeText({ outcome: 'failed', outcome_message: 'The speech model could not be downloaded.' })).toBe(
      'Failed: The speech model could not be downloaded.',
    )
    const notice = 'Transcription ran on the CPU because the GPU couldn\'t be used (RuntimeError: cuDNN failed). This was slower than on the GPU.'
    expect(jobOutcomeText({ outcome: 'partial', outcome_message: notice })).toBe(`Finished with problems: ${notice}`)
    const ollama = 'Ollama still has a model loaded (gemma4:26b), which may make transcription run out of GPU memory and fall back to the CPU. Free it with: ollama stop gemma4:26b.'
    expect(jobOutcomeText({ outcome: 'partial', outcome_message: ollama })).toBe(`Finished with problems: ${ollama}`)
    expect(jobOutcomeText({ outcome: 'kept_existing', outcome_message: null })).toBe('Nothing new; existing lines kept')
    expect(jobOutcomeText({ outcome: null })).toBeNull()
    expect(jobOutcomeText({})).toBeNull()
  })

  it('offers a remote admin Cancel only on their own jobs', () => {
    expect(offersCancel({ owned_by_me: true }, true)).toBe(true)
    expect(offersCancel({ owned_by_me: false }, true)).toBe(false)
    expect(offersCancel({}, true)).toBe(false)
    expect(offersCancel({ owned_by_me: false }, false)).toBe(true)
    expect(offersCancel({}, false)).toBe(true)
  })
})
