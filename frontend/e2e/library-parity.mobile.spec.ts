import { expect, test, type Page } from '@playwright/test'

// Phone (390x844): the More filters fold and the Continue strip fit the width
// and keep 44px touch targets.

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function heightOf(page: Page, selector: string) {
  return (await page.locator(selector).first().boundingBox())?.height ?? 0
}

test('More filters and Continue reading fit a phone', async ({ page }) => {
  await page.route('**/api/library/continue', (r) =>
    r.fulfill({
      json: {
        items: [1, 2, 3].map((id) => ({
          drama_id: id, title_en: `A rather long drama title number ${id}`, title_zh: null, percent_complete: 30 + id,
          last_page: id, last_accessed_at: '2026-09-29T12:00:00', has_cover_art: false,
        })),
      },
    }),
  )
  await page.goto('/')
  await expect(page.getByRole('region', { name: 'Continue reading' })).toBeVisible()
  await page.getByText('More filters', { exact: true }).click()
  await expect(page.getByRole('checkbox', { name: 'wuxia' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  expect(await heightOf(page, '.more-filters > summary')).toBeGreaterThanOrEqual(44)
  expect(await heightOf(page, '.tag-filter .check')).toBeGreaterThanOrEqual(44)
  expect(await heightOf(page, 'a.continue-resume')).toBeGreaterThanOrEqual(44)
})
