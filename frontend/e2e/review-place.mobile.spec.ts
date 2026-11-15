import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

import { clearCheckLines, seedCheckLines } from './compareSelectedHelpers'
import { expectNoSidewaysScroll } from './mockHelpers'
import { expect, test } from './fixtures'

// Review at phone width reopens on the kept line. Title 3 gets 100 lines; three of them need a look (#24, #7 and #12,
// in that order).

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

// The seeded lines' audio goes too, so later specs see title 3 as seeded.
test.afterAll(() => {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
db.update_drama(3, audio_filename=None, source_video_filename=None)`], { cwd: repoRoot })
  clearCheckLines(3)
})

const row = (page: Page, n: number) => page.locator('.review-line').filter({ has: page.locator('.review-idx', { hasText: new RegExp(`^#${n}$`) }) })

test('the queue reopens on the kept card at 390 px, with no sideways scroll, and on its first card once that one is checked', async ({ page }) => {
  const ids = seedCheckLines({ count: 100 })
  await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
  await page.goto('/#/drama/3/review')
  const position = page.getByTestId('queue-position')
  const cards = page.locator('.review-line')
  const bar = page.getByRole('toolbar', { name: 'Line actions' })
  await expect(position).toHaveText('1 of 3 to look at')
  await bar.getByRole('button', { name: 'Skip for now' }).tap()
  await expect(position).toHaveText('2 of 3 to look at')
  await expect.poll(() => page.evaluate(() => localStorage.getItem('baihe.pref.review.at.3'))).toContain('"queue":{')

  await page.goto('/#/library')
  await expect(cards).toHaveCount(0)
  await page.goto('/#/drama/3/review')
  await expect(position).toHaveText('2 of 3 to look at')
  await expect(cards.locator('.review-idx')).toHaveText('Line 7')
  await expect(page.locator('.review-status')).toHaveText('Back where you stopped, on line 7.')
  await expectNoSidewaysScroll(page)

  const checked = await page.request.post(`/api/lines/dramas/3/lines/${ids[6]}/reviewed`, { data: { reviewed: true } })
  expect(checked.ok()).toBe(true)
  await page.goto('/#/library')
  await expect(cards).toHaveCount(0)
  await page.goto('/#/drama/3/review')
  await expect(position).toHaveText('1 of 2 to look at')
  await expect(cards.locator('.review-idx')).toHaveText('Line 24')
  await expectNoSidewaysScroll(page)
})

test('Full view on an audio title reopens with the kept row between the toolbar and the Line actions bar', async ({ page }) => {
  seedCheckLines({ count: 100, audio: true })
  await page.goto('/#/drama/3/review')
  await expect(row(page, 1)).toBeVisible()
  await page.getByRole('button', { name: 'Next page' }).first().tap()
  await expect(row(page, 41)).toBeVisible()
  await row(page, 55).locator('.review-idx').tap()
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => page.evaluate(() => localStorage.getItem('baihe.pref.review.at.3'))).toContain('"lineNumber":55')
  await page.goto('/#/library')
  await expect(page.locator('.review-line')).toHaveCount(0)
  await page.goto('/#/drama/3/review')
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => row(page, 55).evaluate((el) => {
    const r = el.getBoundingClientRect()
    const toolbar = document.querySelector('.review-toolbar')?.getBoundingClientRect()
    const bar = document.querySelector('[role="toolbar"][aria-label="Line actions"]')?.getBoundingClientRect()
    return !!toolbar && !!bar && r.top >= toolbar.bottom - 1 && r.bottom <= bar.top + 1
  }), { timeout: 5000 }).toBe(true)
  await expectNoSidewaysScroll(page)
})
