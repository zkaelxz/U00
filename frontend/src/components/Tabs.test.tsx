import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { PICKER_MIN_TABS, TabLabel, TabPicker, Tabs } from './Tabs'
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

describe('unsaved dot', () => {
  it('marks a tab with unsaved edits and names it for screen readers', () => {
    const html = renderToStaticMarkup(
      <Tabs label="T" idPrefix="t" tabs={[{ id: 'a', label: 'Alpha', unsaved: true }, { id: 'b', label: 'Beta' }]} active="a" onSelect={() => {}} />,
    )
    expect(html).toContain('data-testid="tab-unsaved-a"')
    expect(html).toContain('(unsaved changes)')
    expect(html).not.toContain('tab-unsaved-b')
  })
})

describe('short labels', () => {
  const tab = { id: 'a', label: 'Packages and installs', short: 'Packages' }

  it('on a phone keeps the full label in the accessible name and hides the short one from it', () => {
    const html = renderToStaticMarkup(<TabLabel t={tab} phone />)
    expect(html).toContain('<span class="ui-tab-full">Packages and installs</span>')
    expect(html).toContain('<span class="ui-tab-short" aria-hidden="true">Packages</span>')
  })

  it('elsewhere is the full label only, so the tab text is never doubled', () => {
    expect(renderToStaticMarkup(<TabLabel t={tab} phone={false} />)).toBe('Packages and installs')
    const row = renderToStaticMarkup(<Tabs label="T" idPrefix="t" tabs={[tab, { id: 'b', label: 'Models' }]} active="a" onSelect={() => {}} />)
    expect(row).not.toContain('Packages<')
    expect(row).toContain('>Models</button>')
  })

  it('leaves a tab without a short label as plain text on a phone too', () => {
    expect(renderToStaticMarkup(<TabLabel t={{ id: 'b', label: 'Models' }} phone />)).toBe('Models')
  })
})

describe('TabPicker', () => {
  const six = ['a', 'b', 'c', 'd', 'e', 'f'].map((id, i) => ({ id, label: `Section ${i}`, attention: id === 'b' ? 2 : undefined }))
  const html = renderToStaticMarkup(<TabPicker label="Settings" idPrefix="s" tabs={six} active="b" onSelect={() => {}} />)

  it('is one closed button naming the current section, with no tablist', () => {
    expect(PICKER_MIN_TABS).toBe(5)
    expect(html).toContain('aria-haspopup="listbox"')
    expect(html).toContain('aria-expanded="false"')
    expect(html).toContain('aria-label="Settings: Section 1"')
    expect(html).not.toContain('role="tablist"')
    expect(html).not.toContain('role="listbox"')
  })

  it('still gives every panel a label to point at', () => {
    for (const t of six) expect(html).toContain(`id="${tabButtonId('s', t.id)}"`)
  })

  it('shows what needs attention on the current section', () => {
    expect(html).toContain('2<span class="visually-hidden"> to look at</span>')
  })
})

describe('every tab stays enabled', () => {
  it('has no disabled tab, whatever its count or attention', () => {
    const html = renderToStaticMarkup(<Tabs label="Demo" idPrefix="demo" tabs={tabs} active="a" onSelect={() => undefined} />)
    expect(html).not.toContain('disabled')
    expect(html).not.toContain('aria-disabled')
  })
})
