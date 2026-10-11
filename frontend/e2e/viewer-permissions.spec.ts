import { expect, test, type Page } from '@playwright/test'

import { ME } from './authMocks'
import { openExportBlocks } from './exportBlocks'
import { clearDrama3Lines, clearReviewResults, seedReviewResults } from './reviewResultsSeed'
import { withExportLines, withTranslateLines } from './stageLineMocks'

// A limited household member (library.read and lines.edit, nothing else) on the workspace pages and Settings.
// Only /api/auth/me and /api/meta are mocked; reads go to the seeded API (auth off), and every write is
// answered 403 here and recorded, so nothing reaches the shared library and each page is checked against
// what the server really says to this member: the control is hidden or disabled, or the refusal is plain.
// Library, Quick translate and Diagnostics are covered elsewhere.

const REFUSAL = 'Not allowed from this device or account.'
const DRAMA = 3

test.beforeEach(() => seedReviewResults())
test.afterEach(() => {
  clearReviewResults()
  clearDrama3Lines()
})

async function signInAsMember(page: Page) {
  const refused: string[] = []
  await page.route('**/api/**', (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    const forbidden = () => route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } })
    if (path === '/api/auth/me') return route.fulfill({ json: ME.signedIn })
    // A member is on the household address, not at the PC.
    if (path === '/api/meta') return route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } })
    // Admin-only reads: the real server refuses these to a member.
    if (path === '/api/settings' || path === '/api/settings/engine-routing') return forbidden()
    if (req.method() !== 'GET' && req.method() !== 'HEAD') {
      refused.push(`${req.method()} ${path}`)
      return forbidden()
    }
    return route.fallback()
  })
  return refused
}

const refusal = (page: Page) => page.getByRole('alert').filter({ hasText: REFUSAL })

test('Review: AI review jobs are refused plainly and the page stays usable', async ({ page }) => {
  const refused = await signInAsMember(page)
  await page.goto(`/#/drama/${DRAMA}/review`)
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(4)
  expect(refused).toEqual([])

  const consistency = page.getByRole('button', { name: 'Check consistency', exact: true })
  await consistency.click()
  await expect(refusal(page)).toBeVisible()
  expect(refused).toEqual([`POST /api/review-jobs/dramas/${DRAMA}/consistency`])
  await expect(consistency).toBeEnabled()

  await page.getByRole('button', { name: 'Fix flagged lines' }).click()
  await expect.poll(() => refused).toContain(`POST /api/review-jobs/dramas/${DRAMA}/fix-flagged`)
  await expect(refusal(page).first()).toBeVisible()
})

test('Translate: Translate is refused plainly and starts no job', async ({ page }) => {
  const refused = await signInAsMember(page)
  await withTranslateLines(page, DRAMA)
  await page.goto(`/#/drama/${DRAMA}/translate`)
  const run = page.getByRole('region', { name: 'Translate run' })
  const start = run.getByRole('button', { name: /^Translate \d+ lines?$/ })
  await expect(start).toBeEnabled()
  expect(refused).toEqual([])

  await start.click()
  await expect(refusal(page)).toBeVisible()
  expect(refused).toEqual([`POST /api/translate-run/dramas/${DRAMA}/run`])
  await expect(page.getByTestId('job-status')).toHaveCount(0)
  await expect(start).toBeEnabled()
})

test('Translate: editing the drama (create series) is refused plainly', async ({ page }) => {
  const refused = await signInAsMember(page)
  await page.goto(`/#/drama/${DRAMA}/translate`)
  await page.getByRole('button', { name: /^Create series/ }).click()
  await expect(refusal(page)).toBeVisible()
  expect(refused).toEqual([`POST /api/dramas/${DRAMA}/metadata`])
})

test('Export: Mark as exported and the media job starts are refused plainly', async ({ page }) => {
  const refused = await signInAsMember(page)
  await withExportLines(page, DRAMA)
  await page.goto(`/#/drama/${DRAMA}/export`)
  await expect(page.getByTestId('readiness')).toContainText('lines')
  expect(refused).toEqual([])

  await page.getByRole('button', { name: 'Mark as exported' }).click()
  await expect(refusal(page)).toBeVisible()
  expect(refused).toEqual([`POST /api/export/dramas/${DRAMA}/mark-exported`])

  await page.getByText('Video and audio', { exact: true }).click()
  await openExportBlocks(page)
  await page.getByRole('button', { name: 'Start subtitle-track video export' }).click()
  await expect.poll(() => refused).toContain(`POST /api/export/dramas/${DRAMA}/softsub-video`)
  await expect(page.getByTestId('job-status')).toHaveCount(0)
})

test('Settings: a member gets no keys, engines or system tabs and no sharing list; a refused switch says so', async ({ page }) => {
  const refused = await signInAsMember(page)
  await page.goto('/#/settings')
  const sharing = page.getByRole('region', { name: 'Sharing' })
  const shareDefault = sharing.getByRole('switch', { name: 'New items I create are shared with the household' })
  await expect(shareDefault).toBeVisible()

  // Nothing that writes a key or an engine choice is on the page at all.
  await expect(page.getByRole('tab')).toHaveCount(0)
  await expect(page.locator('input[type="password"]')).toHaveCount(0)
  await expect(page.getByRole('switch', { name: 'Discover' })).toHaveCount(1) // Customize menu is the member's own
  await expect(page.getByText('Stronger translation for hard lines')).toHaveCount(0)
  // The admin-only per-item sharing list is hidden, not just refused.
  await expect(sharing.getByRole('list', { name: 'Titles and series' })).toHaveCount(0)
  expect(refused).toEqual([])

  await shareDefault.click()
  await expect(sharing.getByRole('alert')).toHaveText("You don't have permission to change this.")
  expect(refused).toEqual(['POST /api/sharing/share-by-default'])
})
