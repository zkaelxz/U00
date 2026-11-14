import { describe, expect, it } from 'vitest'

import { checkActionLabel, stepPlace, titleSteps } from './stepModel'

const states = (i: Parameters<typeof titleSteps>[0]) => titleSteps(i).map((s) => `${s.key}:${s.state}`)
const base = { audioReady: false, lineCount: 0, translatedCount: 0, exported: false }

describe('titleSteps', () => {
  it('starts at Add when nothing is there yet', () => {
    expect(states(base)).toEqual(['add:current', 'transcribe:todo', 'translate:todo', 'check:todo', 'export:todo'])
  })

  it('moves to Transcribe once there is audio and no lines', () => {
    expect(states({ ...base, audioReady: true })).toEqual(['add:done', 'transcribe:current', 'translate:todo', 'check:todo', 'export:todo'])
  })

  it('counts imported lines as added and transcribed', () => {
    const s = titleSteps({ ...base, lineCount: 30 })
    expect(s.map((x) => x.state)).toEqual(['done', 'done', 'current', 'todo', 'todo'])
    expect(s[0].note).toBe('Lines added')
    expect(s[2].note).toBe('30 of 30 lines left')
  })

  it('says how many lines are left, then moves to Check, then to Export once exported', () => {
    expect(titleSteps({ ...base, lineCount: 30, translatedCount: 12 })[2].note).toBe('18 of 30 lines left')
    expect(states({ ...base, lineCount: 30, translatedCount: 30 })).toEqual(['add:done', 'transcribe:done', 'translate:done', 'check:current', 'export:todo'])
    expect(states({ ...base, lineCount: 30, translatedCount: 30, exported: true }).every((x) => x.endsWith(':done'))).toBe(true)
  })

  it('shows no current step and no notes until the line count is known', () => {
    const s = titleSteps({ ...base, lineCount: null })
    expect(s.every((x) => x.state === 'todo' && x.note === '')).toBe(true)
  })

  it('says how many lines the check queue holds once it is known', () => {
    const note = (riskCount: number | null | undefined) => titleSteps({ ...base, lineCount: 30, translatedCount: 30, riskCount })[3].note
    expect(note(undefined)).toBe('Read and fix the lines')
    expect(note(null)).toBe('Read and fix the lines')
    expect(note(0)).toBe('Nothing needs a look')
    expect(note(1)).toBe('1 line needs a look')
    expect(note(3)).toBe('3 lines need a look')
  })
})

describe('checkActionLabel', () => {
  it('names the lines the button opens, and gives way to Export when nothing needs a look', () => {
    expect(checkActionLabel(null)).toBe('Check the lines')
    expect(checkActionLabel(1)).toBe('Check the line')
    expect(checkActionLabel(3)).toBe('Check the 3 lines')
    expect(checkActionLabel(0)).toBeNull()
  })
})

describe('stepPlace', () => {
  const steps = titleSteps({ audioReady: true, lineCount: 10, translatedCount: 10, exported: false, riskCount: 3 })

  it('numbers the step among however many steps there are', () => {
    expect(stepPlace(steps, 'check')).toBe('Step 4 of 5 · Check')
    expect(stepPlace(steps, 'add')).toBe('Step 1 of 5 · Add')
    expect(stepPlace(steps.filter((s) => s.key !== 'transcribe'), 'check')).toBe('Step 3 of 4 · Check')
    expect(stepPlace([], 'check')).toBeNull()
  })
})
