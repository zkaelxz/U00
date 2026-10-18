import { expect, test } from './fixtures'
import { openSettingsGroups } from './settingsNav'

const rail = (page: import('@playwright/test').Page) => page.locator('.app-rail')

test('the nav stays on the left until a position is saved, and a saved one survives a reload', async ({ page }) => {
  await page.goto('/#/jobs')
  const viewport = page.viewportSize()!
  let box = (await rail(page).boundingBox())!
  expect(box.x).toBeLessThan(40)

  for (const position of ['right', 'top', 'bottom'] as const) {
    await page.evaluate((p) => localStorage.setItem('baihe.navPosition', p), position)
    await page.reload()
    box = (await rail(page).boundingBox())!
    if (position === 'right') expect(box.x + box.width).toBeGreaterThan(viewport.width - 40)
    else {
      expect(box.width).toBeGreaterThan(box.height)
      if (position === 'top') expect(box.y).toBeLessThan(40)
      else expect(box.y + box.height).toBeGreaterThan(viewport.height - 40)
    }
  }
})

test('a horizontal nav keeps every item reachable and the page clear of it', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.navPosition', 'top'))
  await page.goto('/#/jobs')
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Jobs', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.getByRole('button', { name: 'Dock navigation' }).click()
  const bar = (await rail(page).boundingBox())!
  expect(bar.x).toBe(0)
  // Edge to edge, less a classic scrollbar if the page has one.
  expect(bar.width).toBeGreaterThan(page.viewportSize()!.width - 20)
  const main = (await page.locator('#main-content').boundingBox())!
  expect(main.y).toBeGreaterThanOrEqual(bar.y + bar.height - 1)
})

test('Settings > Display moves the nav and remembers it', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Account and display')
  await page.getByLabel('Nav position').selectOption('bottom')
  const box = (await rail(page).boundingBox())!
  expect(box.y + box.height).toBeGreaterThan(page.viewportSize()!.height - 40)
  await page.reload()
  await openSettingsGroups(page, 'Account and display')
  await expect(page.getByLabel('Nav position')).toHaveValue('bottom')
})
