import { expect, test } from '@playwright/test'

// Library parity batch B3 (inventory P06, L01, L04, L05, P03). Seed data:
// e2e/serve_seeded_api.py. Anything that creates a drama deletes it again,
// so the seeded three are unchanged afterwards.

test('New title sends the Summary (P06)', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New title' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await sheet.getByLabel('English title').fill('E2E Summary Title')
  await sheet.getByText('Credits, summary, series and preset').click()
  await sheet.getByRole('textbox', { name: 'Summary' }).fill('  Two cultivators solve a mystery.  ')
  const req = page.waitForRequest((r) => r.url().endsWith('/api/dramas') && r.method() === 'POST')
  await sheet.getByRole('button', { name: 'Create title', exact: true }).click()
  expect((await req).postDataJSON()).toMatchObject({ title_en: 'E2E Summary Title', summary: 'Two cultivators solve a mystery.' })
  await expect(page).toHaveURL(/#\/drama\/\d+$/)
  await page.getByRole('link', { name: 'Back to Library' }).click()
  await page.getByRole('button', { name: 'Details: E2E Summary Title' }).click()
  const detail = page.getByRole('dialog', { name: 'E2E Summary Title' })
  await detail.getByRole('button', { name: 'Delete title…' }).click()
  await detail.getByLabel('Type DELETE to confirm').fill('DELETE')
  await detail.getByRole('button', { name: 'Delete permanently' }).click()
  await expect(page.getByRole('dialog', { name: 'E2E Summary Title' })).toHaveCount(0)
})

const stats = {
  total_dramas: 5,
  by_status: { transcribed: 2, translated: 3 },
  by_media_type: { audio_drama: 4, novel: 1 },
  total_lines: 4210,
  translated_lines: 2875,
  usage: { input_tokens: 812000, output_tokens: 301000, cache_read_tokens: 243600, estimated_cost_usd: 3.47, call_count: 318 },
}

test('dashboard: API calls, cache-hit share, header stats line (L01)', async ({ page }) => {
  await page.route('**/api/library/stats', (r) => r.fulfill({ json: stats }))
  await page.goto('/')
  await expect(page.getByTestId('stats')).toHaveText('5 titles · 2,875 of 4,210 lines · 1,335 left')
  await page.getByText('$3.47 spent').click()
  await expect(page.getByTestId('stats-usage')).toHaveText('318 API calls · 30% cache hits')
  await expect(page.getByTestId('stats-breakdown')).toHaveCount(0)
})

// The e2e server's library is shared across specs, and lab-benchmark logs
// real usage into it, so the fold's presence has to follow the API payload
// rather than assume a pristine library.
test('dashboard from the real API shows the line and a usage fold only when usage was logged', async ({ page }) => {
  const reply = page.waitForResponse((r) => r.url().endsWith('/api/library/stats'))
  await page.goto('/')
  const { usage } = await (await reply).json()
  await expect(page.getByTestId('stats')).toContainText(/\d+ titles? · [\d,]+ of [\d,]+ lines/)
  await expect(page.locator('.stats-usage')).toHaveCount(usage.call_count || usage.estimated_cost_usd ? 1 : 0)
})

test('no usage fold when nothing was spent or called', async ({ page }) => {
  const idle = { ...stats, usage: { ...stats.usage, call_count: 0, estimated_cost_usd: 0 } }
  await page.route('**/api/library/stats', (r) => r.fulfill({ json: idle }))
  await page.goto('/')
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.locator('.stats-usage')).toHaveCount(0)
})

const costs = {
  items: [
    { id: 1, title_en: 'Grandmaster of Demonic Cultivation', title_zh: '魔道祖师', translation_engine: 'claude', input_tokens: 540000, output_tokens: 201000, cache_read_tokens: 162000, estimated_cost_usd: 3.47, call_count: 212 },
    { id: 2, title_en: "Heaven Official's Blessing", title_zh: '天官赐福', translation_engine: null, input_tokens: 272000, output_tokens: 100000, cache_read_tokens: 0, estimated_cost_usd: 0, call_count: 1 },
  ],
}

test('Cost by drama: tokens, cache hits, and free engines labelled (L04)', async ({ page }) => {
  await page.route('**/api/library/costs', (r) => r.fulfill({ json: costs }))
  await page.goto('/#/library-tools')
  const tools = page.getByRole('region', { name: 'Library tools' })
  await tools.locator('summary', { hasText: 'Cost by title' }).click()
  const rows = tools.getByRole('region', { name: 'Cost by title' }).getByRole('listitem')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toContainText('$3.47')
  await expect(rows.nth(0)).toContainText('540k in · 201k out · 30% cache hits · 212 calls')
  await expect(rows.nth(1)).toContainText('$0.00 (free)')
  await expect(rows.nth(1)).toContainText('272k in · 100k out · 0% cache hits · 1 call')
})

const ref = (id: number, title_en: string, media_type: string | null, status: string) =>
  ({ id, title_en, title_zh: null, media_type, status, updated_at: '2026-09-29T12:00:00' })

const series = {
  items: [
    {
      id: 1, name: 'Mo Dao Zu Shi', character_count: 14, glossary_term_count: 1,
      dramas: [ref(1, 'Grandmaster of Demonic Cultivation', null, 'translated'), ref(4, 'MDZS (manhua)', 'manhua', 'transcribed')],
    },
    { id: 2, name: 'Lonely Series', character_count: 3, glossary_term_count: 5, dramas: [ref(2, 'Solo', 'audio_drama', 'new')] },
  ],
}

test('Series view: only 2+ titles, types, shared counts, Open (L05)', async ({ page }) => {
  await page.route('**/api/library/series', (r) => r.fulfill({ json: series }))
  await page.goto('/#/library-tools')
  const tools = page.getByRole('region', { name: 'Library tools' })
  const panel = tools.getByRole('region', { name: 'Series' })
  await expect(panel).toContainText('Mo Dao Zu Shi')
  await expect(panel).not.toContainText('Lonely Series')
  await expect(panel).toContainText('Audio drama 1 · Manhua 1')
  await expect(panel).toContainText('14 shared characters · 1 glossary term')
  const dramas = panel.getByRole('list', { name: 'Titles in Mo Dao Zu Shi' }).getByRole('listitem')
  await expect(titles).toHaveCount(2)
  await expect(dramas.nth(1)).toContainText('Transcribed')
  await expect(panel.getByRole('link', { name: 'Open MDZS (manhua)' })).toHaveAttribute('href', '#/drama/4')
})

test('Series fold is hidden when no series has 2+ titles (L05)', async ({ page }) => {
  await page.route('**/api/library/series', (r) =>
    r.fulfill({ json: { items: [series.items[1]] } }))
  await page.goto('/#/library-tools')
  const tools = page.getByRole('region', { name: 'Library tools' })
  await expect(tools.locator('summary', { hasText: 'Cost by title' }).or(tools.locator('summary', { hasText: 'Backup' })).first()).toBeVisible()
  await expect(tools.locator('summary', { hasText: /^Series/ })).toHaveCount(0)
})

test('Create and auto-fill lands on the Source stage with Auto-fill open (P03)', async ({ page, request }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New title' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await sheet.getByLabel('English title').fill('E2E Autofill Title')
  const created = page.waitForResponse((r) => r.url().endsWith('/api/dramas') && r.request().method() === 'POST')
  await sheet.getByRole('button', { name: 'Create and auto-fill' }).click()
  const { id } = (await (await created).json()) as { id: number }
  try {
    // The flag is read, then dropped from the address.
    await expect(page).toHaveURL(new RegExp(`#/drama/${id}/source$`))
    const panel = page.getByRole('region', { name: 'Fill in details' })
    await expect(panel.getByRole('textbox', { name: 'Listing URL' })).toBeVisible()
    await expect(panel.getByRole('textbox', { name: 'Listing URL' })).toBeFocused()
  } finally {
    await request.delete(`/api/dramas/${id}?confirm=true&confirm_text=DELETE`)
  }
})
