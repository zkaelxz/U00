import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

// On a phone the speed control is on screen (no keyboard needed),
// a 44px target, and the strip still fits without sideways scrolling.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
import os, wave, struct, math
from core import Line
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 6)))
db.update_drama(3, audio_filename='e2e.wav', source_video_filename=None)
db.save_lines(3, [Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello')])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

test('phone: the speed select is a 44px control and changes the speed', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const speed = page.getByLabel(/^Speed/)
  await expect(speed).toBeVisible()
  expect(await hitHeight(speed)).toBeGreaterThanOrEqual(44)
  await speed.selectOption('0.75')
  expect(await page.locator('audio').evaluate((el) => (el as HTMLAudioElement).playbackRate)).toBe(0.75)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
