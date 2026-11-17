import { expect, test } from './fixtures'

test('Settings names a stopped server in one banner, not one per request', async ({ page }) => {
  await page.goto('/#/library')
  await expect(page.locator('main')).toBeVisible()
  // Every request the page makes fails; the check is that all of them have failed and still one banner shows.
  let pending = 0
  await page.route('**/api/**', async (route) => {
    pending += 1
    await route.abort()
    pending -= 1
  })
  await page.goto('/#/settings')
  const banners = page.locator('.banner:visible').filter({ hasText: 'Could not reach Baihe' })
  await expect(banners.first()).toBeVisible()
  await page.waitForLoadState('networkidle')
  await expect.poll(() => pending).toBe(0)
  await expect(banners).toHaveCount(1)
  await expect(banners.getByRole('button', { name: 'Retry' })).toBeVisible()
})

test('Settings offers Retry when only the settings list failed to load', async ({ page }) => {
  let failSchema = true
  await page.route('**/api/settings/schema', (route) => (failSchema ? route.abort() : route.fallback()))
  await page.goto('/#/settings')
  const banner = page.locator('.banner:visible').filter({ hasText: 'Could not reach Baihe' })
  await expect(banner).toHaveCount(1)
  failSchema = false
  await banner.getByRole('button', { name: 'Retry' }).click()
  await expect(banner).toHaveCount(0)
})
