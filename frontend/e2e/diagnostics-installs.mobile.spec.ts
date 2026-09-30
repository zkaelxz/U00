import { expect, test } from '@playwright/test'

import { guard, mockDiagnostics, openSection } from './diagnosticsInstallsMocks'

// Phone: the Deno block and Test first fit (no sideways scroll, 44 px targets).

const noSideScroll = () => document.documentElement.scrollWidth <= window.innerWidth

test('phone: Deno install and Test first fit the screen', async ({ page }) => {
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

  await openSection(page, /^Packages/)
  await openSection(page, /^Installed packages/)
  await page.getByRole('button', { name: 'Check for updates' }).click()
  const test1 = page.getByRole('button', { name: 'Test pypinyin 0.55.0 first' })
  expect((await test1.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await test1.click()
  await page.getByRole('button', { name: 'Confirm test pypinyin 0.55.0 (takes minutes)' }).click()
  await expect(page.getByTestId('upgrade-test-pypinyin')).toContainText('Not safe', { timeout: 15_000 })
  expect(await page.evaluate(noSideScroll)).toBe(true)
  expect(m.unmocked).toEqual([])
})
