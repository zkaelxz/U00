import { expect, test, type Page } from '@playwright/test'

import { guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'
import { diagTab } from './diagnosticsTabsNav'
import { installHitArea } from './hitArea'

// Phone project (390x844, touch): the Diagnostics tab row fits and every tab fits without sideways scroll.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('tabs are 44px targets, the row scrolls inside itself, and every tab fits the screen', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  for (const name of ['Setup', 'Packages and installs', 'Models', 'System'] as const) {
    const tab = diagTab(page, name)
    expect(await tab.evaluate((e) => window.hitHeight(e)), name).toBeGreaterThanOrEqual(44)
  }
  await noSideways(page)
  for (const tab of ['packages', 'models', 'system']) {
    await page.goto(`/#/diagnostics?tab=${tab}`)
    await expect(page.locator('.diag-panel:not([hidden]) details.section').first()).toBeVisible()
    await noSideways(page)
  }
  // The row may scroll sideways on its own; the page never does.
  const row = await page.getByRole('tablist', { name: 'Diagnostics' }).evaluate((e) => getComputedStyle(e).overflowX)
  expect(row).toBe('auto')
  expect(unmocked).toEqual([])
})

test('a deep link to a card on another tab brings the card into view', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?card=reset')
  await expect(diagTab(page, 'System')).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('summary', { hasText: /^Reset library/ })).toBeInViewport()
  await noSideways(page)
})
