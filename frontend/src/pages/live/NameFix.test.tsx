import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { NameFix } from './NameFix'

describe('NameFix', () => {
  it('starts as one button and lists the names fixed so far', () => {
    const html = renderToStaticMarkup(
      <NameFix sessionId="live_x" fixes={[{ source: '月岡', english: 'Tsukioka' }, { source: '真白', english: 'Mashiro' }]} hasGlossary lang="ja" />,
    )
    expect(html).toContain('Fix a name')
    expect(html).not.toContain('<form')
    expect(html).toContain('月岡</span> → Tsukioka')
    expect(html).toContain(', <span lang="ja">真白')
  })

  it('shows no list before any fix', () => {
    const html = renderToStaticMarkup(<NameFix sessionId="live_x" fixes={[]} hasGlossary={false} lang="zh" />)
    expect(html).not.toContain('live-namefix-list')
  })
})
