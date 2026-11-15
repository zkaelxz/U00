import { describe, expect, it } from 'vitest'

import { flowActive, flowText } from './TitleFlowStart'

describe('the one-button run as the PC holds it', () => {
  it('names the step that runs, and only a run in line or running keeps the button pressed', () => {
    expect(flowText({ state: 'running', step: 'transcribe', reason: null })).toBe('Transcribing')
    expect(flowText({ state: 'running', step: 'names', reason: null })).toBe('Finding names for the glossary')
    expect(flowText({ state: 'review', step: null, reason: null })).toBe('Ready for glossary review')
    expect(flowText({ state: 'idle', step: null, reason: null })).toBe('')
    expect(flowActive({ state: 'waiting', step: 'transcribe', reason: null })).toBe(true)
    expect(flowActive({ state: 'stopped', step: 'transcribe', reason: 'x' })).toBe(false)
    expect(flowActive(null)).toBe(false)
  })

  it('says why a run waits or stopped, in the server\'s words', () => {
    expect(flowText({ state: 'waiting', step: 'transcribe', reason: null })).toBe('Waiting for the item running in the queue')
    expect(flowText({ state: 'stopped', step: 'transcribe', reason: 'No audio.' })).toBe('Stopped: No audio.')
  })
})
