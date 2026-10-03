import { expect, test, type Page } from '@playwright/test'

// Phone (390x844): the Library parity batch B3 additions (dashboard lines,
// cost rows, series view, New drama summary) fit the width and keep 44px
// touch targets.

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

const ref = (id: number, title_en: string, media_type: string) =>
  ({ id, title_en, title_zh: null, media_type, status: 'translated', updated_at: null })

test('dashboard, costs and series fit a phone', async ({ page }) => {
  await page.route('**/api/library/costs', (r) => r.fulfill({
    json: { items: [{ id: 1, title_en: 'A rather long drama title that wraps on a phone', title_zh: null, translation_engine: 'claude', input_tokens: 1_250_000, output_tokens: 540_000, cache_read_tokens: 300_000, estimated_cost_usd: 12.5, call_count: 999 }] },
  }))
  await page.route('**/api/library/series', (r) => r.fulfill({
    json: { items: [{ id: 1, name: 'A series with a long name', character_count: 9, glossary_term_count: 20, dramas: [ref(1, 'A rather long drama title that wraps on a phone', 'audio_drama'), ref(2, 'Second', 'manhua')] }] },
  }))
  await page.goto('/#/library-tools')
  await expect(page.getByRole('heading', { name: 'Library tools' })).toBeVisible()
  const tools = page.getByRole('region', { name: 'Library tools' })
  await tools.locator('summary', { hasText: 'Cost by drama' }).click()
  const open = tools.getByRole('link', { name: 'Open Second' })
  await expect(open).toBeVisible()
  expect((await open.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  const cost = tools.getByRole('region', { name: 'Cost by drama' }).getByRole('link').first()
  expect((await cost.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await expectNoHorizontalOverflow(page)
})

test('New drama: Summary and Create and auto-fill fit a phone', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New drama' }).click()
  const sheet = page.getByRole('dialog', { name: 'New drama' })
  await sheet.getByText('Credits, summary, series and preset').click()
  const summary = sheet.getByRole('textbox', { name: 'Summary' })
  await expect(summary).toBeVisible()
  expect((await summary.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  const autofill = sheet.getByRole('button', { name: 'Create and auto-fill' })
  expect((await autofill.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await expectNoHorizontalOverflow(page)
})
