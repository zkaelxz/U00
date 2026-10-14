import { expect, test } from './fixtures'

test('on a phone the quick bar sits at the bottom by default and moves to the top when saved', async ({ page }) => {
  await page.goto('/#/jobs')
  const bar = page.locator('.phone-bar')
  const height = page.viewportSize()!.height
  const bottom = (await bar.boundingBox())!
  expect(bottom.y + bottom.height).toBeGreaterThan(height - 2)

  await page.evaluate(() => localStorage.setItem('baihe.navPosition', 'top'))
  await page.reload()
  const top = (await bar.boundingBox())!
  expect(top.y).toBeLessThan(2)
  const header = (await page.locator('.app-header').boundingBox())!
  expect(header.y).toBeGreaterThanOrEqual(top.y + top.height - 1)
  await expect(bar.getByRole('link', { name: 'Jobs' })).toBeVisible()
})

test('left and right behave as the bottom bar on a phone', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.navPosition', 'right'))
  await page.goto('/#/jobs')
  const box = (await page.locator('.phone-bar').boundingBox())!
  expect(box.y + box.height).toBeGreaterThan(page.viewportSize()!.height - 2)
})
