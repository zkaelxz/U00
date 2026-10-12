import { describe, expect, it } from 'vitest'

import { EMPTY_GLOSSARY_UI, parseGlossaryUi, readGlossaryUi, writeGlossaryUi } from './glossaryUi'

function fakeStorage(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return {
    data,
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v
    },
  }
}

describe('glossary UI memory (per title, versioned)', () => {
  it('reads nothing as the empty state', () => {
    expect(readGlossaryUi(fakeStorage(), 7)).toEqual(EMPTY_GLOSSARY_UI)
    expect(readGlossaryUi(null, 7)).toEqual(EMPTY_GLOSSARY_UI)
  })

  it('remembers the tab, source, spelling term, English name and name check per title', () => {
    const s = fakeStorage()
    writeGlossaryUi(s, 7, { tab: 'find', source: 'lines' })
    writeGlossaryUi(s, 7, { spellingTermId: 12, english: { '12': 'Wei Ying' }, nameReportOpen: true })
    expect(readGlossaryUi(s, 7)).toEqual({
      tab: 'find', source: 'lines', spellingTermId: 12, english: { '12': 'Wei Ying' }, nameReportOpen: true,
    })
    expect(readGlossaryUi(s, 8)).toEqual(EMPTY_GLOSSARY_UI)
  })

  it('keeps earlier English names when another is written', () => {
    const s = fakeStorage()
    writeGlossaryUi(s, 1, { english: { '1': 'A' } })
    writeGlossaryUi(s, 1, { english: { '2': 'B' } })
    expect(readGlossaryUi(s, 1).english).toEqual({ '1': 'A', '2': 'B' })
  })

  it('ignores another version, corrupt json and wrong types', () => {
    expect(parseGlossaryUi({ v: 2, tab: 'find' })).toEqual(EMPTY_GLOSSARY_UI)
    expect(parseGlossaryUi('x')).toEqual(EMPTY_GLOSSARY_UI)
    expect(parseGlossaryUi({ v: 1, tab: 'nope', source: 'web', spellingTermId: 'a', english: [1], nameReportOpen: 'y' }))
      .toEqual(EMPTY_GLOSSARY_UI)
    const s = fakeStorage({ 'baihe.pref.glossary.ui.3': '{not json' })
    expect(readGlossaryUi(s, 3)).toEqual(EMPTY_GLOSSARY_UI)
  })

  it('survives storage that throws', () => {
    const boom = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } }
    expect(readGlossaryUi(boom, 1)).toEqual(EMPTY_GLOSSARY_UI)
    expect(() => writeGlossaryUi(boom, 1, { tab: 'names' })).not.toThrow()
  })

  it('null clears a remembered spelling term', () => {
    const s = fakeStorage()
    writeGlossaryUi(s, 1, { spellingTermId: 5 })
    writeGlossaryUi(s, 1, { spellingTermId: null })
    expect(readGlossaryUi(s, 1).spellingTermId).toBeNull()
  })
})
