import { test, type Page } from '@playwright/test'

import { ME } from './authMocks'

// Review screenshots for the Diagnostics redesign (UI refresh task 7).
// Skipped unless DIAG_SHOTS_DIR=<dir> is set. Every non-GET /api call is
// aborted, and each GET the page makes is mocked with a realistic state:
// a setup problem, a running job, packages with presets, GPU PyTorch,
// the log and the model cache.

const DIR = process.env.DIAG_SHOTS_DIR
test.skip(!DIR, 'set DIAG_SHOTS_DIR to save screenshots')

const long = 'Traceback (most recent call last): File "translate_engines.py", line 812, in _request_translations_with_retry'

const overview = {
  dependencies: {
    jieba: { installed: true, powers: 'Chinese word segmentation', tier: 'feature' },
    pypinyin: { installed: true, powers: 'Chinese pinyin', tier: 'feature' },
    pandas: { installed: true, powers: 'tables', tier: 'required' },
    transformers: { installed: true, powers: 'local NLLB-200', tier: 'feature' },
    'yt-dlp': { installed: false, powers: 'downloading video', tier: 'feature' },
    cv2: { installed: false, powers: 'Scanlate bubble detection/inpainting', tier: 'feature' },
    torch: { installed: false, powers: 'ML backends', tier: 'feature' },
  },
  file_completeness: { missing_top_level: [], missing_tabs: [], all_present: true },
  library_writable: true,
  gpu: { available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.' },
  model_engine_versions: [
    { name: 'faster-whisper', version: '1.1.0', url: '', installed: true, package: 'faster-whisper', help: 'Transcription.' },
    { name: 'Qwen3-ASR', version: 'not installed', url: '', installed: false, package: 'qwen-asr', help: 'Alternative ASR.' },
  ],
  recent_log_lines: [],
}

const setup = {
  python: { version: '3.12.4', ok: true },
  ffmpeg: { found: true, version: 'ffmpeg version 6.1', libass: false },
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
      packages: ['jieba', 'pypinyin', 'opencc-python-reimplemented'], installed_count: 2,
      to_install: ['opencc-python-reimplemented'], approx_mb: 1,
    },
    {
      id: 'download', group: 'Video', label: 'Download from video sites', help: 'Paste a link to a drama episode.',
      packages: ['yt-dlp'], installed_count: 0, to_install: ['yt-dlp'], approx_mb: 3,
    },
    {
      id: 'scanlate', group: 'Scanlate', label: 'Scanlate (manga/manhua pages)', help: 'Bubble detection.',
      packages: ['cv2', 'torch'], installed_count: 0,
      roles: { cv2: 'required', torch: 'recommended' }, required_missing: ['cv2'], optional_missing: [],
      to_install: ['cv2', 'torch'], approx_mb: 2545,
    },
  ],
  packages: {
    jieba: pkg('jieba', { installed: true, installable: false, installed_version: '0.42.1' }),
    pypinyin: pkg('pypinyin', { installed: true, installable: false, installed_version: '0.53.0' }),
    pandas: pkg('pandas', { installed: true, installable: false, installed_version: '2.2.3' }),
    transformers: pkg('transformers', { installed: true, installable: false, installed_version: '4.57.6' }),
    'opencc-python-reimplemented': pkg('opencc-python-reimplemented'),
    'yt-dlp': pkg('yt-dlp', { approx_mb: 3 }),
    cv2: pkg('cv2', { dist: 'opencv-python', approx_mb: 45, source_url: 'https://pypi.org/project/opencv-python/' }),
    torch: pkg('torch', { approx_mb: 2500 }),
    'qwen-asr': pkg('qwen-asr', { approx_mb: 30, pulls_torch: true }),
  },
}

const CU128 = {
  variant: 'cu128', label: 'NVIDIA GPU (CUDA 12.8)', index_url: 'https://download.pytorch.org/whl/cu128',
  versions: { torch: '2.11.0+cu128', torchvision: '0.26.0+cu128', torchaudio: '2.11.0+cu128' }, needs_nvidia: true,
}
const gpuTorch = {
  nvidia: {
    found: true, gpu_name: 'NVIDIA GeForce RTX 3080 Ti', driver_version: '581.42', status: 'ok',
    recommended: '570.65', minimum: '528.33',
  },
  installed: [
    { name: 'torch', version: null, build: null },
    { name: 'torchvision', version: null, build: null },
    { name: 'torchaudio', version: null, build: null },
  ],
  problems: [], state: 'missing', python_supported: true, recommended: CU128, variants: [CU128], probe: null,
}

async function mockPage(page: Page) {
  await page.route('**/api/**', (route) => {
    const r = route.request()
    return r.method() === 'GET' ? route.continue() : route.abort()
  })
  await page.route((u) => u.pathname === '/api/auth/me', (r) => r.fulfill({ json: ME.authOff }))
  await page.route('**/api/diagnostics', (r) => r.fulfill({ json: overview }))
  await page.route('**/api/diagnostics/setup-checks', (r) => r.fulfill({ json: setup }))
  await page.route('**/api/diagnostics/install-presets', (r) => r.fulfill({ json: presets }))
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch', (r) => r.fulfill({ json: gpuTorch }))
  await page.route('**/api/diagnostics/log**', (r) => r.fulfill({
    json: { lines: ['2026-09-29 12:00:01 INFO started', `2026-09-29 12:01:07 ERROR ${long}`, '2026-09-29 12:02:00 INFO job done'] },
  }))
  await page.route('**/api/diagnostics/pyannote**', (r) => r.fulfill({
    json: { pyannote_installed: true, hf_token_configured: false, ready: false, models: null },
  }))
  await page.route('**/api/diagnostics/model-cache', (r) => r.fulfill({ json: {
    hf_cache: [{ repo_id: 'Systran/faster-whisper-large-v3', repo_type: 'model', revision: 'a'.repeat(40), size_bytes: 3_100_000_000 }],
    hf_total_bytes: 3_100_000_000,
    piper_voices: [{ voice: 'en_US-amy-medium', size_bytes: 63_000_000 }],
    piper_total_bytes: 63_000_000,
    model_files: [],
    model_files_total_bytes: 0,
  } }))
  await page.route('**/api/diagnostics/job-history', (r) => r.fulfill({ json: [
    { job_id: 'a', label: 'Transcribing', status: 'done', description: 'Transcribe Signal ep 3', message: null, error: null,
      gpu_touching: true, started_at: 1, finished_at: 200, duration_seconds: 199 },
    { job_id: 'b', label: 'Translating', status: 'error', description: 'Translate Signal ep 2', message: null, error: 'Timed out',
      gpu_touching: false, started_at: 1, finished_at: 62, duration_seconds: 61 },
  ] }))
  await page.route('**/api/library/stats', (r) => r.fulfill({
    json: { total_dramas: 3, total_lines: 1210, by_status: {}, by_media_type: {}, translated_lines: 0, usage: {} },
  }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { count: 1, items: [{
    job_id: 'translate_1', status: 'running', progress: 0.4, message: 'Batch 2 of 5', error: null,
    description: 'Translate Signal', gpu_touching: false, started_at: Date.now() / 1000 - 185, finished_at: null, updated_at: 0,
  }] } }))
}

/** Opens every closed fold (nested ones too); the before-shots also press the old Build report button. */
async function openAll(page: Page) {
  // Outer folds first: an inner summary is only clickable once its parent is open.
  for (let i = 0; i < 40; i++) {
    const next = page.locator('details:not([open]) > summary').filter({ visible: true }).first()
    if (!(await next.count())) break
    await next.click()
    await page.waitForTimeout(100)
  }
  const build = page.getByRole('button', { name: 'Build report' })
  if (await build.count()) await build.click()
  await page.waitForTimeout(500)
}

const VIEWS = [
  { name: 'desktop-dark', viewport: { width: 1280, height: 800 }, colorScheme: 'dark' as const, touch: false },
  { name: 'desktop-light', viewport: { width: 1280, height: 800 }, colorScheme: 'light' as const, touch: false },
  { name: 'phone-dark', viewport: { width: 390, height: 844 }, colorScheme: 'dark' as const, touch: true },
  { name: 'phone-light', viewport: { width: 390, height: 844 }, colorScheme: 'light' as const, touch: true },
  { name: 'phone360-dark', viewport: { width: 360, height: 800 }, colorScheme: 'dark' as const, touch: true },
]

for (const v of VIEWS) {
  test.describe(v.name, () => {
    test.use({ viewport: v.viewport, colorScheme: v.colorScheme, hasTouch: v.touch, isMobile: v.touch })
    test(`diagnostics ${v.name}`, async ({ page }) => {
      test.setTimeout(120_000)
      await mockPage(page)
      await page.goto('/#/diagnostics')
      await page.getByTestId('diagnostics-summary').getByText(/setup problem/).waitFor()
      await page.waitForTimeout(500)
      await page.screenshot({ path: `${DIR}/${v.name}-top.png` })
      await page.screenshot({ path: `${DIR}/${v.name}-page.png`, fullPage: true })
      await openAll(page)
      await page.screenshot({ path: `${DIR}/${v.name}-open.png`, fullPage: true })
    })
  })
}
