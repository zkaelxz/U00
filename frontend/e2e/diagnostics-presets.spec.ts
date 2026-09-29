import { expect, test, type Page, type Request } from '@playwright/test'

import { ME } from './authMocks'

// Packages > Install by task, sizes, Source links, not-offered packages and
// the pip-cache hint. Every install POST is mocked; a catch-all fails the
// test on any other non-GET /api call, so no real pip run can happen.
// PRESETS_SCREENS_DIR=<dir> saves review screenshots.

const SHOTS = process.env.PRESETS_SCREENS_DIR

const overview = {
  dependencies: {
    jieba: { installed: false, powers: 'Chinese word segmentation', tier: 'feature' },
    pypinyin: { installed: true, powers: 'Chinese pinyin', tier: 'feature' },
    cv2: { installed: false, powers: 'Scanlate bubble detection/inpainting', tier: 'feature' },
    streamlit_drawable_canvas: { installed: false, powers: 'Scanlate manual erase/heal brush', tier: 'feature' },
    torch: { installed: false, powers: 'ML backends', tier: 'feature' },
    pandas: { installed: true, powers: 'tables', tier: 'required' },
  },
  file_completeness: { missing_top_level: [], missing_tabs: [], all_present: true },
  library_writable: true,
  gpu: { available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.' },
  model_engine_versions: [
    { name: 'Qwen3-ASR', version: 'not installed', url: '', installed: false, package: 'qwen-asr', help: 'Alternative ASR.' },
  ],
  recent_log_lines: [],
}

const setup = {
  python: { version: '3.12.4', ok: true },
  ffmpeg: { found: true, version: '6.1' },
  js_runtime: { found: true, name: 'deno' },
  cuda: { torch_installed: false, cuda_available: null },
  files: { all_present: true, missing_top_level: [], missing_tabs: [] },
  library_writable: true,
}

const pkg = (name: string, o: Record<string, unknown> = {}) => ({
  name, dist: name, installed: false, installable: true, powers: '', approx_mb: 1, pulls_torch: false,
  source_url: `https://pypi.org/project/${name}/`, not_offered_reason: null, warning: null, ...o,
})

const presets = {
  tasks: [
    {
      id: 'reader_zh', group: 'Novels & reader', label: 'Chinese reader tools', help: 'Word splitting and pinyin.',
      packages: ['jieba', 'pypinyin', 'opencc-python-reimplemented'], installed_count: 1,
      to_install: ['jieba', 'opencc-python-reimplemented'], approx_mb: 21,
    },
    {
      id: 'scanlate', group: 'Scanlate', label: 'Scanlate (manga/manhua pages)', help: 'Bubble detection.',
      packages: ['cv2', 'torch', 'streamlit_drawable_canvas'], installed_count: 0,
      to_install: ['cv2', 'torch'], approx_mb: 2545,
    },
    {
      id: 'alt_asr', group: 'Audio', label: 'Qwen3-ASR / SenseVoice transcription', help: 'Alternative engines.',
      packages: ['qwen-asr'], installed_count: 0, to_install: ['qwen-asr'], approx_mb: 30,
    },
  ],
  packages: {
    jieba: pkg('jieba', { approx_mb: 20 }),
    pypinyin: pkg('pypinyin', { installed: true, installable: false }),
    'opencc-python-reimplemented': pkg('opencc-python-reimplemented'),
    cv2: pkg('cv2', { dist: 'opencv-python', approx_mb: 45, source_url: 'https://pypi.org/project/opencv-python/' }),
    torch: pkg('torch', { approx_mb: 2500 }),
    streamlit_drawable_canvas: pkg('streamlit_drawable_canvas', {
      installable: false, approx_mb: 5, source_url: 'https://pypi.org/project/streamlit-drawable-canvas/',
      not_offered_reason: 'not offered: it fails to set up with this app\'s pinned Streamlit.',
    }),
    'qwen-asr': pkg('qwen-asr', {
      approx_mb: 30, pulls_torch: true,
      warning: 'installing this would downgrade transformers from 5.2.0 to 4.57.6',
    }),
  },
}

const HINT = 'pip couldn\'t write to its download cache. Close other Python windows, or delete %LOCALAPPDATA%\\pip\\cache.'

async function mockPage(page: Page): Promise<{ sent: Request[]; unmocked: string[] }> {
  const unmocked: string[] = []
  const sent: Request[] = []
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
  await page.route('**/api/diagnostics/dependencies/**', (r) => {
    sent.push(r.request())
    const name = decodeURIComponent(r.request().url().split('/dependencies/')[1].split('/')[0])
    const ok = name !== 'opencc-python-reimplemented'
    return r.fulfill({
      json: {
        package: name, ok,
        output_tail: ok ? [`Successfully installed ${name}`] : ['ERROR: [Errno 13] Permission denied'],
        hint: ok ? null : HINT,
      },
    })
  })
  return { sent, unmocked }
}

const openSection = (page: Page, title: RegExp) => page.locator('summary', { hasText: title }).first().click()

test('tasks list what they need, sizes, links, and install one package at a time', async ({ page }) => {
  const { sent, unmocked } = await mockPage(page)
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  await openSection(page, /^Install by task/)

  const zh = page.getByTestId('task-reader_zh')
  await expect(zh).toContainText('1 of 3 installed')
  await expect(zh).toContainText('approx. 21 MB to download')
  await expect(page.getByTestId('task-scanlate')).toContainText('approx. 2.5 GB to download')
  await expect(page.getByTestId('task-scanlate')).toContainText('streamlit_drawable_canvas: not offered')
  await expect(page.getByTestId('task-alt_asr')).toContainText('would downgrade transformers')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/packages-by-task.png`, fullPage: true })

  await page.getByRole('button', { name: 'Install for Chinese reader tools' }).click()
  expect(sent).toHaveLength(0) // first press only arms
  await page.getByRole('button', { name: 'Confirm install 2 packages (approx. 21 MB)' }).click()
  await expect(page.getByTestId('install-result')).toContainText(
    'Chinese reader tools: install failed for opencc-python-reimplemented (1 installed).')
  expect(sent.map((r) => r.url().split('/dependencies/')[1])).toEqual([
    'jieba/install', 'opencc-python-reimplemented/install',
  ])
  for (const r of sent) {
    expect(r.postDataJSON()).toEqual({ confirm: true })
    expect(r.headers()['x-baihe-local']).toBe('1')
  }
  await expect(page.getByTestId('install-hint')).toContainText('%LOCALAPPDATA%\\pip\\cache')
  expect(unmocked).toEqual([])
})

test('missing packages show size, a safe Source link, and no Install for a not-offered one', async ({ page }) => {
  const { unmocked } = await mockPage(page)
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  await openSection(page, /^Missing packages/)
  const missing = page.getByRole('list', { name: 'Missing packages' })

  const cv2 = missing.locator('li', { hasText: 'cv2' })
  await expect(cv2).toContainText('approx. 45 MB')
  const link = cv2.getByRole('link', { name: /Source: opencv-python on PyPI/ })
  await expect(link).toHaveAttribute('href', 'https://pypi.org/project/opencv-python/')
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(cv2.getByRole('button', { name: 'Install cv2' })).toBeVisible()

  const canvas = missing.locator('li', { hasText: 'streamlit_drawable_canvas' })
  await expect(canvas.getByTestId('pkg-not-offered')).toContainText('not offered')
  await expect(canvas.getByRole('button')).toHaveCount(0)

  await expect(missing.locator('li', { hasText: 'torch' })).toContainText('approx. 2.5 GB')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/packages-missing.png`, fullPage: true })

  // A single-package failure shows the hint too.
  await cv2.getByRole('button', { name: 'Install cv2' }).click()
  await page.getByRole('button', { name: 'Confirm install cv2' }).click()
  await expect(page.getByTestId('install-result')).toContainText('Installed cv2.')
  await expect(page.getByTestId('install-hint')).toHaveCount(0)
  expect(unmocked).toEqual([])
})
