import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Card } from './Card'
import { CardFoldContext } from './cardFold'

const fold = (over: Partial<React.ComponentProps<typeof CardFoldContext.Provider>['value'] & object> = {}) => ({
  storageKey: 'settings.card.x', defaultOpen: false, ...over,
})

describe('Card', () => {
  it('stays an always-open block outside a fold', () => {
    const html = renderToStaticMarkup(<Card title="Spending" meta="No cap">body</Card>)
    expect(html).toContain('card-title')
    expect(html).not.toContain('<details')
  })

  it('folds inside a fold provider: the title and value show while closed, the body is inside the fold', () => {
    const html = renderToStaticMarkup(
      <CardFoldContext.Provider value={fold({ summary: 'Notify when a job finishes: Off' })}>
        <Card title="Notifications" meta="No channel set" aria-label="Notifications">body</Card>
      </CardFoldContext.Provider>,
    )
    expect(html).toContain('<details class="section">')
    expect(html).not.toMatch(/<details[^>]* open/)
    expect(html).toContain('Notify when a job finishes: Off')
    expect(html).toContain('aria-label="Notifications"')
  })

  it('falls back to the meta line as the closed value, and starts open when the card should', () => {
    const closed = renderToStaticMarkup(
      <CardFoldContext.Provider value={fold()}><Card title="Spending" meta="No cap">body</Card></CardFoldContext.Provider>,
    )
    expect(closed).toContain('section-summary')
    expect(closed).toContain('No cap')
    const open = renderToStaticMarkup(
      <CardFoldContext.Provider value={fold({ defaultOpen: true })}><Card title="Spending" meta="No cap">body</Card></CardFoldContext.Provider>,
    )
    expect(open).toMatch(/<details[^>]* open/)
    expect(open).not.toContain('section-summary')
    expect(open).toContain('card-meta')
  })

  it('does not fold a Card nested in a folded Card', () => {
    const html = renderToStaticMarkup(
      <CardFoldContext.Provider value={fold({ defaultOpen: true })}>
        <Card title="Outer"><Card title="Inner">x</Card></Card>
      </CardFoldContext.Provider>,
    )
    expect(html.match(/<details/g)).toHaveLength(1)
    expect(html).toContain('card-title')
  })
})
