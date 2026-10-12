import { expect, test } from './fixtures'
import { cardTitles, mockLibraryOrder } from './libraryOrderMocks'

test.beforeEach(async ({ page }) => {
  await mockLibraryOrder(page)
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('5 titles')
})

test('sorts by stream / release date, undated last, and remembers the choice', async ({ page }) => {
  expect(await cardTitles(page)).toEqual(['Zeta', 'Mid', 'Early', 'Loose', 'Solo'])
  await page.getByLabel('Sort by').selectOption('date-asc')
  expect(await cardTitles(page)).toEqual(['Solo', 'Early', 'Mid', 'Zeta', 'Loose'])
  await expect(page.getByTestId('card-release-date').first()).toHaveText('2022-12-31')
  await page.getByLabel('Sort by').selectOption('date-desc')
  expect(await cardTitles(page)).toEqual(['Zeta', 'Mid', 'Early', 'Solo', 'Loose'])
  await page.reload()
  await expect(page.getByLabel('Sort by')).toHaveValue('date-desc')
  await expect(page.getByTestId('drama-count')).toHaveText('5 titles')
  expect(await cardTitles(page)).toEqual(['Zeta', 'Mid', 'Early', 'Solo', 'Loose'])
})

test('List view shows the date as a column', async ({ page }) => {
  await page.getByRole('radio', { name: 'List' }).check()
  await expect(page.getByRole('columnheader', { name: 'Released' })).toBeVisible()
  await expect(page.getByRole('row', { name: /Solo/ })).toContainText('2022-12-31')
})

test('groups by series: oldest first inside, No series last, collapse is remembered', async ({ page }) => {
  await page.getByLabel('Group by').selectOption('series')
  const groups = page.getByTestId('title-group')
  await expect(groups).toHaveCount(3)
  await expect(groups.nth(0).locator('.title-group-name')).toHaveText('Alpha')
  await expect(groups.nth(1).locator('.title-group-name')).toHaveText('Saga')
  await expect(groups.nth(1)).toContainText('2 titles · Anime ×1 · Manga ×1')
  await expect(groups.nth(2).locator('.title-group-name')).toHaveText('No series')
  // Oldest first by date inside a series ("watch in order"); the undated title is last in No series.
  expect(await groups.nth(1).locator('.drama-card-title').allTextContents()).toEqual(['Mid', 'Zeta'])
  expect(await groups.nth(2).locator('.drama-card-title').allTextContents()).toEqual(['Solo', 'Loose'])

  await groups.nth(1).getByRole('button', { name: /Saga/ }).click()
  await expect(groups.nth(1).locator('.drama-card-title')).toHaveCount(0)
  await page.reload()
  await expect(page.getByLabel('Group by')).toHaveValue('series')
  await expect(page.getByTestId('title-group').nth(1).locator('.drama-card-title')).toHaveCount(0)
  await expect(page.getByTestId('title-group').nth(0).locator('.drama-card-title')).toHaveCount(1)
})

test('search applies inside groups and an empty group disappears', async ({ page }) => {
  await page.getByLabel('Group by').selectOption('series')
  await page.getByLabel('Search title or summary').fill('zeta')
  await expect(page.getByTestId('drama-count')).toHaveText('1 title')
  const groups = page.getByTestId('title-group')
  await expect(groups).toHaveCount(1)
  await expect(groups.first().locator('.title-group-name')).toHaveText('Saga')
  await expect(page.getByRole('link', { name: 'Open series Saga' })).toHaveAttribute('href', '#/series/7')
})

test('groups by media type and by status', async ({ page }) => {
  await page.getByLabel('Group by').selectOption('media')
  await expect(page.getByTestId('title-group').locator('.title-group-name')).toHaveText(['Anime', 'Manga', 'Novel'])
  await page.getByLabel('Group by').selectOption('status')
  await expect(page.getByTestId('title-group')).toHaveCount(1)
})

test('select all in a grouped Library never takes titles hidden in a collapsed group', async ({ page }) => {
  await page.getByLabel('Group by').selectOption('series')
  const groups = page.getByTestId('title-group')
  await expect(groups).toHaveCount(3)
  await groups.nth(1).getByRole('button', { name: /Saga/ }).click()
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  await page.getByRole('button', { name: 'Select all', exact: true }).click()
  const bar = page.getByRole('region', { name: 'Selection' })
  // Alpha (1) + No series (2); Saga's two titles are collapsed away.
  await expect(bar.getByTestId('selected-count')).toHaveText('3 selected')
  await bar.getByRole('button', { name: 'Delete…' }).click()
  await expect(bar).toContainText('Permanently deletes 3 titles with their lines and files. No undo.')
  await expect(bar.getByRole('button', { name: 'Delete 3' })).toBeVisible()
})

test('a group header selects only its own rows, and collapsing drops its titles from the selection', async ({ page }) => {
  await page.getByLabel('Group by').selectOption('series')
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Select all in Saga' }).check()
  const bar = page.getByRole('region', { name: 'Selection' })
  await expect(bar.getByTestId('selected-count')).toHaveText('2 selected')
  await page.getByTestId('title-group').nth(1).getByRole('button', { name: /Saga/ }).click()
  await expect(page.getByTestId('selected-count')).toHaveText('0 selected')
})

test('when the series list fails, grouping by series falls back to the plain list with a Retry', async ({ page }) => {
  let fail = true
  await page.route('**/api/series', (r) =>
    fail ? r.fulfill({ status: 500, json: { error: { code: 'x', message: 'down' } } }) : r.fallback())
  await page.reload()
  await expect(page.getByTestId('drama-count')).toHaveText('5 titles')
  await page.getByLabel('Group by').selectOption('series')
  await expect(page.getByTestId('series-load-failed')).toContainText('Series could not be loaded; showing the plain list.')
  await expect(page.getByLabel('Group by')).toHaveValue('none')
  await expect(page.getByTestId('title-group')).toHaveCount(0)
  fail = false
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByTestId('title-group')).toHaveCount(3)
  await expect(page.getByLabel('Group by')).toHaveValue('series')
  await expect(page.getByTestId('series-load-failed')).toHaveCount(0)
})
