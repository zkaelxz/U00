import { expect, test, type Page } from '@playwright/test'

// Phone project (390x844, touch): Diagnostics admin with the Log, the
// support report and a failed install's Output open. Every POST is mocked;
// a catch-all aborts anything else that isn't a GET.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

const long = 'x'.repeat(400)

/** Aborts any non-GET /api call no test mocked (later page.route mocks win). */
async function guard(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

test('Diagnostics on a phone: job cards, 44px targets, no sideways scroll', async ({ page }) => {
  const unmocked = await guard(page)
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { count: 1, items: [{
    job_id: 'translate_1', status: 'running', progress: 0.4, message: 'Batch 2 of 5', error: null,
    description: 'Translate Signal', gpu_touching: false, started_at: Date.now() / 1000 - 185, finished_at: null, updated_at: 0,
  }] } }))
  await page.route('**/api/diagnostics/log**', (r) =>
    r.fulfill({ json: { lines: [`12:00 ERROR ${long}`, '12:01 INFO fine'] } }))
  await page.route('**/api/diagnostics/support-report', (r) => r.fulfill({ json: { report: `Baihe report\n${long}` } }))
  await page.route('**/api/diagnostics/dependencies/**', (r) =>
    r.fulfill({ json: { package: 'yt-dlp', ok: false, output_tail: [`ERROR: ${long}`] } }))

  await page.goto('/#/diagnostics')
  // Jobs as cards, Cancel on its own line.
  const card = page.locator('ul.job-cards > li').first()
  await expect(card).toContainText('Running 40% · 3m')
  const cancel = card.getByRole('button', { name: 'Cancel' })
  expect((await cancel.boundingBox())!.height).toBeGreaterThanOrEqual(44)

  await page.locator('summary', { hasText: /^Log/ }).click()
  await expect(page.getByLabel('Log lines')).toContainText('INFO fine')
  // Support report: a card with Copy report; the preview fold shows it, plain text one tap away.
  await page.locator('summary', { hasText: "What's in it" }).click()
  await expect(page.getByTestId('report-list')).toContainText('Baihe report')
  await page.getByRole('button', { name: 'Show plain text' }).click()
  await expect(page.getByLabel('Support report')).toContainText('Baihe report')

  // The job blocks installs; drop it so the Install button works.
  await page.unroute('**/api/jobs')
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { count: 0, items: [] } }))
  await page.locator('summary', { hasText: /^Packages/ }).click()
  await page.locator('summary', { hasText: /^Missing packages/ }).click()
  const install = page.getByRole('button', { name: 'Install yt-dlp' })
  await expect(install).toBeEnabled({ timeout: 10_000 })

  // The ConfirmButton takes its own line under the package name.
  const row = page.locator('.pkg-list > li', { hasText: 'yt-dlp' }).first()
  const name = await row.locator('strong').boundingBox()
  const btn = await install.boundingBox()
  expect(btn!.y).toBeGreaterThanOrEqual(name!.y + name!.height - 1)

  await install.click()
  await page.getByRole('button', { name: 'Confirm install yt-dlp' }).click()
  await expect(page.getByTestId('install-result')).toContainText('Install failed for yt-dlp.')
  await expect(page.getByTestId('install-result').locator('pre')).toBeVisible()

  const small = await page.locator('button:not(.link):not(.field-help-btn), summary').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? '').trim().slice(0, 30) }))
      .filter(({ h }) => h < 44))
  expect(small).toEqual([])
  await noSideways(page)
  expect(unmocked).toEqual([])
})

test('Settings on a phone: the extension section fits and its targets are 44px', async ({ page }) => {
  const unmocked = await guard(page)
  await page.route('**/api/extension/status', (r) => r.fulfill({ json: { enabled: true, running: true } }))
  await page.route('**/api/extension/token', (r) => r.fulfill({ json: { token: 'tok-phone' } }))
  await page.goto('/#/settings')
  const ext = page.getByRole('region', { name: 'Browser extension' })
  await expect(ext.locator('.card-meta')).toHaveText('On · running')
  await ext.getByRole('button', { name: 'Show extension token' }).click()
  await ext.getByRole('button', { name: 'Confirm show extension token' }).click()
  await expect(ext.getByLabel('Extension token', { exact: true })).toHaveValue('tok-phone')
  for (const name of ['Copy', 'Hide']) {
    expect((await ext.getByRole('button', { name }).boundingBox())!.height).toBeGreaterThanOrEqual(44)
  }
  await noSideways(page)
  expect(unmocked).toEqual([])
})

test('Diagnostics at 360px: Setup and report cards, Packages with GPU PyTorch, report preview fit', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  const unmocked = await guard(page)
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { count: 0, items: [] } }))
  await page.route('**/api/diagnostics/support-report', (r) => r.fulfill({
    json: { report: `Python: 3.12.4\nModel/engine versions:\n  - faster-whisper: 1.1.0\nRecent errors:\n  12:00 ERROR ${long}` },
  }))
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch', (r) => r.fulfill({ json: {
    nvidia: { found: true, gpu_name: 'NVIDIA GeForce RTX 3080 Ti', driver_version: '581.42', status: 'ok', recommended: '570.65', minimum: '528.33' },
    installed: [{ name: 'torch', version: null, build: null }, { name: 'torchvision', version: null, build: null },
      { name: 'torchaudio', version: null, build: null }],
    problems: [], state: 'missing', python_supported: true, variants: [],
    recommended: {
      variant: 'cu128', label: 'NVIDIA GPU (CUDA 12.8)', index_url: 'https://download.pytorch.org/whl/cu128', needs_nvidia: true,
      versions: { torch: '2.11.0+cu128', torchvision: '0.26.0+cu128', torchaudio: '2.11.0+cu128' },
    },
    probe: null,
  } }))
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('setup-rows')).toBeVisible()
  await page.locator('summary', { hasText: "What's in it" }).click()
  await expect(page.getByTestId('report-list')).toContainText('faster-whisper')
  await page.locator('summary', { hasText: /^Packages/ }).click()
  await expect(page.getByRole('table', { name: 'PyTorch versions' })).toContainText('0.26.0+cu128')
  await expect(page.getByRole('button', { name: 'Set up GPU PyTorch' })).toBeVisible()

  // The version table fits its panel (no inner sideways scroll either).
  const table = await page.getByRole('table', { name: 'PyTorch versions' }).evaluate((t) =>
    ({ scroll: t.parentElement!.scrollWidth, client: t.parentElement!.clientWidth }))
  expect(table.scroll).toBeLessThanOrEqual(table.client)
  const small = await page.locator('button:not(.field-help-btn), summary, a.btn').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? '').trim().slice(0, 30) }))
      .filter(({ h }) => h < 44))
  expect(small).toEqual([])
  await noSideways(page)
  expect(unmocked).toEqual([])
})
