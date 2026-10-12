import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { PageShell } from './PageShell'
import { PAGE_WIDTHS, pageShellClass } from './pageShellWidth'

describe('pageShellClass', () => {
  it('maps each of the three presets to its own class, and nothing else', () => {
    expect(PAGE_WIDTHS).toEqual(['narrow', 'standard', 'wide'])
    expect(pageShellClass('narrow')).toBe('page-shell page-shell-narrow')
    expect(pageShellClass('standard')).toBe('page-shell page-shell-standard')
    expect(pageShellClass('wide')).toBe('page-shell page-shell-wide')
  })

  it('appends a page class after the preset', () => {
    expect(pageShellClass('wide', 'jobs-page')).toBe('page-shell page-shell-wide jobs-page')
  })
})

describe('PageShell', () => {
  it('renders the shared header, then tabs, then the content', () => {
    const html = renderToStaticMarkup(
      <PageShell width="narrow" title="Settings" meta="Engines" actions={<b>act</b>} back={<nav>crumbs</nav>} tabs={<i>tabs</i>}>
        <p>body</p>
      </PageShell>,
    )
    expect(html).toContain('data-page-shell="narrow"')
    expect(html).toContain('aria-label="Settings"')
    expect(html).toContain('<h2 class="page-title">Settings</h2>')
    expect(html).toContain('<p class="page-meta">Engines</p>')
    expect(html.indexOf('crumbs')).toBeLessThan(html.indexOf('page-head'))
    expect(html.indexOf('page-head')).toBeLessThan(html.indexOf('tabs'))
    expect(html.indexOf('tabs')).toBeLessThan(html.indexOf('data-page-body'))
  })

  it('leaves out the tabs and actions slots when a page has none', () => {
    const html = renderToStaticMarkup(<PageShell width="wide" title="Jobs">x</PageShell>)
    expect(html).not.toContain('data-page-tabs')
    expect(html).not.toContain('class="actions"')
  })
})
