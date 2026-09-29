import { expect as baseExpect, test } from '@playwright/test'

import { NOVEL_PREVIEW, VIDEO_PREVIEW, chapterImportResult, mockImports } from './sourcesImportMocks'
import { mockSources, posted } from './sourcesMocks'

// Sources imports (S-4/S-5), desktop: paste a link, preview it, then import
// a novel page, open a series and import chapters, track it, or download a
// video. Every job and write is mocked; nothing reaches a real site.
// Jobs poll every 1.5 s, so allow a few polls per assertion.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

test('chapter link: preview, open series with the chapter ticked, new drama, import with outcomes, track', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, { importHold: true })
  await page.goto('/#/sources')

  const link = page.getByRole('textbox', { name: 'Paste a link' })
  const preview = page.getByRole('button', { name: 'Preview' })
  await link.fill('alpha.example/a')
  await expect(page.getByText('Enter a link that starts with http:// or https://.')).toBeVisible()
  await expect(preview).toBeDisabled()
  await link.fill('https://alpha.example/a/c2?token=abc')
  await preview.click()
  await expect.poll(() => posted(s, '/api/sources/url/preview')[0]?.body).toEqual({ url: 'https://alpha.example/a/c2?token=abc' })

  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByRole('heading', { name: 'Heaven Book 1' })).toBeVisible()
  await expect(card.getByText('Comic', { exact: true })).toBeVisible()
  await expect(card.getByText('Alpha Comics · Chapter 2 · zh · 124 chapters · 18 images')).toBeVisible()
  await expect(card.getByText('Pages load one by one (paced).')).toBeVisible()
  await expect(card.getByText('https://alpha.example/a/c2')).toBeVisible()

  await card.getByRole('button', { name: 'Open series' }).click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText('Alpha Comics · 124 chapters · ongoing · zh')).toBeVisible()
  await expect.poll(() => posted(s, '/api/sources/alpha/series')[0]?.body).toEqual({ series_id: 'a0' })
  await expect(panel.getByRole('checkbox', { name: 'Chapter 2', exact: true })).toBeChecked()
  await panel.getByRole('checkbox', { name: 'Chapter 1', exact: true }).check()
  await panel.getByRole('checkbox', { name: 'Chapter 3', exact: true }).check()

  const importBtn = panel.getByRole('button', { name: 'Import 3 chapters' })
  await expect(importBtn).toBeDisabled()
  await expect(panel.getByText('Still needed: a drama to import into.')).toBeVisible()

  // Comic source: only comic dramas are offered, plus New drama….
  const into = panel.getByRole('combobox', { name: 'Import into' })
  await expect(into.locator('option')).toHaveText(['Choose a drama…', 'Alpha Comic', 'New drama…'])
  await into.selectOption({ label: 'New drama…' })
  const newDrama = panel.getByRole('group', { name: 'New drama' })
  await expect(newDrama.getByRole('textbox', { name: 'Title' })).toHaveValue('Heaven Book 1')
  await newDrama.getByRole('button', { name: 'Create drama' }).click()
  await expect.poll(() => posted(s, '/api/dramas')[0]?.body).toEqual({ source_language: 'zh', title_en: 'Heaven Book 1', media_type: 'manhua' })
  await expect(into).toHaveValue('21')

  await importBtn.click()
  await expect.poll(() => posted(s, '/api/sources/alpha/import')[0]?.body).toEqual({ series_id: 'a0', chapter_ids: ['c1', 'c2', 'c3'], drama_id: 21 })
  await expect(panel.getByTestId('import-progress')).toHaveText('Chapter 2 of 3… 50%')
  await expect(panel.getByRole('checkbox', { name: 'Chapter 1', exact: true })).toBeDisabled()

  // Cancel, then a second run that finishes with per-chapter outcomes.
  await panel.getByTestId('import-bar').getByRole('button', { name: 'Cancel' }).click()
  await expect.poll(() => posted(s, '/api/jobs/sourceimport_21/cancel').length).toBe(1)
  // The server finishes the run with what it got through, marked stopped.
  await expect(panel.getByTestId('import-outcomes').getByText('Stopped. 1 imported')).toBeVisible()
  await expect(panel.getByRole('checkbox', { name: 'Chapter 1', exact: true })).toBeEnabled()
  m.importHold = false
  await panel.getByRole('button', { name: 'Import 3 chapters' }).click()
  const outcomes = panel.getByTestId('import-outcomes')
  await expect(outcomes.getByText('1 imported · 1 already there · 1 failed')).toBeVisible()
  await expect(outcomes.getByText('Imported · 20 pages')).toBeVisible()
  await expect(outcomes.getByText('Already imported')).toBeVisible()
  await expect(outcomes.getByText('Failed: The site took too long to answer.')).toBeVisible()
  await expect(outcomes.getByText(/^Imported 20 pages\. There’s no page viewer here yet/)).toBeVisible()
  await expect(outcomes.getByRole('link', { name: 'Open workspace' })).toHaveAttribute('href', '#/drama/21/source')

  // Track for new chapters (with the chosen drama).
  await panel.getByRole('button', { name: 'Track for new chapters' }).click()
  await expect.poll(() => posted(s, '/api/sources/tracked')[0]?.body).toEqual({ source: 'alpha', series_id: 'a0', tracked: true, drama_id: 21 })
  await expect(panel.getByText('Tracked', { exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Track for new chapters' })).toHaveCount(0)

  // Another drama's stored run (the job id is per drama) is not shown as this one's.
  await into.selectOption({ label: 'Alpha Comic' })
  await expect.poll(() => s.calls.some((c) => c.path === '/api/sources/jobs/sourceimport_12/result')).toBe(true)
  await expect(panel.getByTestId('import-outcomes')).toHaveCount(0)
  await expect(page.locator('img')).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('select all and the 200-chapter cap', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  await mockImports(page, s)
  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1' }))
    localStorage.setItem('baihe.pref.sources.importInto.alpha:a0', '12')
  })
  await page.goto('/#/sources')
  const panel = page.getByRole('region', { name: 'Series' })
  await panel.getByRole('checkbox', { name: 'Select all 124' }).check()
  await expect(panel.getByRole('button', { name: 'Import 124 chapters' })).toBeEnabled()
  await expect(panel.getByRole('combobox', { name: 'Import into' })).toHaveValue('12')
  await panel.getByRole('checkbox', { name: 'Select all 124' }).uncheck()
  await expect(panel.getByRole('button', { name: 'Import 0 chapters' })).toBeDisabled()
  await expect(panel.getByText('Still needed: at least one chapter.')).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('novel page: pick a drama, import the text', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: NOVEL_PREVIEW })
  await page.goto('/#/sources')
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://novels.example/book/5')
  await page.getByRole('textbox', { name: 'Paste a link' }).press('Enter')

  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByText('Some Novel Site · Chapter 5 · zh · 5,120 characters')).toBeVisible()
  await expect(card.getByRole('button', { name: 'Open series' })).toHaveCount(0)
  const into = card.getByRole('combobox', { name: 'Import into' })
  // Novel dramas first, then the rest.
  await expect(into.locator('option')).toHaveText(['Choose a drama…', 'Heaven Novel', 'Alpha Comic', 'Radio Play', 'Stream VOD', 'New drama…'])
  await into.selectOption({ label: 'Heaven Novel' })
  await card.getByRole('button', { name: 'Import text' }).click()
  await expect.poll(() => posted(s, '/api/sources/url/import')[0]?.body).toEqual({ url: 'https://novels.example/book/5', drama_id: 11 })
  await expect(card.getByTestId('url-import-result')).toContainText('Added 5,120 characters to the drama’s novel text.')
  expect(s.unmocked).toEqual([])
})

test('browser check: handoff card, no automatic retry', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, { previewHold: true })
  await page.goto('/#/sources')
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://alpha.example/a/c2')
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByText('Checking the link… 40%')).toBeVisible()
  m.preview = 'handoff'
  await expect(page.getByText('The site showed a browser check. Baihe never gets past these.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open in your browser ↗' })).toHaveAttribute('href', 'https://alpha.example/a/c2')
  await page.waitForTimeout(2000)
  await expect.poll(() => posted(s, '/api/sources/url/preview').length).toBe(1)
  m.preview = 'none'
  m.previewHold = false
  m.previewBody = chapterImportResult() // wrong kind: ignored
  await page.getByRole('button', { name: 'Try again' }).click()
  await expect.poll(() => posted(s, '/api/sources/url/preview').length).toBe(2)
  await expect(page.getByRole('article', { name: 'Link preview' })).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('video link: download into an audio drama (PC only), and the remote 403', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, { previewBody: VIDEO_PREVIEW, hasAudio: true })
  await page.goto('/#/sources')
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://video.example/watch?v=1')
  await page.getByRole('button', { name: 'Preview' }).click()
  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByText('Video', { exact: true })).toBeVisible()
  const into = card.getByRole('combobox', { name: 'Import into' })
  // A novel-narration drama can't take audio.
  await expect(into.locator('option')).toHaveText(['Choose a drama…', 'Alpha Comic', 'Radio Play', 'Stream VOD'])
  await into.selectOption({ label: 'Stream VOD' })
  // Streamer VOD: audio only starts off; the drama has audio, so replacing needs a tick.
  await expect(card.getByRole('checkbox', { name: 'Audio only' })).not.toBeChecked()
  const download = card.getByRole('button', { name: 'Download' })
  await expect(download).toBeDisabled()
  await expect(card.getByText('Still needed: tick “Replace the current audio”.')).toBeVisible()
  await card.getByRole('checkbox', { name: 'Replace the current audio' }).check()
  await download.click()
  await expect.poll(() => posted(s, '/api/media/dramas/14/download-url')[0]?.body).toEqual({
    url: 'https://video.example/watch?v=1', audio_only: false, confirm_replace_audio: true,
  })
  await expect(card.getByTestId('job-status')).toContainText('done')
  await expect(card.getByRole('link', { name: 'Open Stream VOD in the workspace' })).toHaveAttribute('href', '#/drama/14/source')

  // A 403 (not at the PC): plain message, then the link box turns into the PC-only note.
  m.downloadForbidden = true
  await into.selectOption({ label: 'Radio Play' })
  await expect(card.getByRole('checkbox', { name: 'Audio only' })).toBeChecked()
  await card.getByRole('checkbox', { name: 'Replace the current audio' }).check()
  await card.getByRole('button', { name: 'Download' }).click()
  await expect(page.getByText('Importing from a link is PC only for now.')).toBeVisible()
  expect(s.unmocked).toEqual([])
})
