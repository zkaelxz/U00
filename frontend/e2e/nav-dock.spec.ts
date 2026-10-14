import { expect, test } from '@playwright/test'

test('the nav switches between a floating pill and a docked rail, and remembers it', async ({ page }) => {
  await page.goto('/#/jobs')
  const rail = page.locator('.app-rail')
  await expect(rail).toBeVisible()
  await expect(rail).not.toHaveClass(/is-docked/)
  await page.getByRole('button', { name: 'Dock navigation' }).click()
  await expect(rail).toHaveClass(/is-docked/)
  await expect(rail.getByText('Baihe Studio')).toBeVisible()
  const box = await rail.boundingBox()
  expect(box?.x).toBe(0)
  expect(box?.height).toBeGreaterThanOrEqual(page.viewportSize()!.height - 1)
  await page.reload()
  await expect(page.locator('.app-rail')).toHaveClass(/is-docked/)
  await page.getByRole('button', { name: 'Float navigation' }).click()
  await expect(page.locator('.app-rail')).not.toHaveClass(/is-docked/)
})
