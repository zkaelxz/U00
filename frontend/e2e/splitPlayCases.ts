import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

// "Play around the cut" in the split dialog, shared by the desktop and phone
// projects. The line runs 2s-6s of a real 8s tone; the cut time is typed in
// so the expected window (cut -1.5s .. cut +1.5s) does not depend on the estimate.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

const seed = (withMedia: boolean) => python(`
import os, wave, struct, math
from core import Line
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 8)))
db.update_drama(3, audio_filename=${withMedia ? "'e2e.wav'" : 'None'}, source_video_filename=None)
db.save_lines(3, [Line(idx=0, start=2.0, end=6.0, zh='谢谢朋友们', en='Thanks friends')])
`)

async function openSplit(page: Page, loop = false) {
  await page.goto('/#/drama/3/review')
  // The sheet covers the toggle, so Loop is set before the dialog opens.
  if (loop) await page.getByRole('switch', { name: 'Loop line' }).click()
  await page.getByRole('button', { name: 'More actions for line 1' }).click()
  await page.getByRole('dialog', { name: 'Line #1' }).getByRole('button', { name: 'Split line…' }).click()
  const split = page.getByRole('dialog', { name: 'Split line #1' })
  await split.getByLabel('Split at (s)').fill('4')
  return split
}

const audio = (page: Page) => page.locator('audio, video').first()
const state = (page: Page) => audio(page).evaluate((el: HTMLMediaElement) => ({ t: el.currentTime, paused: el.paused }))

export function splitPlayTests() {
  test.afterAll(() => python('db.save_lines(3, [])'))

  test('seeks to the cut minus the lead-in, plays, and stops at the end of the window', async ({ page }) => {
    seed(true)
    const split = await openSplit(page)
    await split.getByRole('button', { name: '▶ Play around the cut' }).click()
    await expect.poll(async () => (await state(page)).paused).toBe(false)
    expect((await state(page)).t).toBeGreaterThanOrEqual(2.5)
    await expect.poll(async () => (await state(page)).paused, { timeout: 10_000 }).toBe(true)
    // Stops at 5.5s (the cut plus the lead-in), not at the line end (6s).
    const { t } = await state(page)
    expect(t).toBeGreaterThanOrEqual(5.5)
    expect(t).toBeLessThan(6)
  })

  test('with Loop on, it wraps back to the start of the window instead of stopping', async ({ page }) => {
    seed(true)
    const split = await openSplit(page, true)
    await split.getByRole('button', { name: '▶ Play around the cut' }).click()
    // Sampled in the page: the window above 5s lasts well under Playwright's poll back-off.
    const samples = await audio(page).evaluate(
      (el: HTMLMediaElement) =>
        new Promise<{ t: number; paused: boolean }[]>((resolve) => {
          const out: { t: number; paused: boolean }[] = []
          const id = setInterval(() => out.push({ t: el.currentTime, paused: el.paused }), 25)
          setTimeout(() => (clearInterval(id), resolve(out)), 5000)
        }),
    )
    const peak = samples.findIndex((x) => x.t >= 5.4)
    expect(peak).toBeGreaterThanOrEqual(0)
    expect(samples.slice(peak).some((x) => x.t < 3.5)).toBe(true)
    expect(samples.every((x) => !x.paused)).toBe(true)
  })

  test('without media the button is absent and the dialog still works', async ({ page }) => {
    seed(false)
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    const split = await openSplit(page)
    await expect(split.getByRole('button', { name: '▶ Play around the cut' })).toHaveCount(0)
    await expect(split.getByRole('button', { name: 'Split line' })).toBeEnabled()
    expect(errors).toEqual([])
  })
}
