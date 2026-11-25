import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { limitGroups } from '../pages/titleOrder'
import { Paged } from './ShowMore'

describe('Paged', () => {
  it('draws one page and says how many more there are', () => {
    const items = Array.from({ length: 7 }, (_, i) => `row ${i}`)
    const html = renderToStaticMarkup(<Paged items={items} step={3}>{(v) => <ul>{v.map((x) => <li key={x}>{x}</li>)}</ul>}</Paged>)
    expect(html.match(/<li>/g)).toHaveLength(3)
    expect(html).toContain('Showing 3 of 7.')
    expect(html).toContain('Show more')
  })
  it('has no footer when everything fits', () => {
    const html = renderToStaticMarkup(<Paged items={['a']} step={3}>{(v) => <ul>{v.map((x) => <li key={x}>{x}</li>)}</ul>}</Paged>)
    expect(html).not.toContain('Show more')
  })
})

describe('limitGroups', () => {
  const g = (key: string, n: number) => ({ key, label: key, items: Array.from({ length: n }, (_, i) => ({ id: i })), mix: '' })
  it('cuts across groups in drawing order and drops empty ones', () => {
    const out = limitGroups([g('a', 3), g('b', 3), g('c', 3)] as never, 4)
    expect(out.map((x) => [x.key, x.items.length])).toEqual([['a', 3], ['b', 1]])
  })
})
