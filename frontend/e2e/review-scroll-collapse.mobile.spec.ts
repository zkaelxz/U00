import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Collapsing the Review toolbar hides the header and stage tabs and shortens the page, which pulls scrollY down by about 110px. With
// the old 60px expand line the bar collapsed at 161, landed at 57, expanded again and flickered on every scroll.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}') for i in range(50)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

test('phone: the toolbar settles after collapsing and after expanding instead of flickering', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  const bar = page.locator('.review-toolbar')
  await page.waitForTimeout(500)

  // Count class flips: a flicker is a loop, so even one extra flip after the first one is the bug.
  await page.evaluate(() => {
    const w = window as unknown as { __flips: number }
    w.__flips = 0
    new MutationObserver(() => { w.__flips += 1 }).observe(document.querySelector('.review-toolbar')!, { attributes: true, attributeFilter: ['class'] })
  })
  const flips = () => page.evaluate(() => (window as unknown as { __flips: number }).__flips)

  await page.evaluate(() => window.scrollTo(0, 161))
  await expect(bar).toHaveClass(/is-collapsed/)
  await page.waitForTimeout(800)
  await expect(bar).toHaveClass(/is-collapsed/)
  expect(await flips()).toBe(1)

  await page.evaluate(() => window.scrollTo(0, 15))
  await expect(bar).not.toHaveClass(/is-collapsed/)
  await page.waitForTimeout(800)
  await expect(bar).not.toHaveClass(/is-collapsed/)
  expect(await flips()).toBe(2)
})
