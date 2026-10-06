import { expect, test } from './fixtures'

// The breadcrumb on a phone: 44px targets, long titles cut off, no sideways scroll.

test('crumbs inside a title are 44px tall and fit a 390px screen', async ({ page }) => {
  await page.route('**/api/**/dramas/1', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const res = await route.fetch()
    const json = await res.json()
    await route.fulfill({ json: { ...json, title_en: 'Very long title '.repeat(40) } })
  })
  await page.goto('/#/drama/1/review')
  const nav = page.getByRole('navigation', { name: 'Breadcrumb' })
  await expect(nav.getByRole('link').nth(1)).toContainText('Very long title')
  await expect(nav.locator('[aria-current="page"]')).toHaveText('Review')
  for (const link of await nav.getByRole('link').all()) {
    expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  }
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  await nav.getByRole('link', { name: 'Library' }).tap()
  await expect(page).toHaveURL(/#\/library$/)
})
