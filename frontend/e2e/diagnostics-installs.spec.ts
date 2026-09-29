import { expect, test } from '@playwright/test'

import { guard, mockDiagnostics } from './diagnosticsInstallsMocks'

// Deno install (Setup): starts a server job with a two-step confirm, then
// polls its status for progress and the result.

const SHOTS = process.env.INSTALLS_SCREENS_DIR

test('no JS runtime: Install Deno takes two presses, shows progress, then says to restart', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m)
  await page.goto('/#/diagnostics')
  const block = page.getByTestId('deno-install')
  await expect(block).toContainText("Downloads Deno's official release and checks its checksum.")
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/deno-offer.png`, fullPage: true })
  await block.getByRole('button', { name: 'Install Deno' }).click()
  expect(m.sent).toHaveLength(0) // first press only arms
  await block.getByRole('button', { name: 'Confirm install Deno (about 45 MB)' }).click()
  await expect(block.getByTestId('deno-progress')).toContainText('Downloading Deno v2.9.7')
  expect(m.sent.map((r) => new URL(r.url()).pathname)).toEqual(['/api/diagnostics/deno/install'])
  expect(m.sent[0].postDataJSON()).toEqual({ confirm: true })
  expect(m.sent[0].headers()['x-baihe-local']).toBe('1')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/deno-progress.png`, fullPage: true })
  await expect(block.getByTestId('deno-result')).toContainText('Restart Baihe', { timeout: 15_000 })
  await expect(block.getByTestId('deno-progress')).toHaveCount(0)
  await expect(block.getByRole('button', { name: 'Install Deno' })).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('a JS runtime already found: no Deno block', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await page.route((u) => u.pathname === '/api/diagnostics/deno', (r) => r.fulfill({
    json: {
      runtime_found: true, runtime_name: 'node', deno_on_path: false, deno_installed: false, can_install: true,
      install_method: 'download', job_id: 'deno_install', job: null, last_result: null,
    },
  }))
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('setup-rows')).toContainText('JS runtime')
  await expect(page.getByTestId('deno-install')).toHaveCount(0)
})

test('away from the PC: no Install Deno button', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m)
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('deno-install')).toContainText('Installing is PC only.')
  await expect(page.getByRole('button', { name: 'Install Deno' })).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})
