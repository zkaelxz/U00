import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Phones: the waveform starts folded away and opens from a button without
// widening the page.

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
db.save_lines(3, [Line(idx=0, start=1.0, end=2.0, zh='一', en='One')])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

test('folded by default, opens on demand, no sideways scroll', async ({ page }) => {
  await page.route('**/api/media/dramas/3/peaks*', (route) => {
    const n = Number(new URL(route.request().url()).searchParams.get('buckets'))
    return route.fulfill({ json: { start: 0, end: 20, buckets: n, peaks: Array(n).fill(100) } })
  })
  await page.goto('/#/drama/3/review')
  await expect(page.getByRole('button', { name: 'Show waveform' })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Waveform' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Show waveform' }).click()
  await expect(page.getByRole('group', { name: 'Waveform' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
