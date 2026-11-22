import { describe, expect, it } from 'vitest'

import { flowActive, flowText, stoppedJob } from './flowRun'

const STEPS = { transcribe: 'Transcribing', names: 'Finding names for the glossary' }

describe('the one-button run as the PC holds it', () => {
  it('names the step that runs, and only a run in line or running keeps the button pressed', () => {
    expect(flowText({ state: 'running', step: 'transcribe', reason: null, job_id: null }, STEPS)).toBe('Transcribing')
    expect(flowText({ state: 'running', step: 'names', reason: null, job_id: null }, STEPS)).toBe('Finding names for the glossary')
    expect(flowText({ state: 'review', step: null, reason: null, job_id: null }, STEPS)).toBe('Ready for glossary review')
    expect(flowText({ state: 'idle', step: null, reason: null, job_id: null }, STEPS)).toBe('')
    expect(flowActive({ state: 'waiting', step: 'transcribe', reason: null, job_id: null })).toBe(true)
    expect(flowActive({ state: 'stopped', step: 'transcribe', reason: 'x', job_id: null })).toBe(false)
    expect(flowActive(null)).toBe(false)
  })

  it('says why a run waits or stopped, in the server\'s words', () => {
    expect(flowText({ state: 'waiting', step: 'transcribe', reason: null, job_id: null }, STEPS)).toBe('Waiting for the item running in the queue')
    expect(flowText({ state: 'stopped', step: 'transcribe', reason: 'No audio.', job_id: null }, STEPS)).toBe('Stopped: No audio.')
  })

  it('says a worker that died in plain words and points at its job for the details', () => {
    const died = { state: 'stopped' as const, step: 'transcribe' as const, job_id: 'transcribe_3',
      reason: 'Subprocess exited unexpectedly (exit code 1) without reporting a result.' }
    expect(flowText(died, STEPS)).toBe('Transcription stopped unexpectedly.')
    expect(flowText({ ...died, step: 'names' }, STEPS)).toBe('Finding names stopped unexpectedly.')
    expect(stoppedJob(died)).toBe('transcribe_3')
    expect(stoppedJob({ ...died, job_id: null })).toBeNull()
    expect(stoppedJob({ state: 'running', step: 'transcribe', reason: null, job_id: 'transcribe_3' })).toBeNull()
  })
})
