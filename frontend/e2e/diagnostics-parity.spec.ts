import { expect, test, type Page, type Request } from '@playwright/test'

// Diagnostics parity (inventory Q01, Q14, job history, bug bundles). Every
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

test('core checks sit at the top and flag ffmpeg without libass', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  await page.goto('/#/diagnostics')
  const core = page.getByRole('list', { name: 'Core checks' })
  await expect(core).toContainText('Python: 3.11.9')
  await expect(core).toContainText('Problem: ffmpeg has no libass')
  await expect(core).toContainText('JS runtime: deno')
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
  }
  await page.route('**/api/diagnostics/model-cache', (r) => r.fulfill({ json: cache }))
  const sent: Request[] = []
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
  expect(unmocked).toEqual([])
})

test('saved bug bundles list and delete', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  let bundles = [{
    id: 4, drama_id: 1, drama_title: 'Signal', line_id: 3, label: 'Bad pronoun', engine: 'claude', model: 'm',
    produced_output: 'He went home.', replayed: true, replay_output: 'She went home.', reproduced: false,
    created_at: '2026-09-29T12:00:00',
  }]
  await page.route('**/api/diagnostics/bug-bundles', (r) => r.fulfill({ json: bundles }))
  const bodies: unknown[] = []
  await page.route('**/api/diagnostics/bug-bundles/4/delete', (r) => {
    bodies.push(r.request().postDataJSON())
    bundles = []
    return r.fulfill({ json: { bundle_id: 4, deleted: true } })
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Saved bug bundles/)
  const list = page.getByRole('list', { name: 'Saved bug bundles' })
  await list.locator('summary', { hasText: '#4 Bad pronoun · Signal' }).click()
  await expect(list).toContainText('Output when saved: He went home.')
  await expect(list).toContainText('No longer reproduces')
  await list.getByRole('button', { name: 'Delete bundle #4' }).click()
  await list.getByRole('button', { name: 'Confirm delete bundle #4' }).click()
  await expect(page.locator('summary', { hasText: /^Saved bug bundles/ })).toHaveCount(0)
  expect(bodies).toEqual([{ confirm: true }])
  expect(unmocked).toEqual([])
})
