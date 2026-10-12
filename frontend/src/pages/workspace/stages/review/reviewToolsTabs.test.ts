import { describe, expect, it } from 'vitest'

import { readSectionOpen, writeSectionOpen, type StorageLike } from '../../../../components/sectionStorage'
import { FIRST_CARD, isReviewToolsTab, readLastTab, REVIEW_TOOLS_TABS, seedFirstCard, writeLastTab } from './reviewToolsTabs'

function memory(): StorageLike {
  const data = new Map<string, string>()
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) }
}

const throwing: StorageLike = {
  getItem: () => {
    throw new Error('blocked')
  },
  setItem: () => {
    throw new Error('blocked')
  },
}

describe('Review tools tabs', () => {
  it('has the six tabs in order, one level', () => {
    expect(REVIEW_TOOLS_TABS.map((t) => t.label)).toEqual(['Check', 'Fix', 'Coverage', 'Restructure', 'History', 'Extras'])
    expect(isReviewToolsTab('coverage')).toBe(true)
    expect(isReviewToolsTab('nope')).toBe(false)
  })

  it('remembers the last tab for each title separately', () => {
    const s = memory()
    expect(readLastTab(s, 3)).toBe('check')
    writeLastTab(s, 3, 'coverage')
    writeLastTab(s, 4, 'history')
    expect(readLastTab(s, 3)).toBe('coverage')
    expect(readLastTab(s, 4)).toBe('history')
    expect(readLastTab(s, 5)).toBe('check')
  })

  it('falls back to Check for an unknown remembered value or blocked storage', () => {
    const s = memory()
    s.setItem('baihe.pref.review.tools.tab.3', JSON.stringify('gone'))
    expect(readLastTab(s, 3)).toBe('check')
    expect(readLastTab(throwing, 3)).toBe('check')
    expect(readLastTab(null, 3)).toBe('check')
    expect(() => writeLastTab(throwing, 3, 'fix')).not.toThrow()
  })

  it('opens a tab’s first card the first time only, and leaves the viewer’s own choice alone', () => {
    const s = memory()
    expect(readSectionOpen(s, FIRST_CARD.coverage!, false)).toBe(false)
    seedFirstCard(s, 'coverage')
    expect(readSectionOpen(s, FIRST_CARD.coverage!, false)).toBe(true)
    // The viewer closes it: showing the tab again must not reopen it.
    writeSectionOpen(s, FIRST_CARD.coverage!, false)
    seedFirstCard(s, 'coverage')
    expect(readSectionOpen(s, FIRST_CARD.coverage!, true)).toBe(false)
    // Other tabs' cards stay closed until their tab is shown.
    seedFirstCard(s, 'history')
    expect(readSectionOpen(s, 'review.records', false)).toBe(false)
  })

  it('does not override a card the viewer already set', () => {
    const s = memory()
    writeSectionOpen(s, FIRST_CARD.fix!, false)
    seedFirstCard(s, 'fix')
    expect(readSectionOpen(s, FIRST_CARD.fix!, true)).toBe(false)
  })

  it('tolerates blocked storage', () => {
    expect(() => seedFirstCard(throwing, 'check')).not.toThrow()
    expect(() => seedFirstCard(null, 'check')).not.toThrow()
  })
})
