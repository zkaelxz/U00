import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'
import { openFoldFor } from './reviewFolds'

// A line edit survives what used to lose it: leaving Review while its save
// fails, and a Records write (Use this version) while it is open. Lines are
// seeded into the throwaway library like review-stage.spec.ts; the failing
// save and the version endpoints are mocked, every other call is real.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=3.0, zh='谢谢', en='Thanks, friend'),
])
`], { cwd: repoRoot })
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')
const LINE_SAVE = '**/api/lines/dramas/3/lines/*'

async function open(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(2)
}

async function typeInto(page: Page, n: number, text: string) {
  const row = rows(page).nth(n)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill(text)
  return row
}

test('an edit whose save fails on the way out of Review is offered back on return', async ({ page }) => {
  await open(page)
  await typeInto(page, 0, 'Hello, kept')
  await page.route(LINE_SAVE, (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'The line changed.' } } }))
  const refused = page.waitForResponse((r) => r.url().includes('/api/lines/dramas/3/lines/') && r.status() === 409)
  await page.goto('/#/drama/3/translate')
  await refused
  await page.unroute(LINE_SAVE)

  await open(page)
  await expect(page.getByRole('status').filter({ hasText: 'Your unsaved edit to #1 is back.' })).toBeVisible()
  const row = rows(page).nth(0)
  await expect(row.getByLabel('Translation')).toHaveValue('Hello, kept')
  await row.getByLabel('Translation').press('Control+s')
  await row.getByLabel('Translation').press('Escape')
  await expect(row.getByTestId('line-en')).toContainText('Hello, kept')

  // Saved now: nothing is offered on the next visit.
  await page.goto('/#/drama/3/translate')
  await open(page)
  await expect(rows(page).nth(0).getByLabel('Translation')).toHaveCount(0)
})

test('Use this version during an edit saves the edit first, so it never comes back as a conflict', async ({ page }) => {
  const calls: string[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/lines\/dramas\/3\/lines\/\d+$|\/versions\/9\/activate$/.test(r.url())) {
      calls.push(r.url().endsWith('/activate') ? 'activate' : 'save')
    }
  })
  await page.route('**/api/review/dramas/3/versions', (route) => route.fulfill({
    json: [{ id: 9, drama_id: 3, label: 'Pass 2', engine: 'claude', model: 'm', is_active: false, created_at: '2026-09-01' }],
  }))
  await page.route('**/api/review/dramas/3/versions/9/activate', (route) => route.fulfill({
    json: { drama_id: 3, version_id: 9, label: 'Pass 2', activated: true, lines_changed: 0, conflicts: [] },
  }))
  await open(page)
  const row = await typeInto(page, 0, 'Hello, before the switch')

  await openFoldFor(page, 'Records')
  await page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^Records$/ }) }).click()
  const list = page.getByTestId('versions-list')
  await list.getByRole('button', { name: 'Use this version Pass 2' }).click()
  await list.getByRole('button', { name: 'Confirm: replace the English with Pass 2' }).click()
  await expect(page.getByTestId('activate-status')).toContainText('Now using “Pass 2”')

  expect(calls).toEqual(['save', 'activate'])
  await expect(row.getByLabel('Translation')).toHaveCount(0)
  await expect(row.getByTestId('line-en')).toContainText('Hello, before the switch')
  await expect(page.getByTestId('line-conflict')).toHaveCount(0)
})

test('Use this version waits while the open edit cannot be saved', async ({ page }) => {
  let activated = false
  await page.route('**/api/review/dramas/3/versions', (route) => route.fulfill({
    json: [{ id: 9, drama_id: 3, label: 'Pass 2', engine: 'claude', model: 'm', is_active: false, created_at: '2026-09-01' }],
  }))
  await page.route('**/api/review/dramas/3/versions/9/activate', (route) => {
    activated = true
    return route.fulfill({ json: { drama_id: 3, version_id: 9, label: 'Pass 2', activated: true, lines_changed: 0, conflicts: [] } })
  })
  await open(page)
  const row = await typeInto(page, 0, 'Hello, not saved')
  await page.route(LINE_SAVE, (route) => route.abort())

  await openFoldFor(page, 'Records')
  await page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^Records$/ }) }).click()
  const list = page.getByTestId('versions-list')
  await list.getByRole('button', { name: 'Use this version Pass 2' }).click()
  await list.getByRole('button', { name: 'Confirm: replace the English with Pass 2' }).click()

  await expect(page.getByTestId('records-held')).toHaveText('Save or discard your edit to #1 first.')
  expect(activated).toBe(false)
  await expect(row.getByLabel('Translation')).toHaveValue('Hello, not saved')
})
