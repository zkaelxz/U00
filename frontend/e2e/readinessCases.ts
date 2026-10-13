import { expect, test, type Page } from '@playwright/test'

import { openAdvanced, enableDeveloperMode } from './sourceHelpers'

// What is installed decides which Transcribe controls work. GET /api/setup/readiness is mocked
// (the seeded server answers "everything present" to every other spec); the transcribe config is the
// seeded one with Whisper mode on. Shared by the desktop and phone specs.

const FIELDS = [
  'faster_whisper', 'torch', 'torchaudio', 'uroman', 'soundfile', 'audio_separator', 'demucs', 'pyannote', 'onnxruntime', 'openai',
  'anthropic', 'yt_dlp', 'pypdf', 'genanki', 'ebooklib', 'cv2', 'jieba', 'sudachi', 'kiwi', 'nagisa', 'soynlp', 'pytesseract',
  'paddle_ocr', 'transformers_ok', 'qwen3_asr', 'ffmpeg', 'ffmpeg_libass', 'js_runtime', 'tesseract_program', 'gpu_found',
  'torch_cuda', 'hf_token_configured', 'key_claude', 'key_deepseek', 'key_gemini', 'key_openai', 'key_groq', 'ollama_reachable',
  'ollama_model_pulled',
]
const ALL = (on: boolean) => Object.fromEntries(FIELDS.map((f) => [f, on]))
/** A fresh GPU server: an NVIDIA card, FFmpeg, and nothing else. */
const FRESH = { ...ALL(false), ffmpeg: true, ffmpeg_libass: true, gpu_found: true }
const READY = ALL(true)

async function setup(page: Page, readiness: Record<string, boolean>, config: Record<string, unknown> = {}) {
  await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: readiness }))
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const real = await route.fetch()
    await route.fulfill({
      response: real,
      json: { ...(await real.json()), transcript_mode: 'whisper', asr_backend_choice: 'whisper', whisper_installed: readiness.faster_whisper, ...config },
    })
  })
  // The Whisper row of the card reads the install presets: they must agree with readiness, as they do on a real PC.
  const whisper = readiness.faster_whisper
  await page.route('**/api/diagnostics/install-presets', (r) => r.fulfill({
    json: {
      tasks: [{
        id: 'transcribe', group: 'Audio', label: 'Transcribe speech (Whisper)', help: 'Turn audio into timed lines.',
        packages: ['faster_whisper'], installed_count: whisper ? 1 : 0, to_install: whisper ? [] : ['faster_whisper'], approx_mb: 80,
      }],
      packages: {
        faster_whisper: {
          name: 'faster_whisper', dist: 'faster_whisper', installed: whisper, installable: !whisper, powers: '', approx_mb: 80,
          pulls_torch: false, source_url: 'https://pypi.org/project/faster_whisper/', not_offered_reason: null, warning: null,
        },
      },
    },
  }))
  await page.route('**/api/media/dramas/1/status', (r) =>
    r.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }))
  await page.route('**/api/metadata/dramas/1/analyze-media', (r) =>
    r.fulfill({ json: { drama_id: 1, duration_seconds: 600, has_video: false, has_audio: true, audio_track_count: 1, sample_rate: 44100 } }))
}

const transcribeButton = (page: Page) => page.getByRole('button', { name: 'Transcribe', exact: true })
const beforeYouRun = (page: Page) => page.getByRole('region', { name: 'Before you run' })

export function registerReadinessCases(prefix: string) {
  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  })

  test(`${prefix}fresh install: Transcribe is off with the reasons, and the GPU tip is a calm suggestion`, async ({ page }) => {
    await setup(page, FRESH)
    await page.goto('/#/drama/1/source?tab=transcribe')
    await expect(transcribeButton(page)).toBeDisabled()
    const card = beforeYouRun(page)
    await expect(card.getByTestId('preflight-whisper')).toContainText('Not installed')
    const tip = card.getByTestId('preflight-gpu_torch')
    await expect(tip).toContainText("An NVIDIA GPU was found but PyTorch isn't installed")
    await expect(tip).toContainText('about 2.5 GB')
    await expect(tip).toContainText('Recommended')
    await expect(tip.getByRole('button', { name: /Set up GPU/ })).toBeVisible()
    await expect(card.getByRole('link', { name: 'See all recommended installs' })).toHaveAttribute('href', '#/diagnostics?card=recommended')
  })

  test(`${prefix}fresh install: each control that needs something is off, says why, and links the fix`, async ({ page }) => {
    await enableDeveloperMode(page)
    await setup(page, FRESH)
    await page.goto('/#/drama/1/source?tab=transcribe')
    await expect(page.getByTestId('settings-summary')).toBeVisible()
    // Detect speakers after transcribing sits on the Transcribe tab.
    await expect(page.getByRole('switch', { name: 'Detect speakers after transcribing' })).toBeDisabled()
    const tools = await openAdvanced(page)

    const vocals = tools.getByRole('switch', { name: 'Separate vocals first' })
    await expect(vocals).toBeDisabled()
    await expect(tools.getByTestId('needs-separation')).toContainText("needs audio-separator (or Demucs), which isn't installed")
    await expect(tools.getByTestId('needs-separation').getByRole('link', { name: 'Open Diagnostics' }))
      .toHaveAttribute('href', '#/diagnostics?card=packages&package=audio-separator')

    for (const backend of ['qwen3_asr', 'qwen3_asr_vad', 'qwen3_asr_long']) {
      await expect(tools.locator(`option[value="${backend}"]`)).toBeDisabled()
    }
    await expect(tools.locator('option[value="whisper"]')).toBeEnabled()
    await expect(tools.getByTestId('needs-qwen3')).toContainText('Missing: torch, transformers and soundfile')

    await expect(tools.getByRole('switch', { name: 'Realign long segments' })).toBeDisabled()
    await expect(tools.getByTestId('needs-realign')).toContainText('torch, torchaudio and uroman')

    await expect(tools.getByRole('switch', { name: 'Use Groq' })).toBeDisabled()
    await expect(tools.getByTestId('needs-groq_key').getByRole('link', { name: 'Add in Settings' })).toHaveAttribute('href', '#/settings')

    await expect(tools.getByRole('button', { name: 'Detect speakers only' })).toBeDisabled()
    await expect(tools.getByTestId('needs-speakers_package')).toContainText('pyannote.audio and torch')
    await expect(tools.getByTestId('needs-speakers_token')).toContainText("accept the speaker model's terms")
    await expect(tools.getByTestId('needs-speakers_token').getByRole('link', { name: /Accept the model terms/ }))
      .toHaveAttribute('href', /huggingface\.co\/pyannote\//)
  })

  test(`${prefix}the GPU tip shows only with a GPU found and a torch without CUDA`, async ({ page }) => {
    await setup(page, { ...READY, torch: true, torch_cuda: false })
    await page.goto('/#/drama/1/source?tab=transcribe')
    await expect(page.getByTestId('settings-summary')).toBeVisible()
    await expect(beforeYouRun(page).getByTestId('preflight-gpu_torch')).toContainText('PyTorch is CPU-only')
    // Nothing is missing, so the tip never stops a run.
    await expect(transcribeButton(page)).toBeEnabled()

    await page.unroute('**/api/setup/readiness')
    await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: { ...READY, gpu_found: false, torch_cuda: false } }))
    await page.reload()
    await expect(page.getByTestId('settings-summary')).toBeVisible()
    await expect(beforeYouRun(page)).toHaveCount(0)

    await page.unroute('**/api/setup/readiness')
    await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: READY }))
    await page.reload()
    await expect(page.getByTestId('settings-summary')).toBeVisible()
    await expect(beforeYouRun(page)).toHaveCount(0)
  })

  test(`${prefix}Groq on with Whisper missing is not disabled`, async ({ page }) => {
    await setup(page, { ...READY, faster_whisper: false }, { use_groq: true })
    await page.goto('/#/drama/1/source?tab=transcribe')
    await expect(page.getByTestId('settings-summary')).toBeVisible()
    await expect(transcribeButton(page)).toBeEnabled()
    await expect(page.locator('#transcribe-not-installed')).toHaveCount(0)
    await expect(page.getByTestId('run-line')).toContainText('Groq')
  })

  test(`${prefix}Groq on without a key is blocked by the key, not by Whisper`, async ({ page }) => {
    await setup(page, { ...READY, faster_whisper: false, key_groq: false }, { use_groq: true })
    await page.goto('/#/drama/1/source?tab=transcribe')
    await expect(beforeYouRun(page).getByTestId('preflight-groq_key')).toContainText('Groq API key')
    await expect(beforeYouRun(page).getByTestId('preflight-whisper')).toHaveCount(0)
    await expect(transcribeButton(page)).toBeDisabled()
  })

  test(`${prefix}a missing Hugging Face token says add a token in Settings, not "not installed"`, async ({ page }) => {
    await setup(page, READY)
    await page.route('**/api/transcribe/dramas/1/run', (r) => r.fulfill({
      status: 503,
      json: { error: { code: 'dependency_unavailable', message: 'No Hugging Face token is configured. Add it in Settings.', details: { reason: 'no_token', engine: 'Hugging Face' } } },
    }))
    await page.goto('/#/drama/1/source?tab=transcribe')
    await expect(transcribeButton(page)).toBeEnabled()
    await transcribeButton(page).click()
    const banner = page.locator('.error-banner')
    await expect(banner).toContainText('No Hugging Face token is set. Add it in Settings.')
    await expect(banner).not.toContainText('not installed')
    await expect(banner.getByRole('link', { name: 'Open Settings' })).toHaveAttribute('href', '#/settings')
  })

  test(`${prefix}a missing package says install it in Diagnostics, with the package picked`, async ({ page }) => {
    await setup(page, READY)
    await page.route('**/api/transcribe/dramas/1/run', (r) => r.fulfill({
      status: 503,
      json: { error: { code: 'dependency_unavailable', message: 'The paddle OCR backend needs paddleocr. Install it in Diagnostics.', details: { reason: 'missing_package', package: 'paddleocr' } } },
    }))
    await page.goto('/#/drama/1/source?tab=transcribe')
    await transcribeButton(page).click()
    const banner = page.locator('.error-banner')
    await expect(banner).toContainText("A package this needs isn't installed. Install it in Diagnostics.")
    await expect(banner).not.toContainText('Add it in Settings')
    await expect(banner.getByRole('link', { name: 'Open Diagnostics' })).toHaveAttribute('href', '#/diagnostics?card=packages&package=paddleocr')
  })

  test(`${prefix}Diagnostics lists the recommended installs with what was found`, async ({ page }) => {
    await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: FRESH }))
    await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch', (r) => r.fulfill({
      json: {
        nvidia: { found: true, gpu_name: 'NVIDIA Tesla V100', driver_version: '570.65', status: 'ok', recommended: '570.65', minimum: '528.33' },
        installed: [], problems: [], state: 'missing', python_supported: true, probe: null, recommended_reason: null,
        recommended: { variant: 'cu128', label: 'CUDA 12.8', index_url: 'https://download.pytorch.org/whl/cu128', versions: { torch: '2.11.0', torchvision: '0.26.0', torchaudio: '2.11.0' }, needs_nvidia: true },
        variants: [],
      },
    }))
    await page.goto('/#/diagnostics?card=recommended')
    const card = page.getByTestId('recommended-installs')
    await expect(card).toBeVisible()
    await expect(card.getByTestId('hardware-GPU')).toContainText('NVIDIA Tesla V100, driver 570.65')
    await expect(card.getByTestId('hardware-FFmpeg')).toBeVisible()
    await expect(card.getByTestId('hardware-JavaScript runtime')).toBeVisible()
    const whisper = card.getByTestId('recommended-whisper')
    await expect(whisper).toContainText('Missing')
    await expect(whisper).toContainText('Required')
    await expect(card.getByTestId('recommended-gpu_torch')).toContainText('about 2.5 GB')
    for (const id of ['separation', 'speakers', 'asmr', 'qwen3', 'ocr', 'ollama']) {
      const row = card.getByTestId(`recommended-${id}`)
      await expect(row).toContainText('Missing')
      await expect(row).toContainText('Optional')
      await expect(row).toContainText('Size:')
    }
    await expect(card.getByTestId('recommended-ollama').getByRole('link', { name: /Get Ollama/ })).toBeVisible()
  })

  test(`${prefix}a fully set up PC shows every row ready`, async ({ page }) => {
    await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: READY }))
    await page.goto('/#/diagnostics?card=recommended')
    const card = page.getByTestId('recommended-installs')
    for (const id of ['whisper', 'gpu_torch', 'separation', 'speakers', 'asmr', 'qwen3', 'ocr', 'ollama']) {
      await expect(card.getByTestId(`recommended-${id}`)).toContainText('Ready')
    }
  })

  test(`${prefix}a package link opens the folds around the package and picks it`, async ({ page }) => {
    const pkg = (name: string) => ({
      name, dist: name, installed: false, installable: true, powers: 'vocal separation', approx_mb: 30, pulls_torch: true,
      source_url: `https://pypi.org/project/${name}/`, not_offered_reason: null, warning: null,
    })
    await page.route('**/api/diagnostics/install-presets', (r) => r.fulfill({
      json: {
        tasks: [{
          id: 'vocals', group: 'Audio', label: 'Remove background music', help: 'Before transcribing.', packages: ['audio-separator'],
          installed_count: 0, to_install: ['audio-separator'], approx_mb: 30,
        }],
        packages: { 'audio-separator': pkg('audio-separator') },
      },
    }))
    await page.goto('/#/diagnostics?card=packages&package=audio-separator')
    const picked = page.locator('[data-pkg="audio-separator"]').first()
    await expect(picked).toBeVisible()
    await expect(picked).toHaveAttribute('aria-current', 'true')
  })
}
