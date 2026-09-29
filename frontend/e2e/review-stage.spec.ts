import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// The seeded API has dramas but no lines, so this spec writes three lines for
// drama 3 straight into the throwaway library the test server uses (the same
// SQLite file), then drives the real API. The job endpoints are mocked.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeAll(() => {
  const code = `
import db
from core import Line
db.configure_library_dir(${JSON.stringify(libraryDir)})
db.save_lines(3, [
    Line(idx=1, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=2, start=1.5, end=3.0, zh='再见', en='', flag='uncertain', flag_note='check'),
    Line(idx=3, start=3.0, end=4.5, zh='谢谢', en='Thanks, friend'),
])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

const job = (status: string) => ({
  job_id: 'rj', status, progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1,
})

test('lists, filters, searches and edits a line against the real API', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.getByTestId('line-counts')).toContainText('3 in this view · 1 flagged · 1 untranslated')
  await expect(page.getByTestId('line-flag')).toContainText('uncertain')

  await page.getByLabel('Show').selectOption('flagged')
  await expect(page.locator('.review-line')).toHaveCount(1)
  await page.getByLabel('Show').selectOption('all')

  await page.getByLabel('Search lines').fill('Thanks')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.locator('.review-line')).toHaveCount(1)
  await page.getByRole('button', { name: 'Clear search' }).click()
  await expect(page.locator('.review-line')).toHaveCount(3)

  await page.screenshot({ path: 'e2e/screenshots-tmp/review-after.png', fullPage: true })

  const row = page.locator('.review-line').nth(0)
  await row.getByRole('button', { name: 'Edit' }).click()
  await row.getByLabel('Translation').fill('Hello, you')
  await row.getByRole('button', { name: 'Save line' }).click()
  await expect(page.getByTestId('line-en').filter({ hasText: 'Hello, you' })).toBeVisible()

  // Restore the original text.
  const edited = page.locator('.review-line').nth(0)
  await edited.getByRole('button', { name: 'Edit' }).click()
  await edited.getByLabel('Translation').fill('Hello there')
  await edited.getByRole('button', { name: 'Save line' }).click()
  await expect(page.getByTestId('line-en').filter({ hasText: 'Hello there' })).toBeVisible()

  // Dismiss the flag.
  await page.getByRole('button', { name: 'Dismiss flag' }).click()
  await expect(page.getByTestId('line-flag')).toHaveCount(0)
})

test('a stale edit shows the changed-elsewhere message', async ({ page }) => {
  await page.route('**/api/lines/dramas/3/lines/*', (route) =>
    route.fulfill({
      status: 409,
      json: { error: { code: 'conflict', message: 'This line changed since you loaded it.' } },
    }),
  )
  await page.goto('/#/drama/3/review')
  const row = page.locator('.review-line').nth(2)
  await row.getByRole('button', { name: 'Edit' }).click()
  await row.getByLabel('Translation').fill('Thank you')
  await row.getByRole('button', { name: 'Save line' }).click()
  await expect(row.getByTestId('line-conflict')).toContainText('changed elsewhere')
})

test('find and replace previews then applies and shows stale ids', async ({ page }) => {
  await page.route('**/api/lines/dramas/3/find-replace/apply', async (route) => {
    const id = route.request().postDataJSON().matches[0].id
    await route.fulfill({ json: { applied: 0, stale: 1, applied_ids: [], stale_ids: [id] } })
  })
  await page.goto('/#/drama/3/review')
  await page.getByLabel('Find', { exact: true }).fill('friend')
  await page.getByLabel('Replace with').fill('pal')
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('fr-matches')).toContainText('Thanks, pal')
  await page.getByRole('button', { name: /Apply 1 change/ }).click()
  await expect(page.getByTestId('fr-result')).toContainText('Applied 0; skipped 1')
  await expect(page.getByTestId('fr-stale')).toContainText('Skipped: #3')
})

test('a finished review job refetches the lines', async ({ page }) => {
  let done = false
  let lineFetches = 0
  await page.route('**/api/review-jobs/dramas/3/flag', (route) => route.fulfill({
    json: { job_id: 'rj', drama_id: 1, kind: 'flag', engine: 'x', model: null, line_count: 3 },
  }))
  await page.route('**/api/jobs/rj', (route) => route.fulfill({ json: job(done ? 'done' : 'running') }))
  await page.route('**/api/review/dramas/3/lines?*', (route) => {
    lineFetches += 1
    return route.continue()
  })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line')).toHaveCount(3)
  const before = lineFetches
  await page.getByRole('button', { name: 'Flag lines for a second look' }).click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  done = true
  await expect(page.getByTestId('job-status')).toContainText('done')
  await expect.poll(() => lineFetches).toBeGreaterThan(before)
})
