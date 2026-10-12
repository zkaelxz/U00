import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from './fixtures'
import { clearDrama3Lines } from './reviewResultsSeed'

// Waveform labels never wrap or clip; a stretch with no line is drawn dashed and
// says speech was detected, with a one-click "Transcribe this gap" and a Gaps
// chip that jumps to it; a timing-uncertain line offers its two actions.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const GAP = { start: 9, end: 11.5, seconds: 2.5, pieces: 1, part: 1, parts: 1, after_line_id: null, before_line_id: null, speech: true }

test.beforeEach(() => {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db, os, wave, struct, math
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 14)))
db.update_drama(3, audio_filename='e2e.wav', source_video_filename=None)
db.save_lines(3, [Line(idx=0, start=1.0, end=1.15, zh='一', en='One'),
                  Line(idx=1, start=3.0, end=4.0, zh='二', en='Two'),
                  Line(idx=2, start=5.0, end=6.0, zh='三', en='Three', flag='timing_uncertain', flag_note='Split evenly by length')])
`], { cwd: repoRoot })
})
test.afterAll(() => clearDrama3Lines())

async function open(page: import('@playwright/test').Page) {
  await page.route('**/api/media/dramas/3/peaks*', (route) => {
    const n = Number(new URL(route.request().url()).searchParams.get('buckets'))
    return route.fulfill({ json: { start: 0, end: 20, buckets: n, peaks: Array(n).fill(100) } })
  })
  await page.route('**/api/transcribe/dramas/3/gaps', (route) => route.fulfill({ json: { gaps: [GAP], speech_checked: true } }))
  await page.goto('/#/drama/3/review')
  await expect(page.getByTestId('wave-box')).toBeVisible()
}

test('a narrow region shows no label (and never a wrapped one); the full text is in its tooltip', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await open(page)
  const cues = page.locator('[data-testid="wave-cue"], [data-testid="wave-cue-active"]')
  await expect(cues).toHaveCount(3)
  for (const cue of await cues.all()) {
    const w = (await cue.boundingBox())!.width
    const label = cue.locator('.review-wave-cue-label')
    if (w < 30) await expect(label).toHaveCount(0)
    else {
      await expect(label).toHaveCSS('white-space', 'nowrap')
      const h = (await label.boundingBox())!.height
      expect(h).toBeLessThan(24)
    }
    await expect(cue).toHaveAttribute('title', /^Line #\d+: /)
  }
  // The playhead is drawn above the label layer.
  const z = await page.evaluate(() => ({
    head: Number(getComputedStyle(document.querySelector('.review-wave-head')!).zIndex),
    cue: Number(getComputedStyle(document.querySelector('.review-wave-cue')!).zIndex),
  }))
  expect(z.head).toBeGreaterThan(z.cue)
})

test('the gap is dashed, says speech was detected, and Transcribe this gap adds lines for it', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  const bodies: unknown[] = []
  await page.route('**/api/transcribe/dramas/3/gaps/add-lines', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { new_line_ids: [9001] } })
  })
  await open(page)
  const gap = page.getByTestId('wave-gap')
  await expect(gap).toHaveCSS('border-left-style', 'dashed')
  // Too narrow at this zoom for the sentence: hidden, with the full text in the tooltip.
  await expect(page.getByTestId('wave-gap-text')).toHaveCount(0)
  await expect(gap).toHaveAttribute('title', /^No line here: speech detected/)
  await page.getByRole('button', { name: 'Gaps 1' }).click()
  await page.getByRole('button', { name: 'Zoom in' }).click()
  await page.getByRole('button', { name: 'Zoom in' }).click()
  await expect(page.getByTestId('wave-gap-text')).toHaveText('No line here: speech detected')
  await page.getByTestId('wave-gap-button').click()
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0]).toMatchObject({ start: 9, end: 11.5, after_line_id: null })
})

test('the Gaps chip sits next to Flagged and jumps to the next gap', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await open(page)
  const chip = page.getByRole('button', { name: 'Gaps 1' })
  await expect(chip).toBeVisible()
  await chip.click()
  await expect(page.getByTestId('wave-range')).toContainText('0:0')
  const range = await page.getByTestId('wave-range').textContent()
  expect(range).toMatch(/0:0\d\.\d+ – 0:\d\d/)
  await expect(page.getByRole('status').filter({ hasText: /^Gap:/ })).toBeVisible()
})

test('a timing-uncertain line offers Snap to speech, Check timing and the Whisper hint', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  let runs = 0
  await page.route('**/api/timing-check/dramas/3', (route) =>
    route.fulfill({ json: { job_id: '', status: 'idle', progress: 0, message: '', result: null, last_check: null, suggestions: [] } }))
  await page.route('**/api/timing-check/dramas/3/run', (route) => {
    runs += 1
    return route.fulfill({ json: { job_id: 'timingchk_3' } })
  })
  await open(page)
  const row = page.locator('.review-line').nth(2)
  const actions = row.getByTestId('timing-uncertain-actions')
  await expect(actions).toBeVisible()
  await expect(actions).toContainText('Re-transcribe with Whisper large-v3-turbo for word timings')
  await expect(actions.getByRole('link', { name: 'open Media' })).toHaveAttribute('href', '#/drama/3/source')
  await actions.getByRole('button', { name: 'Check timing' }).click()
  await expect.poll(() => runs).toBe(1)
})
