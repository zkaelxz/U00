import { expect as baseExpect, test, type Page } from '@playwright/test'

import { chapterImportResult, mockImports } from './sourcesImportMocks'
import { mockSources, posted } from './sourcesMocks'

// Desktop: the chapter picker marks what the chosen drama already
// has (and what earlier runs left failed or not attempted), a partial run
// lists its not-attempted chapters, and "Retry failed chapters (N)" sends
// exactly the retry set. Every call is mocked (sourcesImportMocks.ts).
// Jobs poll every 1.5 s, so allow a few polls per assertion.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

const SHOTS = process.env.STEP107_SHOTS_DIR
const TIMED_OUT = 'The site took too long to answer.'
const NOT_ATTEMPTED = 'Not attempted: the import stopped before this chapter.'

// Open series alpha:a0 (124 chapters) with drama 12 (Alpha Comic) remembered.
async function openSeries(page: Page, dramaId: number | null = 12) {
  await page.addInitScript((id) => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1' }))
    if (id) localStorage.setItem('baihe.pref.sources.importInto.alpha:a0', String(id))
  }, dramaId)
  await page.goto('/#/sources')
  return page.getByRole('region', { name: 'Series' })
}

const row = (page: Page, name: string) =>
  page.locator('.sources-pick > li').filter({ has: page.getByRole('checkbox', { name, exact: true }) })

const stateGets = (s: { calls: { method: string; path: string }[] }) =>
  s.calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/sources/alpha/import-state?'))

test('marks imported, failed and not-attempted chapters; Select all skips imported; Retry sends the saved set', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  const m = await mockImports(page, s, {
    importHold: true,
    importState: {
      imported_chapter_ids: ['c1', 'c2'],
      retry: [
        { chapter_id: 'c3', title: 'Chapter 3', status: 'failed', error: TIMED_OUT },
        { chapter_id: 'c4', title: 'Chapter 4', status: 'not_attempted', error: NOT_ATTEMPTED },
      ],
    },
  })
  const panel = await openSeries(page)
  await expect.poll(() => stateGets(s)[0]?.path).toBe('/api/sources/alpha/import-state?series_id=a0&drama_id=12')

  await expect(row(page, 'Chapter 1').getByText('Imported', { exact: true })).toBeVisible()
  await expect(row(page, 'Chapter 2').getByText('Imported', { exact: true })).toBeVisible()
  await expect(row(page, 'Chapter 3').getByText('Failed', { exact: true })).toBeVisible()
  await expect(row(page, 'Chapter 3').getByText(TIMED_OUT)).toBeVisible()
  await expect(row(page, 'Chapter 4').getByText('Not attempted', { exact: true })).toBeVisible()
  await expect(row(page, 'Chapter 5').locator('.pill')).toHaveCount(0)

  // Select all leaves the imported ones out; they can still be ticked by hand.
  await panel.getByRole('checkbox', { name: 'Select all 122 not yet imported' }).check()
  await expect(panel.getByRole('checkbox', { name: 'Chapter 1', exact: true })).not.toBeChecked()
  await expect(panel.getByRole('button', { name: 'Import 122 chapters' })).toBeEnabled()
  await panel.getByRole('checkbox', { name: 'Chapter 1', exact: true }).check()
  await expect(panel.getByRole('button', { name: 'Import 123 chapters' })).toBeEnabled()
  await panel.getByRole('checkbox', { name: 'Select all 122 not yet imported' }).uncheck()

  if (SHOTS) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await panel.getByTestId('import-retry').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/step107-desktop.png` })
  }

  // Retry sends exactly the saved retry ids, and waits while the job runs.
  const retry = panel.getByRole('button', { name: 'Retry failed chapters (2)' })
  await retry.click()
  await expect.poll(() => posted(s, '/api/sources/alpha/import')[0]?.body).toEqual({ series_id: 'a0', chapter_ids: ['c3', 'c4'], drama_id: 12 })
  await expect(retry).toBeDisabled()
  await expect(panel.getByRole('button', { name: 'Importing…' })).toBeDisabled()

  // Both import now; the state reloads after the run and the retry goes away.
  m.importBody = chapterImportResult({
    chapters: [
      { chapter_id: 'c3', title: 'Chapter 3', outcome: 'imported', pages: 8 },
      { chapter_id: 'c4', title: 'Chapter 4', outcome: 'imported', pages: 9 },
    ],
    imported_count: 2, skipped_count: 0, failed_count: 0, retry_chapter_ids: [], partial: false,
  })
  m.importState = { imported_chapter_ids: ['c1', 'c2', 'c3', 'c4'], retry: [] }
  m.importHold = false
  await expect(panel.getByTestId('import-outcomes').getByText('2 imported', { exact: true })).toBeVisible()
  await expect(row(page, 'Chapter 4').getByText('Imported', { exact: true })).toBeVisible()
  await expect(panel.getByTestId('import-retry')).toHaveCount(0)
  expect(stateGets(s).length).toBeGreaterThanOrEqual(2)
  expect(s.unmocked).toEqual([])
})

test('a partial run shows failed and not attempted, then Retry sends exactly those ids', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  const m = await mockImports(page, s, {
    importHold: true,
    importBody: chapterImportResult({
      chapters: [
        { chapter_id: 'c1', title: 'Chapter 1', outcome: 'imported', pages: 20 },
        { chapter_id: 'c2', title: 'Chapter 2', outcome: 'failed', error: TIMED_OUT },
        { chapter_id: 'c3', title: 'Chapter 3', outcome: 'not_attempted', error: NOT_ATTEMPTED },
        { chapter_id: 'c4', title: 'Chapter 4', outcome: 'not_attempted', error: NOT_ATTEMPTED },
      ],
      imported_count: 1, skipped_count: 0, failed_count: 1, not_attempted_count: 2,
      retry_chapter_ids: ['c2', 'c3', 'c4'], partial: true,
    }),
  })
  const panel = await openSeries(page)
  await expect.poll(() => stateGets(s).length).toBe(1)
  await expect(panel.getByTestId('import-retry')).toHaveCount(0)
  for (const n of [1, 2, 3, 4]) await panel.getByRole('checkbox', { name: `Chapter ${n}`, exact: true }).check()
  await panel.getByRole('button', { name: 'Import 4 chapters' }).click()
  await expect.poll(() => posted(s, '/api/sources/alpha/import')[0]?.body).toEqual({ series_id: 'a0', chapter_ids: ['c1', 'c2', 'c3', 'c4'], drama_id: 12 })

  // What the server saved for this run.
  m.importState = {
    imported_chapter_ids: ['c1'],
    retry: [
      { chapter_id: 'c2', title: 'Chapter 2', status: 'failed', error: TIMED_OUT },
      { chapter_id: 'c3', title: 'Chapter 3', status: 'not_attempted', error: NOT_ATTEMPTED },
      { chapter_id: 'c4', title: 'Chapter 4', status: 'not_attempted', error: NOT_ATTEMPTED },
    ],
  }
  m.importHold = false
  const outcomes = panel.getByTestId('import-outcomes')
  await expect(outcomes.getByText('1 imported · 1 failed · 2 not attempted')).toBeVisible()
  await expect(outcomes.getByText(`Failed: ${TIMED_OUT}`)).toBeVisible()
  await expect(outcomes.getByText('Not attempted', { exact: true })).toHaveCount(2)

  // The picker is refreshed from the saved state.
  await expect(row(page, 'Chapter 1').getByText('Imported', { exact: true })).toBeVisible()
  await expect(row(page, 'Chapter 2').getByText('Failed', { exact: true })).toBeVisible()
  await expect(row(page, 'Chapter 3').getByText('Not attempted', { exact: true })).toBeVisible()
  expect(stateGets(s).length).toBeGreaterThanOrEqual(2)

  m.importHold = true
  await panel.getByRole('button', { name: 'Retry failed chapters (3)' }).click()
  await expect.poll(() => posted(s, '/api/sources/alpha/import')[1]?.body).toEqual({ series_id: 'a0', chapter_ids: ['c2', 'c3', 'c4'], drama_id: 12 })
  expect(posted(s, '/api/sources/alpha/import')).toHaveLength(2)
  expect(s.unmocked).toEqual([])
})

test('more than 200 to retry: sends the first 200 and says so; no drama, no state call', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  const retry = Array.from({ length: 230 }, (_, i) => ({ chapter_id: `x${i + 1}`, title: '', status: 'failed', error: 'Import failed.' }))
  await mockImports(page, s, { importHold: true, importState: { imported_chapter_ids: [], retry } })
  const panel = await openSeries(page, null)
  await expect(panel.getByRole('combobox', { name: 'Import into' })).toHaveValue('')
  await expect(panel.getByTestId('import-retry')).toHaveCount(0)
  expect(stateGets(s)).toHaveLength(0)

  await panel.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Alpha Comic' })
  await expect(panel.getByText('Retries the first 200 of 230; run it again for the rest.')).toBeVisible()
  await panel.getByRole('button', { name: 'Retry failed chapters (230)' }).click()
  await expect.poll(() => (posted(s, '/api/sources/alpha/import')[0]?.body as { chapter_ids: string[] } | undefined)?.chapter_ids)
    .toEqual(retry.slice(0, 200).map((r) => r.chapter_id))
  expect(s.unmocked).toEqual([])
})
