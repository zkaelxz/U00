import { expect, test, type Request } from '@playwright/test'

import { mockBrowser, status } from './browserInstallMocks'
import { guard, mockDiagnostics, openSection } from './diagnosticsInstallsMocks'

// Diagnostics > Setup "Install browser support": a server job started with a
// two-step confirm, polled for progress, cancellable, then a result. Every
// endpoint is mocked; nothing is downloaded.

test('no Chrome or Edge: install takes two presses, shows progress, then the result', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  const sent: Request[] = []
  await mockBrowser(page, sent)
  await page.goto('/#/diagnostics')
  await openSection(page, /^Setup/)
  const block = page.getByTestId('browser-install')
  await expect(block).toContainText('Downloads about 150 MB.')
  await expect(block).toContainText('Not needed if Chrome or Edge is installed.')
  await block.getByRole('button', { name: 'Install browser support' }).click()
  expect(sent).toHaveLength(0) // first press only arms
  await block.getByRole('button', { name: 'Confirm install browser support (about 150 MB)' }).click()
  await expect(block.getByTestId('browser-progress')).toContainText('40% · Downloading the browser…')
  expect(sent[0].postDataJSON()).toEqual({ confirm: true })
  expect(sent[0].headers()['x-baihe-local']).toBe('1')
  await expect(block.getByTestId('browser-result')).toContainText('Browser support installed.', { timeout: 15_000 })
  await expect(block.getByTestId('browser-progress')).toHaveCount(0)
  await expect(block.getByRole('button', { name: 'Install browser support' })).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('cancel stops the install and says so; the buttons are phone-sized', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await page.route((u) => u.pathname === '/api/jobs/browser_install/cancel', (r) => {
    m.sent.push(r.request())
    return r.fulfill({ json: { job_id: 'browser_install', cancelled: true } })
  })
  const sent: Request[] = []
  await mockBrowser(page, sent)
  await page.goto('/#/diagnostics')
  await openSection(page, /^Setup/)
  const block = page.getByTestId('browser-install')
  await block.getByRole('button', { name: 'Install browser support' }).click()
  await block.getByRole('button', { name: 'Confirm install browser support (about 150 MB)' }).click()
  const cancel = block.getByRole('button', { name: 'Cancel install' })
  await expect(cancel).toBeVisible()
  expect((await cancel.boundingBox())!.height).toBeGreaterThanOrEqual(40)
  await cancel.click()
  await expect(block.getByTestId('browser-result')).toContainText('Cancelled.', { timeout: 15_000 })
  expect(sent.some((r) => r.url().endsWith('/api/jobs/browser_install/cancel'))).toBe(true)
})

test('Chrome or Edge found: no browser row', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await mockBrowser(page, [], status({ system_browser_found: true, refusal: 'Chrome or Edge is already installed, so this is not needed.' }))
  await page.goto('/#/diagnostics')
  await openSection(page, /^Setup/)
  await expect(page.getByTestId('setup-rows')).toContainText('JS runtime')
  await expect(page.getByTestId('browser-install')).toHaveCount(0)
})

test('the playwright package missing: the reason shows and there is no button', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await mockBrowser(page, [], status({
    playwright_installed: false, refusal: 'Install the playwright package first (Diagnostics > Packages).',
  }))
  await page.goto('/#/diagnostics')
  await openSection(page, /^Setup/)
  const block = page.getByTestId('browser-install')
  await expect(block).toContainText('Install the playwright package first')
  await expect(block.getByRole('button', { name: 'Install browser support' })).toHaveCount(0)
})

test('away from the PC: the install is not offered', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await mockBrowser(page, [])
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.goto('/#/diagnostics')
  await openSection(page, /^Setup/)
  await expect(page.getByTestId('browser-install')).toContainText('Installing is PC only.')
  await expect(page.getByRole('button', { name: 'Install browser support' })).toHaveCount(0)
})
