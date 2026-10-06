import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Playback speed (remembered) and the timing hotkeys, against a
// seeded drama with a 6 s tone and three lines at 0-1.5, 2-3.5 and 4-5.5 s.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
import os, wave, struct, math
from core import Line
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 6)))
db.update_drama(3, audio_filename='e2e.wav', source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}') for i in range(3)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

const rate = (page: import('@playwright/test').Page) => page.locator('audio').evaluate((el) => (el as HTMLAudioElement).playbackRate)

test('the speed applies to the player and is remembered after a reload', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const speed = page.getByLabel(/^Speed/)
  await expect(speed).toHaveValue('1')
  await speed.selectOption('0.5')
  expect(await rate(page)).toBe(0.5)

  await page.reload()
  await expect(page.getByLabel(/^Speed/)).toHaveValue('0.5')
  await expect.poll(() => rate(page)).toBe(0.5)
})

test('hotkeys set and nudge the selected line through a normal edit', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await expect(rows).toHaveCount(3)
  const second = rows.nth(1)
  await rows.first().focus()
  await page.keyboard.press('j')
  await expect(second).toHaveAttribute('aria-current', 'true')

  // Playhead at 2.4 s: S sets the start there (the previous line ends at 1.5).
  await page.getByLabel('Jump to time').fill('2.4')
  await page.getByRole('button', { name: 'Jump' }).click()
  await second.focus()
  await page.keyboard.press('s')
  await expect(second).toContainText('0:02.40')

  await page.keyboard.press('x')
  await expect(second).toContainText('0:02.50')
  await page.keyboard.press('Shift+z')
  await expect(second).toContainText('0:02.00')

  // The end moves in 0.5 s steps up to where the next line starts, not past it.
  await page.keyboard.press('Shift+v')
  await expect(page.getByText(/#2 end 0:04\.00/).first()).toBeVisible()
  await page.keyboard.press('v')
  await expect(page.getByText('End would overlap line #3.').first()).toBeVisible()

  // Saved on the server, not just on screen.
  await page.reload()
  await expect(rows.nth(1)).toContainText('0:02.00')
})

test('the new keys are in the shortcut list', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)
  await page.keyboard.press('?')
  await expect(page.getByText('Nudge the start 0.1 s earlier / later (Shift: 0.5 s)')).toBeVisible()
})
