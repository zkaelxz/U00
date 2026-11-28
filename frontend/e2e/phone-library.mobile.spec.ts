import { expect, test } from './fixtures'
import { hitHeight, installHitArea } from './hitArea'
import { DRAMAS, mockLibraryOrder } from './libraryOrderMocks'

// Phone (390x844): the Library's first title is on screen, the filters sit in a sheet, the new title
// sheet keeps Create and Clear and close in reach, and small controls keep a 44px hit area.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  await mockLibraryOrder(page)
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('5 titles')
})

test('the first title card starts above the fold and the filters open in a sheet', async ({ page }) => {
  const viewport = page.viewportSize()!
  const card = page.locator('.drama-card').first()
  const box = (await card.boundingBox())!
  // Two Continue tiles sit above the list, so the card itself starts on the first screen but may run past it.
  expect(box.y, 'first card starts inside the viewport').toBeLessThan(viewport.height)
  await expect(page.getByLabel('Sort by')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Favorite' })).toBeVisible()
  await page.getByRole('button', { name: 'Filters' }).tap()
  const sheet = page.getByRole('dialog', { name: 'Filters' })
  for (const name of ['Status', 'Group by']) await expect(sheet.getByLabel(name)).toBeVisible()
  await sheet.getByLabel('Status').selectOption('translated')
  await sheet.getByRole('button', { name: 'Close' }).tap()
  await expect(page.getByRole('button', { name: 'Filters (1)' })).toBeVisible()
})

test('New title, opened from the Add page, keeps Create title and Clear and close on screen', async ({ page }) => {
  await page.getByRole('navigation', { name: 'Quick' }).getByRole('link', { name: 'Find & Add', exact: true }).tap()
  await page.getByRole('button', { name: 'Add a title by hand' }).tap()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  const viewport = page.viewportSize()!
  for (const name of ['Create title', 'Clear and close']) {
    const box = (await sheet.getByRole('button', { name }).boundingBox())!
    expect(box.y + box.height, `${name} inside the viewport`).toBeLessThanOrEqual(viewport.height)
  }
})

test('help and small buttons keep a 44px hit area', async ({ page }) => {
  await page.getByRole('navigation', { name: 'Quick' }).getByRole('link', { name: 'Find & Add', exact: true }).tap()
  await page.getByRole('button', { name: 'Add a title by hand' }).tap()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  expect(await hitHeight(sheet.locator('.field-help-btn').first())).toBeGreaterThanOrEqual(44)
  await sheet.getByRole('button', { name: 'Close', exact: true }).tap()
  for (const name of ['Read', 'Details']) {
    expect(await hitHeight(page.getByRole('link', { name: new RegExp(`^${name}`) }).or(page.getByRole('button', { name: new RegExp(`^${name}`) })).first())).toBeGreaterThanOrEqual(44)
  }
  expect(await hitHeight(page.getByRole('button', { name: 'Select', exact: true }))).toBeGreaterThanOrEqual(44)
})

test('300 titles show 20 cards, then 20 more per Show more', async ({ page }) => {
  const many = Array.from({ length: 300 }, (_, i) => ({ ...DRAMAS[0], id: 1000 + i, title_en: `Title ${i}`, series_id: null }))
  await page.route('**/api/library/dramas**', (route) => {
    if (new URL(route.request().url()).pathname !== '/api/library/dramas') return route.fallback()
    return route.fulfill({ json: { items: many, count: many.length } })
  })
  await page.goto('/')
  await expect(page.locator('.drama-card')).toHaveCount(20)
  await page.getByTestId('library-more').getByRole('button', { name: 'Show more' }).tap()
  await expect(page.locator('.drama-card')).toHaveCount(40)
  await expect(page.getByTestId('library-more')).toContainText('Showing 40 of 300')
})
