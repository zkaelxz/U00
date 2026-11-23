import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { MisheardSuggestion } from '../../../../types/misheard'
import { MisheardGroup } from './MisheardGroup'

const suggestion = (over: Partial<MisheardSuggestion> = {}): MisheardSuggestion => ({
  heard: '亞羅赫', suggested: '亞洛赫', source: 'glossary', heard_count: 2, suggested_count: 12, line_count: 2, term_id: null,
  lines: [{ idx: 3, line_id: 40, zh: '亞羅赫來了', start: 1, end: 2 }, { idx: 8, line_id: 41, zh: '亞羅赫說話', start: 1, end: 2 }], ...over,
})

const render = (items: MisheardSuggestion[], listen = false) =>
  renderToStaticMarkup(
    <MisheardGroup dramaId={1} items={items} onGoTo={() => {}} onListen={listen ? () => {} : undefined} onChanged={() => {}} />,
  ).replace(/<!-- -->/g, '')

describe('the May be mis-heard group', () => {
  it('names the suggestion, both counts and underlines the heard spelling in each line', () => {
    const html = render([suggestion()])
    expect(html).toContain('Maybe <strong>亞洛赫</strong>?')
    expect(html).toContain('on 2 lines')
    expect(html).toContain('appears 12 times')
    expect(html).toContain('#4')
    expect(html).toContain('<u>亞羅赫</u>來了')
  })

  it('says where a never-heard spelling comes from instead of counting it as 0 times', () => {
    const glossary = render([suggestion({ suggested_count: 0 })])
    expect(glossary).toContain('on 2 lines. It&#x27;s in your glossary.')
    expect(glossary).not.toContain('0 times')
    expect(render([suggestion({ suggested_count: 0, source: 'name' })])).toContain('It&#x27;s a character name.')
    expect(render([suggestion({ suggested_count: 1 })])).toContain('appears 1 time.')
  })

  it('offers Use here per line and Use everywhere only when more than one line holds the spelling', () => {
    const many = render([suggestion()])
    expect(many.match(/>Use here<\/button>/g)).toHaveLength(2)
    expect(many).toContain('Use everywhere (2 lines)')
    const one = render([suggestion({ line_count: 1, lines: [{ idx: 3, line_id: 40, zh: '亞羅赫來了', start: 1, end: 2 }] })])
    expect(one).not.toContain('Use everywhere (')
    expect(one).toContain('on 1 line;')
  })

  it('offers Keep as heard on each suggestion and a way back only when something is kept', () => {
    expect(render([suggestion()]).match(/>Keep as heard<\/button>/g)).toHaveLength(1)
    expect(render([suggestion()])).not.toContain('kept word')
    const html = renderToStaticMarkup(
      <MisheardGroup dramaId={1} items={[suggestion()]} kept={2} onGoTo={() => {}} onChanged={() => {}} />,
    ).replace(/<!-- -->/g, '')
    expect(html).toContain('Show 2 kept words again')
  })

  it('shows Listen only with audio to play, and the remember button only for a glossary term', () => {
    expect(render([suggestion()])).not.toContain('>Listen<')
    expect(render([suggestion()], true).match(/>Listen<\/button>/g)).toHaveLength(2)
    expect(render([suggestion()])).not.toContain('remember this spelling')
    expect(render([suggestion({ term_id: 5 })])).toContain('Use everywhere and remember this spelling')
  })
})
