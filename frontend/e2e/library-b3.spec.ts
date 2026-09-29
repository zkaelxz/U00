import { expect, test } from '@playwright/test'

// Library parity batch B3 (inventory P06, L01, L04, L05, P03). Seed data:
// e2e/serve_seeded_api.py. Anything that creates a drama deletes it again,
// so the seeded three are unchanged afterwards.

test('New drama sends the Summary (P06)', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New drama' }).click()
  const sheet = page.getByRole('dialog', { name: 'New drama' })
  await sheet.getByLabel('English title').fill('E2E Summary Drama')
  await sheet.getByText('Credits, summary, series and preset').click()
  await sheet.getByLabel('Summary').fill('  Two cultivators solve a mystery.  ')
  const req = page.waitForRequest((r) => r.url().endsWith('/api/dramas') && r.method() === 'POST')
  await sheet.getByRole('button', { name: 'Create drama', exact: true }).click()
  expect((await req).postDataJSON()).toMatchObject({ title_en: 'E2E Summary Drama', summary: 'Two cultivators solve a mystery.' })
  await expect(page.getByTestId('created-notice')).toContainText('Created “E2E Summary Drama”.')

  const detail = page.getByRole('dialog', { name: 'E2E Summary Drama' })
  await detail.getByRole('button', { name: 'Delete drama…' }).click()
  await detail.getByLabel('Type DELETE to confirm').fill('DELETE')
  await detail.getByRole('button', { name: 'Delete permanently' }).click()
  await expect(page.getByRole('dialog', { name: 'E2E Summary Drama' })).toHaveCount(0)
})

const stats = {
  total_dramas: 5,
  by_status: { transcribed: 2, translated: 3 },
  by_media_type: { audio_drama: 4, novel: 1 },
  total_lines: 4210,
  translated_lines: 2875,
  usage: { input_tokens: 812000, output_tokens: 301000, cache_read_tokens: 243600, estimated_cost_usd: 3.47, call_count: 318 },
}

test('dashboard: API calls, cache-hit share, counts by status and type (L01)', async ({ page }) => {
  await page.route('**/api/library/stats', (r) => r.fulfill({ json: stats }))
  await page.goto('/')
  await expect(page.getByTestId('stats')).toHaveText(
    '5 dramas · 2875 of 4210 lines translated · $3.47 spent · 318 API calls · 30% cache hits',
  )
  const breakdown = page.getByTestId('stats-breakdown')
  await expect(breakdown).toContainText('By status: Transcribed 2 · Translated 3')
  await expect(breakdown).toContainText('By type: Audio drama 4 · Novel 1')
})

test('dashboard from the real API shows the call count', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('stats')).toContainText(/\d+ API calls?/)
  await expect(page.getByTestId('stats-breakdown')).toContainText('By type:')
})

const costs = {
  items: [
    { id: 1, title_en: 'Grandmaster of Demonic Cultivation', title_zh: '魔道祖师', translation_engine: 'claude', input_tokens: 540000, output_tokens: 201000, cache_read_tokens: 162000, estimated_cost_usd: 3.47, call_count: 212 },
    { id: 2, title_en: "Heaven Official's Blessing", title_zh: '天官赐福', translation_engine: null, input_tokens: 272000, output_tokens: 100000, cache_read_tokens: 0, estimated_cost_usd: 0, call_count: 1 },
  ],
}

test('Cost by drama: tokens, cache hits, and free engines labelled (L04)', async ({ page }) => {
  await page.route('**/api/library/costs', (r) => r.fulfill({ json: costs }))
  await page.goto('/')
  const tools = page.getByRole('region', { name: 'Library tools' })
  await tools.locator('summary', { hasText: 'Cost by drama' }).click()
  const rows = tools.getByRole('region', { name: 'Cost by drama' }).getByRole('listitem')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toContainText('$3.47')
  await expect(rows.nth(0)).toContainText('540k in · 201k out · 30% cache hits · 212 calls')
  await expect(rows.nth(1)).toContainText('$0.00 (free)')
  await expect(rows.nth(1)).toContainText('272k in · 100k out · 0% cache hits · 1 call')
})
