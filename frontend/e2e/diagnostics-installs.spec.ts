import { expect, test } from '@playwright/test'

import { guard, mockDiagnostics, openSection } from './diagnosticsInstallsMocks'

// Deno install (Setup) and "Test first" (Packages): both start a server job
// with a two-step confirm, then poll its status for progress and the result.

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

test('Test first: two presses with the check target, progress, then the verdict and details', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  await openSection(page, /^Installed packages/)
  const list = page.getByRole('list', { name: 'Installed packages' })
  await expect(list.getByRole('button', { name: /Test pypinyin/ })).toHaveCount(0) // no test before a check
  await page.getByRole('button', { name: 'Check for updates' }).click()
  const row = list.locator('li', { hasText: 'pypinyin' })
  await row.getByRole('button', { name: 'Test pypinyin 0.55.0 first' }).click()
  await row.getByRole('button', { name: 'Confirm test pypinyin 0.55.0 (takes minutes)' }).click()
  const result = page.getByTestId('upgrade-test-pypinyin')
  await expect(result).toContainText('Testing pypinyin 0.55.0…')
  const post = m.sent.find((r) => r.url().includes('/test-upgrade'))!
  expect(post.postDataJSON()).toEqual({ confirm: true, target: '0.55.0' })
  expect(post.headers()['x-baihe-local']).toBe('1')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/test-first-running.png`, fullPage: true })
  await expect(result).toContainText('Not safe: pypinyin 0.55.0: 1 test fails', { timeout: 15_000 })
  await expect(result.locator('details')).toHaveAttribute('open', '')
  await expect(result).toContainText('tests/test_pinyin.py::test_tone')
  // The Update action is still there: the test only informs it.
  await expect(row.getByRole('button', { name: 'Update pypinyin to 0.55.0' })).toBeVisible()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/test-first-result.png`, fullPage: true })
  expect(m.unmocked).toEqual([])
})

test('away from the PC: no Install Deno or Test first buttons', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m)
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('deno-install')).toContainText('Installing is PC only.')
  await expect(page.getByRole('button', { name: 'Install Deno' })).toHaveCount(0)
  await openSection(page, /^Packages/)
  await openSection(page, /^Installed packages/)
  await page.getByRole('button', { name: 'Check for updates' }).click()
  await expect(page.getByRole('button', { name: /Test pypinyin/ })).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})
