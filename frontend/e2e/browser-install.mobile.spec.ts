import { expect, test, type Request } from '@playwright/test'

import { mockBrowser } from './browserInstallMocks'
import { guard, mockDiagnostics, openSection } from './diagnosticsInstallsMocks'

// Phone: the browser support row fits (no sideways scroll, 44 px targets).

const noSideScroll = () => document.documentElement.scrollWidth <= window.innerWidth

test('phone: Install browser support fits the screen', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await mockBrowser(page, [] as Request[])
  await page.goto('/#/diagnostics')
  await openSection(page, /^Setup/)
  const install = page.getByRole('button', { name: 'Install browser support' })
  await expect(install).toBeVisible()
  expect((await install.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(noSideScroll)).toBe(true)
  await install.click()
  const confirm = page.getByRole('button', { name: 'Confirm install browser support (about 150 MB)' })
  expect((await confirm.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await confirm.click()
  const cancel = page.getByRole('button', { name: 'Cancel install' })
  await expect(cancel).toBeVisible()
  expect((await cancel.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(noSideScroll)).toBe(true)
  await expect(page.getByTestId('browser-result')).toContainText('Browser support installed.', { timeout: 15_000 })
  expect(await page.evaluate(noSideScroll)).toBe(true)
  expect(m.unmocked).toEqual([])
})
