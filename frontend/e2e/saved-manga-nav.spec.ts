import { expect, test } from './fixtures'
import { mockSavedComics, newState } from './savedMangaMocks'
import { navLink, openMenu, openSettingsGroups } from './settingsNav'

// Saved manga has no left-menu item: the save folder card is in Settings and the
// saved series are a Library tools section. Only /api/saved-comics/* is mocked;
// the rest runs against the seeded API.

test('Settings has the save folder card and Use this folder saves it', async ({ page }) => {
  const s = newState()
  await mockSavedComics(page, s)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Save folder' })
  await expect(card.getByTestId('save-folder')).toHaveText('C:\\Baihe\\data\\saved_comics')
  await expect(card).toContainText('Default')

  const input = card.getByLabel('Save to another folder', { exact: true })
  await input.fill('relative')
  await card.getByRole('button', { name: 'Use this folder' }).click()
  await expect(card.getByRole('alert')).toContainText('starting with its drive')
  await input.fill('D:\\Manga')
  await card.getByRole('button', { name: 'Use this folder' }).click()
  await expect(card.getByTestId('save-folder')).toHaveText('D:\\Manga')
  await card.getByRole('button', { name: 'Use the default' }).click()
  await expect(card.getByTestId('save-folder')).toHaveText('C:\\Baihe\\data\\saved_comics')
  expect(s.folderPosts).toEqual([{ folder: 'relative' }, { folder: 'D:\\Manga' }, { folder: '' }])

  await card.getByRole('button', { name: 'Open folder' }).click()
  await expect.poll(() => s.opened).toBe(1)
})

test('Library tools lists saved chapters and #/manga still works', async ({ page }) => {
  await mockSavedComics(page, newState())
  await page.goto('/#/library-tools')
  await page.locator('summary', { hasText: 'Saved manga' }).click()
  const section = page.getByRole('region', { name: 'Saved manga' })
  const list = section.getByRole('list', { name: 'Saved series' })
  await expect(list).toContainText('MangaK · 2 chapters')
  await list.getByRole('link', { name: 'Chapters' }).click()
  await expect(page).toHaveURL('/#/manga/MangaK/Test%20Camp')
  await expect(page.getByRole('list', { name: 'Chapters' })).toBeVisible()

  await page.goto('/#/manga')
  await expect(page.getByRole('heading', { name: 'Saved manga' })).toBeVisible()
  await expect(page.getByRole('list', { name: 'Saved series' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Save folder' })).toHaveCount(0)
})

test('the menu has no Saved manga item', async ({ page }) => {
  await mockSavedComics(page, newState())
  await page.goto('/#/library')
  await openMenu(page)
  await expect(navLink(page, 'Library tools')).toBeVisible()
  await expect(navLink(page, 'Saved manga')).toHaveCount(0)
})

test('from another device Settings does not show the save folder card', async ({ page }) => {
  const s = newState()
  await mockSavedComics(page, s, { local: false })
  await page.route(/\/api\/meta$/, (route) =>
    route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: false } }))
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  await expect(page.getByRole('region', { name: 'Save folder' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Open folder' })).toHaveCount(0)
  expect(s.calls.filter((c) => c !== 'GET /api/saved-comics/folder')).toEqual([])
})
