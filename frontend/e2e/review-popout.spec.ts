import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Pop out: the Review player moves (the same element, not a copy) into a
// floating window and comes back when that window closes. Headless Chromium
// can't open a real Document Picture-in-Picture window, so the API is stubbed
// with an iframe that behaves like one (own document, close() fires pagehide).

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

test.beforeEach(() => {
  python(`
import os, wave, struct, math
from core import Line
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 6)))
db.update_drama(3, audio_filename='e2e.wav', source_video_filename=None)
db.save_lines(3, [Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there')])
`)
})

const STUB = () => {
  // Chromium defines the real API as a read-only property, so plain assignment is ignored.
  Object.defineProperty(window, 'documentPictureInPicture', { configurable: true, value: {
    requestWindow: async () => {
      const frame = document.createElement('iframe')
      frame.id = 'pip-stub'
      frame.style.cssText = 'position:fixed;right:0;bottom:0;width:420px;height:300px;z-index:99'
      document.body.appendChild(frame)
      const win = frame.contentWindow as Window
      win.close = () => {
        frame.remove()
        win.dispatchEvent(new Event('pagehide'))
      }
      return win
    },
  } })
}

test('the control is hidden where the API is missing', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'documentPictureInPicture', { configurable: true, value: undefined })
  })
  await page.goto('/#/drama/3/review')
  await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Pop out' })).toHaveCount(0)
})

test('pop out moves the player into the floating window and Return puts it back', async ({ page }) => {
  await page.addInitScript(STUB)
  await page.goto('/#/drama/3/review')
  const popOut = page.getByRole('button', { name: 'Pop out' })
  await expect(popOut).toBeVisible()
  await expect(page.locator('.review-player-panel')).toHaveCount(1)
  await page.locator('.review-player-panel').evaluate((el) => ((el as HTMLElement).dataset.mark = 'same'))

  await popOut.click()
  const frame = page.frameLocator('#pip-stub')
  await expect(frame.locator('.review-player-panel[data-mark="same"]')).toBeVisible()
  await expect(page.locator('.review-player-panel')).toHaveCount(0)
  // Controls inside the floating window still work.
  await frame.getByLabel('Jump to time').fill('0:03')
  await frame.getByRole('button', { name: 'Jump' }).click()
  await expect(page.getByTestId('player-time')).toContainText('0:03.00')

  // Closing the floating window puts the same panel back.
  await page.evaluate(() => (document.getElementById('pip-stub') as HTMLIFrameElement).contentWindow?.close())
  await expect(page.locator('.review-player-panel[data-mark="same"]')).toBeVisible()
  await expect(page.locator('#pip-stub')).toHaveCount(0)
  await expect(popOut).toBeVisible()
})

test('the pop-out caption grows with the window and follows the Subtitle size control', async ({ page }) => {
  await page.setViewportSize({ width: 2000, height: 1050 })
  await page.addInitScript(STUB)
  await page.goto('/#/drama/3/review')
  await page.getByRole('button', { name: 'Pop out' }).click()
  const frame = page.frameLocator('#pip-stub')
  const caption = frame.getByTestId('player-caption')
  const size = () => caption.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
  await expect.poll(size).toBeLessThan(20)

  await page.evaluate(() => {
    const f = document.getElementById('pip-stub') as HTMLIFrameElement
    f.style.width = '1900px'
    f.style.height = '900px'
  })
  await expect.poll(size).toBeGreaterThanOrEqual(40)
  const normal = await size()
  await frame.getByLabel('Subtitle size').selectOption('larger')
  await expect.poll(size).toBeGreaterThan(normal)
  // The video still fills the window above the caption and controls.
  await expect(frame.locator('.review-player-panel')).toBeVisible()
})
