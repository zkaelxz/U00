import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from './fixtures'
import { openTab } from './reviewFolds'
import { clearDrama3Lines, clearReviewResults, seedReviewResults } from './reviewResultsSeed'
import { seedVideo } from './playerMocks'

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.afterAll(() => {
  clearReviewResults()
  clearDrama3Lines()
})

test('phone: every tab fits the width, light and dark', async ({ page }) => {
  seedReviewResults()
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review')
    await expect(page.getByRole('tablist', { name: 'Review tools' })).toBeVisible()
    for (const tab of ['Check', 'Fix', 'Coverage', 'Restructure', 'History', 'Extras']) {
      await openTab(page, tab)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${tab} ${scheme}`).toBe(true)
    }
  }
})

test('phone: once scrolled the sticky area shrinks and at least 8 lines show', async ({ page }) => {
  test.skip(!seedVideo(), 'OpenCV is not installed, so there is no test video')
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.save_lines(3, [Line(idx=i, start=i*2.0, end=i*2.0+1.5, zh=f'第{i}句', en=f'Line number {i} translated text') for i in range(60)])`], { cwd: repoRoot })
  await page.goto('/#/drama/3/review')
  await page.locator('.review-line:not(.review-skeleton)').first().waitFor()
  const sticky = () =>
    page.evaluate(() => (document.querySelector('.review-toolbar') as HTMLElement).getBoundingClientRect().bottom / innerHeight)
  expect(await sticky()).toBeGreaterThan(0.3)
  await page.mouse.wheel(0, 900)
  await expect(page.locator('.review-toolbar.is-collapsed')).toBeVisible()
  await expect.poll(sticky).toBeLessThan(0.25)
  // Lines at least half in the space between the sticky area and the bottom edit bar.
  const visible = await page.evaluate(() => {
    const top = (document.querySelector('.review-toolbar') as HTMLElement).getBoundingClientRect().bottom
    const bar = document.querySelector('.review-editbar') as HTMLElement | null
    const bottom = bar ? bar.getBoundingClientRect().top : innerHeight
    return [...document.querySelectorAll('.review-line:not(.review-skeleton)')].filter((e) => {
      const r = e.getBoundingClientRect()
      const mid = (r.top + r.bottom) / 2
      return mid >= top && mid <= bottom
    }).length
  })
  expect(visible).toBeGreaterThanOrEqual(8)
  // The player keeps its place in the page: scrolling back restores the full bar.
  await page.mouse.wheel(0, -2000)
  await expect(page.locator('.review-toolbar.is-collapsed')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
