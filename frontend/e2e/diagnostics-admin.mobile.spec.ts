import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

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
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { count: 2, items: [{
    job_id: 'translate_1', status: 'running', progress: 0.4, message: 'Batch 2 of 5', error: null,
    description: 'Translate Signal', gpu_touching: false, started_at: Date.now() / 1000 - 185, finished_at: null, updated_at: 0,
  }, {
    job_id: 'dub_2', status: 'error', progress: null, message: '', error: `Provider failed: ${long}`,
    description: 'Dub Signal', gpu_touching: false, started_at: 10, finished_at: 20, updated_at: 0,
  }] } }))
  await page.route('**/api/diagnostics/log**', (r) =>
    r.fulfill({ json: { lines: [`12:00 ERROR ${long}`, '12:01 INFO fine'] } }))
  await page.route('**/api/diagnostics/dependencies/**', (r) =>
    r.fulfill({ json: { package: 'yt-dlp', ok: false, output_tail: [`ERROR: ${long}`] } }))

  await page.goto('/#/diagnostics')
  // Jobs as cards, Cancel on its own line.
  const card = page.locator('ul.job-cards > li').first()
  await expect(card).toContainText('Running 40% · 3m')
  const cancel = card.getByRole('button', { name: 'Cancel' })
  expect((await cancel.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  // A failed job's card: readable name, Delete on its own 44px line, no sideways scroll.
  const failed = page.locator('ul.job-cards > li').nth(1)
  expect((await failed.locator('strong').boundingBox())!.width).toBeGreaterThan(150)
  expect((await failed.getByRole('button', { name: /^Delete/ }).boundingBox())!.height).toBeGreaterThanOrEqual(44)

  await page.locator('summary', { hasText: /^Log/ }).click()
  await expect(page.getByLabel('Log lines')).toContainText('INFO fine')
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

  const small = await page.locator('button:not(.link):not(.field-help-btn):not(.toggle), summary').evaluateAll((els) =>
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
  await openSettingsGroups(page)
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

test('Diagnostics at 360px: Setup card, Packages with GPU PyTorch fit', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  const unmocked = await guard(page)
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { count: 0, items: [] } }))
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
  await page.locator('summary', { hasText: /^Packages/ }).click()
  await expect(page.getByRole('table', { name: 'PyTorch versions' })).toContainText('0.26.0+cu128')
  await expect(page.getByRole('button', { name: 'Set up GPU PyTorch' })).toBeVisible()

  // The version table fits its panel (no inner sideways scroll either).
  const table = await page.getByRole('table', { name: 'PyTorch versions' }).evaluate((t) =>
    ({ scroll: t.parentElement!.scrollWidth, client: t.parentElement!.clientWidth }))
  expect(table.scroll).toBeLessThanOrEqual(table.client)
  const small = await page.locator('button:not(.field-help-btn):not(.toggle), summary, a.btn').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? '').trim().slice(0, 30) }))
      .filter(({ h }) => h < 44))
  expect(small).toEqual([])
  await noSideways(page)
  expect(unmocked).toEqual([])
})

test('Job history time by stage fits a phone and its summaries are 44px', async ({ page }) => {
  const unmocked = await guard(page)
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { count: 0, items: [] } }))
  await page.route('**/api/diagnostics/job-history', (r) => r.fulfill({ json: [{
    job_id: 'a', label: 'Translating Signal episode 12 with the long description', status: 'done', description: null,
    message: '', error: null, gpu_touching: true, started_at: 1, finished_at: 2, duration_seconds: 4000,
  }] }))
  await page.route('**/api/jobs/a/stages', (r) => r.fulfill({ json: { job_id: 'a', runs: [{
    run_started_at: 100, running: false, total_seconds: 4000, cost_usd: 1.25, stages: [
      { stage: 'Preparing', started_at: 100, duration_seconds: 3.4, cost_usd: 0 },
      { stage: 'Translating batches with the reviewer pass and glossary checks', started_at: 104,
        duration_seconds: 3725, cost_usd: 1.2 },
      { stage: 'Saving', started_at: 3829, duration_seconds: 271.6, cost_usd: 0.05 },
    ] }] } }))
  await page.goto('/#/diagnostics')
  await page.locator('summary', { hasText: /^Job history/ }).click()
  const history = page.getByRole('list', { name: 'Job history' })
  await history.locator('summary', { hasText: 'Translating Signal' }).click()
  const rows = history.getByRole('list', { name: 'Time by stage' }).locator('li')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(1)).toContainText('1 h 02 min · $1.20')
  await expect(history).toContainText('Total 1 h 06 min · estimated $1.25')
  // Each stage's time stays inside the row.
  for (let i = 0; i < 3; i++) {
    const row = (await rows.nth(i).boundingBox())!
    const nums = (await rows.nth(i).locator('.stage-times-nums').boundingBox())!
    expect(nums.x + nums.width).toBeLessThanOrEqual(row.x + row.width + 1)
  }
  const small = await history.locator('summary').evaluateAll((els) =>
    els.map((e) => e.getBoundingClientRect().height).filter((h) => h < 44))
  expect(small).toEqual([])
  await noSideways(page)
  expect(unmocked).toEqual([])
})
