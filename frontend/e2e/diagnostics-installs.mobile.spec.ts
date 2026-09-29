import { expect, test } from '@playwright/test'

import { guard, mockDiagnostics } from './diagnosticsInstallsMocks'

// Phone: the Deno block fits (no sideways scroll, 44 px targets).

const noSideScroll = () => document.documentElement.scrollWidth <= window.innerWidth

test('phone: Deno install fits the screen', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m)
  await page.goto('/#/diagnostics')
  const install = page.getByRole('button', { name: 'Install Deno' })
  await expect(install).toBeVisible()
  expect((await install.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await install.click()
  await page.getByRole('button', { name: 'Confirm install Deno (about 45 MB)' }).click()
  await expect(page.getByTestId('deno-result')).toContainText('Restart Baihe', { timeout: 15_000 })
  expect(await page.evaluate(noSideScroll)).toBe(true)
  expect(m.unmocked).toEqual([])
})
