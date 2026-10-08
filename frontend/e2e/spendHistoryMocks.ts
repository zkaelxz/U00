import type { Page } from '@playwright/test'

const row = (label: string, cost_usd: number, calls: number, extra = {}) => ({
  key: null, label, is_other: false, cost_usd, calls, input_tokens: 1000, output_tokens: 500, cache_read_tokens: 0, ...extra,
})
const month = (m: string, cost_usd: number, calls: number, extra = {}) => ({
  month: m, cost_usd, calls, input_tokens: 1000, output_tokens: 500, cache_read_tokens: 0, overlaps_reset: false, cap_counted_usd: null, ...extra,
})

export const OCT = {
  months: [month('2026-10', 12.5, 40, { overlaps_reset: true, cap_counted_usd: 7.25 }), month('2026-09', 3.1, 9)],
  selected_month: '2026-10',
  cap_reset_at: '2026-10-05T00:00:00',
  max_months: 36,
  by_operation: [row('Translate', 10, 30, { key: 'translate' }), row('Benchmark judge', 2.5, 10, { key: 'benchmark_judge' })],
  by_engine_model: [row('claude / claude-sonnet-5-5', 12.5, 40)],
  by_title: [row('Alpha', 9, 30), row('deleted title', 3.5, 10)],
}
export const SEP = {
  ...OCT,
  selected_month: '2026-09',
  by_operation: [row('Qa', 3.1, 9, { key: 'qa' })],
  by_engine_model: [row('openai / gpt-x', 3.1, 9)],
  by_title: [row('Alpha', 3.1, 9)],
}

export async function mockSpendHistory(page: Page, data: unknown = OCT) {
  await page.route('**/api/settings/spend-history**', (route) => {
    const month = new URL(route.request().url()).searchParams.get('month')
    return route.fulfill({ json: month === '2026-09' ? SEP : data })
  })
}
