import { expect, test, type Page } from '@playwright/test'

import { ME } from './authMocks'
import { EMPTY_STATUS, PENDING_BASE, planFor } from './pendingInstallMocks'

// An install that would replace files the running app has loaded is queued for
// the next start instead of run. Every call is mocked; a catch-all fails the
// test on any other non-GET /api call, so no pip run can happen.

const overview = {
  dependencies: {
    paddleocr: { installed: false, powers: 'PaddleOCR backend', tier: 'feature' },
    paddlepaddle: { installed: false, powers: 'PaddleOCR engine', tier: 'feature' },
    pandas: { installed: true, powers: 'tables', tier: 'required' },
  },
  file_completeness: { missing_top_level: [], all_present: true },
  library_writable: true,
  gpu: { available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.' },
  model_engine_versions: [],
  recent_log_lines: [],
}
const setup = {
  python: { version: '3.12.4', ok: true }, ffmpeg: { found: true, version: '6.1' }, js_runtime: { found: true, name: 'deno' },
  cuda: { torch_installed: false, cuda_available: null }, files: { all_present: true, missing_top_level: [] },
  library_writable: true,
}
const pkg = (name: string) => ({
  name, dist: name, installed: false, installable: true, powers: '', approx_mb: 100, pulls_torch: false,
  source_url: null, not_offered_reason: null, warning: null,
})
const presets = {
  tasks: [{
    id: 'ocr', group: 'Video', label: 'Read burned-in captions (OCR)', help: 'Hard subtitles.',
    packages: ['paddleocr', 'paddlepaddle'], installed_count: 0, to_install: ['paddleocr', 'paddlepaddle'], approx_mb: 800,
  }],
  packages: { paddleocr: pkg('paddleocr'), paddlepaddle: pkg('paddlepaddle') },
}

const RESTART_PLAN = planFor(['paddleocr', 'paddlepaddle'], {
  mode: 'restart',
  changes: [{ name: 'numpy', from_version: '2.5.3', to_version: '2.3.5', kind: 'downgrade' }],
  summary: ['Install paddleocr 3.2.0, paddlepaddle 3.0.0.', 'Change numpy 2.5.3 -> 2.3.5 (downgrade).'],
  loaded: ['numpy'],
  needs_confirm: ['numpy 2.5.3 -> 2.3.5 is a downgrade of a package Baihe itself needs.'],
  note: "Baihe is using numpy right now, and Windows can't replace files in use.",
})

async function mockPage(page: Page, plan: unknown) {
  const unmocked: string[] = []
  const calls: { url: string; body: unknown }[] = []
  let status: unknown = EMPTY_STATUS
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route((u) => u.pathname === '/api/auth/me', (r) => r.fulfill({ json: ME.authOff }))
  await page.route('**/api/diagnostics', (r) => r.fulfill({ json: overview }))
  await page.route('**/api/diagnostics/setup-checks', (r) => r.fulfill({ json: setup }))
  await page.route('**/api/diagnostics/install-presets', (r) => r.fulfill({ json: presets }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
  await page.route((u) => u.pathname === PENDING_BASE, (r) => r.fulfill({ json: status }))
  await page.route((u) => u.pathname === `${PENDING_BASE}/plan`, (r) => r.fulfill({ json: plan }))
  await page.route((u) => u.pathname === `${PENDING_BASE}/queue`, (r) => {
    calls.push({ url: r.request().url(), body: r.request().postDataJSON() })
    status = { ...EMPTY_STATUS, packages: ['paddleocr', 'paddlepaddle'], before: { numpy: '2.5.3' } }
    return r.fulfill({ json: { queued: true, install_now: false, plan } })
  })
  await page.route((u) => u.pathname === `${PENDING_BASE}/cancel`, (r) => {
    calls.push({ url: r.request().url(), body: null })
    status = EMPTY_STATUS
    return r.fulfill({ json: { cancelled: true } })
  })
  await page.route('**/api/diagnostics/dependencies/**', (r) => {
    calls.push({ url: r.request().url(), body: null })
    return r.fulfill({ json: { package: 'x', ok: true, output_tail: [], hint: null } })
  })
  return { unmocked, calls, setStatus: (s: unknown) => { status = s } }
}

const openPackages = async (page: Page) => {
  await page.goto('/#/diagnostics')
  const summary = page.locator('summary', { hasText: /^Packages/ }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
  for (const s of await page.getByTestId('install-tasks').locator('summary').all()) await s.click()
}

const startTaskInstall = async (page: Page) => {
  await page.getByRole('button', { name: 'Install for Read burned-in captions (OCR)' }).click()
  await page.getByRole('button', { name: /^Confirm install 2 packages/ }).click()
}

test('a loaded package queues the install for restart, with the risk spelled out', async ({ page }) => {
  const { unmocked, calls } = await mockPage(page, RESTART_PLAN)
  await openPackages(page)
  await startTaskInstall(page)

  const panel = page.getByTestId('install-plan')
  await expect(panel).toContainText('Install paddleocr 3.2.0, paddlepaddle 3.0.0.')
  await expect(panel).toContainText('Change numpy 2.5.3 -> 2.3.5 (downgrade).')
  await expect(panel).toContainText('Installs when you restart Baihe.')
  await expect(panel).toContainText('Nothing is closed for you.')
  const queueButton = panel.getByRole('button', { name: 'Install when I restart Baihe' })
  await expect(queueButton).toBeDisabled()
  await panel.getByLabel('I understand, install anyway').check()
  await queueButton.click()

  expect(calls).toHaveLength(1)
  expect(calls[0].body).toEqual({ packages: ['paddleocr', 'paddlepaddle'], confirm: true, accept_risk: true })
  await expect(page.getByTestId('pending-install')).toContainText('paddleocr, paddlepaddle will install when you restart Baihe')
  await expect(page.getByTestId('task-ocr').getByText('pending install (restart)').first()).toBeVisible()
  expect(unmocked).toEqual([])
})

test('the queued install can be cancelled before restart', async ({ page }) => {
  const { unmocked, calls, setStatus } = await mockPage(page, RESTART_PLAN)
  setStatus({ ...EMPTY_STATUS, packages: ['paddleocr'] })
  await openPackages(page)
  await page.getByRole('button', { name: 'Cancel the queued install' }).click()
  await expect(page.getByTestId('pending-install')).toHaveCount(0)
  expect(calls.map((c) => new URL(c.url).pathname)).toEqual([`${PENDING_BASE}/cancel`])
  expect(unmocked).toEqual([])
})

test('a refused plan only explains why, and nothing is queued', async ({ page }) => {
  const refused = planFor(['paddleocr', 'paddlepaddle'], {
    mode: 'restart', blocked: ['This would put two OpenCV packages side by side (opencv-contrib-python and opencv-python).'],
  })
  const { unmocked, calls } = await mockPage(page, refused)
  await openPackages(page)
  await startTaskInstall(page)
  await expect(page.getByTestId('install-plan')).toContainText('two OpenCV packages')
  await expect(page.getByRole('button', { name: 'Install when I restart Baihe' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByTestId('install-plan')).toHaveCount(0)
  expect(calls).toEqual([])
  expect(unmocked).toEqual([])
})

test("last start's outcome is shown in plain words and can be dismissed", async ({ page }) => {
  const { setStatus } = await mockPage(page, RESTART_PLAN)
  setStatus({
    ...EMPTY_STATUS,
    result: {
      status: 'failed', packages: ['paddleocr'], message: 'The install did not work. Your earlier packages were put back.',
      tail: ['ERROR: no matching distribution'], restored: ['numpy'], restore_failed: [], finished: 1,
    },
  })
  await openPackages(page)
  await expect(page.getByTestId('pending-install-result')).toContainText('Your earlier packages were put back.')
})
