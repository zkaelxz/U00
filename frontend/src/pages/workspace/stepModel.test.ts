import { describe, expect, it } from 'vitest'

import { checkActionLabel, focusStep, linesOpenOnAll, mainInStepsPanel, stepLinks, stepPlace, titleSteps, type StepKey, type StepLink } from './stepModel'

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

  it('says nothing needs a look only once it is Check’s turn; before that a zero count asks to read the lines', () => {
    const note = (lineCount: number, translatedCount: number, riskCount: number) => titleSteps({ ...base, lineCount, translatedCount, riskCount })[3].note
    expect(note(0, 0, 0)).toBe('Read and fix the lines')
    expect(note(24, 23, 0)).toBe('Read and fix the lines')
    expect(note(24, 23, 2)).toBe('2 lines need a look')
    expect(note(24, 24, 0)).toBe('Nothing needs a look')
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

describe('stepLinks', () => {
  const text = (l: StepLink) => `${l.word ? `${l.word} · ` : ''}${l.name}${l.note ? `, ${l.note}` : ''}`
  const texts = (i: Parameters<typeof stepLinks>[0], open: Parameters<typeof stepLinks>[1] = null) => stepLinks(i, open).map(text)
  const marked = (i: Parameters<typeof stepLinks>[0], open: Parameters<typeof stepLinks>[1] = null) => stepLinks(i, open).filter((l) => l.marked).map((l) => l.key)

  it('names a new title\'s steps: Now, Next, then the names alone', () => {
    expect(texts(base)).toEqual(['Now · Add', 'Next · Transcribe', 'Translate', 'Check', 'Export'])
  })

  it('shows only the five names while the progress is loading', () => {
    expect(texts({ ...base, lineCount: null, riskCount: 3 })).toEqual(['Add', 'Transcribe', 'Translate', 'Check', 'Export'])
    expect(marked({ ...base, lineCount: null })).toEqual([])
  })

  it('counts the lines once transcribed and the lines left to translate, with the check count after them', () => {
    expect(texts({ ...base, audioReady: true, lineCount: 24, translatedCount: 23, riskCount: 3 })).toEqual([
      'Done · Add', 'Done · Transcribe, 24 lines', 'Now · Translate, 1 of 24 left', 'Next · Check, 3 lines need a look', 'Export',
    ])
    expect(texts({ ...base, lineCount: 1, translatedCount: 0 })[1]).toBe('Done · Transcribe, 1 line')
  })

  it('moves to Check once every line has English, as drawn', () => {
    expect(texts({ ...base, audioReady: true, lineCount: 24, translatedCount: 24, riskCount: 3 })).toEqual([
      'Done · Add', 'Done · Transcribe, 24 lines', 'Done · Translate', 'Now · Check, 3 lines need a look', 'Next · Export',
    ])
  })

  it('says how many lines need a look in the singular, the plural and when none do, and nothing while it is unknown', () => {
    const check = (riskCount: number | null | undefined) => texts({ ...base, lineCount: 24, translatedCount: 24, riskCount })[3]
    expect(check(1)).toBe('Now · Check, 1 line needs a look')
    expect(check(3)).toBe('Now · Check, 3 lines need a look')
    expect(check(0)).toBe('Now · Check, nothing needs a look')
    expect(check(null)).toBe('Now · Check')
    expect(check(undefined)).toBe('Now · Check')
  })

  it('says nothing needs a look only on Check’s own turn; a count above zero shows on any step', () => {
    expect(texts({ ...base, lineCount: 0, translatedCount: 0, riskCount: 0 })[3]).toBe('Check')
    expect(texts({ ...base, audioReady: true, lineCount: 24, translatedCount: 23, riskCount: 0 })[3]).toBe('Next · Check')
    expect(texts({ ...base, audioReady: true, lineCount: 24, translatedCount: 23, riskCount: 2 })[3]).toBe('Next · Check, 2 lines need a look')
    expect(texts({ ...base, lineCount: 24, translatedCount: 24, riskCount: 0 })[3]).toBe('Now · Check, nothing needs a look')
  })

  it('marks every step done once exported, Transcribe still with its line count', () => {
    expect(texts({ ...base, audioReady: true, lineCount: 24, translatedCount: 24, exported: true, riskCount: 3 })).toEqual([
      'Done · Add', 'Done · Transcribe, 24 lines', 'Done · Translate', 'Done · Check', 'Done · Export',
    ])
  })

  it('never puts a note on Add or Export', () => {
    for (const i of [base, { ...base, lineCount: 24, translatedCount: 24, riskCount: 2 }, { ...base, lineCount: 24, translatedCount: 24, exported: true }]) {
      const links = stepLinks(i)
      expect(links[0].note).toBeNull()
      expect(links[4].note).toBeNull()
    }
  })

  it('keeps the longer notes as each step\'s tooltip', () => {
    const tips = stepLinks({ ...base, lineCount: 24, translatedCount: 24, riskCount: 3 }).map((l) => l.tip)
    expect(tips).toEqual(['Lines added', '24 lines', 'Every line has English', '3 lines need a look', 'SRT, VTT, ASS or video'])
    expect(stepLinks(base)[0].tip).toBe('Add audio, video or a transcript')
  })

  it('marks the step whose page is open, else the current one, else the last once every step is done', () => {
    const i = { ...base, lineCount: 24, translatedCount: 24, riskCount: 3 }
    expect(marked(i, 'check')).toEqual(['check'])
    expect(marked(i, 'translate')).toEqual(['translate'])
    expect(marked(i)).toEqual(['check'])
    expect(marked({ ...i, exported: true })).toEqual(['export'])
    expect(focusStep(titleSteps({ ...i, exported: true }), null)).toBe('export')
    expect(focusStep(titleSteps({ ...base, lineCount: null }), null)).toBeNull()
    expect(focusStep(titleSteps({ ...base, lineCount: null }), 'translate')).toBe('translate')
  })
})

describe('mainInStepsPanel', () => {
  const keys: StepKey[] = ['add', 'transcribe', 'translate', 'check', 'export']
  it('on the title page, puts the main button in the steps panel only while Check is the current step', () => {
    expect(keys.filter((k) => mainInStepsPanel(null, k, true, 3))).toEqual(['check'])
    expect(mainInStepsPanel(null, null, true, 3)).toBe(false)
  })
  it('on Review, always in the steps panel; on the other stage pages, never', () => {
    for (const k of keys) expect(mainInStepsPanel('check', k, false, 3)).toBe(true)
    for (const open of ['translate', 'export', 'add', 'transcribe', null] as const) {
      expect(keys.filter((k) => mainInStepsPanel(open, k, false, 3))).toEqual([])
    }
  })
  it('keeps it under the header when nothing needs a look, and treats an unknown count as something may', () => {
    expect(mainInStepsPanel(null, 'check', true, 0)).toBe(false)
    for (const k of keys) expect(mainInStepsPanel('check', k, false, 0)).toBe(false)
    expect(mainInStepsPanel(null, 'check', true, null)).toBe(true)
    expect(mainInStepsPanel('check', 'check', false, null)).toBe(true)
    expect(mainInStepsPanel('check', 'export', false, 1)).toBe(true)
  })
})

describe('linesOpenOnAll', () => {
  const at = (translated: number, risk: number | null = 3) => ({ audioReady: true, lineCount: 24, translatedCount: translated, exported: false, riskCount: risk })
  it('is true on the title page while some lines have no English (the Translate step)', () => {
    expect(linesOpenOnAll(true, at(23))).toBe(true)
    expect(linesOpenOnAll(true, at(0))).toBe(true)
  })
  it('is false once every line is translated, while the count is unknown, and on Review', () => {
    expect(linesOpenOnAll(true, at(24))).toBe(false)
    expect(linesOpenOnAll(true, { ...at(0), lineCount: null })).toBe(false)
    expect(linesOpenOnAll(false, at(23))).toBe(false)
  })
})
