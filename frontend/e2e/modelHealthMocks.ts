import type { Page } from '@playwright/test'

// Model health mocks shared by model-health.spec.ts and
// model-health.mobile.spec.ts. The e2e API has no keys, so its real status
// only shows built-in defaults and tiers; these rows add saved presets that
// are retired / older so the warning and switch UI can be driven.

type Item = Record<string, unknown>

const item = (o: Item): Item => ({
  engine: 'claude', model: 'claude-sonnet-5', kind: 'default', where: 'claude built-in default', preset_id: null,
  status: 'current', message: 'claude-sonnet-5 (claude) is current.', replacement: null, note: null,
  listed_by_provider: null, severity: 0, can_switch: false, ...o,
})

export const RETIRED_PRESET = item({
  engine: 'deepseek', model: 'deepseek-chat', kind: 'preset', where: 'Preset: Old DeepSeek', preset_id: 9, status: 'retired',
  message: 'deepseek-chat (deepseek) has been retired by the provider (2026-07). Suggested replacement: deepseek-v4-flash.',
  replacement: 'deepseek-v4-flash', note: "DeepSeek's legacy alias, retired July 2026 (api-docs.deepseek.com).",
  severity: 3, can_switch: true,
})

export const OLDER_PRESET = item({
  model: 'claude-sonnet-4-6', kind: 'preset', where: 'Preset: Title A', preset_id: 7, status: 'legacy',
  message: 'claude-sonnet-4-6 (claude) is an older model that is still offered. Suggested replacement: claude-sonnet-5.',
  replacement: 'claude-sonnet-5', note: 'Previous generation, still offered.', severity: 1, can_switch: true,
})

const DEPRECATED_TIER = item({
  engine: 'gemini', model: 'gemini-old', kind: 'tier', where: 'Workflow tier: Balanced', status: 'deprecated',
  message: 'gemini-old (gemini) is deprecated and retires on 2026-12-01.', severity: 2,
  key: 'balanced', builtin_model: 'gemini-old', is_override: false, candidates: [],
})

export const CURRENT_ROWS = [
  item({}),
  item({ engine: 'gemini', model: 'gemini-flash-latest', where: 'gemini built-in default', message: 'gemini-flash-latest (gemini) is current.' }),
  item({ engine: 'ollama', model: 'gemma4:12b', where: 'ollama built-in default', status: 'unknown', message: "gemma4:12b (ollama) isn't in the registry or a provider check yet." }),
]

export const status = (o: { items?: Item[]; checked_at?: string | null; engines_checked?: Item } = {}) => {
  const items = o.items ?? [RETIRED_PRESET, OLDER_PRESET, DEPRECATED_TIER, ...CURRENT_ROWS]
  return {
    items,
    warnings: items.filter((i) => (i.severity as number) >= 2).length,
    checked_at: o.checked_at ?? null,
    engines_checked: o.engines_checked ?? {},
    registry_updated: '2026-09-29',
  }
}

/** Catch-all first (Playwright tries the newest route first): any unmocked non-GET /api call fails the test. */
export async function guardWrites(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}
