import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { SourceLink } from './ExternalLink'

const render = (href: string | null | undefined, text?: string) =>
  renderToStaticMarkup(createElement(SourceLink, { href }, text))

describe('SourceLink', () => {
  it('opens an http(s) address in a new tab without leaking the opener', () => {
    const html = render('https://novel.example/book/7')
    expect(html).toContain('href="https://novel.example/book/7"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('Open original page')
  })

  it('takes a custom label', () => {
    expect(render('http://c.example/1', 'Open chapter page')).toContain('>Open chapter page<')
  })

  it.each(['javascript:alert(1)', 'data:text/html,<b>x</b>', 'file:///etc/passwd', 'ftp://x.example/a', '', null, undefined])(
    'renders nothing for %s',
    (href) => {
      expect(render(href)).toBe('')
    },
  )

  it('escapes the address instead of rendering it as markup', () => {
    const html = render('https://x.example/"><script>alert(1)</script>')
    expect(html).not.toContain('<script>')
  })
})
