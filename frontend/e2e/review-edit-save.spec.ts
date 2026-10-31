import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// The real seeded API (drama 3 of the throwaway e2e library): typing in a line then leaving
// the box by Tab or a click saves it, Ctrl+Enter saves like Enter, and an open Replace panel
// is not covered by the sticky toolbar.

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
db.save_lines(3, [Line(idx=i, start=i, end=i + 1, zh='一', en='Line %d' % i) for i in range(6)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

async function storedEn(page: import('@playwright/test').Page, n: number) {
  const res = await page.request.get('/api/review/dramas/3/lines?page=1&page_size=50&only=all')
  const body = await res.json()
  const lines = Array.isArray(body) ? body : body.lines
  return lines[n].en as string
}

test('Tab out of an edited translation saves it', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await page.getByTestId('line-en').first().click()
  const box = page.getByLabel('Translation')
  await box.fill('Typed then Tab')
  await box.press('Tab')
  await expect.poll(() => storedEn(page, 0)).toBe('Typed then Tab')
})

test('clicking elsewhere saves, but Cancel still warns instead of saving', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await page.getByTestId('line-en').first().click()
  await page.getByLabel('Translation').fill('Clicked away')
  await page.getByRole('heading', { level: 1 }).first().click()
  await expect.poll(() => storedEn(page, 0)).toBe('Clicked away')

  await page.getByLabel('Translation').fill('Not saved')
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Unsaved changes' })).toBeVisible()
  expect(await storedEn(page, 0)).toBe('Clicked away')
})

test('Ctrl+Enter saves and moves on like Enter', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await page.getByTestId('line-en').first().click()
  await page.getByLabel('Translation').fill('By Ctrl+Enter')
  await page.getByLabel('Translation').press('Control+Enter')
  await expect.poll(() => storedEn(page, 0)).toBe('By Ctrl+Enter')
  await expect(page.getByLabel('Translation')).toHaveValue('Line 1')
})

test('the sticky toolbar does not cover the Replace panel after Preview', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/drama/3/review')
  await page.getByRole('button', { name: /^Replace/ }).click()
  const panel = page.getByRole('region', { name: 'Find and replace' })
  await panel.getByLabel('Find').fill('Line')
  await panel.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('fr-matches')).toBeVisible()
  await panel.scrollIntoViewIfNeeded()
  const bar = (await page.locator('.review-toolbar').boundingBox())!
  const labels = (await panel.getByLabel('Find').boundingBox())!
  expect(labels.y >= bar.y + bar.height || labels.y + labels.height <= bar.y).toBe(true)
})
