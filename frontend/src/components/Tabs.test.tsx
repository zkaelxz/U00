import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Tabs } from './Tabs'
import { tabButtonId, tabPanelId } from './tabIds'

const tabs = [
  { id: 'a', label: 'Alpha' },
  { id: 'b', label: 'Beta', attention: 2 },
  { id: 'c', label: 'Gamma', count: 0 },
]

describe('Tabs', () => {
  const html = renderToStaticMarkup(<Tabs label="Demo" idPrefix="demo" tabs={tabs} active="b" onSelect={() => undefined} />)

  it('is a tablist whose selected tab alone is in the tab order', () => {
    expect(html).toContain('role="tablist"')
    expect(html).toContain('aria-label="Demo"')
    expect(html.match(/role="tab"/g)).toHaveLength(3)
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1)
    expect(html.match(/tabindex="0"/g)).toHaveLength(1)
    expect(html.match(/tabindex="-1"/g)).toHaveLength(2)
  })

  it('links each tab to its panel through the shared ids', () => {
    expect(html).toContain(`id="${tabButtonId('demo', 'a')}"`)
    expect(html).toContain(`aria-controls="${tabPanelId('demo', 'a')}"`)
  })

  it('announces what needs attention and shows a zero count', () => {
    expect(html).toContain('pill-warn')
    expect(html).toContain('2<span class="visually-hidden"> to look at</span>')
    expect(html).toContain('>0</span>')
  })
})
