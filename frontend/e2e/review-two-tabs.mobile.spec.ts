import { expect, test, type Page } from '@playwright/test'

import { clearDrama3Lines, python } from './reviewResultsSeed'

// Phone: a save refused because the line changed elsewhere shows its notice inside 390 px with a 44 px
// Reload, against the real API (review-two-tabs.spec.ts covers the desktop flows).

test.beforeEach(() => python(`
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=3.0, zh='谢谢', en='Thanks, friend'),
])
`))
test.afterEach(() => clearDrama3Lines())
const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

// The line changed on another device while this phone had it open (the change is written straight to the
// throwaway library, like a save from elsewhere).
function changeElsewhere() {
  python(`
import contextlib
with contextlib.closing(db.get_conn()) as c:
    c.execute("UPDATE lines SET en = 'Changed elsewhere' WHERE drama_id = 3 AND idx = 1")
    c.commit()
`)
}

test('phone: a line changed elsewhere is refused on save, and the notice and Reload fit the screen', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(2)
  const row = rows(page).nth(1)
  await row.getByTestId('line-en').tap()
  await page.getByRole('button', { name: 'Edit #2' }).tap()
  await row.getByLabel('Translation').fill('Thanks from the phone')
  changeElsewhere()
  await page.getByRole('toolbar', { name: 'Line actions' }).getByRole('button', { name: 'Save', exact: true }).tap()

  const conflict = row.getByTestId('line-conflict')
  await expect(conflict).toContainText('changed elsewhere')
  const reload = conflict.getByRole('button', { name: 'Reload' })
  const rb = (await reload.boundingBox())!
  expect(rb.height).toBeGreaterThanOrEqual(44)
  const cb = (await conflict.boundingBox())!
  expect(cb.x).toBeGreaterThanOrEqual(0)
  expect(cb.x + cb.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await reload.tap()
  await expect(conflict).toHaveCount(0)
  await expect(row.getByLabel('Translation')).toHaveValue('Changed elsewhere')
})
