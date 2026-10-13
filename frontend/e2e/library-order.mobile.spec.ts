import { expect, test } from './fixtures'
import { hitHeight, installHitArea } from './hitArea'
import { cardTitles, mockLibraryOrder } from './libraryOrderMocks'

// Phone (390x844): sort and group controls fit, are touch sized, and the groups collapse.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  await mockLibraryOrder(page)
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('5 titles')
})

test('sort, group and collapse on a phone', async ({ page }) => {
  await page.getByLabel('Sort by').selectOption('date-asc')
  expect(await cardTitles(page)).toEqual(['Solo', 'Early', 'Mid', 'Zeta', 'Loose'])
  await page.getByLabel('Group by').selectOption('series')
  const groups = page.getByTestId('title-group')
  await expect(groups).toHaveCount(3)
  const head = groups.nth(1).getByRole('button', { name: /Saga/ })
  expect(await hitHeight(head)).toBeGreaterThanOrEqual(44)
  await head.click()
  await expect(groups.nth(1).locator('.drama-card-title')).toHaveCount(0)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})

test('the series page sort control fits a phone', async ({ page }) => {
  await page.route('**/api/series/7', (r) => r.fulfill({
    json: {
      id: 7, name: 'Saga', is_private: false, owned_by_me: true, can_edit: true, has_instructions: false,
      glossary_term_count: 0, character_count: 0,
      titles: [
        { id: 1, title_en: 'Later', title_zh: null, media_type: 'anime', status: null, episode_number: null, release_date: '2025-01-01', line_count: 1 },
        { id: 2, title_en: 'Sooner', title_zh: null, media_type: 'anime', status: null, episode_number: null, release_date: '2024-01-01', line_count: 1 },
      ],
    },
  }))
  await page.goto('/#/series/7')
  const sort = page.getByLabel('Sort titles by')
  await expect(sort).toHaveValue('date-asc')
  expect(await hitHeight(sort)).toBeGreaterThanOrEqual(44)
  await expect(page.locator('.series-titles .table-title').first()).toContainText('Sooner')
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
