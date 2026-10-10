import { expect, test, type Page } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone (390x844): the More filters fold and the Continue shelf fit the width
// and keep 44px touch targets.

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function heightOf(page: Page, selector: string) {
  return (await hitHeight(page.locator(selector).first()))
}

test('More filters and Continue fit a phone', async ({ page }) => {
  await page.route('**/api/library/continue', (r) =>
    r.fulfill({
      json: {
        items: [1, 2, 3].map((id) => ({
          drama_id: id, title_en: `A rather long title title number ${id}`, title_zh: null, percent_complete: 30 + id,
          last_page: id, last_accessed_at: `2026-09-29T12:0${id}:00`, has_cover_art: false,
        })),
      },
    }),
  )
  await page.route('**/api/library/recent', (r) => r.fulfill({ json: { items: [] } }))
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  // Phone: one item, then More.
  await expect(shelf.getByRole('listitem')).toHaveCount(1)
  await shelf.getByRole('button', { name: 'More (2)' }).click()
  await expect(shelf.getByRole('listitem')).toHaveCount(3)
  await expect(shelf.getByRole('button', { name: 'Less' })).toBeVisible()
  await shelf.getByRole('button', { name: 'Less' }).click()
  await page.getByText('More filters', { exact: true }).click()
  await expect(page.getByRole('checkbox', { name: 'wuxia' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  expect(await heightOf(page, '.more-filters > summary')).toBeGreaterThanOrEqual(44)
  expect(await heightOf(page, '.tag-filter .check')).toBeGreaterThanOrEqual(44)
  expect(await heightOf(page, '.continue-item .btn')).toBeGreaterThanOrEqual(44)
})
