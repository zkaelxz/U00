import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { MisheardSuggestion } from '../../../../types/misheard'
import { MisheardGroup } from './MisheardGroup'

const suggestion = (over: Partial<MisheardSuggestion> = {}): MisheardSuggestion => ({
  heard: '達羅赫', suggested: '達洛赫', source: 'glossary', heard_count: 2, suggested_count: 12, line_count: 2,
  lines: [{ idx: 3, line_id: 40, zh: '達羅赫來了' }, { idx: 8, line_id: 41, zh: '達羅赫說話' }], ...over,
})

const render = (items: MisheardSuggestion[]) =>
  renderToStaticMarkup(<MisheardGroup dramaId={1} items={items} onGoTo={() => {}} onChanged={() => {}} />).replace(/<!-- -->/g, '')

describe('the May be mis-heard group', () => {
  it('names the suggestion, both counts and underlines the heard spelling in each line', () => {
    const html = render([suggestion()])
    expect(html).toContain('Maybe <strong>達洛赫</strong>?')
    expect(html).toContain('on 2 lines')
    expect(html).toContain('appears 12 times')
    expect(html).toContain('#4')
    expect(html).toContain('<u>達羅赫</u>來了')
  })

  it('offers Use here per line and Use everywhere only when more than one line holds the spelling', () => {
    const many = render([suggestion()])
    expect(many.match(/>Use here<\/button>/g)).toHaveLength(2)
    expect(many).toContain('Use everywhere (2 lines)')
    const one = render([suggestion({ line_count: 1, lines: [{ idx: 3, line_id: 40, zh: '達羅赫來了' }] })])
    expect(one).not.toContain('Use everywhere (')
    expect(one).toContain('on 1 line;')
  })
})
