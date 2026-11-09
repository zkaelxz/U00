import { describe, expect, it } from 'vitest'

import { titleSteps } from './stepModel'

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
})
