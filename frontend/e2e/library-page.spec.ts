import { expect, test } from '@playwright/test'

// Assumes LibraryPage is wired in at '/'. Seed data: e2e/serve_seeded_api.py.
// Creates then deletes its own drama so the seeded three are unchanged afterwards.

test('shows summary panels and global line search', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('stats')).toContainText('drama(s)')
  await page.getByText(/^Recently active \(/).click()
  await expect(page.getByRole('region', { name: 'Recently active' })).toContainText('Signal')
  await page.getByText('Search all lines', { exact: true }).click()
  await page.getByLabel('Search all lines').fill('zzz-no-such-line')
  await page.getByRole('button', { name: 'Search' }).click()
  await expect(page.getByTestId('search-count')).toHaveText('0 match(es)')
})

test('create form validates client-side', async ({ page }) => {
  await page.goto('/')
  await page.getByText('New drama', { exact: true }).click()
  await page.getByRole('button', { name: 'Create drama' }).click()
  await expect(page.getByRole('alert')).toContainText('title')
})

test('create then delete with typed confirmation', async ({ page }) => {
  await page.goto('/')
  await page.getByText('New drama', { exact: true }).click()
  await page.getByLabel('English title').fill('E2E Temp Drama')
  await page.getByRole('button', { name: 'Create drama' }).click()
  const detail = page.getByRole('region', { name: 'E2E Temp Drama' })
  await expect(detail).toBeVisible()

  await detail.getByRole('button', { name: 'Delete drama…' }).click()
  const confirm = detail.getByRole('button', { name: 'Delete permanently' })
  await expect(confirm).toBeDisabled()
  await detail.getByLabel('Type DELETE to confirm').fill('DELETE')
  await confirm.click()
  await expect(page.getByRole('region', { name: 'E2E Temp Drama' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'E2E Temp Drama' })).toHaveCount(0)
})
