import { expect, test } from '@playwright/test'

import { guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'
import { diagTab, type DiagnosticsTabName } from './diagnosticsTabsNav'
import { installHitArea } from './hitArea'
import { expectNoSidewaysScroll } from './mockHelpers'

// Phone project (390x844, touch): the Diagnostics panes are one row of tabs that scrolls inside itself; the page never scrolls sideways.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

test('tabs are 44px targets, the row scrolls inside itself, and every tab fits the screen', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  const names: DiagnosticsTabName[] = ['Setup', 'Packages and installs', 'Speaker detection', 'Model status', 'Models on disk', 'Loaded now',
    'Test models', 'Resources and ports', 'Log', 'Reset library']
  for (const name of names) {
    const tab = diagTab(page, name)
    expect(await tab.evaluate((e) => window.hitHeight(e)), name).toBeGreaterThanOrEqual(44)
  }
  await expectNoSidewaysScroll(page)
  for (const tab of ['packages', 'model-status', 'models', 'loaded', 'system', 'log']) {
    await page.goto(`/#/diagnostics?tab=${tab}`)
    await expect(page.locator('.diag-panel:not([hidden]) .section-flat').first()).toBeVisible()
    await expectNoSidewaysScroll(page)
  }
  // The row may scroll sideways on its own; the page never does.
  const row = await page.getByRole('tablist', { name: 'Diagnostics' }).evaluate((e) => getComputedStyle(e).overflowX)
  expect(row).toBe('auto')
  expect(unmocked).toEqual([])
})

test('a deep link to a card on another pane brings the card and its tab into view', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?card=reset')
  await expect(diagTab(page, 'Reset library')).toHaveAttribute('aria-selected', 'true')
  await expect(diagTab(page, 'Reset library')).toBeInViewport()
  await expect(page.locator('.diag-panel:not([hidden]) .section-title', { hasText: /^Reset library/ })).toBeInViewport()
  await expectNoSidewaysScroll(page)
})
