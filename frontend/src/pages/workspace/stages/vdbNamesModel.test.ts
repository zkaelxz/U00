import { describe, expect, it } from 'vitest'

import type { VdbNamesStatus } from '../../../types/vdbNames'
import { vdbAgeText, vdbNamesSummary, vdbToggleBlocker } from './vdbNamesModel'

const status = (over: Partial<VdbNamesStatus> = {}): VdbNamesStatus => ({
  downloaded: true, entry_count: 10, name_count: 25, age_days: 3, newer_than_30_days: true, ...over,
})

describe('vtuber names model', () => {
  it('summarises the download and the title choice', () => {
    expect(vdbNamesSummary(null, false)).toBe('')
    expect(vdbNamesSummary(status({ downloaded: false }), false)).toBe('not downloaded')
    expect(vdbNamesSummary(status(), true)).toBe('25 names · on for this title')
    expect(vdbNamesSummary(status(), false)).toBe('25 names · off for this title')
  })

  it('says how old the list is and asks for a refresh when it is old', () => {
    expect(vdbAgeText(status({ age_days: 0 }))).toBe('Downloaded today')
    expect(vdbAgeText(status({ age_days: 1 }))).toBe('Downloaded 1 day ago')
    expect(vdbAgeText(status({ age_days: 90, newer_than_30_days: false }))).toBe('Downloaded 90 days ago. Download again to refresh it.')
  })

  it('blocks the title switch only while nothing is downloaded', () => {
    expect(vdbToggleBlocker(status({ downloaded: false }))).toMatch(/Download the VTuber names first/)
    expect(vdbToggleBlocker(status())).toBeNull()
    expect(vdbToggleBlocker(null)).toBeNull()
  })
})
