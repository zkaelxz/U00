import { expect, test } from '@playwright/test'

test.use({ storageState: { cookies: [], origins: [] } })

test('jump links open a folded settings section and scroll to it', async ({ page }) => {
  await page.goto('/#/settings')
  const nav = page.getByRole('navigation', { name: 'Jump to a settings section' })
  await expect(nav.getByRole('button')).toHaveCount(8)

  const notifications = page.getByRole('region', { name: 'Notifications' })
  await expect(notifications).toBeHidden() // folded by default
  const fold = page.locator('#settings-alerts')
  await expect(fold.locator('details.section').first()).not.toHaveAttribute('open', '')

  await nav.getByRole('button', { name: 'Notifications, backups, updates' }).click()
  await expect(fold.locator('details.section').first()).toHaveAttribute('open', '')
  await expect(notifications).toBeVisible()
  await expect(fold.locator('summary').first()).toBeFocused()
  // The app routes on the hash, so a jump must leave it alone.
  expect(new URL(page.url()).hash).toBe('#/settings')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('an opened or closed settings group is remembered after a reload', async ({ page }) => {
  await page.goto('/#/settings')
  const summary = page.locator('#settings-advanced details.section > summary').first()
  await summary.click()
  // Section saves its state in the toggle handler, after the open attribute changes.
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem('baihe.section.settings.advanced')))
    .toBe('1')
  await page.reload()
  await expect(page.locator('#settings-advanced details.section').first()).toHaveAttribute('open', '')
})
