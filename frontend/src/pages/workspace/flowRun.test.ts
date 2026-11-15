import { describe, expect, it } from 'vitest'

import { flowActive, flowText } from './flowRun'

const STEPS = { transcribe: 'Transcribing', names: 'Finding names for the glossary' }

describe('the one-button run as the PC holds it', () => {
  it('names the step that runs, and only a run in line or running keeps the button pressed', () => {
    expect(flowText({ state: 'running', step: 'transcribe', reason: null }, STEPS)).toBe('Transcribing')
    expect(flowText({ state: 'running', step: 'names', reason: null }, STEPS)).toBe('Finding names for the glossary')
    expect(flowText({ state: 'review', step: null, reason: null }, STEPS)).toBe('Ready for glossary review')
    expect(flowText({ state: 'idle', step: null, reason: null }, STEPS)).toBe('')
    expect(flowActive({ state: 'waiting', step: 'transcribe', reason: null })).toBe(true)
    expect(flowActive({ state: 'stopped', step: 'transcribe', reason: 'x' })).toBe(false)
    expect(flowActive(null)).toBe(false)
  })

  it('says why a run waits or stopped, in the server\'s words', () => {
    expect(flowText({ state: 'waiting', step: 'transcribe', reason: null }, STEPS)).toBe('Waiting for the item running in the queue')
    expect(flowText({ state: 'stopped', step: 'transcribe', reason: 'No audio.' }, STEPS)).toBe('Stopped: No audio.')
  })
})
