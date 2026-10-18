import { expect, test } from '@playwright/test'

import { untilTestEnds } from './stageLineMocks'
import { enableDeveloperMode, openAdvanced, openTranscribeOptions, transcribeCard } from './sourceHelpers'

// Basic Transcribe: the everyday controls on the Transcribe tab, the Advanced options folds on Tools, and the seven
// developer knobs only in the DOM when Developer Mode is on.
const KNOBS = ['Beam size', 'VAD threshold', 'Hallucination guard', 'Separation backend', 'Hardsub OCR', 'Hardsub interval', 'Whisper repeat guard']

test('with Developer Mode off the seven knobs are not in the DOM', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = await openAdvanced(page)
  await expect(card.getByLabel('Expected speakers', { exact: true })).toBeVisible()
  for (const k of KNOBS) await expect(card.getByLabel(k, { exact: true })).toHaveCount(0)
  await expect(card.getByLabel('Sensitivity', { exact: true })).toBeVisible()
})

test('a developer value changed with the mode on is still saved after the mode turns off', async ({ page }) => {
  const saves: Record<string, unknown>[] = []
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    // Read with GET: route.fetch() would forward the POST and save into the shared library.
    const resp = await route.fetch({ method: 'GET' })
    const cfg = await resp.json()
    if (route.request().method() !== 'POST') return route.fulfill({ response: resp, json: cfg })
    saves.push(route.request().postDataJSON() as Record<string, unknown>)
    return route.fulfill({ response: resp, json: { ...cfg, ...saves[saves.length - 1] } })
  })
  await enableDeveloperMode(page)
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = await openAdvanced(page)
  await card.getByLabel('Beam size', { exact: true }).fill('8')
  await page.unroute('**/api/assistant/settings')
  await page.route('**/api/assistant/settings', (r) => r.fulfill({ json: { developer_mode: false, engine: null, model: null, engine_choices: [] } }))
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('baihe:developer-mode', { detail: { on: false } })))
  await expect(card.getByLabel('Beam size', { exact: true })).toHaveCount(0)
  await card.getByRole('button', { name: 'Save options' }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0]).toMatchObject({ beam_size: 8 })
  // The section's one-line summary says what was changed, with the hidden knobs counted.
  await expect(card.getByTestId('advanced-summary')).toContainText('1 developer option changed')
})

test('a Whisper model not recommended for the language is disabled with its reason, and the saved fallback is announced', async ({ page }) => {
  const reason = 'Whisper medium isn’t recommended for Japanese: in our tests it made about 1.9 points more errors than large-v3-turbo and was slower.'
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    const cfg = await resp.json()
    if (route.request().method() !== 'GET') return route.fulfill({ response: resp, json: cfg })
    return route.fulfill({
      response: resp,
      json: { ...cfg, selectable_whisper_sizes: ['large-v3', 'large-v3-turbo', 'medium'], whisper_exclusions: { medium: { ja: reason } }, whisper_size_notice: 'This title was saved with medium. It now uses large-v3.' },
    })
  })
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = transcribeCard(page)
  await expect(card.getByTestId('whisper-size-notice')).toContainText('saved with medium')
  await card.getByLabel('Source language').selectOption('ja')
  const medium = card.getByLabel('Accuracy and speed', { exact: true }).locator('option', { hasText: /^medium/ })
  await expect(medium).toBeDisabled()
  await expect(medium).toHaveText(/not for Japanese/)
  // The reason lives on the option; only the selected model gets a note.
  await expect(medium).toHaveAttribute('title', reason)
  await expect(card.getByTestId('whisper-exclusion-reason')).toHaveCount(0)
  await card.getByLabel('Source language').selectOption('ko')
  await expect(card.getByLabel('Accuracy and speed', { exact: true }).locator('option', { hasText: /^medium/ })).toBeEnabled()
})

// The config a title's Transcribe form is built from, with the fields the form reads overridden.
async function mockConfig(page: import('@playwright/test').Page, patch: Record<string, unknown>) {
  // The advisor's wording is the server's (tests/test_asr_advice.py); this pins that the form shows its top note.
  await page.route('**/api/transcribe/asr-advice?*', async (route) => {
    const long = new URL(route.request().url()).searchParams.get('asr_backend') === 'qwen3_asr_long'
    return route.fulfill({ json: { notes: [long
      ? { id: 'qwen_long_audio', severity: 'warn', text: 'Qwen3 ASR on long audio gives no word timings. Use Whisper.', fix: { asr_backend_choice: 'whisper' } }
      : { id: 'recommended', severity: 'tip', text: 'Recommended: Whisper large-v3 with Separate vocals first off.', fix: null }] } })
  })
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    const cfg = await resp.json()
    if (route.request().method() !== 'GET') return route.fulfill({ response: resp, json: cfg })
    return route.fulfill({ response: resp, json: { ...cfg, ...patch } })
  })
}

test('with Whisper the model is pickable, only large-v3 and turbo are listed, and one line says what will run', async ({ page }) => {
  await mockConfig(page, {
    transcript_mode: 'whisper', asr_backend_choice: 'whisper', whisper_size: 'large-v3', use_groq: false,
    selectable_whisper_sizes: ['large-v3', 'large-v3-turbo'], gpu_runtime: { status: 'ok', reason: null },
  })
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = transcribeCard(page)
  const model = card.getByLabel('Accuracy and speed', { exact: true })
  await expect(model).toBeEnabled()
  await expect(model.locator('option')).toHaveText(['Most accurate (recommended)', 'Faster'])
  await expect(card.getByTestId('run-line')).toContainText('This run will use: Whisper · Whisper large-v3 · GPU')
  await expect(card.getByTestId('transcribe-advisor')).toContainText('Whisper large-v3 with Separate vocals first off')
  await expect(card.getByTestId('transcribe-advisor').getByRole('button')).toHaveCount(0)
})

test('Use recommended in the advisor line switches the backend to Whisper', async ({ page }) => {
  await mockConfig(page, {
    transcript_mode: 'whisper', asr_backend_choice: 'qwen3_asr_long', whisper_size: 'large-v3', use_groq: false,
    selectable_whisper_sizes: ['large-v3', 'large-v3-turbo'], gpu_runtime: { status: 'ok', reason: null },
  })
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = transcribeCard(page)
  await card.getByTestId('before-you-run').locator('summary').click()
  await card.getByTestId('transcribe-advisor').getByRole('button', { name: 'Use recommended' }).click()
  await expect(card.getByTestId('run-line')).toContainText('Whisper · Whisper large-v3')
  await expect(card.getByTestId('transcribe-advisor')).toContainText('Recommended: Whisper large-v3')
})

test('a saved hidden size is shown as an older choice with a one-click switch, and is not changed until clicked', async ({ page }) => {
  await mockConfig(page, {
    transcript_mode: 'whisper', asr_backend_choice: 'whisper', whisper_size: 'medium', use_groq: false,
    selectable_whisper_sizes: ['large-v3', 'large-v3-turbo'],
  })
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = transcribeCard(page)
  await expect(card.getByLabel('Accuracy and speed', { exact: true })).toHaveValue('medium')
  await expect(card.getByLabel('Accuracy and speed', { exact: true }).locator('option:checked')).toHaveText('medium (older choice, hidden)')
  await card.getByTestId('before-you-run').locator('summary').click()
  await card.getByRole('button', { name: 'Switch to large-v3' }).click()
  await expect(card.getByLabel('Accuracy and speed', { exact: true })).toHaveValue('large-v3')
  await expect(card.getByTestId('whisper-hidden-size')).toHaveCount(0)
})

test('with Qwen3 ASR on long audio the Whisper model and Whisper-only switches are off with the reason, and the run line names Qwen3', async ({ page }) => {
  await enableDeveloperMode(page)
  await mockConfig(page, {
    transcript_mode: 'whisper', asr_backend_choice: 'qwen3_asr_long', whisper_size: 'large-v3', use_groq: false,
    selectable_whisper_sizes: ['large-v3', 'large-v3-turbo'],
    // A missing cuBLAS only affects Whisper, so the Qwen3 line must not claim a CPU fallback.
    gpu_runtime: { status: 'missing', reason: 'cublas64_12.dll is missing' },
  })
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = transcribeCard(page)
  await expect(card.getByLabel('Accuracy and speed', { exact: true })).toBeDisabled()
  await expect(card.getByTestId('whisper-not-used')).toContainText('Not used with Qwen3 ASR')
  await expect(card.getByTestId('whisper-not-used')).toContainText('Qwen3-ASR 1.7B')
  await expect(card.getByTestId('run-line')).toContainText('Qwen3 ASR on long windows · Qwen3-ASR 1.7B')
  await expect(card.getByTestId('run-line')).not.toContainText('large-v3')
  await expect(card.getByTestId('transcribe-advisor')).toContainText('Use Whisper')

  const advanced = await openAdvanced(page)
  for (const name of ['Whisper fast mode', 'Whisper repeat guard']) {
    await expect(advanced.getByRole('switch', { name })).toBeDisabled()
  }
  for (const name of ['VAD threshold', 'Min silence', 'Hallucination guard']) {
    await expect(advanced.getByLabel(name, { exact: true })).toBeDisabled()
  }
  // A switch the backend does use stays on.
  await expect(advanced.getByRole('switch', { name: 'Separate vocals first' })).toBeEnabled()
})

test('with Whisper and the CUDA runtime missing the run line says it runs on the CPU', async ({ page }) => {
  await mockConfig(page, {
    transcript_mode: 'whisper', asr_backend_choice: 'whisper', use_groq: false, selectable_whisper_sizes: ['large-v3', 'large-v3-turbo'],
    gpu_runtime: { status: 'missing', reason: 'cublas64_12.dll is missing' },
  })
  await page.route('**/api/settings', (route) => untilTestEnds(async () => {
    const resp = await route.fetch()
    const body = await resp.json()
    return route.fulfill({ response: resp, json: { ...body, use_gpu: true } })
  }))
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  await expect(transcribeCard(page).getByTestId('run-line')).toContainText('CUDA libraries missing, runs on CPU')
})

test('Whisper fast mode explains that it is off by default and what it costs', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const advanced = await openAdvanced(page)
  await page.getByRole('button', { name: 'Help: Whisper fast mode' }).focus()
  await expect(page.getByText('Off by default. Decodes several chunks of speech at once')).toBeVisible()
})

test('the notes above Transcribe are one closed "Before you run" line with the detail inside, and the GPU note is plain words', async ({ page }) => {
  const reason = 'Whisper medium isn’t recommended for Japanese: in our tests it made about 1.9 points more errors than large-v3-turbo and was slower.'
  await mockConfig(page, {
    transcript_mode: 'whisper', asr_backend_choice: 'whisper', whisper_size: 'large-v3', use_groq: false,
    selectable_whisper_sizes: ['large-v3', 'large-v3-turbo', 'medium'], whisper_exclusions: { medium: { ja: reason } },
    gpu_runtime: { status: 'missing', reason: 'cublas64_12.dll is missing' },
  })
  await page.route('**/api/settings', (route) => untilTestEnds(async () => {
    const resp = await route.fetch()
    return route.fulfill({ response: resp, json: { ...(await resp.json()), use_gpu: true } })
  }))
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = transcribeCard(page)
  await card.getByLabel('Source language').selectOption('ja')
  const details = card.getByTestId('before-you-run')
  await expect(details).toHaveCount(1)
  await expect(details.locator('summary')).toHaveText('Before you run')
  await expect(card.getByTestId('run-line')).toBeHidden()
  await expect(card.getByTestId('gpu-note')).toContainText("Your graphics card isn't set up for Baihe, so this will be slow. Fix it in Diagnostics.")
  await expect(card.getByTestId('gpu-note')).not.toContainText('CUDA')
  await details.locator('summary').click()
  await expect(card.getByTestId('run-line')).toBeVisible()
  await expect(card.getByTestId('gpu-unavailable-reason')).toContainText('cublas64_12.dll is missing')
})
