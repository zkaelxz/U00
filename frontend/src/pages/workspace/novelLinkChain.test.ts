import { describe, expect, it } from 'vitest'

import { ChainStall, nextStep, runChain, type ChainActions, type ChainState, type ChainStep } from './novelLinkChain'

const EMPTY: ChainState = { chapters: 0, hasNovelText: false, lines: 0, glossary: 'idle' }

// A fake server: each action changes the state the way the real job would, unless it is told to fail.
function fake(start: ChainState, failAt?: ChainStep, noEffect?: ChainStep) {
  const state = { ...start }
  const calls: string[] = []
  const act = (name: ChainStep, effect: () => void) => async () => {
    calls.push(name)
    if (name === failAt) throw new Error(`${name} failed`)
    if (name !== noEffect) effect()
  }
  const actions: ChainActions = {
    read: async () => ({ ...state }),
    importLink: act('import', () => { state.chapters = 3 }),
    attach: act('attach', () => { state.hasNovelText = true }),
    tag: act('tag', () => { state.lines = 40 }),
    extract: act('extract', () => { state.glossary = 'done' }),
    waitExtract: act('wait', () => { state.glossary = 'done' }),
  }
  return { actions, calls }
}

describe('nextStep reads the server state', () => {
  it('goes from nothing to import', () => expect(nextStep(EMPTY)).toBe('import'))
  it('attaches saved chapters that are not yet the novel text', () => {
    expect(nextStep({ ...EMPTY, chapters: 3 })).toBe('attach')
  })
  it('tags once the text is attached', () => {
    expect(nextStep({ ...EMPTY, chapters: 3, hasNovelText: true })).toBe('tag')
  })
  it('extracts glossary terms once there are lines', () => {
    expect(nextStep({ chapters: 3, hasNovelText: true, lines: 9, glossary: 'idle' })).toBe('extract')
  })
  it('waits for a run that is going and stops at review when it is done', () => {
    const lined = { chapters: 3, hasNovelText: true, lines: 9 }
    expect(nextStep({ ...lined, glossary: 'running' })).toBe('wait')
    expect(nextStep({ ...lined, glossary: 'done' })).toBe('review')
  })
})

describe('runChain', () => {
  it('runs every step in order and stops at the glossary review', async () => {
    const { actions, calls } = fake(EMPTY)
    const seen: ChainStep[] = []
    expect(await runChain(actions, (s) => seen.push(s))).toBe('review')
    expect(calls).toEqual(['import', 'attach', 'tag', 'extract'])
    expect(seen.at(-1)).toBe('review')
  })

  it('resumes from the title as the server holds it', async () => {
    const { actions, calls } = fake({ chapters: 3, hasNovelText: true, lines: 40, glossary: 'running' })
    await runChain(actions, () => {})
    expect(calls).toEqual(['wait'])
  })

  it.each(['import', 'attach', 'tag', 'extract'] as const)('stops with the %s step error and runs nothing after it', async (step) => {
    const { actions, calls } = fake(EMPTY, step)
    await expect(runChain(actions, () => {})).rejects.toThrow(`${step} failed`)
    expect(calls.at(-1)).toBe(step)
  })

  it('treats a step that changed nothing as a failure instead of repeating it', async () => {
    const { actions, calls } = fake(EMPTY, undefined, 'import')
    await expect(runChain(actions, () => {})).rejects.toBeInstanceOf(ChainStall)
    expect(calls).toEqual(['import'])
  })
})
