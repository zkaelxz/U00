import { expect, test, type Page, type Request } from '@playwright/test'

// Diagnostics parity (inventory Q01, Q14). Every
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

test('the Setup card shows every check and flags ffmpeg without libass first', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  await page.goto('/#/diagnostics')
  const rows = page.getByRole('list', { name: 'Setup checks' }).locator('li')
  await expect(rows.first()).toContainText('FFmpeg has no libass')
  await expect(rows.first().locator('.pill')).toHaveText('Problem')
  const python = rows.filter({ hasText: 'Python' })
  await expect(python).toContainText('3.11.9')
  await expect(python.locator('.pill')).toHaveText('OK')
  await expect(rows.filter({ hasText: 'JS runtime' })).toContainText(/deno/i)
  await expect(page.getByTestId('diagnostics-summary')).toContainText('1 setup problem')
  expect(unmocked).toEqual([])
})

test('model cache delete is two-step, PC-only and refreshes the list', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  let cache = {
    hf_cache: [{ repo_id: 'org/model', repo_type: 'model', revision: REV, size_bytes: 2048 }],
    hf_total_bytes: 2048,
    model_files: [
      { folder: 'torch' as const, name: 'htdemucs.th', size_bytes: 512 },
      { folder: 'torch' as const, name: 'other.th', size_bytes: 256 },
    ],
    model_files_total_bytes: 768,
  }
  await page.route('**/api/diagnostics/model-cache', (r) => r.fulfill({ json: cache }))
  const sent: Request[] = []
  await page.route('**/api/diagnostics/model-cache/files/torch/htdemucs.th/delete', (r) => {
    sent.push(r.request())
    cache = { ...cache, model_files: cache.model_files.slice(1), model_files_total_bytes: 256 }
    return r.fulfill({ json: { deleted: true, name: 'htdemucs.th' } })
  })
  await page.route(`**/api/diagnostics/model-cache/hf/${REV}/delete`, (r) => {
    sent.push(r.request())
    cache = { ...cache, hf_cache: [], hf_total_bytes: 0 }
    return r.fulfill({ json: { deleted: true, name: REV } })
  })
  await page.goto('/#/diagnostics')
  // Setup has a problem in this mock, so it is already open.
  await page.getByRole('button', { name: 'Delete org/model' }).click()
  expect(sent).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm delete org/model' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Deleted org/model.' })).toBeVisible()
  expect(sent[0].postDataJSON()).toEqual({ confirm: true })
  expect(sent[0].headers()['x-baihe-local']).toBe('1')
  await expect(page.getByRole('list', { name: 'Downloaded models' })).toHaveCount(0)
  await expect(page.getByRole('list', { name: 'Model files' })).toContainText('htdemucs.th (PyTorch hub)')
  await page.getByRole('button', { name: 'Delete htdemucs.th' }).click()
  await page.getByRole('button', { name: 'Confirm delete htdemucs.th' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Deleted htdemucs.th.' })).toBeVisible()
  expect(sent[1].headers()['x-baihe-local']).toBe('1')
  // Something stays listed, so the section (and its notice) is still on screen.
  await expect(page.getByRole('list', { name: 'Model files' })).not.toContainText('htdemucs.th')
  expect(unmocked).toEqual([])
})

