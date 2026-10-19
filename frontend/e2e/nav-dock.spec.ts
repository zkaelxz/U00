import { expect, test } from '@playwright/test'

import { runClock } from './mockHelpers'

test('the nav switches between a floating pill, a docked rail and a minimized rail, and remembers it', async ({ page }) => {
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
  await page.getByRole('button', { name: 'Minimize navigation' }).click()
  await expect(rail).toHaveClass(/is-minimized/)
  expect((await rail.boundingBox())?.width).toBeLessThan(80)
  await page.reload()
  await expect(page.locator('.app-rail')).toHaveClass(/is-minimized/)
  await page.getByRole('button', { name: 'Expand navigation' }).click()
  await expect(page.locator('.app-rail')).not.toHaveClass(/is-minimized/)
  await page.getByRole('button', { name: 'Float navigation' }).click()
  await expect(page.locator('.app-rail')).not.toHaveClass(/is-docked/)
})

test('a floating pill that was clicked collapses shortly after the pointer leaves, without a click on the page', async ({ page }) => {
  await page.goto('/#/jobs')
  const rail = page.locator('.app-rail')
  const rest = (await rail.boundingBox())!.width
  await rail.hover()
  await rail.getByRole('link').nth(1).click()
  await expect(rail).toHaveClass(/is-open/)
  await page.mouse.move(700, 400)
  await expect(rail).not.toHaveClass(/is-open/)
  await expect.poll(async () => (await rail.boundingBox())!.width).toBeLessThanOrEqual(rest + 1)
})

test('keyboard focus keeps the floating pill open and tapping outside closes it', async ({ page }) => {
  await page.clock.install()
  await page.goto('/#/jobs')
  const rail = page.locator('.app-rail')
  await rail.getByRole('link').first().focus()
  await page.keyboard.press('Tab')
  await expect(rail).toHaveClass(/is-open/)
  await runClock(page, 5_000)
  await expect(rail).toHaveClass(/is-open/)
  await page.mouse.click(700, 400)
  await expect(rail).not.toHaveClass(/is-open/)
})
