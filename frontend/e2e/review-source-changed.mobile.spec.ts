import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { openFoldFor } from './reviewFolds'
import { mockSourceChanged } from './sourceChangedMocks'

// Phone: the "Source changed" badge, the Review checks count and both actions
// fit the screen with no sideways scroll.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('phone: badge, count and both actions on screen', async ({ page }) => {
  await mockSourceChanged(page)
  await page.goto('/#/drama/3/review?tools=problems')
  await openFoldFor(page, 'Source changed')
  await expect(page.locator('.review-line[data-line-id="1"]').getByTestId('source-changed-badge')).toHaveText('Source changed')
  const panel = page.getByTestId('source-changed')
  await expect(panel.getByTestId('source-changed-count')).toHaveText('2 lines changed in the source since they were translated.')
  for (const name of ['Retranslate these', /Keep English for all/]) {
    const button = panel.getByRole('button', { name })
    await button.scrollIntoViewIfNeeded()
    await expect(button).toBeVisible()
    const box = (await button.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(390)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test("phone at 320 px: ⋯ stays in the row's top-right corner, nothing scrolls sideways, and the source text is not a ⋯ tap", async ({ page }) => {
  // Real lines, not the mock: the first one has a speaker and a Source changed badge, which is what filled the meta line.
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh='这是一个很长的中文句子用来测试第一行文字会不会被右上角的按钮盖住' * 2, en='Line %d' % i, speaker='Narrator', flag='source_changed' if i == 0 else '') for i in range(6)])
`], { cwd: repoRoot })
  await page.setViewportSize({ width: 320, height: 700 })
  await page.goto('/#/drama/3/review')
  const row = page.locator('.review-line:not(.review-skeleton)').first()
  await expect(row.getByTestId('source-changed-badge')).toBeVisible()
  // clientWidth, not innerWidth: a phone browser zooms out to fit wide content and then innerWidth grows with it.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)

  const more = row.getByRole('button', { name: /^More actions for line/ })
  const rowBox = (await row.boundingBox())!
  const moreBox = (await more.boundingBox())!
  expect(Math.abs(moreBox.x + moreBox.width - (rowBox.x + rowBox.width))).toBeLessThanOrEqual(1)
  expect(Math.abs(moreBox.y - rowBox.y)).toBeLessThanOrEqual(1)

  // A tap in the middle of the right end of a row's first text line picks the row; it must not reach ⋯ and open the More sheet.
  await page.getByRole('radio', { name: /^All/ }).check({ force: true })
  const second = page.locator('.review-line').nth(1)
  const zh = second.locator('.review-zh')
  const zhBox = (await zh.boundingBox())!
  await zh.tap({ position: { x: zhBox.width - 6, y: 20 } })
  await expect(second).toHaveAttribute('aria-current', 'true')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
