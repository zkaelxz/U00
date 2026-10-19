import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { SettingsKeyForm } from './SettingsKeyForm'
import { KEY_LINKS } from './settings/providerBalanceLinks'

const form = (engine: string) => renderToStaticMarkup(
  <SettingsKeyForm engine={engine} label={engine} configured={false} onResult={() => {}} />,
)

describe('SettingsKeyForm "Get a key"', () => {
  for (const [engine, url] of Object.entries(KEY_LINKS)) {
    it(`links ${engine} to its own site in a new tab without a referrer`, () => {
      const html = form(engine)
      expect(html).toContain(`href="${url}"`)
      expect(html).toContain('target="_blank"')
      expect(html).toContain('rel="noreferrer noopener"')
      expect(html).toContain('Get a key')
    })
  }

  it('shows no link for an engine without a known page', () => {
    expect(form('mistral')).not.toContain('Get a key')
  })
})
