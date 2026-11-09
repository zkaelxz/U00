import { expect, test } from '@playwright/test'

// The Simple / Advanced view decides whether the menu lists the admin and system pages. A browser that never chose is Simple.

test.describe('a browser that never chose a view', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('lists no admin or system pages, and Advanced brings them back and is remembered', async ({ page }) => {
    await page.goto('/#/jobs')
    const rail = page.locator('.app-rail')
    await expect(rail.getByText('Settings', { exact: true })).toBeVisible()
    await expect(rail.getByText('Diagnostics', { exact: true })).toHaveCount(0)
    await expect(rail.getByText('Library tools', { exact: true })).toHaveCount(0)
    await page.getByRole('radio', { name: 'Menu: Advanced' }).check({ force: true })
    await expect(rail.getByText('Diagnostics', { exact: true })).toBeVisible()
    await expect(rail.getByText('Library tools', { exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('radio', { name: 'Menu: Advanced' })).toBeChecked()
  })
})
