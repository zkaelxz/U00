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

test('Esc cancels, details are lazy, Alt+Down jumps to flagged lines', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const row = page.locator('.review-line').nth(0)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('discard me')
  await row.getByLabel('Translation').press('Escape')
  await expect(row.getByTestId('line-en')).toContainText('Hello there')

  await expect(page.getByLabel('Speaker')).toHaveCount(0)
  await row.getByRole('button', { name: 'Edit details' }).click()
  await expect(row.getByLabel('Speaker')).toBeVisible()
  await expect(page.getByLabel('Speaker')).toHaveCount(1)
  await row.getByRole('button', { name: 'Edit details' }).click()
  await expect(page.getByLabel('Speaker')).toHaveCount(0)

  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('Alt+ArrowDown')
  await expect(page.locator('.review-line[data-flagged="true"]')).toBeFocused()
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

  // Click the English text, type, Enter saves.
  const row = page.locator('.review-line').nth(0)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Hello, you')
  await row.getByLabel('Translation').press('Enter')
  await expect(page.getByTestId('line-en').filter({ hasText: 'Hello, you' })).toBeVisible()

  // Restore the original text with Ctrl+S.
  const edited = page.locator('.review-line').nth(0)
  await edited.getByTestId('line-en').click()
  await edited.getByLabel('Translation').fill('Hello there')
  await edited.getByLabel('Translation').press('Control+s')
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
  await row.getByTestId('line-en').click()
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
  await page.locator('summary', { hasText: 'Find and replace' }).click()
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
  await page.locator('summary', { hasText: 'AI review' }).click()
  await page.getByRole('button', { name: 'Flag lines for a second look' }).click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  done = true
  await expect(page.getByTestId('job-status')).toContainText('done')
  await expect.poll(() => lineFetches).toBeGreaterThan(before)
})

const improveOut = (en: string) => ({
  line_id: 1, current_en: en, suggestion: 'Hi there', changed: true, engine: 'x', model: null,
})

async function openAi(page: import('@playwright/test').Page, n: number, item: string) {
  const row = page.locator('.review-line').nth(n)
  await row.getByRole('button', { name: 'AI actions', exact: true }).click()
  await row.getByRole('button', { name: item, exact: true }).click()
  return row
}

test('improve, use this, and the row shows the saved value', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/improve', async (route) => {
    expect(route.request().postDataJSON().issue).toBe('too stiff')
    await route.fulfill({ json: { ...improveOut('Hello there'), line_id: Number(route.request().url().split('/lines/')[1].split('/')[0]) } })
  })
  await page.goto('/#/drama/3/review')
  const row = await openAi(page, 0, 'Improve')
  await row.getByLabel('What to fix (optional)').fill('too stiff')
  await row.getByRole('button', { name: 'Suggest' }).click()
  await expect(row.getByTestId('line-ai-suggestion')).toHaveText('Hi there')
  await expect(row.getByTestId('line-en')).toContainText('Hello there')
  await row.getByRole('button', { name: 'Use this' }).click()
  await expect(page.getByTestId('line-en').filter({ hasText: 'Hi there' })).toBeVisible()
  await expect(page.getByTestId('line-ai-panel')).toHaveCount(0)

  // Restore the seeded text.
  const again = page.locator('.review-line').nth(0)
  await again.getByTestId('line-en').click()
  await again.getByLabel('Translation').fill('Hello there')
  await again.getByLabel('Translation').press('Enter')
  await expect(page.getByTestId('line-en').filter({ hasText: 'Hello there' })).toBeVisible()
})

test('a kept line says so, and why-this shows an explanation', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/improve', (route) =>
    route.fulfill({ json: { ...improveOut('Thanks, friend'), suggestion: 'Thanks, friend', changed: false } }))
  await page.route('**/api/line-ai/dramas/3/lines/*/explain', (route) =>
    route.fulfill({ json: { line_id: 1, explanation: 'Friendly register.', engine: 'x', model: null } }))
  await page.goto('/#/drama/3/review')
  const row = await openAi(page, 2, 'Improve')
  await row.getByRole('button', { name: 'Suggest' }).click()
  await expect(row.getByTestId('line-ai-result')).toContainText('kept this line')
  await expect(row.getByRole('button', { name: 'Use this' })).toHaveCount(0)
  await row.getByRole('button', { name: 'Close' }).click()

  await openAi(page, 0, 'Why this?')
  await expect(row.page().getByTestId('line-ai-explanation')).toHaveText('Friendly register.')
  await page.getByRole('button', { name: 'Hide explanation' }).click()
  await expect(page.getByTestId('line-ai-panel')).toHaveCount(0)
})

test('no configured key shows a short message pointing to Settings', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/explain', (route) =>
    route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: 'no key' } } }))
  await page.goto('/#/drama/3/review')
  const row = await openAi(page, 0, 'Why this?')
  await expect(row.getByTestId('line-ai-unavailable')).toContainText('Settings')
})

test('applying a suggestion over a concurrent edit shows the conflict', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/improve', (route) =>
    route.fulfill({ json: improveOut('Thanks, friend') }))
  await page.route('**/api/lines/dramas/3/lines/*', (route) => {
    expect(route.request().postDataJSON()).toEqual({ en: 'Hi there', expected: { en: 'Thanks, friend' } })
    return route.fulfill({
      status: 409,
      json: { error: { code: 'conflict', message: 'This line changed since you loaded it.' } },
    })
  })
  await page.goto('/#/drama/3/review')
  const row = await openAi(page, 2, 'Improve')
  await row.getByRole('button', { name: 'Suggest' }).click()
  await row.getByRole('button', { name: 'Use this' }).click()
  await expect(row.getByTestId('line-conflict')).toContainText('changed elsewhere')
})
