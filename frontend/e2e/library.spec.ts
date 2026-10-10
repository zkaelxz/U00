import { expect, test, type Page } from '@playwright/test'

// Data comes from e2e/serve_seeded_api.py -- three dramas.

// The Library's drama list, so the Continue shelf and Library tools never match.
const dramas = (page: Page) => page.getByRole('region', { name: 'Titles' })

test('shows the library from the real API', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
  // A reachable server shows no status badge.
  await expect(page.getByTestId('api-status')).toHaveCount(0)
  await expect(dramas(page).getByRole('link', { name: 'Signal', exact: true })).toBeVisible()
  // Humanized badges, no raw codes.
  const card = page.locator('.drama-card', { hasText: 'Signal' })
  await expect(card).toContainText('Not started')
  await expect(card).toContainText('Korean')
  await expect(card).toContainText('Video drama')
})

test('filters go through the API', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')

  await page.getByLabel('Quick filter').selectOption('Favorite')
  await expect(page.getByTestId('drama-count')).toHaveText('1 title')
  await expect(
    dramas(page).getByRole('link', { name: 'Grandmaster of Demonic Cultivation', exact: true }),
  ).toBeVisible()

  await page.getByLabel('Quick filter').selectOption('')
  await page.getByLabel('Search title or summary').fill('Heaven')
  await expect(page.getByTestId('drama-count')).toHaveText('1 title')

  await page.getByLabel('Search title or summary').fill('zzz-nothing')
  await expect(page.getByText('No titles match.')).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
})

test('Details opens a sheet; Esc closes it and returns focus', async ({ page }) => {
  await page.goto('/')
  const details = dramas(page).getByRole('button', { name: 'Details: Grandmaster of Demonic Cultivation' })
  await details.click()
  const sheet = page.getByRole('dialog', { name: 'Grandmaster of Demonic Cultivation' })
  await expect(sheet).toContainText('Seeded for the e2e test.')
  await expect(sheet).toContainText('墨香铜臭 (Mo Xiang Tong Xiu)')
  await expect(sheet).toContainText('Audio drama')
  await expect(sheet.getByRole('link', { name: 'Open workspace' })).toHaveClass(/btn-primary/)
  await expect(sheet.getByRole('link', { name: 'Read', exact: true })).toHaveAttribute('href', /#\/read\/\d+$/)
  await page.keyboard.press('Escape')
  await expect(sheet).toBeHidden()
  await expect(details).toBeFocused()
})

test('grid and list views; the choice is remembered', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.drama-grid .drama-card')).toHaveCount(3)
  await page.getByRole('radio', { name: 'List' }).check()
  await expect(dramas(page).getByRole('row')).toHaveCount(4) // header + 3 dramas
  const row = dramas(page).getByRole('row').filter({ hasText: 'Signal' })
  await expect(row.getByRole('link', { name: 'Signal', exact: true })).toHaveAttribute('href', /#\/drama\/\d+$/)
  await expect(row).toContainText('Korean')
  await row.getByRole('button', { name: 'Details: Signal' }).click()
  await expect(page.getByRole('dialog', { name: 'Signal' })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.reload()
  await expect(page.getByRole('radio', { name: 'List' })).toBeChecked()
  await expect(page.locator('.drama-grid')).toHaveCount(0)
})

test('the card title opens the workspace in one click', async ({ page }) => {
  await page.goto('/')
  await dramas(page).getByRole('link', { name: 'Signal', exact: true }).click()
  await expect(page).toHaveURL(/#\/drama\/\d+$/)
  // The seeded drama has no lines, so its current stage is Source.
  await expect(page.getByRole('link', { name: 'Media', exact: true })).toHaveAttribute('aria-current', 'page')
})
