import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Phone, audio-only title: no blank caption card between cues, and the four filter chips fit
// without being cut off.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db, os, wave
from core import Line
db.configure_library_dir(${JSON.stringify(libraryDir)})
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b'\\0\\0' * 8000 * 6)
db.update_drama(3, audio_filename='e2e.wav', source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i + 1, end=i + 2, zh='一', en='Line %d' % i) for i in range(6)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

test('phone: no blank caption card and the filter chips are not clipped', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.getByRole('button', { name: 'Hide player' })).toBeVisible()
  await expect(page.getByTestId('player-caption')).toBeHidden()
  const chips = page.locator('.review-chips')
  expect(await chips.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('phone: the floating player opens above the line bar, so Prev and Next stay tappable', async ({ page }) => {
  // Phones have no always-on-top window, so Pop out floats the player in the page.
  await page.addInitScript(() => Object.defineProperty(window, 'documentPictureInPicture', { configurable: true, value: undefined }))
  await page.goto('/#/drama/3/review')
  await page.getByRole('button', { name: 'Pop out' }).tap()
  const box = (await page.getByRole('group', { name: 'Floating player' }).boundingBox())!
  const bar = (await page.getByRole('toolbar', { name: 'Line actions' }).boundingBox())!
  expect(box.y + box.height).toBeLessThanOrEqual(bar.y)
  await page.getByRole('toolbar', { name: 'Line actions' }).getByRole('button', { name: /Next/ }).tap()
})

test('phone: when the filter chips scroll, the row fades at the edge they continue past', async ({ page }) => {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db
from core import Line
db.configure_library_dir(${JSON.stringify(libraryDir)})
db.save_lines(3, [Line(idx=i, start=i + 1, end=i + 1.5, zh='一', en='Line %d' % i) for i in range(120)])
`], { cwd: repoRoot })
  await page.setViewportSize({ width: 360, height: 780 })
  await page.goto('/#/drama/3/review')
  const chips = page.locator('.review-chips')
  await expect(chips.getByText('All 120')).toBeVisible()
  const fade = () => chips.evaluate((el) => parseFloat(getComputedStyle(el).getPropertyValue('--chips-fade')) || 0)
  expect(await chips.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
  expect(await fade()).toBeGreaterThan(0)
  await chips.evaluate((el) => el.scrollTo({ left: el.scrollWidth }))
  await expect.poll(fade).toBe(0)
})
