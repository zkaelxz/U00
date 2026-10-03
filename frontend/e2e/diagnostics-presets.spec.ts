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
    transformers: { installed: true, powers: 'local NLLB-200', tier: 'feature' },
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
      roles: { cv2: 'required', torch: 'recommended', streamlit_drawable_canvas: 'optional' },
      required_missing: ['cv2'], optional_missing: [],
      to_install: ['cv2', 'torch'], approx_mb: 2545,
    },
    {
      id: 'hardsub_ocr', group: 'Video', label: 'Read burned-in captions (OCR)', help: 'Hard subtitles.',
      packages: ['pypinyin', 'transformers', 'paddleocr'], installed_count: 2,
      roles: { pypinyin: 'required', transformers: 'recommended', paddleocr: 'optional' },
      required_missing: [], optional_missing: ['paddleocr'], to_install: [], approx_mb: 0,
    },
    {
      id: 'alt_asr', group: 'Audio', label: 'Qwen3-ASR / SenseVoice transcription', help: 'Alternative engines.',
      packages: ['qwen-asr'], installed_count: 0, to_install: ['qwen-asr'], approx_mb: 30,
    },
  ],
  packages: {
    jieba: pkg('jieba', { approx_mb: 20 }),
    pypinyin: pkg('pypinyin', { installed: true, installable: false, installed_version: '0.53.0', min_version: '0.50' }),
    transformers: pkg('transformers', {
      installed: true, installable: false, installed_version: '4.40.0', min_version: '4.46', below_min: true,
    }),
    paddleocr: pkg('paddleocr', { approx_mb: 600 }),
    pandas: pkg('pandas', { installed: true, installable: false, installed_version: '2.2.3' }),
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

const CU128 = {
  variant: 'cu128', label: 'NVIDIA GPU (CUDA 12.8)', index_url: 'https://download.pytorch.org/whl/cu128',
  versions: { torch: '2.11.0+cu128', torchvision: '0.26.0+cu128', torchaudio: '2.11.0+cu128' }, needs_nvidia: true,
}
const VERIFY = {
  torch: '2.11.0+cu128', torchvision: '0.26.0+cu128', torchaudio: '2.11.0+cu128', cuda_build: '12.8',
  cuda_available: true, device: 'NVIDIA GeForce RTX 3080 Ti', error: null,
}
const gpuTorch = (probe: boolean) => ({
  nvidia: {
    found: true, gpu_name: 'NVIDIA GeForce RTX 3080 Ti', driver_version: '581.42', status: 'ok',
    recommended: '570.65', minimum: '528.33',
  },
  installed: [
    { name: 'torch', version: '2.11.0+cu128', build: 'cuda' },
    { name: 'torchvision', version: '0.29.0', build: null },
    { name: 'torchaudio', version: '2.11.0+cu128', build: 'cuda' },
  ],
  problems: ['torchvision 0.29.0 doesn\'t match torch 2.11.0+cu128 (torch 2.11 needs torchvision 0.26.x).'],
  state: 'mismatched', python_supported: true, recommended: CU128,
  variants: [CU128], probe: probe ? { ...VERIFY, torchvision: '0.29.0' } : null,
})

const UPDATES = {
  checked_at: 1_790_000_000,
  packages: {
    pypinyin: {
      name: 'pypinyin', dist: 'pypinyin', installed_version: '0.53.0', status: 'update', latest: '0.55.0',
      target: '0.55.0', reason: null,
    },
    transformers: {
      name: 'transformers', dist: 'transformers', installed_version: '4.40.0', status: 'held_back', latest: '6.0.0',
      target: null, reason: 'held back by constraints.txt (transformers<6)',
    },
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
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch', (r) => r.fulfill({ json: gpuTorch(false) }))
  // The CUDA check is a POST (CSRF-checked); it doesn't count as an install.
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch/check', (r) => r.fulfill({ json: gpuTorch(true) }))
  await page.route((u) => u.pathname === '/api/diagnostics/package-updates/check', (r) => {
    sent.push(r.request())
    return r.fulfill({ json: UPDATES })
  })
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch/setup', (r) => {
    sent.push(r.request())
    return r.fulfill({
      json: { package: 'torch', ok: true, output_tail: ['Successfully installed torch'], hint: null, variant: 'cu128', verify: VERIFY },
    })
  })
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

  await expect(page.getByTestId('install-tasks').getByRole('heading', { name: 'Install by task' })).toBeVisible()
  // The task groups start folded; open each to see its tasks.
  for (const summary of await page.getByTestId('install-tasks').locator('summary').all()) await summary.click()
  const zh = page.getByTestId('task-reader_zh')
  await expect(zh.locator('.pill')).toHaveText('1 of 3 installed')
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

test('a task lists its missing packages with size, a safe Source link, and no Install for a not-offered one', async ({ page }) => {
  const { unmocked } = await mockPage(page)
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  for (const summary of await page.getByTestId('install-tasks').locator('details.section > summary').all()) await summary.click()
  const scan = page.getByTestId('task-details-scanlate')
  await scan.locator('summary').click()
  const cv2 = scan.locator('li', { hasText: 'cv2' })
  await expect(cv2).toContainText('approx. 45 MB')
  const link = cv2.getByRole('link', { name: /Source: opencv-python on PyPI/ })
  await expect(link).toHaveAttribute('href', 'https://pypi.org/project/opencv-python/')
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  // A required package is installed with its task, not one by one.
  await expect(cv2.getByRole('button')).toHaveCount(1) // Source link only
  await expect(scan.locator('li', { hasText: 'streamlit_drawable_canvas' }).getByRole('button')).toHaveCount(0)
  await expect(scan.locator('li', { hasText: 'torch' })).toContainText('approx. 2.5 GB')
  await expect(page.getByTestId('task-scanlate')).toContainText('streamlit_drawable_canvas: not offered')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/packages-missing.png`, fullPage: true })

  // An optional extra is installed on its own, from its task.
  const ocr = page.getByTestId('task-details-hardsub_ocr')
  await ocr.locator('summary').click()
  await ocr.getByRole('button', { name: 'Install paddleocr' }).click()
  await page.getByRole('button', { name: 'Confirm install paddleocr' }).click()
  await expect(page.getByTestId('install-result')).toContainText('Installed paddleocr.')
  await expect(page.getByTestId('install-hint')).toHaveCount(0)
  expect(unmocked).toEqual([])
})

test('GPU PyTorch: shows the GPU, the mismatch, checks CUDA, and sets up the matched set', async ({ page }) => {
  const { sent, unmocked } = await mockPage(page)
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  const panel = page.getByTestId('gpu-torch')
  // A mismatch is a problem, so the fold is open by itself.
  await expect(page.locator('details', { has: panel }).locator('> summary')).toContainText('GPU PyTorch')
  await expect(page.locator('details', { has: panel })).toHaveAttribute('open', '')
  await expect(panel.locator('.pill')).toHaveText("Versions don't match")
  await expect(panel.getByTestId('gpu-torch-state')).toContainText("don't match")
  await expect(panel.getByTestId('gpu-torch-driver')).toHaveText('NVIDIA GeForce RTX 3080 Ti, driver 581.42')
  await expect(panel).toContainText('torchvision 0.29.0 doesn\'t match torch 2.11.0+cu128')
  // Installed beside the recommended matched set.
  const versions = panel.getByRole('table', { name: 'PyTorch versions' })
  await expect(versions.locator('tbody tr')).toHaveText([
    /torch\s*2\.11\.0\+cu128 \(CUDA build\)\s*2\.11\.0\+cu128/,
    /torchvision\s*0\.29\.0\s*0\.26\.0\+cu128/,
    /torchaudio\s*2\.11\.0\+cu128 \(CUDA build\)\s*2\.11\.0\+cu128/,
  ])
  await expect(panel.getByTestId('gpu-torch-recommended')).toContainText(
    'Recommended: NVIDIA GPU (CUDA 12.8), from https://download.pytorch.org/whl/cu128')

  await panel.getByRole('button', { name: 'Check CUDA' }).click()
  await expect(panel.getByTestId('gpu-torch-probe')).toHaveText('torch 2.11.0+cu128: CUDA works on NVIDIA GeForce RTX 3080 Ti.')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/packages-gpu-torch.png`, fullPage: true })

  await panel.getByRole('button', { name: 'Set up GPU PyTorch' }).click()
  expect(sent).toHaveLength(0)
  await page.getByRole('button', { name: 'Confirm install GPU PyTorch (about 2.5 GB)' }).click()
  await expect(page.getByTestId('install-result')).toContainText('PyTorch is set up. Restart Baihe to load it.')
  expect(sent).toHaveLength(1)
  expect(sent[0].postDataJSON()).toEqual({ confirm: true, variant: 'cu128' })
  expect(sent[0].headers()['x-baihe-local']).toBe('1')
  expect(unmocked).toEqual([])
})

test('installed packages show versions; Update appears only after a check, with its target', async ({ page }) => {
  const { sent, unmocked } = await mockPage(page)
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  await openSection(page, /^Installed packages/)
  const list = page.getByRole('list', { name: 'Installed packages' })
  const pinyin = list.locator('li', { hasText: 'pypinyin' })
  await expect(pinyin.getByTestId('pkg-version')).toHaveText('v0.53.0')
  await expect(list.locator('li', { hasText: 'pandas' }).getByTestId('pkg-version')).toHaveText('v2.2.3')
  await expect(list.getByRole('button')).toHaveCount(0)
  expect(sent).toHaveLength(0) // nothing asked PyPI on load

  await page.getByRole('button', { name: 'Check for updates' }).click()
  await expect(page.getByTestId('update-check')).toContainText('1 update available, 1 held back')
  expect(sent.map((r) => new URL(r.url()).pathname)).toEqual(['/api/diagnostics/package-updates/check'])
  await expect(pinyin.getByTestId('pkg-update')).toHaveText('Update to 0.55.0 available')
  await expect(list.locator('li', { hasText: 'transformers' }).getByTestId('pkg-update')).toHaveText(
    'Newer 6.0.0 exists but is held back by constraints.txt (transformers<6)')
  await expect(list.locator('li', { hasText: 'transformers' }).getByRole('button')).toHaveCount(0)
  await expect(list.locator('li', { hasText: 'pandas' }).getByRole('button')).toHaveCount(0)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/packages-updates.png`, fullPage: true })

  await pinyin.getByRole('button', { name: 'Update pypinyin to 0.55.0' }).click()
  await page.getByRole('button', { name: 'Confirm update pypinyin to 0.55.0' }).click()
  await expect(page.getByTestId('install-result')).toContainText('Updated pypinyin.')
  expect(new URL(sent[1].url()).pathname).toBe('/api/diagnostics/dependencies/pypinyin/upgrade')
  expect(sent[1].postDataJSON()).toEqual({ confirm: true, target: '0.55.0' })
  // An update can move other packages too, so the whole check is dropped:
  // no stale targets or Update buttons stay on the page until the next check.
  // (The version shown comes from the page's reload of Diagnostics.)
  await expect(list.getByTestId('pkg-update')).toHaveCount(0)
  await expect(list.getByRole('button')).toHaveCount(0)
  expect(unmocked).toEqual([])
})

test('tasks label each package Required / Recommended / Optional with the app\'s minimum version', async ({ page }) => {
  const { unmocked } = await mockPage(page)
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)

  const scan = page.getByTestId('task-scanlate')
  await expect(scan.locator('.pill')).toHaveText('Needs 1 required package')
  await expect(scan.locator('.pill')).toHaveClass(/pill-warn/)
  await expect(scan.getByTestId('task-pkg-cv2')).toContainText('cv2 · Required')
  await expect(scan.getByTestId('task-pkg-torch')).toContainText('torch · Recommended')
  await expect(scan.getByTestId('task-pkg-streamlit_drawable_canvas')).toContainText('· Optional')

  const ocr = page.getByTestId('task-hardsub_ocr')
  await expect(ocr).toContainText('Ready')
  await expect(ocr.getByTestId('task-pkg-pypinyin')).toHaveText('✓ pypinyin · Required ≥ 0.50')
  await expect(ocr.getByTestId('task-optional')).toHaveText('Optional, not installed by this button: paddleocr')
  await expect(ocr).toContainText('transformers 4.40.0 is older than the 4.46 the app needs.')
  await expect(ocr.getByRole('button')).toHaveCount(0)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/packages-roles.png`, fullPage: true })

  await openSection(page, /^Installed packages/)
  await expect(page.getByRole('list', { name: 'Installed packages' }).locator('li', { hasText: 'transformers' })
    .getByTestId('pkg-below-min')).toBeVisible()
  expect(unmocked).toEqual([])
})
