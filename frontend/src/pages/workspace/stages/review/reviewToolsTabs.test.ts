import { describe, expect, it } from 'vitest'

import { readSectionOpen, writeSectionOpen, type StorageLike } from '../../../../components/sectionStorage'
import {
  asReviewToolsGroup, FIRST_CARD, groupOfTool, isReviewToolsGroup, readAddedTools, readLastTab, REVIEW_TOOLS, REVIEW_TOOLS_GROUPS,
  reviewToolsGroupForLink, seedFirstCard, shownGroups, shownTools, writeAddedTools, writeLastTab, type ReviewTool,
} from './reviewToolsTabs'

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

// The seven tabs and what each held before the tools were grouped by purpose.
const OLD_LAYOUT: Record<string, ReviewTool[]> = {
  check: ['checks'],
  fix: ['fixFlagged', 'flags', 'markReviewed'],
  coverage: ['coverage'],
  voices: ['voices'],
  restructure: ['structure', 'resplit', 'merge', 'cleanup', 'shorten'],
  history: ['records', 'versionsAndNotes', 'compareTranscription', 'retime', 'retranscribe', 'retranslate', 'threeRun', 'style'],
  extras: ['audioTags', 'burnPreview'],
}

const inGroup = (group: string, simple = false) => shownTools(simple).filter((t) => t.group === group).map((t) => t.id)

describe('Review tools groups', () => {
  it('has the four groups in order, one level', () => {
    expect(REVIEW_TOOLS_GROUPS.map((g) => g.label)).toEqual(['Look for problems', 'Change many lines', 'Records', 'Finish'])
    expect(isReviewToolsGroup('problems')).toBe(true)
    expect(isReviewToolsGroup('coverage')).toBe(false)
    expect(isReviewToolsGroup('nope')).toBe(false)
  })

  it('holds each group’s tools in order', () => {
    expect(inGroup('problems')).toEqual(['checks', 'flags', 'coverage', 'compareTranscription', 'audioTags'])
    expect(inGroup('change')).toEqual([
      'fixFlagged', 'retranslate', 'threeRun', 'structure', 'resplit', 'merge', 'shorten', 'cleanup', 'retime', 'retranscribe', 'voices',
    ])
    expect(inGroup('records')).toEqual(['records', 'versionsAndNotes', 'style'])
    expect(inGroup('finish')).toEqual(['burnPreview', 'markReviewed'])
  })

  it('puts every tool the seven tabs showed in exactly one group, and adds none', () => {
    const old = Object.values(OLD_LAYOUT).flat()
    expect(new Set(old).size).toBe(old.length)
    for (const tool of old) {
      const groups = REVIEW_TOOLS.filter((t) => t.id === tool).map((t) => t.group)
      expect(groups, tool).toHaveLength(1)
      expect(REVIEW_TOOLS_GROUPS.map((g) => g.id)).toContain(groups[0])
    }
    expect(REVIEW_TOOLS.map((t) => t.id).sort()).toEqual([...old].sort())
  })

  it('Simple shows the same tools as before (Check and Fix) and none of the others', () => {
    expect(shownTools(true).map((t) => t.id).sort()).toEqual([...OLD_LAYOUT.check, ...OLD_LAYOUT.fix].sort())
    expect(inGroup('problems', true)).toEqual(['checks', 'flags'])
    expect(inGroup('change', true)).toEqual(['fixFlagged'])
    expect(inGroup('records', true)).toEqual([])
    expect(inGroup('finish', true)).toEqual(['markReviewed'])
    expect(shownGroups(true).map((g) => g.label)).toEqual(['Look for problems', 'Change many lines', 'Finish'])
    expect(shownGroups(false)).toEqual(REVIEW_TOOLS_GROUPS)
    expect(shownTools(false)).toEqual(REVIEW_TOOLS)
  })

  it('Simple also shows a tool a button asked for, and the group that holds it', () => {
    expect(shownTools(true, new Set(['retranscribe'])).filter((t) => t.group === 'change').map((t) => t.id)).toEqual(['fixFlagged', 'retranscribe'])
    expect(shownGroups(true, new Set(['versionsAndNotes'])).map((g) => g.id)).toEqual(['problems', 'change', 'records', 'finish'])
  })

  it('opens the tools that other parts of Review ask for in their new groups', () => {
    expect(groupOfTool('compareTranscription')).toBe('problems')
    expect(groupOfTool('retime')).toBe('change')
    expect(groupOfTool('retranscribe')).toBe('change')
    expect(groupOfTool('voices')).toBe('change')
    expect(groupOfTool('coverage')).toBe('problems')
  })

  it('maps an old tab id to the group that holds that tab’s first tool', () => {
    for (const [tab, tools] of Object.entries(OLD_LAYOUT)) expect(asReviewToolsGroup(tab), tab).toBe(groupOfTool(tools[0]))
    expect(asReviewToolsGroup('finish')).toBe('finish')
    expect(asReviewToolsGroup('toString')).toBeNull()
    expect(asReviewToolsGroup(null)).toBeNull()
    // An old finished comparison's link names History; the comparison now sits with the checks.
    expect(reviewToolsGroupForLink('history', 'compare')).toBe('problems')
    expect(reviewToolsGroupForLink('history', null)).toBe('records')
    expect(reviewToolsGroupForLink(null, 'compare')).toBeNull()
  })

  it('remembers the last group for each title separately', () => {
    const s = memory()
    expect(readLastTab(s, 3)).toBe('problems')
    writeLastTab(s, 3, 'change')
    writeLastTab(s, 4, 'records')
    expect(readLastTab(s, 3)).toBe('change')
    expect(readLastTab(s, 4)).toBe('records')
    expect(readLastTab(s, 5)).toBe('problems')
  })

  it('reads a tab remembered before the grouping as the group that holds its tools', () => {
    const s = memory()
    s.setItem('baihe.pref.review.tools.tab.3', JSON.stringify('history'))
    s.setItem('baihe.pref.review.tools.tab.4', JSON.stringify('restructure'))
    s.setItem('baihe.pref.review.tools.tab.5', JSON.stringify('extras'))
    expect(readLastTab(s, 3)).toBe('records')
    expect(readLastTab(s, 4)).toBe('change')
    expect(readLastTab(s, 5)).toBe('problems')
  })

  it('remembers the tools a button added for each title, dropping unknown ids and surviving blocked storage', () => {
    const s = memory()
    expect(readAddedTools(s, 3)).toEqual(new Set())
    writeAddedTools(s, 3, new Set(['retranscribe']))
    expect(readAddedTools(s, 3)).toEqual(new Set(['retranscribe']))
    expect(readAddedTools(s, 4)).toEqual(new Set())
    s.setItem('baihe.pref.review.tools.added.5', JSON.stringify(['retime', 'gone']))
    expect(readAddedTools(s, 5)).toEqual(new Set(['retime']))
    s.setItem('baihe.pref.review.tools.added.6', JSON.stringify('retime'))
    expect(readAddedTools(s, 6)).toEqual(new Set())
    expect(readAddedTools(throwing, 3)).toEqual(new Set())
    expect(() => writeAddedTools(throwing, 3, new Set(['retranscribe']))).not.toThrow()
  })

  it('falls back to Look for problems for an unknown remembered value or blocked storage', () => {
    const s = memory()
    s.setItem('baihe.pref.review.tools.tab.3', JSON.stringify('gone'))
    expect(readLastTab(s, 3)).toBe('problems')
    expect(readLastTab(throwing, 3)).toBe('problems')
    expect(readLastTab(null, 3)).toBe('problems')
    expect(() => writeLastTab(throwing, 3, 'change')).not.toThrow()
  })

  it('opens a group’s first card the first time only, and leaves the viewer’s own choice alone', () => {
    const s = memory()
    expect(readSectionOpen(s, FIRST_CARD.finish!, false)).toBe(false)
    seedFirstCard(s, 'finish')
    expect(readSectionOpen(s, FIRST_CARD.finish!, false)).toBe(true)
    // The viewer closes it: showing the group again must not reopen it.
    writeSectionOpen(s, FIRST_CARD.finish!, false)
    seedFirstCard(s, 'finish')
    expect(readSectionOpen(s, FIRST_CARD.finish!, true)).toBe(false)
    // Other groups' cards stay closed until their group is shown, and Records opens none.
    seedFirstCard(s, 'records')
    expect(readSectionOpen(s, 'review.records', false)).toBe(false)
    expect(readSectionOpen(s, FIRST_CARD.problems!, false)).toBe(false)
  })

  it('does not override a card the viewer already set', () => {
    const s = memory()
    writeSectionOpen(s, FIRST_CARD.problems!, false)
    seedFirstCard(s, 'problems')
    expect(readSectionOpen(s, FIRST_CARD.problems!, true)).toBe(false)
  })

  it('tolerates blocked storage', () => {
    expect(() => seedFirstCard(throwing, 'problems')).not.toThrow()
    expect(() => seedFirstCard(null, 'problems')).not.toThrow()
  })
})
