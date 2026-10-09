import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { seedLines, tickBox } from './compareSelectedHelpers'
import { mockRetranscribeLines } from './retranscribeLinesMocks'

// Review: tick lines and press "Re-transcribe selected"; one job runs on exactly
// those ids, the proposals list old text next to new with a checkbox each, and
// "Apply selected" sends only the ticked ones, exactly as shown. The waveform's
// "Transcribe this gap" adds the lines for a gap and starts the same run on them.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const action = (page: import('@playwright/test').Page) =>
  page.getByTestId('selection-bar').getByRole('button', { name: 'Re-transcribe selected' })

test('tick lines, re-transcribe them, choose which new text to apply', async ({ page }) => {
  const ids = seedLines(6)
  const seen = await mockRetranscribeLines(page, [
    { line_id: ids[1], number: 2, base_zh: '第1句', proposed_zh: '第一句话', had_english: true },
    { line_id: ids[2], number: 3, base_zh: '第2句', proposed_zh: '第二句话', had_english: true },
    { line_id: ids[4], number: 5, base_zh: '', proposed_zh: '新听到的', had_english: false },
  ], { hold: true, failures: [{ line_id: ids[3], number: 4, reason: 'empty' }] })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(6)
  for (const n of [2, 3, 4, 5]) await tickBox(page, n).check()
  await action(page).click()

  const panel = page.getByTestId('retranscribe-lines')
  await expect(panel).toBeVisible()
  // One start, on exactly the ticked ids; progress and Cancel while it runs.
  await expect.poll(() => seen.starts).toEqual([{ line_ids: [ids[1], ids[2], ids[3], ids[4]] }])
  await expect(panel.getByTestId('retranscribe-lines-progress')).toBeVisible()
  await panel.getByRole('button', { name: 'Cancel' }).click()
  await expect.poll(() => seen.cancels).toBe(1)
  seen.release()

  const rows = panel.getByTestId('retranscribe-lines-row')
  await expect(rows).toHaveCount(3)
  await expect(rows.first()).toContainText('第1句')
  await expect(rows.first()).toContainText('第一句话')
  await expect(rows.first()).toContainText('clears its English')
  await expect(rows.nth(2)).toContainText('(empty)')
  await expect(panel.getByTestId('retranscribe-lines-failed')).toContainText('#4 (no speech heard)')
  expect(seen.applies).toEqual([]) // nothing is applied by looking

  await rows.nth(1).getByRole('checkbox').uncheck()
  await panel.getByRole('button', { name: 'Apply selected (2)' }).click()
  await expect(panel.getByTestId('retranscribe-lines-note')).toContainText('Replaced the source text of 2 lines')
  await expect(panel.getByTestId('retranscribe-lines-note')).toContainText('Cleared the English on 1')
  expect(seen.applies).toEqual([{
    job_id: 'retranscribe_3',
    items: [
      { line_id: ids[1], expected_zh: '第1句', expected_proposed: '第一句话' },
      { line_id: ids[4], expected_zh: '', expected_proposed: '新听到的' },
    ],
  }])
  await expect(rows).toHaveCount(1)
})

test('the action appears with the selection bar and goes with it', async ({ page }) => {
  seedLines(6)
  await mockRetranscribeLines(page, [])
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(6)
  await tickBox(page, 1).check()
  await expect(action(page)).toBeEnabled()
  await page.getByTestId('selection-bar').getByRole('button', { name: 'Clear' }).click()
  await expect(page.getByTestId('selection-bar')).toHaveCount(0)
})

test('"Transcribe this gap" adds the lines for the gap and re-transcribes them', async ({ page }) => {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
import os, wave, struct, math
from core import Line
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 12)))
db.update_drama(3, audio_filename='e2e.wav', source_video_filename=None)
db.save_lines(3, [Line(idx=0, start=1.0, end=2.0, zh='一', en='One'), Line(idx=1, start=3.0, end=4.0, zh='二', en='Two'), Line(idx=2, start=13.0, end=14.0, zh='三', en='Three')])
`], { cwd: repoRoot })
  const seen = await mockRetranscribeLines(page, [
    { line_id: 901, number: 3, base_zh: '', proposed_zh: '缺失的话', had_english: false },
  ])
  await page.route('**/api/media/dramas/3/peaks*', (route) => {
    const u = new URL(route.request().url())
    const n = Number(u.searchParams.get('buckets'))
    return route.fulfill({ json: { start: Number(u.searchParams.get('start')), end: Number(u.searchParams.get('end')), buckets: n, peaks: Array.from({ length: n }, (_, i) => (i % 7) * 30) } })
  })
  const lines = (await (await page.request.get('/api/review/dramas/3/lines?page=1&page_size=50&only=all')).json()).lines as { id: number }[]
  await page.route('**/api/transcribe/dramas/3/gaps', (route) =>
    route.fulfill({ json: { gaps: [{ start: 4, end: 13, seconds: 9, pieces: 1, after_line_id: lines[1].id, before_line_id: lines[2].id, speech: true }], speech_checked: true } }))
  const adds: unknown[] = []
  await page.route('**/api/transcribe/dramas/3/gaps/add-lines', (route) => {
    adds.push(route.request().postDataJSON())
    return route.fulfill({ json: { new_line_ids: [901], line_ids: [1, 2, 901, 3], split: 'single', history_id: 5, lines_fingerprint: 'f' } })
  })
  await page.goto('/#/drama/3/review')
  const button = page.getByTestId('wave-gap-button')
  await expect(button).toBeVisible()
  await expect(button).toHaveAccessibleName(/0:04\.00 – 0:13\.00 \(9 s, speech heard\)/)
  await button.click()

  await expect.poll(() => adds).toEqual([{ expected_line_ids: lines.map((l) => l.id), start: 4, end: 13, after_line_id: lines[1].id }])
  const panel = page.getByTestId('retranscribe-lines')
  await expect(panel).toBeVisible()
  await expect.poll(() => seen.starts).toEqual([{ line_ids: [901] }])
  const row = panel.getByTestId('retranscribe-lines-row')
  await expect(row).toContainText('缺失的话')
  await row.getByRole('checkbox').check()
  await panel.getByRole('button', { name: 'Apply selected (1)' }).click()
  expect(seen.applies).toEqual([{ job_id: 'retranscribe_3', items: [{ line_id: 901, expected_zh: '', expected_proposed: '缺失的话' }] }])
})
