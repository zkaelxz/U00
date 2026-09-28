import { expect, test } from '@playwright/test'

// Data comes from e2e/serve_seeded_api.py -- three dramas.

test('shows API status and the library from the real API', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('api-status')).toContainText('API v0.1')
  await expect(page.getByTestId('drama-count')).toHaveText('3 drama(s)')
  await expect(page.getByRole('button', { name: 'Signal' })).toBeVisible()
})

test('filters go through the API', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('3 drama(s)')

  await page.getByLabel('Quick filter').selectOption('Favorite')
  await expect(page.getByTestId('drama-count')).toHaveText('1 drama(s)')
  await expect(
    page.getByRole('button', { name: 'Grandmaster of Demonic Cultivation' }),
  ).toBeVisible()

  await page.getByLabel('Quick filter').selectOption('')
  await page.getByLabel('Search title or summary').fill('Heaven')
  await expect(page.getByTestId('drama-count')).toHaveText('1 drama(s)')
})

test('selecting a drama loads its details', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Grandmaster of Demonic Cultivation' }).click()
  const detail = page.getByRole('region', { name: 'Grandmaster of Demonic Cultivation' })
  await expect(detail).toContainText('Seeded for the e2e test.')
  await expect(detail).toContainText('墨香铜臭 (Mo Xiang Tong Xiu)')
})
