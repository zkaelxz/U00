import { expect, test } from './fixtures'
import { hitHeight, installHitArea } from './hitArea'
import { mockLibraryOrder } from './libraryOrderMocks'

// Phone (390x844): the Library's first title is on screen, the filters sit in a sheet, the new title
// sheet keeps Create and Discard draft in reach, and small controls keep a 44px hit area.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  await mockLibraryOrder(page)
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('5 titles')
})

test('the first title card is above the fold and the filters open in a sheet', async ({ page }) => {
  const viewport = page.viewportSize()!
  const card = page.locator('.drama-card').first()
  const box = (await card.boundingBox())!
  expect(box.y + box.height, 'first card inside the viewport').toBeLessThanOrEqual(viewport.height)
  await expect(page.getByLabel('Sort by')).toBeHidden()
  await page.getByRole('button', { name: 'Filters' }).tap()
  const sheet = page.getByRole('dialog', { name: 'Filters' })
  for (const name of ['Status', 'Quick filter', 'Sort by', 'Group by']) await expect(sheet.getByLabel(name)).toBeVisible()
  await sheet.getByLabel('Status').selectOption('translated')
  await sheet.getByRole('button', { name: 'Close' }).tap()
  await expect(page.getByRole('button', { name: 'Filters (1)' })).toBeVisible()
})

test('New title keeps Create title and Discard draft on screen', async ({ page }) => {
  await page.getByRole('button', { name: 'New title' }).first().tap()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  const viewport = page.viewportSize()!
  for (const name of ['Create title', 'Discard draft']) {
    const box = (await sheet.getByRole('button', { name }).boundingBox())!
    expect(box.y + box.height, `${name} inside the viewport`).toBeLessThanOrEqual(viewport.height)
  }
})

test('help and small buttons keep a 44px hit area', async ({ page }) => {
  await page.getByRole('button', { name: 'New title' }).first().tap()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  expect(await hitHeight(sheet.locator('.field-help-btn').first())).toBeGreaterThanOrEqual(44)
  await sheet.getByRole('button', { name: 'Close' }).tap()
  for (const name of ['Read', 'Details']) {
    expect(await hitHeight(page.getByRole('link', { name: new RegExp(`^${name}`) }).or(page.getByRole('button', { name: new RegExp(`^${name}`) })).first())).toBeGreaterThanOrEqual(44)
  }
  expect(await hitHeight(page.getByRole('button', { name: 'Select', exact: true }))).toBeGreaterThanOrEqual(44)
})
