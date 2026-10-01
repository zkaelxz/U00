import { expect, test, type Page, type Request } from '@playwright/test'

// Diagnostics parity (inventory Q01, Q14, job history). Every
// POST is mocked; a catch-all fails the test on any other non-GET /api call.

const overview = {
  dependencies: { jieba: { installed: true, powers: 'Chinese word segmentation', tier: 'feature' } },
  file_completeness: { missing_top_level: [], missing_tabs: [], all_present: true },
  library_writable: true,
  gpu: { available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.' },
  model_engine_versions: [],
  recent_log_lines: [],
}

const setup = {
  python: { version: '3.11.9', ok: true },
  ffmpeg: { found: true, version: 'ffmpeg version 6.1', libass: false },
  js_runtime: { found: true, name: 'deno' },
  cuda: { torch_installed: false, cuda_available: null },
  files: { all_present: true, missing_top_level: [], missing_tabs: [] },
  library_writable: true,
}

const REV = 'a'.repeat(40)

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

async function mockPage(page: Page) {
  await page.route('**/api/diagnostics', (r) => r.fulfill({ json: overview }))
  await page.route('**/api/diagnostics/setup-checks', (r) => r.fulfill({ json: setup }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
}

const openSection = (page: Page, title: RegExp) => page.locator('summary', { hasText: title }).first().click()

test('the Setup card shows every check and flags ffmpeg without libass first', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  await page.goto('/#/diagnostics')
  const rows = page.getByRole('list', { name: 'Setup checks' }).locator('li')
  await expect(rows.first()).toContainText('ffmpeg has no libass')
  await expect(rows.first().locator('.pill')).toHaveText('Problem')
  const python = rows.filter({ hasText: 'Python' })
  await expect(python).toContainText('3.11.9')
  await expect(python.locator('.pill')).toHaveText('OK')
  await expect(rows.filter({ hasText: 'JS runtime' })).toContainText('deno')
  await expect(page.getByTestId('diagnostics-summary')).toContainText('1 setup problem')
  expect(unmocked).toEqual([])
})

test('Job history hides a finished job\'s stale progress text', async ({ page }) => {
  await guard(page)
  await mockPage(page)
  const item = (o: Record<string, unknown>) => ({
    job_id: 'x', label: 'Transcribing', status: 'done', description: null, message: 'Transcribing... 99%', error: null,
    gpu_touching: false, started_at: 1, finished_at: 2, duration_seconds: 1, ...o,
  })
  await page.route('**/api/diagnostics/job-history', (r) => r.fulfill({
    json: [item({ job_id: 'a' }), item({ job_id: 'b', label: 'Translating', status: 'error', error: 'Timed out' })],
  }))
  await page.goto('/#/diagnostics')
  await openSection(page, /^Job history/)
  const history = page.getByRole('list', { name: 'Job history' })
  await history.locator('summary', { hasText: 'Transcribing' }).click()
  await history.locator('summary', { hasText: 'Translating' }).click()
  await expect(history).toContainText('No details.')
  await expect(history).toContainText('Timed out')
  await expect(history).not.toContainText('Transcribing... 99%')
})

test('model cache delete is two-step, PC-only and refreshes the list', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  let cache = {
    hf_cache: [{ repo_id: 'org/model', repo_type: 'model', revision: REV, size_bytes: 2048 }],
    hf_total_bytes: 2048,
    piper_voices: [{ voice: 'en_US-amy-medium', size_bytes: 1024 }],
    piper_total_bytes: 1024,
    model_files: [{ folder: 'torch' as const, name: 'htdemucs.th', size_bytes: 512 }],
    model_files_total_bytes: 512,
  }
  await page.route('**/api/diagnostics/model-cache', (r) => r.fulfill({ json: cache }))
  const sent: Request[] = []
  await page.route('**/api/diagnostics/model-cache/files/torch/htdemucs.th/delete', (r) => {
    sent.push(r.request())
    cache = { ...cache, model_files: [], model_files_total_bytes: 0 }
    return r.fulfill({ json: { deleted: true, name: 'htdemucs.th' } })
  })
  await page.route(`**/api/diagnostics/model-cache/hf/${REV}/delete`, (r) => {
    sent.push(r.request())
    cache = { ...cache, hf_cache: [], hf_total_bytes: 0 }
    return r.fulfill({ json: { deleted: true, name: REV } })
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Model cache/)
  await page.getByRole('button', { name: 'Delete org/model' }).click()
  expect(sent).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm delete org/model' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Deleted org/model.' })).toBeVisible()
  expect(sent[0].postDataJSON()).toEqual({ confirm: true })
  expect(sent[0].headers()['x-baihe-local']).toBe('1')
  await expect(page.getByRole('list', { name: 'Downloaded models' })).toHaveCount(0)
  await expect(page.getByRole('list', { name: 'Piper voices' })).toContainText('en_US-amy-medium')
  await expect(page.getByRole('list', { name: 'Model files' })).toContainText('htdemucs.th (PyTorch hub)')
  await page.getByRole('button', { name: 'Delete htdemucs.th' }).click()
  await page.getByRole('button', { name: 'Confirm delete htdemucs.th' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Deleted htdemucs.th.' })).toBeVisible()
  expect(sent[1].headers()['x-baihe-local']).toBe('1')
  await expect(page.getByRole('list', { name: 'Model files' })).toHaveCount(0)
  expect(unmocked).toEqual([])
})

test('Job history shows time by stage once an entry is opened, fetched lazily', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  const item = (o: Record<string, unknown>) => ({
    job_id: 'x', label: 'Translating', status: 'done', description: null, message: '', error: null,
    gpu_touching: false, started_at: 1, finished_at: 2, duration_seconds: 90, ...o,
  })
  await page.route('**/api/diagnostics/job-history', (r) => r.fulfill({
    json: [item({ job_id: 'a' }), item({ job_id: 'b', label: 'Transcribing' }), item({ job_id: 'c', label: 'Dubbing' })],
  }))
  const asked: string[] = []
  await page.route('**/api/jobs/*/stages', (r) => {
    const id = new URL(r.request().url()).pathname.split('/')[3]
    asked.push(id)
    if (id === 'c') return r.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } })
    if (id === 'b') return r.fulfill({ json: { job_id: 'b', runs: [] } })
    return r.fulfill({ json: { job_id: 'a', runs: [
      { run_started_at: 100, running: false, total_seconds: 90, cost_usd: 0.03, stages: [
        { stage: 'Preparing', started_at: 100, duration_seconds: 3.4, cost_usd: 0 },
        { stage: 'Translating', started_at: 103.4, duration_seconds: 72, cost_usd: 0.03 },
        { stage: 'Saving', started_at: 175.4, duration_seconds: 14.6, cost_usd: 0 },
      ] },
      { run_started_at: 10, running: false, total_seconds: 5, cost_usd: 0, stages: [] },
    ] } })
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Job history/)
  const history = page.getByRole('list', { name: 'Job history' })
  await expect(history.locator('summary')).toHaveCount(3)
  expect(asked).toEqual([])

  await history.locator('summary', { hasText: 'Translating' }).click()
  const stages = history.getByRole('list', { name: 'Time by stage' })
  const rows = stages.locator('li')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toContainText('Preparing')
  await expect(rows.nth(0)).toContainText('3.4 s')
  await expect(rows.nth(1)).toContainText('1 min 12 s · $0.03')
  await expect(rows.nth(2)).not.toContainText('$')
  await expect(history).toContainText('Total 1 min 30 s · estimated $0.03')
  await expect(history).toContainText('Latest of 2 runs.')
  // The bar is the stage's share of the total: 72 of 90 s = 80%.
  const bar = rows.nth(1).locator('.stage-times-bar > span')
  expect(await bar.evaluate((e) => (e as HTMLElement).style.width)).toBe('80%')

  await history.locator('summary', { hasText: 'Transcribing' }).click()
  await expect(history).toContainText('No stage timing recorded for this job.')
  await history.locator('summary', { hasText: 'Dubbing' }).click()
  await expect(history).toContainText('Stage timing unavailable.')

  // Closing and reopening does not fetch again.
  await history.locator('summary', { hasText: 'Translating' }).click()
  await history.locator('summary', { hasText: 'Translating' }).click()
  await expect(rows).toHaveCount(3)
  expect(asked).toEqual(['a', 'b', 'c'])
  expect(unmocked).toEqual([])
})
