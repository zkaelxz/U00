import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'
import { openBenchTab } from './benchSections'
import { NOVEL_PREVIEW, mockImports } from './sourcesImportMocks'
import { mockAccess } from './sourcesAccessMocks'
import { mockScanlate } from './scanlateMocks'
import { mockSources, openManage } from './sourcesMocks'
import { enableDeveloperMode } from './sourceHelpers'

// The last controls that fail for a missing package or program say so before the click: both OCR engine
// lists, the comic text detector, the page-text reader and the Sources browser tests. Readiness is faked; the seeded server answers "all present" otherwise.

async function readiness(page: Page, patch: Record<string, boolean>) {
  await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: { ...patch } }))
}

test('Benchmark OCR list marks a backend that is not installed', async ({ page }) => {
  await enableDeveloperMode(page)
  await readiness(page, { pytesseract: true, tesseract_program: true, paddle_ocr: false, manga_ocr: true })
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Run a benchmark')
  const card = page.getByRole('region', { name: 'Run a benchmark' })
  await card.getByLabel('Stage', { exact: true }).selectOption('ocr')
  const backend = card.getByLabel('OCR backend 1', { exact: true })
  await expect(backend.locator('option[value="paddle"]')).toContainText('(not installed)')
  await backend.selectOption('paddle')
  await expect(card.getByTestId('needs-ocr_backend')).toContainText('paddleocr and paddlepaddle')
})

test('Chapter OCR is off, with the fix, when the chosen engine is not installed', async ({ page }) => {
  await readiness(page, { pytesseract: true, tesseract_program: false })
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Novel text' }).click()
  await page.locator('.section-title', { hasText: 'Chapter images (OCR)' }).click()
  await page.getByLabel('Page images', { exact: true }).setInputFiles([{ name: 'p1.png', mimeType: 'image/png', buffer: Buffer.from('x') }])
  await expect(page.getByTestId('needs-ocr_backend')).toContainText('Tesseract program')
  await expect(page.getByRole('button', { name: 'Extract text from images' })).toBeDisabled()
})

test('Comic text detector says the OpenCV heuristic will be used until the ML model can load', async ({ page }) => {
  await mockScanlate(page, { pageCount: 3, lastPage: 1 })
  await readiness(page, { cv2: true, pytesseract: true, tesseract_program: true, paddle_ocr: true, torch: false })
  await page.goto('/#/comic/7?page=1')
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Translate pages' })
  await panel.getByText('Advanced').click()
  await expect(panel.getByTestId('needs-ml_detector')).toContainText('needs torch')
  await panel.getByLabel('Text detector', { exact: true }).selectOption('cv')
  await expect(panel.getByTestId('needs-ml_detector')).toHaveCount(0)
})

test('Link import says a simpler reader is used without trafilatura', async ({ page }) => {
  await readiness(page, { trafilatura: false })
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: NOVEL_PREVIEW })
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://novels.example/book/5')
  await page.getByRole('textbox', { name: 'Paste a link' }).press('Enter')
  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByTestId('needs-trafilatura')).toContainText('simpler built-in reader')
})

test('Sources Browser and Signed-in tests are off, with the fix, when no browser is found', async ({ page }) => {
  await readiness(page, { playwright: true, browser_found: false })
  const s = await mockSources(page)
  await mockAccess(page, s)
  const settings = await openManage(page)
  await settings.getByRole('row', { name: /Beta Novels/ }).getByRole('button', { name: 'Details: Beta Novels' }).click()
  const access = settings.getByRole('group', { name: 'Access tests and sign-in: Beta Novels' })
  await access.getByRole('textbox', { name: 'Page on this site' }).fill('https://beta.example/book/1')
  await expect(access.getByTestId('needs-browser_program')).toContainText('No browser was found')
  await expect(access.getByRole('button', { name: 'Static' })).toBeEnabled()
  await expect(access.getByRole('button', { name: 'Browser', exact: true })).toBeDisabled()
  await expect(access.getByRole('button', { name: 'Open sign-in window' })).toBeDisabled()
})
