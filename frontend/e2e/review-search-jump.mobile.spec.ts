import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Parity R05 on a phone: "Show on its page" is a full touch target and the
// search results fit the screen without sideways scrolling.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5,
                       zh=('独一无二' if i == 44 else f'第{i}句'), en=f'Line {i}') for i in range(50)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

test('phone: a search hit opens on its page from a 44px button', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await expect(rows).toHaveCount(40)
  await page.locator('.review-search-toggle').click()
  await page.getByPlaceholder('Search source or translation').fill('独一无二')
  await expect(rows).toHaveCount(1)

  const show = page.getByRole('button', { name: 'Show on its page: line 45' })
  expect(await hitHeight(show)).toBeGreaterThanOrEqual(44)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)

  await show.tap()
  await expect(rows).toHaveCount(10)
  await expect(page.locator('.review-line[aria-current="true"]')).toContainText('#45')
})
