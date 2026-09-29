import { expect, test } from '@playwright/test'

// Assumes LibraryPage is wired in at '/'. Seed data: e2e/serve_seeded_api.py.
// Creates then deletes its own drama so the seeded three are unchanged afterwards.

test('stats line, Continue shelf and global line search', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('stats')).toContainText('3 dramas')
  const shelf = page.getByRole('region', { name: 'Continue' })
  const resume = shelf.getByRole('link', { name: 'Resume work on Signal' })
  await expect(resume).toHaveAttribute('href', /#\/drama\/\d+\/source$/)
  await expect(resume).toHaveClass(/btn/)

  await page.getByRole('radio', { name: 'Lines' }).check()
  await page.getByLabel('Search all lines').fill('zzz-no-such-line')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByTestId('search-count')).toHaveText('0 matches')
  await page.getByRole('radio', { name: 'Titles' }).check()
  await expect(page.getByTestId('drama-count')).toHaveText('3 dramas')
})

test('create form validates client-side', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New drama' }).click()
  const sheet = page.getByRole('dialog', { name: 'New drama' })
  await expect(sheet.getByLabel('English title')).toBeFocused()
  await sheet.getByRole('button', { name: 'Create drama' }).click()
  await expect(sheet.getByRole('alert')).toContainText('title')
  // Humanized options; the values stay raw.
  await expect(sheet.getByLabel('Media type').locator('option:checked')).toHaveText('Audio drama')
  await expect(sheet.getByLabel('Source language').locator('option:checked')).toHaveText('Chinese')
})

test('create (Enter submits) then delete with typed confirmation', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New drama' }).click()
  await page.getByLabel('English title').fill('E2E Temp Drama')
  await page.getByLabel('English title').press('Enter')
  await expect(page.getByTestId('created-notice')).toContainText('Created “E2E Temp Drama”.')
  const detail = page.getByRole('dialog', { name: 'E2E Temp Drama' })
  await expect(detail).toBeVisible()

  await detail.getByRole('button', { name: 'Delete drama…' }).click()
  const confirm = detail.getByRole('button', { name: 'Delete permanently' })
  await expect(confirm).toBeDisabled()
  await detail.getByLabel('Type DELETE to confirm').fill('DELETE')
  await confirm.click()
  await expect(page.getByRole('dialog', { name: 'E2E Temp Drama' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'E2E Temp Drama' })).toHaveCount(0)
  await expect(page.getByTestId('created-notice')).toHaveCount(0)
})
