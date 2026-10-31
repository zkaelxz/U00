// Static, public billing/usage pages per key provider. Kept in the frontend so
// no API response carries a URL; nothing here is user-supplied and no key is
// ever placed in a link. 'none' means the provider has no page worth linking.
type BalanceLink = { url: string } | 'none'

export const BALANCE_LINKS: Record<string, BalanceLink> = {
  claude: { url: 'https://platform.claude.com/settings/billing' },
  openai: { url: 'https://platform.openai.com/settings/organization/billing/overview' },
  gemini: { url: 'https://aistudio.google.com/usage' },
  deepseek: { url: 'https://platform.deepseek.com/usage' },
  groq: { url: 'https://console.groq.com/settings/billing' },
  // A Hugging Face token only downloads models; there is no balance to check.
  hf_token: 'none',
}

export function balanceLinkFor(engine: string): string | null {
  const link = BALANCE_LINKS[engine]
  return link && link !== 'none' ? link.url : null
}

// Where a person creates a key, from each provider's own site. Same rules as above: static, https, no query.
export const KEY_LINKS: Record<string, string> = {
  claude: 'https://platform.claude.com/settings/keys',
  openai: 'https://platform.openai.com/api-keys',
  gemini: 'https://aistudio.google.com/apikey',
  deepseek: 'https://platform.deepseek.com/api_keys',
  groq: 'https://console.groq.com/keys',
  hf_token: 'https://huggingface.co/settings/tokens',
}

export const keyLinkFor = (engine: string): string | null => KEY_LINKS[engine] ?? null
