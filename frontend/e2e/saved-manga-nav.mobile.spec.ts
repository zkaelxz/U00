import { expect, test } from './fixtures'
import { mockSavedComics, newState } from './savedMangaMocks'
import { navLink, openMenu, openSettingsGroups } from './settingsNav'

// Phone project: the moved save folder card and Saved manga section fit, .

test('save folder card in Settings and Saved manga in Library tools fit a phone', async ({ page }) => {
  await mockSavedComics(page, newState())
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  const card = page.getByRole('region', { name: 'Save folder' })
  await expect(card.getByTestId('save-folder')).toBeVisible()
  // Dense .btn-sm buttons are 32px with a 44px+ touch area (index.css), the same as elsewhere.
  await expect(card.getByRole('button', { name: 'Use this folder' })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Open folder' })).toBeVisible()
  await expect(card.getByLabel('Save to another folder', { exact: true })).toBeVisible()

  await page.goto('/#/library-tools')
  await page.locator('summary', { hasText: 'Saved manga' }).click()
  await expect(page.getByRole('list', { name: 'Saved series' })).toBeVisible()
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)

  await openMenu(page)
  await expect(navLink(page, 'Saved manga')).toHaveCount(0)
})
