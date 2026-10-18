import { describe, expect, it } from 'vitest'

import { firstRunSteps, showGetStarted } from './getStartedLogic'

describe('showGetStarted', () => {
  it('shows only for a loaded empty library that was not dismissed', () => {
    expect(showGetStarted(0, false)).toBe(true)
    expect(showGetStarted(0, true)).toBe(false)
    expect(showGetStarted(1, false)).toBe(false)
    expect(showGetStarted(null, false)).toBe(false)
    expect(showGetStarted(undefined, false)).toBe(false)
  })
})

const NONE = { faster_whisper: false, ollama_reachable: false, key_claude: false, key_deepseek: false, key_gemini: false, key_openai: false, key_groq: false }
const ids = (steps: { id: string }[]) => steps.map((s) => s.id)

describe('firstRunSteps', () => {
  it('lists key, transcription and Make subtitles for an admin with nothing set up', () => {
    const steps = firstRunSteps({ canSetUp: true, canCreate: true, readiness: NONE, totalDramas: 0 })
    expect(steps.map((s) => [s.id, s.done])).toEqual([['key', false], ['transcription', false], ['subtitles', false]])
    expect(steps.map((s) => s.href)).toEqual(['#/settings', '#/diagnostics?card=recommended', '#/make-subtitles'])
  })

  it('drops the key step when a keyed engine or a running Ollama exists', () => {
    for (const patch of [{ key_gemini: true }, { ollama_reachable: true }]) {
      expect(ids(firstRunSteps({ canSetUp: true, canCreate: true, readiness: { ...NONE, ...patch }, totalDramas: 0 }))).toEqual(['transcription', 'subtitles'])
    }
  })

  it('marks transcription done once it is installed, and waits on an unknown readiness', () => {
    const done = firstRunSteps({ canSetUp: true, canCreate: true, readiness: { ...NONE, faster_whisper: true }, totalDramas: 0 })
    expect(done.find((s) => s.id === 'transcription')?.done).toBe(true)
    const unknown = firstRunSteps({ canSetUp: true, canCreate: true, readiness: null, totalDramas: 0 })
    expect(unknown.map((s) => [s.id, s.done])).toEqual([['transcription', false], ['subtitles', false]])
  })

  it('gives a member only the steps they can act on, with no link to install', () => {
    const steps = firstRunSteps({ canSetUp: false, canCreate: false, readiness: NONE, totalDramas: 0 })
    expect(steps).toEqual([{ id: 'transcription', label: 'Install transcription', done: false, href: null }])
  })
})
