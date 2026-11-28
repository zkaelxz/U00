import { expect, test, type Page } from '@playwright/test'
import { diagSection, diagSectionHead, diagTab, openDiagTab } from './diagnosticsTabsNav'
import { status } from './modelHealthMocks'

import { REV_OTHER, REV_WHISPER, cacheMock, guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'

// Diagnostics (desktop): each pane shows its sections in full. GPU PyTorch sits in Packages with a one-line state,
// Speaker detection has its own pane and the one model list is Models on disk.

const fold = (page: Page, title: RegExp) => diagSection(page, title)

test('Setup and Model status show in full on their panes; GPU PyTorch has a one-line state in Packages', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  await expect(fold(page, /^Setup/)).toBeVisible()
  // No card is a fold; only long lists inside one (Installed packages, the other configured models) still fold.
  await expect(page.locator('.diag-panel > .diag-card > details.section')).toHaveCount(0)
  await openDiagTab(page, 'Model status')
  await expect(fold(page, /^Model status/)).toBeVisible()
  await openDiagTab(page, 'Packages and installs')
  await expect(page.getByTestId('gpu-torch')).toBeVisible()
  await expect(diagSectionHead(page, /^GPU PyTorch/)).toContainText('PyTorch 2.11.0+cu128 · CUDA build · RTX 3080 Ti')
  expect(unmocked).toEqual([])
})

test('a problem shows in the summary line and on GPU PyTorch', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page, { ffmpegFound: false, gpuState: 'cpu_on_gpu' })
  await page.goto('/#/diagnostics')
  await expect(fold(page, /^Setup/)).toBeVisible()
  await expect(page.getByTestId('diagnostics-summary')).toContainText('1 setup problem')
  await openDiagTab(page, 'Packages and installs')
  await expect(page.getByTestId('gpu-torch')).toBeVisible()
})

test('Models on disk holds one model list with sizes and Delete; speaker detection has its own pane', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  let cache = cacheMock()
  await page.route('**/api/diagnostics/model-cache', (r) => r.fulfill({ json: cache }))
  const sent: string[] = []
  await page.route(`**/api/diagnostics/model-cache/hf/${REV_WHISPER}/delete`, (r) => {
    sent.push(r.request().url())
    cache = { ...cache, hf_cache: cache.hf_cache.filter((e) => e.revision !== REV_WHISPER) }
    return r.fulfill({ json: { deleted: true, name: REV_WHISPER } })
  })
  await page.goto('/#/diagnostics')
  await openDiagTab(page, 'Speaker detection')
  const speaker = fold(page, /^Speaker detection/)
  await expect(diagSectionHead(page, /^Speaker detection/)).toContainText('pyannote: ready')
  await expect(speaker.getByRole('group', { name: 'Speaker detection' })).toContainText('Pyannote: installed')
  await expect(page.locator('summary', { hasText: /^Model cache/ })).toHaveCount(0)

  await openDiagTab(page, 'Models on disk')
  const models = fold(page, /^Models$/)
  await expect(models).toBeVisible()
  // Each engine row carries its own downloads; an installed engine with none says so.
  const whisper = models.getByRole('list', { name: 'Whisper (faster-whisper) downloads' })
  await expect(whisper).toContainText('Systran/faster-whisper-large-v3 (model) · 3.1 GB')
  await expect(whisper.locator('code')).toHaveText(REV_WHISPER.slice(0, 12))
  await expect(models.getByRole('list', { name: 'pyannote diarization model downloads' })).toContainText('speaker-diarization-3.1')
  await expect(models.locator('li', { hasText: 'SenseVoice (FunASR): 1.2.0' })).toContainText('not downloaded')
  await expect(models.locator('li', { hasText: 'Qwen3-ASR: not installed' })).not.toContainText('not downloaded')
  // Downloads no engine claims and other model files keep their own groups.
  await expect(models.getByRole('list', { name: 'Downloaded models' })).toContainText('someone/unknown-model')
  await expect(models.getByRole('list', { name: 'Model files' })).toContainText('model.pt (PyTorch hub)')
  await expect(models).toContainText('A deleted model downloads again when a feature needs it.')

  await models.getByRole('button', { name: 'Delete Systran/faster-whisper-large-v3' }).click()
  expect(sent).toHaveLength(0)
  await models.getByRole('button', { name: 'Confirm delete Systran/faster-whisper-large-v3' }).click()
  await expect(models.getByRole('status').filter({ hasText: 'Deleted Systran/faster-whisper-large-v3.' })).toBeVisible()
  expect(sent).toHaveLength(1)
  await expect(models.locator('li', { hasText: 'Whisper (faster-whisper): 1.1.0' })).toContainText('not downloaded')
  expect(REV_OTHER).toBeTruthy()
  expect(unmocked).toEqual([])
})

const WHISPER_DOWNLOADS = [
  ['whisper_large_v3', 'Whisper large-v3 (about 3 GB)', true],
  ['whisper_large_v3_turbo', 'Whisper large-v3-turbo (about 1.6 GB)', false],
  ['whisper_ja_1_5b', 'Whisper Japanese 1.5B (about 3 GB)', false],
  ['whisper_breeze_asr_25', 'Breeze-ASR-25, Chinese (about 3 GB)', false],
  ['pyannote_diarization', 'pyannote speaker model community-1 (needs your Hugging Face token)', false],
] as const

test('each model download names its model and its own state; pyannote gets its own Download', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page, { cache: { hf_cache: [], hf_total_bytes: 0, model_files: [], model_files_total_bytes: 0 } })
  await page.route('**/api/diagnostics/model-downloads', (r) => r.fulfill({
    json: { models: WHISPER_DOWNLOADS.map(([id, label, downloaded]) => ({ id, label, downloaded, job_id: `model-download:${id}` })) },
  }))
  await page.route('**/api/jobs/**', (r) => r.fulfill({ status: 404, json: { detail: 'No such job.' } }))
  await page.goto('/#/diagnostics')
  await openDiagTab(page, 'Models on disk')
  const models = fold(page, /^Models$/)
  const list = models.getByRole('list', { name: 'Whisper (faster-whisper) models' })
  await expect(list.locator('> li')).toHaveCount(4)
  await expect(list.locator('li', { hasText: 'Whisper large-v3 (about 3 GB)' })).toContainText('downloaded')
  await expect(list.locator('li', { hasText: 'Whisper large-v3 (about 3 GB)' }).getByRole('button')).toHaveCount(0)
  const turbo = list.locator('li', { hasText: 'Whisper large-v3-turbo' })
  await expect(turbo).toContainText('not downloaded')
  await expect(turbo.getByRole('button', { name: 'Download Whisper large-v3-turbo (about 1.6 GB)' })).toBeVisible()
  await expect(list.getByRole('button')).toHaveCount(3)
  // The engine line does not repeat a vague "not downloaded" when its models list their own state.
  await expect(models.locator('li', { hasText: 'Whisper (faster-whisper): 1.1.0' }).locator('> span').first()).not.toContainText('not downloaded')
  const pyannote = models.locator('li', { hasText: 'pyannote diarization model' })
  await expect(pyannote).toContainText('not downloaded')
  await expect(pyannote.getByRole('button', { name: /^Download pyannote speaker model community-1/ })).toBeVisible()
  await models.scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('models-downloads.png'), fullPage: true })
})

test('Packages has no Missing packages fold; a task lists what it still needs', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?card=packages')
  await expect(page.locator('.section-title', { hasText: /^Missing packages/ })).toHaveCount(0)
  const ocr = page.getByTestId('task-details-hardsub_ocr')
  await ocr.locator('summary').click()
  await expect(ocr).toContainText('paddleocr')
  await expect(ocr.getByRole('button', { name: 'Install paddleocr' })).toBeVisible()
  // A package no task installs keeps its reason.
  await expect(page.getByRole('list', { name: 'Packages not part of a task' })).toContainText("separate program")
  expect(unmocked).toEqual([])
})

test('a not-installed engine on Models says what it is for and links to Packages and installs; there is no Install button', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?card=models')
  const models = fold(page, /^Models$/)
  const engines = models.getByRole('list', { name: 'Model engines', exact: true })
  await expect(engines).toHaveCount(1)
  for (const name of ['Whisper (faster-whisper)', 'Qwen3-ASR', 'SenseVoice (FunASR)', 'pyannote diarization model']) {
    await expect(engines.locator(':scope > li', { hasText: name })).toHaveCount(1)
  }
  await expect(models.getByRole('button', { name: /^Install/ })).toHaveCount(0)
  const qwen = engines.locator(':scope > li', { hasText: 'Qwen3-ASR' })
  await expect(qwen).toContainText('Qwen3-ASR help text')
  await expect(engines.locator(':scope > li', { hasText: 'SenseVoice' }).getByTestId('engine-not-installed')).toHaveCount(0)
  await qwen.getByRole('link', { name: 'Install it in Packages and installs' }).click()
  await expect(diagTab(page, 'Packages and installs')).toHaveAttribute('aria-selected', 'true')
  await expect(diagSectionHead(page, /^Packages/)).toBeInViewport()
  expect(unmocked).toEqual([])
})

test('Model status links to Settings for keys', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.route('**/api/models/status', (r) => r.fulfill({ json: status({ items: [], checked_at: '2026-10-10T10:00:00Z' }) }))
  await page.goto('/#/diagnostics?card=model-status')
  await expect(fold(page, /^Model status/).getByRole('link', { name: 'Settings', exact: true })).toHaveAttribute('href', '#/settings')
})
