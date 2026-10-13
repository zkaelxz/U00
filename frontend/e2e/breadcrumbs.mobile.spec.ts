import { expect, test } from './fixtures'

// A title on a phone has no breadcrumb row: the 44px back arrow and the title say the same. A long title is cut off and
// nothing scrolls sideways. The row itself is covered at desktop width by breadcrumbs.spec.ts.

test('a title has no crumb row on a phone; the 44px back arrow leads to the Library and a long title fits', async ({ page }) => {
  await page.route('**/api/**/dramas/1', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const res = await route.fetch()
    const json = await res.json()
    await route.fulfill({ json: { ...json, title_en: 'Very long title '.repeat(40) } })
  })
  await page.goto('/#/drama/1/review')
  await expect(page.getByTestId('drama-title')).toContainText('Very long title')
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toHaveCount(0)
  const back = page.getByRole('link', { name: 'Back to Library' })
  expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  await back.tap()
  await expect(page).toHaveURL(/#\/library$/)
})
