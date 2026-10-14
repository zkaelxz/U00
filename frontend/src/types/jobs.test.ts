import { describe, expect, it } from 'vitest'

import { cancelHint, cancellingText, jobFailed, jobName, jobOutcomeDetail, jobOutcomeText, jobSucceeded, offersCancel, queueLine, queuePlace, shownStatus } from './jobs'

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

  it('offers a member Cancel only on their own jobs, and an admin on every job', () => {
    expect(offersCancel({ owned_by_me: true }, false)).toBe(true)
    expect(offersCancel({ owned_by_me: false }, false)).toBe(false)
    expect(offersCancel({}, false)).toBe(false)
    expect(offersCancel({ owned_by_me: false }, true)).toBe(true)
    expect(offersCancel({}, true)).toBe(true)
  })

  it('words the queue in counts and the caller\'s own place', () => {
    expect(queueLine({ running: 1, waiting: 2 })).toBe('1 job running (all lanes), 2 waiting in the GPU queue')
    expect(queueLine({ running: 0, waiting: 3 })).toBe('0 jobs running (all lanes), 3 waiting in the GPU queue')
    expect(queuePlace(2)).toBe('Waiting: #2 in the GPU queue')
  })
})

describe('what a person reads for a finished job', () => {
  it('shows a done job with a failed or cancelled outcome as Failed or Cancelled', () => {
    expect(shownStatus({ status: 'done', outcome: 'failed' })).toBe('error')
    expect(shownStatus({ status: 'done', outcome: 'cancelled' })).toBe('cancelled')
    expect(shownStatus({ status: 'done', outcome: 'ok' })).toBe('done')
    expect(shownStatus({ status: 'done', outcome: 'partial' })).toBe('done')
    expect(shownStatus({ status: 'running', outcome: null })).toBe('running')
    expect(shownStatus({ status: 'error', outcome: 'failed' })).toBe('error')
  })

  it('says a job closed by a restart was interrupted, once', () => {
    const restarted = { status: 'cancelled', outcome: 'interrupted' as const,
      outcome_message: 'Interrupted: Baihe restarted while this was running.' }
    expect(jobFailed(restarted)).toBe(true)
    expect(jobOutcomeText(restarted)).toBe('Interrupted: Baihe restarted while this was running.')
    expect(jobOutcomeDetail(restarted)).toBe('Interrupted: Baihe restarted while this was running.')
    expect(jobOutcomeText({ outcome: 'interrupted', outcome_message: null })).toBe('Interrupted: Baihe restarted')
  })

  it('says Finished once when the message only repeats it', () => {
    expect(jobOutcomeText({ outcome: 'ok', outcome_message: 'Finished.' })).toBe('Finished.')
    expect(jobOutcomeText({ outcome: 'ok', outcome_message: 'Finished' })).toBe('Finished')
    expect(jobOutcomeText({ outcome: 'ok', outcome_message: 'Translated 12 lines.' })).toBe('Finished: Translated 12 lines.')
  })

  it('shows only the reason under a Failed pill', () => {
    const reason = 'The speech model could not be downloaded.'
    expect(jobOutcomeDetail({ status: 'done', outcome: 'failed', outcome_message: reason })).toBe(reason)
    expect(jobOutcomeDetail({ status: 'error', outcome: 'failed', outcome_message: 'Failed' })).toBeNull()
    expect(jobOutcomeDetail({ status: 'done', outcome: 'ok', outcome_message: 'Finished.' })).toBeNull()
    expect(jobOutcomeDetail({ status: 'done', outcome: 'partial', outcome_message: 'Two lines failed.' })).toBe('Finished with problems: Two lines failed.')
    expect(jobOutcomeDetail({ status: 'running', outcome: null })).toBeNull()
  })
})

describe('cancel hint table', () => {
  it('says how fast Cancel works for each kind of job', () => {
    expect(cancelHint('transcribe_3')).toBe('Cancel: stops at once.')
    expect(cancelHint('softsub_video_3')).toBe('Cancel: stops at once.')
    expect(cancelHint('translate_3')).toContain('stops after the current AI request (usually under a minute, up to 5')
    expect(cancelHint('translate_3')).toContain('still billed')
    expect(cancelHint('bulk_translate_3')).toBe('Cancel: stops after the current batch.')
    expect(cancelHint('library_backup')).toBe('Cancel: stops after the current step.')
    expect(cancelHint(null)).toBe('Cancel: stops after the current step.')
  })

  it('puts the same wait in the Cancelling text', () => {
    expect(cancellingText('dub_2')).toBe('Cancelling... Stops at once.')
    expect(cancellingText('translate_2')).toMatch(/^Cancelling\.\.\. Stops after the current AI request/)
  })
})

describe('jobName', () => {
  it('names a Whisper package install by its group, not the raw package', () => {
    for (const pkg of ['faster_whisper', 'ctranslate2']) {
      expect(jobName({ job_id: 'dependency_install', description: `Installing ${pkg}` })).toBe('Installing Whisper')
    }
    expect(jobName({ job_id: 'dependency_install', description: 'Updating faster_whisper' })).toBe('Updating Whisper')
    expect(jobName({ job_id: 'dependency_install', description: 'Installing soundfile' })).toBe('Installing audio support')
  })

  it('leaves other jobs and unlisted packages as the server named them', () => {
    expect(jobName({ job_id: 'dependency_install', description: 'Installing jieba' })).toBe('Installing jieba')
    expect(jobName({ job_id: 'transcribe_1', description: 'Installing faster_whisper' })).toBe('Installing faster_whisper')
    expect(jobName({ job_id: 'dependency_install', description: null })).toBe('dependency_install')
  })
})
