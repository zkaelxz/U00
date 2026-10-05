import { describe, expect, it } from 'vitest'
import { keyRows } from '../settingsKeys'
import { BALANCE_LINKS, balanceLinkFor } from './providerBalanceLinks'

// Every provider that has a write-only key field in Settings.
const KEY_FIELD_ENGINES = keyRows({
  claude: false, deepseek: false, gemini: false, openai: false, groq: false, hf_token: false,
}).filter((r) => r.writable)

describe('provider balance links', () => {
  it('every key field has a link or an explicit none', () => {
    expect(KEY_FIELD_ENGINES.length).toBeGreaterThan(0)
    for (const { engine } of KEY_FIELD_ENGINES) expect(BALANCE_LINKS[engine], engine).toBeDefined()
  })

  it('links are plain https pages with no query, fragment or credentials', () => {
    for (const link of Object.values(BALANCE_LINKS)) {
      if (link === 'none') continue
      const u = new URL(link.url)
      expect(u.protocol).toBe('https:')
      expect(u.search + u.hash + u.username + u.password).toBe('')
    }
  })

  it('none and unknown providers render no link', () => {
    expect(balanceLinkFor('hf_token')).toBeNull()
    expect(balanceLinkFor('mistral')).toBeNull()
    expect(balanceLinkFor('claude')).toContain('https://')
  })
})
