import { expect, test } from './fixtures'

// The breadcrumb inside a title, against the seeded API.

const crumbs = (page: import('@playwright/test').Page) => page.getByRole('navigation', { name: 'Breadcrumb' })

test('a title shows Library > title > stage, and Library goes back', async ({ page }) => {
  await page.goto('/#/drama/1/review')
  // The heading reads "Drama #1" until the title loads; reading it earlier pins the placeholder.
  await expect(page.getByTestId('drama-title')).not.toHaveText(/^Title #\d+$/)
  const title = (await page.getByTestId('drama-title').textContent())!.trim()
  await expect(crumbs(page).getByRole('link')).toHaveText(['Library', title])
  await expect(crumbs(page).locator('[aria-current="page"]')).toHaveText('Review')
  await expect(crumbs(page).locator('[aria-current]')).toHaveCount(1)
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page')

  await crumbs(page).getByRole('link', { name: 'Library' }).click()
  await expect(page).toHaveURL(/#\/library$/)
  await expect(crumbs(page)).toHaveCount(0)
})

test('the browser Back button still returns to the previous page', async ({ page }) => {
  await page.goto('/#/sources')
  await page.goto('/#/drama/1/review')
  await page.goBack()
  await expect(page).toHaveURL(/#\/sources$/)
})

test('top-level pages and the reader', async ({ page }) => {
  await page.goto('/#/sources')
  await expect(crumbs(page)).toHaveCount(0)
  await page.goto('/#/library-tools')
  await expect(crumbs(page).locator('[aria-current="page"]')).toHaveText('Library tools')
  await page.goto('/#/read/1')
  await expect(crumbs(page).locator('[aria-current="page"]')).toHaveText('Reader')
  await expect(crumbs(page).getByRole('link', { name: 'Library' })).toHaveAttribute('href', '#/library')
})

test('a very long title is cut off, not scrolled', async ({ page }) => {
  await page.route('**/api/**/dramas/1', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const res = await route.fetch()
    const json = await res.json()
    await route.fulfill({ json: { ...json, title_en: 'Very long title '.repeat(40) } })
  })
  await page.goto('/#/drama/1/review')
  await expect(crumbs(page).getByRole('link').nth(1)).toContainText('Very long title')
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
