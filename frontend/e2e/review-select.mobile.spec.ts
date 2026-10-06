import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Phone: the tick box is a 44px target, sits in the row's own gutter and does
// not take over taps on the row.

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

const box = (page: import('@playwright/test').Page, n: number) => page.getByRole('checkbox', { name: `Select line #${n}`, exact: true })

test('phone: 44px tick box, range, select all, bar, and taps still reach the row', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await expect(rows).toHaveCount(40)

  const target = page.locator('.review-select').first()
  const b = await target.boundingBox()
  expect(Math.round(b!.width)).toBeGreaterThanOrEqual(44)
  expect(Math.round(b!.height)).toBeGreaterThanOrEqual(44)

  const heightBefore = (await rows.nth(1).boundingBox())!.height
  await box(page, 2).tap()
  await expect(page.getByTestId('selection-bar')).toContainText('1 selected')
  expect((await rows.nth(1).boundingBox())!.height).toBe(heightBefore)

  await page.getByRole('button', { name: 'Select all shown' }).tap()
  await expect(page.getByTestId('selection-bar')).toContainText('40 selected')
  await page.getByTestId('selection-bar').getByRole('button', { name: 'Clear' }).tap()
  await expect(page.getByTestId('selection-bar')).toHaveCount(0)

  await rows.nth(7).locator('.review-body').tap()
  await expect(rows.nth(7)).toHaveAttribute('aria-current', 'true')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})

test('phone: keyboard reaches the active row box and then the selection bar', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  let reached = false
  for (let i = 0; i < 60 && !reached; i++) {
    await page.keyboard.press('Tab')
    reached = (await page.evaluate(() => document.activeElement?.getAttribute('aria-label'))) === 'Select line #1'
  }
  expect(reached).toBe(true)
  await page.keyboard.press('Space')
  await expect(page.getByTestId('selection-bar')).toContainText('1 selected')
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab')
    if (await page.evaluate(() => !!document.activeElement?.closest('[data-testid=selection-bar]'))) break
  }
  await expect(page.getByTestId('selection-bar').locator(':focus')).toHaveCount(1)
})
