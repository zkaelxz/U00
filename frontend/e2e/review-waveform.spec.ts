import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

// The Review waveform: peaks come from a mocked route (no ffmpeg, no audio
// decoding), lines and the edge edits go to the real seeded API.

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
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 12)))
db.update_drama(3, audio_filename='e2e.wav', source_video_filename=None)
db.save_lines(3, [Line(idx=0, start=1.0, end=2.0, zh='一', en='One'),
                  Line(idx=1, start=3.0, end=4.0, zh='二', en='Two'),
                  Line(idx=2, start=5.0, end=6.0, zh='三', en='Three')])
`)
})

async function open(page: Page) {
  const peakCalls: string[] = []
  await page.route('**/api/media/dramas/3/peaks*', async (route) => {
    const u = new URL(route.request().url())
    peakCalls.push(u.search)
    const n = Number(u.searchParams.get('buckets'))
    await route.fulfill({
      json: { start: Number(u.searchParams.get('start')), end: Number(u.searchParams.get('end')), buckets: n, peaks: Array.from({ length: n }, (_, i) => (i % 7) * 30) },
    })
  })
  await page.goto('/#/drama/3/review')
  await expect(page.getByRole('group', { name: 'Waveform' })).toBeVisible()
  const ids: number[] = (await (await page.request.get('/api/review/dramas/3/lines?page=1&page_size=50&only=all')).json()).lines.map((l: { id: number }) => l.id)
  return { peakCalls, ids }
}

async function lineTimes(page: Page) {
  const body = await (await page.request.get('/api/review/dramas/3/lines?page=1&page_size=50&only=all')).json()
  return body.lines.map((l: { start: number; end: number }) => [l.start, l.end])
}

// The timeline maps the visible window across the box, so a time is a pixel.
async function xOf(page: Page, seconds: number) {
  const box = (await page.getByTestId('wave-box').boundingBox())!
  const [from, to] = (await page.getByTestId('wave-range').innerText()).split('–').map((t) => {
    const [m, s] = t.trim().split(':')
    return Number(m) * 60 + Number(s)
  })
  return { x: box.x + ((seconds - from) / (to - from)) * box.width, y: box.y + box.height / 2 }
}

test('draws neighbouring cues around the active line and asks for its window', async ({ page }) => {
  const { peakCalls, ids } = await open(page)
  await page.getByTestId(`line-${ids[1]}`).click()
  await expect(page.getByTestId('wave-cue-active')).toHaveCount(1)
  await expect(page.getByTestId('wave-cue')).toHaveCount(2)
  await expect(page.getByTestId('wave-handle-start')).toBeVisible()
  await expect.poll(() => peakCalls.length).toBeGreaterThan(0)
  expect(peakCalls[peakCalls.length - 1]).toMatch(/start=\d.*&end=\d.*&buckets=\d+/)
})

test('dragging an edge saves it, and stops at the next line instead of overlapping it', async ({ page }) => {
  const { ids } = await open(page)
  await page.getByTestId(`line-${ids[1]}`).click()
  const end = page.getByTestId('wave-handle-end')
  await expect(end).toBeVisible()

  // 4.0 -> 4.5: inside the gap, saved as dragged (to the pixel).
  let from = await xOf(page, 4)
  let to = await xOf(page, 4.5)
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move(to.x, to.y, { steps: 5 })
  await page.mouse.up()
  await expect.poll(async () => (await lineTimes(page))[1][1]).toBeGreaterThan(4.3)
  const [, e1] = (await lineTimes(page))[1]
  expect(e1).toBeLessThan(4.7)

  // Far past the next line's start (5.0): held at 5.0.
  from = await xOf(page, e1)
  to = await xOf(page, 5.8)
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move(to.x, to.y, { steps: 5 })
  await page.mouse.up()
  await expect.poll(async () => (await lineTimes(page))[1][1]).toBe(5)
  expect((await lineTimes(page))[2]).toEqual([5, 6])
})

test('arrow keys nudge a focused edge; start never reaches end', async ({ page }) => {
  const { ids } = await open(page)
  await page.getByTestId(`line-${ids[1]}`).click()
  const start = page.getByTestId('wave-handle-start')
  await start.focus()
  await page.keyboard.press('ArrowRight')
  await expect.poll(async () => (await lineTimes(page))[1][0]).toBe(3.05)
  for (let i = 0; i < 4; i += 1) await page.keyboard.press('Shift+ArrowRight')
  // 3.05 + 4 x 0.5 would pass the end (4.0): it stops 0.1 s short.
  await expect.poll(async () => (await lineTimes(page))[1][0]).toBe(3.9)
})

test('clicking the waveform seeks the player', async ({ page }) => {
  const { ids } = await open(page)
  await page.getByTestId(`line-${ids[1]}`).click()
  const at = await xOf(page, 3.5)
  await page.mouse.click(at.x, at.y)
  await expect(page.getByTestId('player-time')).toContainText('0:03.')
})

test('zoom changes the visible window', async ({ page }) => {
  await open(page)
  const before = await page.getByTestId('wave-range').innerText()
  await page.getByRole('button', { name: 'Zoom in' }).click()
  await expect(page.getByTestId('wave-range')).not.toHaveText(before)
})

test('can be hidden', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: 'Hide waveform' }).click()
  await expect(page.getByRole('group', { name: 'Waveform' })).toHaveCount(0)
})
