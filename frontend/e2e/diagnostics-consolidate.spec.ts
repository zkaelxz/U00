import { expect, test, type Page } from '@playwright/test'
import { mockDependencyInstall } from './dependencyInstallMock'
import { openSection } from './diagnosticsInstallsMocks'

import { REV_OTHER, REV_WHISPER, cacheMock, guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'

// Diagnostics (desktop): Setup, Model health and GPU PyTorch are folds that
// open by themselves only on a problem; speaker detection and the one model
// list live inside Setup; Packages has no "Missing packages" fold.

const fold = (page: Page, title: RegExp) => page.locator('details.section', { has: page.locator('> summary', { hasText: title }) }).first()
const savedKey = (page: Page, key: string) =>
  page.waitForFunction((k) => localStorage.getItem(`baihe.section.${k}`) === '1', key)

test('Setup, Model health and GPU PyTorch start folded when all is well, with a one-line summary', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  const setup = fold(page, /^Setup/)
  await expect(setup).toHaveJSProperty('open', false)
  await expect(setup.locator('> summary')).toContainText('All 6 OK')
  await expect(fold(page, /^Model health/)).toHaveJSProperty('open', false)
  await openSection(page, /^Packages/)
  const gpu = fold(page, /^GPU PyTorch/)
  await expect(gpu).toHaveJSProperty('open', false)
  await expect(gpu.locator('> summary')).toContainText('PyTorch 2.11.0+cu128 · CUDA build · RTX 3080 Ti')
  expect(unmocked).toEqual([])
})

test('a problem opens Setup and GPU PyTorch by themselves; the viewer choice is remembered', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page, { ffmpegFound: false, gpuState: 'cpu_on_gpu' })
  await page.goto('/#/diagnostics')
  await expect(fold(page, /^Setup/)).toHaveJSProperty('open', true)
  await expect(page.getByTestId('setup-summary')).toHaveText('1 problem: FFmpeg')
  await openSection(page, /^Packages/)
  await expect(fold(page, /^GPU PyTorch/)).toHaveJSProperty('open', true)

  // Folding Setup is saved as closed, so a reload keeps it closed.
  await fold(page, /^Setup/).locator('> summary').click()
  await expect(fold(page, /^Setup/)).toHaveJSProperty('open', false)
  await page.waitForFunction(() => localStorage.getItem('baihe.section.diagnostics.setup') === '0')
  await page.reload()
  await expect(fold(page, /^Setup/)).toHaveJSProperty('open', false)

  // And opening a folded one is saved as open.
  await fold(page, /^Model health/).locator('> summary').click()
  await savedKey(page, 'diagnostics.modelHealth')
  await page.reload()
  await expect(fold(page, /^Model health/)).toHaveJSProperty('open', true)
})

test('Setup holds speaker detection and one model list with sizes and Delete', async ({ page }) => {
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
  await fold(page, /^Setup/).locator('> summary').click()
  const setup = fold(page, /^Setup/)

  // Speaker detection is a part of Setup, not a card of its own.
  await expect(setup.getByRole('group', { name: 'Speaker detection' })).toContainText('Pyannote: installed')
  await expect(page.locator('summary', { hasText: /^Speaker detection/ })).toHaveCount(0)
  await expect(page.locator('summary', { hasText: /^Model cache/ })).toHaveCount(0)

  // Each engine row carries its own downloads; an installed engine with none says so.
  const whisper = setup.getByRole('list', { name: 'Whisper (faster-whisper) downloads' })
  await expect(whisper).toContainText('Systran/faster-whisper-large-v3 (model) · 3.1 GB')
  await expect(whisper.locator('code')).toHaveText(REV_WHISPER.slice(0, 12))
  await expect(setup.getByRole('list', { name: 'pyannote diarization model downloads' })).toContainText('speaker-diarization-3.1')
  await expect(setup.locator('li', { hasText: 'SenseVoice (FunASR): 1.2.0' })).toContainText('not downloaded')
  await expect(setup.locator('li', { hasText: 'Qwen3-ASR: not installed' })).not.toContainText('not downloaded')
  // Downloads no engine claims and other model files keep their own groups.
  await expect(setup.getByRole('list', { name: 'Downloaded models' })).toContainText('someone/unknown-model')
  await expect(setup.getByRole('list', { name: 'Model files' })).toContainText('model.pt (PyTorch hub)')
  await expect(setup).toContainText('A deleted model downloads again when a feature needs it.')

  await setup.getByRole('button', { name: 'Delete Systran/faster-whisper-large-v3' }).click()
  expect(sent).toHaveLength(0)
  await setup.getByRole('button', { name: 'Confirm delete Systran/faster-whisper-large-v3' }).click()
  await expect(setup.getByRole('status').filter({ hasText: 'Deleted Systran/faster-whisper-large-v3.' })).toBeVisible()
  expect(sent).toHaveLength(1)
  await expect(setup.locator('li', { hasText: 'Whisper (faster-whisper): 1.1.0' })).toContainText('not downloaded')
  expect(REV_OTHER).toBeTruthy()
  expect(unmocked).toEqual([])
})

test('Packages has no Missing packages fold; a task lists what it still needs', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
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

test('each model engine is listed once, with Install in its Setup row; the other folds link to it', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: true } }))
  const { started: installed } = await mockDependencyInstall(page, () => ({ ok: true, output_tail: ['done'] }))
  await page.goto('/#/diagnostics')
  // Open every fold: a name must still show up in only one engine list.
  for (const title of [/^Setup/, /^Model health/, /^Packages/]) await openSection(page, title)

  const engines = page.getByRole('list', { name: 'Model engines', exact: true })
  await expect(engines).toHaveCount(1)
  await expect(page.getByRole('list', { name: 'Model engines not installed' })).toHaveCount(0)
  for (const name of ['Whisper (faster-whisper)', 'Qwen3-ASR', 'SenseVoice (FunASR)', 'pyannote diarization model']) {
    await expect(engines.locator(':scope > li', { hasText: name })).toHaveCount(1)
    await expect(page.locator('li', { hasText: `${name}:` })).toHaveCount(1)
  }
  await expect(page.getByTestId('diagnostics-summary')).toBeVisible()

  // Install lives in the engine's row, only for an engine that isn't installed.
  const qwen = engines.locator(':scope > li', { hasText: 'Qwen3-ASR' })
  await expect(engines.getByRole('button', { name: /^Install/ })).toHaveCount(1)
  await qwen.getByRole('button', { name: /^Install/ }).click()
  await qwen.getByRole('button', { name: /^Confirm install/ }).click()
  await expect(qwen.getByTestId('install-result')).toBeVisible({ timeout: 10_000 })
  expect(installed).toHaveLength(1)
})

test('the links in Packages and Model health open Setup at its model engines', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  const setup = fold(page, /^Setup/)
  await expect(setup).toHaveJSProperty('open', false)
  await openSection(page, /^Packages/)
  await page.getByTestId('dependency-panel').getByRole('button', { name: 'Show model engines' }).click()
  await expect(setup).toHaveJSProperty('open', true)
  await expect(page.getByRole('heading', { name: 'Model engines' })).toBeFocused()

  await setup.locator('> summary').click()
  await expect(setup).toHaveJSProperty('open', false)
  await openSection(page, /^Model health/)
  await fold(page, /^Model health/).getByRole('button', { name: 'Show model engines' }).click()
  await expect(setup).toHaveJSProperty('open', true)
})
