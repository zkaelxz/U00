import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { ThemeMenu, ThemeMenuItems } from './ThemeMenu'

describe('ThemeMenu', () => {
  it('renders a labelled, collapsed menu button that names the current theme', () => {
    const html = renderToStaticMarkup(createElement(ThemeMenu))
    expect(html).toContain('aria-haspopup="menu"')
    expect(html).toContain('aria-expanded="false"')
    expect(html).toContain('aria-label="Theme: Match this device. Change theme"')
    expect(html).not.toContain('role="menu"')
  })

  it('lists the four themes as radio menu items; only the current one is checked and tabbable', () => {
    const refs = { current: [] as (HTMLButtonElement | null)[] }
    const html = renderToStaticMarkup(
      createElement(ThemeMenuItems, { id: 'm', theme: 'sepia', itemRefs: refs, onKeyDown: () => {}, onPick: () => {} }),
    )
    expect(html).toContain('role="menu"')
    expect(html.match(/role="menuitemradio"/g)).toHaveLength(5)
    for (const label of ['Match this device', 'Light', 'Dark', 'Sepia', 'OLED black']) expect(html).toContain(`<span>${label}</span>`)
    expect(html.match(/aria-checked="true"/g)).toHaveLength(1)
    expect(html.match(/tabindex="0"/g)).toHaveLength(1)
    expect(html).toMatch(/aria-checked="true"[^>]*>.*?<span>Sepia<\/span>/)
  })
})
