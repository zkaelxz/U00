import { expect, test, type Page } from '@playwright/test'
import { openSection } from './diagnosticsInstallsMocks'
import { diagTab, openDiagTab } from './diagnosticsTabsNav'
import { status } from './modelHealthMocks'

import { REV_OTHER, REV_WHISPER, cacheMock, guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'

// Diagnostics (desktop): Setup and Model status are open folds on their tabs; GPU PyTorch inside
// Packages opens only on a problem. Speaker detection is its own fold on the Packages and installs tab
// and the one model list is the Models card.

const fold = (page: Page, title: RegExp) => page.locator('details.section', { has: page.locator('> summary', { hasText: title }) }).first()

test('Setup and Model status start open on their tabs; GPU PyTorch is folded when all is well, with a one-line summary', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  await expect(fold(page, /^Setup/)).toHaveJSProperty('open', true)
  await openDiagTab(page, 'Models')
  await expect(fold(page, /^Model status/)).toHaveJSProperty('open', true)
  await openDiagTab(page, 'Packages and installs')
  const gpu = fold(page, /^GPU PyTorch/)
  await expect(gpu).toHaveJSProperty('open', false)
  await expect(gpu.locator('> summary')).toContainText('PyTorch 2.11.0+cu128 · CUDA build · RTX 3080 Ti')
  expect(unmocked).toEqual([])
})

test('a problem opens GPU PyTorch by itself; folding a card is remembered per card', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page, { ffmpegFound: false, gpuState: 'cpu_on_gpu' })
  await page.goto('/#/diagnostics')
  await expect(fold(page, /^Setup/)).toHaveJSProperty('open', true)
  await expect(page.getByTestId('diagnostics-summary')).toContainText('1 setup problem')
  await openDiagTab(page, 'Packages and installs')
  await expect(fold(page, /^GPU PyTorch/)).toHaveJSProperty('open', true)

  // Folding Setup is saved as closed, so a reload keeps it closed.
  await openDiagTab(page, 'Setup')
  await fold(page, /^Setup/).locator('> summary').click()
  await expect(fold(page, /^Setup/)).toHaveJSProperty('open', false)
  await page.waitForFunction(() => localStorage.getItem('baihe.section.diagnostics.setup') === '0')
  await page.reload()
  await expect(fold(page, /^Setup/)).toHaveJSProperty('open', false)

  // Folding Model status is saved the same way.
  await openDiagTab(page, 'Models')
  await fold(page, /^Model status/).locator('> summary').click()
  await page.waitForFunction(() => localStorage.getItem('baihe.section.diagnostics.modelHealth') === '0')
  await page.reload()
  await expect(fold(page, /^Model status/)).toHaveJSProperty('open', false)
})

test('Models holds one model list with sizes and Delete; speaker detection is a Packages card', async ({ page }) => {
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
  await openDiagTab(page, 'Packages and installs')
  const speaker = fold(page, /^Speaker detection/)
  await expect(speaker).toHaveJSProperty('open', false)
  await expect(speaker.locator('> summary')).toContainText('Ready')
  await openSection(page, /^Speaker detection/)
  await expect(speaker.getByRole('group', { name: 'Speaker detection' })).toContainText('Pyannote: installed')
  await expect(page.locator('summary', { hasText: /^Model cache/ })).toHaveCount(0)

  await openDiagTab(page, 'Models')
  const models = fold(page, /^Models/)
  await expect(models).toHaveJSProperty('open', true)
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

test('Packages has no Missing packages fold; a task lists what it still needs', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?card=packages')
  await openSection(page, /^Packages/)
  await expect(page.locator('summary', { hasText: /^Missing packages/ })).toHaveCount(0)
  await page.getByTestId('install-tasks').locator('details.section > summary').first().click()
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
  const models = fold(page, /^Models/)
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
  await expect(fold(page, /^Packages/)).toBeInViewport()
  expect(unmocked).toEqual([])
})

test('Model status links to Settings for keys', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.route('**/api/models/status', (r) => r.fulfill({ json: status({ items: [], checked_at: '2026-10-10T10:00:00Z' }) }))
  await page.goto('/#/diagnostics?card=model-status')
  await expect(fold(page, /^Model status/).getByRole('link', { name: 'Settings', exact: true })).toHaveAttribute('href', '#/settings')
})
