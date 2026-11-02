import { describe, expect, it, vi } from 'vitest'

import { TitleFlowStall, nextTitleFlowStep, runTitleFlow, type TitleFlowActions, type TitleFlowState } from './titleFlowChain'

describe('title flow', () => {
  it('picks the next step from what the title holds', () => {
    expect(nextTitleFlowStep({ lines: 0, glossary: 'idle' })).toBe('transcribe')
    expect(nextTitleFlowStep({ lines: 5, glossary: 'idle' })).toBe('extract')
    expect(nextTitleFlowStep({ lines: 5, glossary: 'running' })).toBe('wait')
    expect(nextTitleFlowStep({ lines: 5, glossary: 'error' })).toBe('extract')
    expect(nextTitleFlowStep({ lines: 5, glossary: 'done' })).toBe('review')
  })

  const run = (states: TitleFlowState[]) => {
    let i = 0
    const actions: TitleFlowActions = {
      read: async () => states[Math.min(i++, states.length - 1)],
      transcribe: vi.fn(async () => undefined),
      extract: vi.fn(async () => undefined),
      waitExtract: vi.fn(async () => undefined),
    }
    return { actions, done: runTitleFlow(actions, () => undefined) }
  }

  it('transcribes, extracts, then stops at the review', async () => {
    const { actions, done } = run([
      { lines: 0, glossary: 'idle' }, { lines: 9, glossary: 'idle' }, { lines: 9, glossary: 'done' },
    ])
    await expect(done).resolves.toBe('review')
    expect(actions.transcribe).toHaveBeenCalledTimes(1)
    expect(actions.extract).toHaveBeenCalledTimes(1)
  })

  it('fails instead of looping when a step changes nothing', async () => {
    const { done } = run([{ lines: 0, glossary: 'idle' }])
    await expect(done).rejects.toBeInstanceOf(TitleFlowStall)
  })
})
