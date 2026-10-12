import { describe, expect, it } from 'vitest'

import type { GlossaryTerm, NameReport, NameReportGroup } from '../../../types/translateStage'
import { groupsForTab, planApply, scanNotes, tabCount } from './nameReportModel'

const term = (over: Partial<GlossaryTerm> = {}): GlossaryTerm => ({
  id: 7, term_original: 'Yalo', term_translation: 'Yarrow', notes: '', category: null, policy: null,
  enforce_exact: false, aliases: [], banned_translations: [], ...over,
})

const group = (over: Partial<NameReportGroup> = {}): NameReportGroup => ({
  key: 'yalo', canonical: 'Yalo', total: 6, titles_total: 2, mark: 'right', examples: [],
  variants: [{ text: 'Yalo', count: 4 }, { text: 'Yaloch', count: 2 }],
  titles: [], ...over,
})

const report = (over: Partial<NameReport> = {}): NameReport => ({
  titles_total: 3, titles_scanned: 3, titles_unavailable: 0, titles_failed: 0, reasons: [], truncated: false,
  singletons_omitted: 0, groups_omitted: 0, groups: [group({ mark: null }), group({ key: 'b', mark: 'wrong' })],
  counts: { unresolved: 1, right: 0, wrong: 1, skip: 0 }, ...over,
})

describe('groupsForTab / tabCount', () => {
  it('lists unresolved groups under To check and marked ones under their mark', () => {
    const r = report()
    expect(groupsForTab(r, 'open').map((g) => g.key)).toEqual(['yalo'])
    expect(groupsForTab(r, 'wrong').map((g) => g.key)).toEqual(['b'])
    expect(groupsForTab(r, 'skip')).toEqual([])
    expect(tabCount(r, 'open')).toBe(1)
    expect(tabCount(r, 'wrong')).toBe(1)
  })
})

describe('scanNotes', () => {
  it('is empty for a complete scan', () => {
    expect(scanNotes(report())).toEqual([])
  })
  it('says what was not covered', () => {
    const notes = scanNotes(report({ titles_scanned: 2, titles_unavailable: 1, reasons: ['Japanese needs pykakasi.'], titles_failed: 2, groups_omitted: 1 }))
    expect(notes).toHaveLength(4)
    expect(notes[0]).toContain('2 of 3 titles')
    expect(notes[1]).toContain('Japanese needs pykakasi.')
    expect(notes[2]).toContain('2 titles could not be read')
  })
})

describe('planApply', () => {
  it('adds the missing spellings as aliases of the term that already holds one', () => {
    const plan = planApply([term()], group(), '')
    expect(plan).toEqual({ kind: 'alias', body: { id: 7, aliases: ['Yaloch'] } })
  })
  it('keeps the aliases already there and finds the term by an alias', () => {
    const plan = planApply([term({ term_original: 'Other', aliases: ['yaloch', 'Z'] })], group(), '')
    expect(plan).toEqual({ kind: 'alias', body: { id: 7, aliases: ['yaloch', 'Z', 'Yalo'] } })
  })
  it('is covered when every spelling is already a term or alias', () => {
    expect(planApply([term({ aliases: ['Yaloch'] })], group(), '')).toEqual({ kind: 'covered' })
  })
  it('creates a term only when an English name is given', () => {
    expect(planApply([], group(), '  ')).toEqual({ kind: 'needs-english' })
    expect(planApply([term({ term_original: 'Mirel' })], group(), ' Yarrow ')).toEqual({
      kind: 'create', body: { term_original: 'Yalo', term_translation: 'Yarrow', aliases: ['Yaloch'] },
    })
  })
})
