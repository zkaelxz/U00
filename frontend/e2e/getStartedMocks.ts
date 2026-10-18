import type { Page } from '@playwright/test'

export const STATS = (total: number) => ({
  total_dramas: total, by_status: {}, by_media_type: {}, total_lines: 0, translated_lines: 0,
  usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, estimated_cost_usd: 0, call_count: 0 },
})
const ENGINE = (name: string, key_configured: boolean, free: boolean) =>
  ({ name, label: `${name}.`, free, models: null, key_configured })

const NO_ENGINE = { faster_whisper: false, ollama_reachable: false, key_claude: false, key_deepseek: false, key_gemini: false, key_openai: false, key_groq: false }

export async function mockFirstRun(page: Page, total = 0) {
  await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: NO_ENGINE }))
  await page.route('**/api/library/stats', (r) => r.fulfill({ json: STATS(total) }))
  await page.route('**/api/translate/engines', (r) => r.fulfill({
    json: {
      items: [ENGINE('claude', false, false), ENGINE('gemini', false, false), ENGINE('ollama', true, true), ENGINE('deepseek', true, true)],
      default_engine: 'claude',
    },
  }))
}
