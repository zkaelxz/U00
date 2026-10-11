import { expect as baseExpect, test } from '@playwright/test'

import { NOVEL_PREVIEW, VIDEO_PREVIEW, chapterImportResult, mockImports } from './sourcesImportMocks'
import { SOURCES, mockSources, posted } from './sourcesMocks'

// Sources imports (S-4/S-5), desktop: paste a link, preview it, then import
// a novel page, open a series and import chapters, track it, or download a
// video. Every job and write is mocked; nothing reaches a real site.
// Jobs poll every 1.5 s, so allow a few polls per assertion.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

test('chapter link: preview, open series with the chapter ticked, new title, import with outcomes, track', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, { importHold: true })
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()

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
  await expect(panel.getByText('Alpha Comics · 124 chapters · Ongoing · Chinese')).toBeVisible()
  await expect.poll(() => posted(s, '/api/sources/alpha/series')[0]?.body).toEqual({ series_id: 'a0' })
  await expect(panel.getByRole('checkbox', { name: 'Chapter 2', exact: true })).toBeChecked()
  await panel.getByRole('checkbox', { name: 'Chapter 1', exact: true }).check()
  await panel.getByRole('checkbox', { name: 'Chapter 3', exact: true }).check()

  const importBtn = panel.getByRole('button', { name: 'Import 3 chapters' })
  await expect(importBtn).toBeDisabled()
  await expect(panel.getByText('Still needed: a title to import into.')).toBeVisible()

  // Comic source: only comic dramas are offered, plus New drama….
  const into = panel.getByRole('combobox', { name: 'Import into' })
  await expect(into.locator('option')).toHaveText(['Choose a title…', 'Alpha Comic', 'New title…'])
  await into.selectOption({ label: 'New title…' })
  const newDrama = panel.getByRole('group', { name: 'New title' })
  await expect(newDrama.getByRole('textbox', { name: 'Title' })).toHaveValue('Heaven Book 1')
  await newDrama.getByRole('button', { name: 'Create title' }).click()
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

test('novel page: pick a title, import the text', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: NOVEL_PREVIEW })
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://novels.example/book/5')
  await page.getByRole('textbox', { name: 'Paste a link' }).press('Enter')

  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByText('Some Novel Site · Chapter 5 · zh · 5,120 characters')).toBeVisible()
  await expect(card.getByRole('button', { name: 'Open series' })).toHaveCount(0)
  const into = card.getByRole('combobox', { name: 'Import into' })
  // Only novel dramas: the text import refuses any other media type.
  await expect(into.locator('option')).toHaveText(['Choose a title…', 'Heaven Novel', 'New title…'])
  await into.selectOption({ label: 'Heaven Novel' })
  await card.getByRole('button', { name: 'Import text' }).click()
  await expect.poll(() => posted(s, '/api/sources/url/import')[0]?.body).toEqual({ url: 'https://novels.example/book/5', drama_id: 11 })
  await expect(card.getByTestId('url-import-result')).toContainText('Added 5,120 characters to the title’s novel text.')
  expect(s.unmocked).toEqual([])
})

test('browser check: handoff card, no automatic retry', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, { previewHold: true })
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://alpha.example/a/c2')
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByText('Checking the link… 40%')).toBeVisible()
  m.preview = 'handoff'
  await expect(page.getByText('The site showed a browser check. Baihe never gets past these.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open in your browser ↗' })).toHaveAttribute('href', 'https://alpha.example/a/c2')
  // Proving a non-event: no automatic retry of the preview may follow the handoff card.
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
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://video.example/watch?v=1')
  await page.getByRole('button', { name: 'Preview' }).click()
  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByText('Video', { exact: true })).toBeVisible()
  const into = card.getByRole('combobox', { name: 'Import into' })
  // A novel-narration drama can't take audio.
  await expect(into.locator('option')).toHaveText(['Choose a title…', 'Alpha Comic', 'Radio Play', 'Stream VOD'])
  await into.selectOption({ label: 'Stream VOD' })
  // Streamer VOD: audio only starts off; the drama has audio, so replacing needs a tick.
  await expect(card.getByRole('switch', { name: 'Audio only' })).not.toBeChecked()
  const download = card.getByRole('button', { name: 'Download' })
  await expect(download).toBeDisabled()
  await expect(card.getByText('Still needed: tick “Replace the current audio”.')).toBeVisible()
  await card.getByRole('checkbox', { name: 'Replace the current audio' }).check()
  await download.click()
  await expect.poll(() => posted(s, '/api/media/dramas/14/download-url')[0]?.body).toEqual({
    url: 'https://video.example/watch?v=1', audio_only: false, confirm_replace_audio: true,
  })
  await expect(card.getByTestId('job-status')).toContainText('Done')
  await expect(card.getByRole('link', { name: 'Open Stream VOD in the workspace' })).toHaveAttribute('href', '#/drama/14/source')

  // A 403 (not at the PC): plain message, then the link box turns into the PC-only note.
  m.downloadForbidden = true
  await into.selectOption({ label: 'Radio Play' })
  // The finished download was Stream VOD's: it is not shown under Radio Play.
  await expect(card.getByRole('link', { name: /in the workspace$/ })).toHaveCount(0)
  await expect(card.getByTestId('job-status')).toHaveCount(0)
  await expect(card.getByRole('switch', { name: 'Audio only' })).toBeChecked()
  await card.getByRole('checkbox', { name: 'Replace the current audio' }).check()
  await card.getByRole('button', { name: 'Download' }).click()
  await expect(page.getByText('Importing from a link is PC only for now.')).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('import while another title job runs: the server message, no outcomes', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  // An older chapter import for this drama is stored as done; a start is refused.
  await mockImports(page, s, { importJob: 'done', importStartConflict: 'Transcribing is running for this title. Try again when it finishes.' })
  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1' }))
    localStorage.setItem('baihe.pref.sources.importInto.alpha:a0', '12')
  })
  await page.goto('/#/sources')
  const panel = page.getByRole('region', { name: 'Series' })
  await panel.getByRole('checkbox', { name: 'Chapter 1', exact: true }).check()
  await panel.getByRole('button', { name: 'Import 1 chapter' }).click()
  await expect.poll(() => posted(s, '/api/sources/alpha/import').length).toBe(1)
  await expect(panel.getByText('Transcribing is running for this title. Try again when it finishes.')).toBeVisible()
  const polls = () => s.calls.filter((c) => c.path === '/api/sources/jobs/sourceimport_12/result').length
  const before = polls()
  // Proving a non-event: a reattached poll would start within this window.
  await page.waitForTimeout(2000)
  // Not reattached: no polling of the stored run, and its outcomes never show.
  expect(polls()).toBe(before)
  await expect(panel.getByTestId('import-outcomes')).toHaveCount(0)
  await expect(panel.getByTestId('import-progress')).toHaveCount(0)
  await expect(panel.getByRole('button', { name: 'Import 1 chapter' })).toBeEnabled()
  expect(s.unmocked).toEqual([])
})

test('a remembered title that is gone or the wrong type is not used', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  await mockImports(page, s)
  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1' }))
    // Radio Play is not a comic drama.
    localStorage.setItem('baihe.pref.sources.importInto.alpha:a0', '13')
  })
  await page.goto('/#/sources')
  const panel = page.getByRole('region', { name: 'Series' })
  await panel.getByRole('checkbox', { name: 'Chapter 1', exact: true }).check()
  await expect(panel.getByRole('combobox', { name: 'Import into' })).toHaveValue('')
  await expect(panel.getByRole('button', { name: 'Import 1 chapter' })).toBeDisabled()
  await expect(panel.getByText('Still needed: a title to import into.')).toBeVisible()
  expect(s.calls.some((c) => c.path.startsWith('/api/sources/jobs/sourceimport_'))).toBe(false)
  expect(s.unmocked).toEqual([])
})

test('the chapter ticked from a link is not ticked again when the series is reopened from results', async ({ page }) => {
  const s = await mockSources(page, { search: 'done' })
  await mockImports(page, s)
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://alpha.example/a/c2')
  await page.getByRole('button', { name: 'Preview' }).click()
  await page.getByRole('article', { name: 'Link preview' }).getByRole('button', { name: 'Open series' }).click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByRole('checkbox', { name: 'Chapter 2', exact: true })).toBeChecked()
  await panel.getByRole('button', { name: 'Close' }).click()
  await expect(panel).toHaveCount(0)
  await page.getByRole('radio', { name: 'Search by title' }).check()
  await page.getByRole('button', { name: 'Open on Alpha Comics' }).first().click()
  await expect(panel.getByRole('checkbox', { name: 'Chapter 1', exact: true })).toBeVisible()
  await expect(panel.getByRole('checkbox', { name: 'Chapter 2', exact: true })).not.toBeChecked()
  expect(s.unmocked).toEqual([])
})

test('Track shows for a source without chapter import', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  await mockImports(page, s)
  // Alpha without import: Track still works (R4 doesn't need import).
  const list = SOURCES.map((x) => (x.name === 'alpha' ? { ...x, import_supported: false } : x))
  await page.route(/\/api\/sources(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(list) }),
  )
  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1' }))
  })
  await page.goto('/#/sources')
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText('Alpha Comics · 124 chapters · Ongoing · Chinese')).toBeVisible()
  await expect(panel.getByTestId('import-bar')).toHaveCount(0)
  await expect(panel.getByRole('combobox', { name: 'Import into' })).toHaveCount(0)
  // No import picker, so no drama list (New chapters loads one once something is tracked).
  expect(s.calls.some((c) => c.path.startsWith('/api/library/dramas'))).toBe(false)
  await panel.getByRole('button', { name: 'Track for new chapters' }).click()
  await expect.poll(() => posted(s, '/api/sources/tracked')[0]?.body).toEqual({ source: 'alpha', series_id: 'a0', tracked: true })
  await expect(panel.getByRole('button', { name: 'Track for new chapters' })).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('save chosen chapters of a comic series as CBZ files, without a title', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s)
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://alpha.example/a/c2')
  await page.getByRole('button', { name: 'Preview' }).click()
  await page.getByRole('article', { name: 'Link preview' }).getByRole('button', { name: 'Open series' }).click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByRole('checkbox', { name: 'Chapter 2', exact: true })).toBeChecked()
  await panel.getByRole('checkbox', { name: 'Chapter 2', exact: true }).uncheck()

  const save = panel.getByTestId('save-cbz')
  await expect(save.getByRole('button', { name: 'Save 0 chapters as CBZ' })).toBeDisabled()
  await panel.getByRole('checkbox', { name: 'Chapter 1', exact: true }).check()
  await panel.getByRole('checkbox', { name: 'Chapter 3', exact: true }).check()
  // No drama is needed to save.
  await save.getByRole('button', { name: 'Save 2 chapters as CBZ' }).click()
  await expect.poll(() => posted(s, '/api/sources/alpha/save')[0]?.body).toEqual({ series_id: 'a0', chapter_ids: ['c1', 'c3'] })
  const outcomes = panel.getByTestId('save-outcomes')
  await expect(outcomes.getByText('1 saved · 1 already saved')).toBeVisible()
  await expect(outcomes.getByText('Saved · 20 pages')).toBeVisible()
  await expect(outcomes.getByText('Already saved', { exact: true })).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('with no novel titles yet the picker offers New title as a button', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: NOVEL_PREVIEW, dramas: [] })
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://novels.example/book/5')
  await page.getByRole('textbox', { name: 'Paste a link' }).press('Enter')
  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByText('No novel titles yet.')).toBeVisible()
  await card.getByRole('button', { name: 'New title', exact: true }).click()
  await expect(card.getByRole('group', { name: 'New title' }).getByLabel('Title')).toBeVisible()
  expect(s.unmocked).toEqual([])
})
