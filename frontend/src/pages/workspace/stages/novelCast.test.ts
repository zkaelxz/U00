import { describe, expect, it } from 'vitest'

import { initialPick, reportText } from './novelCastLogic'

const person = (over: object) => ({ term: '林晚', name: 'Lin Wan', pronouns: 'she/her', who: '', occurrences: 3, current_pronouns: '', exists: false, ...over })

describe('initialPick', () => {
  it('ticks a person whose pronouns the scan read and that are not set yet', () => {
    expect(initialPick(person({}))).toEqual({ checked: true, pronouns: 'she/her' })
  })
  it('leaves an unclear reading unticked and a person with pronouns already set unticked', () => {
    expect(initialPick(person({ pronouns: '' }))).toEqual({ checked: false, pronouns: '' })
    expect(initialPick(person({ current_pronouns: 'they/them' }))).toEqual({ checked: false, pronouns: 'she/her' })
  })
})

describe('reportText', () => {
  it('says what was set, what was left and what had nowhere to go', () => {
    expect(reportText({ set: ['A'], added: ['B'], kept: ['C'], not_applied: ['D'] })).toBe(
      'Set pronouns for A, B. Left as they were: C (already set). Not applied: D (no speaker of that name yet, and the title has no series to keep them in).')
    expect(reportText({ set: [], added: [], kept: [], not_applied: [] })).toBe('Nothing to apply.')
  })
})
