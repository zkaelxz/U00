import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { NameReportGroup } from '../../../types/translateStage'
import { ApplyForm, GroupCard, NameReportPanel } from './NameReportPanel'

const group = (over: Partial<NameReportGroup> = {}): NameReportGroup => ({
  key: 'yalo', canonical: 'Yalo', total: 6, titles_total: 3, mark: null,
  variants: [{ text: 'Yalo', count: 4 }, { text: 'Yaloch', count: 2 }],
  titles: [
    { drama_id: 1, title: 'Alpha Stream', count: 4, in_glossary: true, glossary_term: 'Yalo', glossary_english: 'Yarrow' },
    { drama_id: 2, title: 'Beta Stream', count: 2, in_glossary: false, glossary_term: null, glossary_english: null },
  ],
  examples: [{ drama_id: 2, line_idx: 4, snippet: 'then Yaloch left' }],
  ...over,
})

const card = (g: NameReportGroup, busy = false) =>
  renderToStaticMarkup(<ul><GroupCard group={g} busy={busy} onMark={() => undefined} onApplied={() => undefined} /></ul>)

describe('NameReportPanel', () => {
  it('opens as a labelled region with a Close button and no result yet', () => {
    const html = renderToStaticMarkup(<NameReportPanel onClose={() => undefined} />)
    expect(html).toContain('aria-label="Name check across all titles"')
    expect(html).toContain('Close')
    expect(html).toContain('Copy report')
    expect(html).toContain('Loading…')
    expect(html).not.toContain('error-banner')
  })

  it('disables Copy report until there is something to copy, and says why', () => {
    const html = renderToStaticMarkup(<NameReportPanel onClose={() => undefined} />)
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*aria-describedby="name-report-copy-reason"[^>]*>Copy report/)
    expect(html).toContain('Run the check to get a report to copy.')
  })
})

describe('GroupCard', () => {
  it('shows the counts, spellings, titles with their glossary state and an example line', () => {
    const html = card(group())
    expect(html).toContain('3 titles · 6 mentions')
    expect(html).toContain('Yaloch <span class="muted">×2</span>')
    expect(html).toContain('in glossary as “Yalo” → “Yarrow”')
    expect(html).toContain('not in glossary')
    expect(html).toContain('Beta Stream #5: then Yaloch left')
    expect(html).toContain('and 1 more')
  })

  it('has Right, Wrong and Skip with tooltips, and marks the current one pressed', () => {
    const html = card(group({ mark: 'wrong' }))
    for (const label of ['Mark Yalo right', 'Mark Yalo wrong', 'Mark Yalo skip']) expect(html).toContain(`aria-label="${label}"`)
    expect(html).toMatch(/aria-pressed="true"[^>]*aria-label="Mark Yalo wrong"/)
    expect(html).toContain('title="These are different names.')
    expect(html).toContain('Undo mark')
  })

  it('only offers Apply to titles for a group marked right, and says so otherwise', () => {
    const open = card(group())
    expect(open).toMatch(/<button[^>]*disabled=""[^>]*>Apply to titles…/)
    expect(open).toContain('Mark it Right first.')
    const right = card(group({ mark: 'right' }))
    expect(right).not.toMatch(/disabled=""[^>]*>Apply to titles…/)
    expect(right).not.toContain('Mark it Right first.')
  })

  it('disables the marks while one is being saved', () => {
    expect(card(group(), true)).toMatch(/disabled=""[^>]*aria-label="Mark Yalo right"|aria-label="Mark Yalo right"[^>]*disabled=""/)
  })
})

describe('ApplyForm', () => {
  const html = renderToStaticMarkup(<ApplyForm group={group()} onDone={() => undefined} />)

  it('lists the titles ticked, notes which already have the name, and prefills the English', () => {
    expect(html.match(/type="checkbox"/g)).toHaveLength(2)
    expect(html).toContain('Alpha Stream (already has it)')
    expect(html).toContain('value="Yarrow"')
    expect(html).toContain('Add to 2 titles…')
    expect(html).toContain('1 more title has this name')
  })

  it('asks for a confirm before anything is saved (the confirm step is not shown at first)', () => {
    expect(html).not.toContain('Yes, add')
  })
})
