import { expect, test } from '@playwright/test'

import { SHOTS_DIR } from './comicMocks'
import { mockScanlate } from './scanlateMocks'

// The Comic page's Translate panel at the desktop viewport. All /api calls
// are mocked (comicMocks + scanlateMocks); nothing may fall through.

test('translate all pages, follow the job, then read the typeset pages', async ({ page }) => {
  const s = await mockScanlate(page, { pageCount: 3, lastPage: 1 })
  await page.goto('/#/comic/7?page=1')
  await expect(page.getByTestId('comic-page-label')).toHaveText('Page 1 of 3')
  const typeset = page.getByRole('button', { name: 'Typeset' })
  await expect(typeset).toBeDisabled()

  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Translate pages' })
  await expect(panel).toBeVisible()
  // Only engines with a key are offered; the unusable default falls back.
  const engine = panel.getByLabel('AI engine', { exact: true })
  await expect(engine).toHaveValue('ollama')
  await expect(engine.locator('option')).toHaveCount(1)
  // The closed select shows the engine's name; its description is in the field help.
  await expect(engine.locator('option')).toHaveText('Ollama (local)')
  await expect(panel.locator('.field-help-text', { hasText: 'Keys stay on the PC' })).toContainText('Ollama (local): Ollama (free, local)')

  await panel.getByRole('button', { name: 'Translate all pages' }).click()
  await expect(panel.getByTestId('job-status')).toHaveText(/Done · Pages: 3 translated\./)
  expect(s.runs).toEqual([{ mode: 'missing', engine: 'ollama', detect_backend: 'auto' }])
  // The viewer reloaded: typeset images are available now.
  await expect(typeset).toBeEnabled()
  await expect.poll(() => s.images.some((i) => i.endsWith(':rendered'))).toBe(true)
  // Notes are text, never markup.
  await expect(panel.getByText('<b>1 region</b> had no translation.')).toBeVisible()
  if (SHOTS_DIR) await page.screenshot({ path: `${SHOTS_DIR}/scanlate-desktop.png`, fullPage: true })
  expect(s.unmocked).toEqual([])
})

test('redo this page, redo all needs a second press, export gives a download', async ({ page }) => {
  const s = await mockScanlate(page, { pageCount: 3, lastPage: 1 })
  await page.goto('/#/comic/7?page=2')
  // Wait for the pages before clicking: while they load, the pager appears in
  // the sticky bar and shifts the Translate button. Playwright then retries the
  // click with scrollIntoView({ block: 'end' }), which on a sticky element
  // scrolls the page back to page 1.
  await expect(page.getByTestId('comic-page-label')).toHaveText('Page 2 of 3')
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Translate pages' })
  // A page's hand-edited text boxes are replaced, so the first press only asks.
  await panel.getByRole('button', { name: 'Redo page 2' }).click()
  expect(s.runs).toEqual([])
  await panel.getByRole('button', { name: 'Redo page 2' }).press('Escape')
  await expect(panel.getByRole('button', { name: 'Replace text on page 2' })).toHaveCount(0)
  expect(s.runs).toEqual([])
  await panel.getByRole('button', { name: 'Redo page 2' }).click()
  await panel.getByRole('button', { name: 'Replace text on page 2' }).click()
  await expect(panel.getByTestId('job-status')).toHaveText(/Done/)
  expect(s.runs.at(-1)).toMatchObject({ mode: 'page', page_id: 7 * 100 + 2 })

  await panel.getByText('Advanced').click()
  await panel.getByRole('button', { name: /Redo all/ }).click()
  expect(s.runs).toHaveLength(1)
  await panel.getByRole('button', { name: 'Replace text on every page' }).click()
  await expect.poll(() => s.runs.length).toBe(2)
  expect(s.runs.at(-1)).toMatchObject({ mode: 'all', confirm: true })
  await expect(panel.getByTestId('job-status')).toHaveText(/Done/)

  await panel.getByRole('button', { name: 'Export ZIP' }).click()
  await expect(panel.getByRole('link', { name: 'Download ZIP' })).toHaveAttribute(
    'href', '/api/artifacts/dramas/7/scanlate_zip')
  expect(s.exports).toEqual([{ formats: ['zip'] }])
  expect(s.unmocked).toEqual([])
})

test('an empty title shows the upload panel; picking files uploads them', async ({ page }) => {
  const s = await mockScanlate(page, { pageCount: 0, lastPage: 1 })
  await page.goto('/#/comic/7')
  const panel = page.getByRole('region', { name: 'Translate pages' })
  await expect(panel).toBeVisible()
  await expect(panel.getByText('Upload pages first.')).toBeVisible()
  await panel.getByLabel('Page images or PDFs').setInputFiles([
    { name: 'a.png', mimeType: 'image/png', buffer: Buffer.from('x') },
    { name: 'b.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4') },
  ])
  await panel.getByRole('button', { name: 'Upload pages' }).click()
  await expect(panel.getByText('Added 2 pages. 1 tall strip was sliced.')).toBeVisible()
  expect(s.uploads).toBe(1)
  // A type the server refuses is caught before sending.
  await panel.getByLabel('Page images or PDFs').setInputFiles([
    { name: 'c.gif', mimeType: 'image/gif', buffer: Buffer.from('x') },
  ])
  await expect(panel.getByRole('alert')).toHaveText(/use PNG, JPEG, WebP or PDF/)
  await expect(panel.getByRole('button', { name: 'Upload pages' })).toBeDisabled()
  expect(s.unmocked).toEqual([])
})
