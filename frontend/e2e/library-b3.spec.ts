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
