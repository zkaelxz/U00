import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, type Page } from '@playwright/test'

// The real seeded API (drama 3 of the throwaway e2e library): one line flagged
// reading_speed (11.8 characters/second: over Normal, under Relaxed), one
// flagged for overlap, one clean.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

export function seedReadingSpeed() {
  python(`
from core import Line
db.update_drama(3, audio_filename=None, reading_speed_mode='normal')
db.save_lines(3, [
    Line(idx=0, start=0.0, end=5.0, zh='一', en='word ' * 11 + 'end', flag='reading_speed', flag_note='11.8 characters/second'),
    Line(idx=1, start=5.0, end=9.0, zh='二', en='Second line', flag='timing_overlap', flag_note='overlaps'),
    Line(idx=2, start=9.0, end=12.0, zh='三', en='Third line'),
])
`)
}

export function resetReadingSpeed() {
  python("db.update_drama(3, reading_speed_mode='normal')")
}

export async function runReadingSpeedScenario(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)
  await expect(page.getByTestId('line-counts')).toContainText('2 flagged')

  const fold = page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: /^Flag lines for review$/ }) })
  if ((await fold.getAttribute('open')) === null) await fold.locator(':scope > summary').click()
  await expect(fold).toHaveAttribute('open', '')

  // The setting is saved on the title and reloads with the page.
  const select = fold.getByLabel('Reading speed check')
  await expect(select).toHaveValue('normal')
  await expect(fold.getByText(/Off never flags/)).toBeVisible()
  await select.selectOption('relaxed')
  await expect(select).toHaveValue('relaxed')
  await page.reload()
  await expect(fold.getByLabel('Reading speed check')).toHaveValue('relaxed')

  // A mode change alone keeps the old flag; Re-check clears it (11.8/s is fine when Relaxed).
  await expect(page.getByTestId('line-counts')).toContainText('2 flagged')
  await fold.getByRole('button', { name: 'Re-check with the current setting' }).click()
  await fold.getByRole('button', { name: 'Confirm' }).click()
  await expect(fold.getByTestId('flag-result-clear')).toContainText('Cleared 1 reading-speed flag, flagged 0 lines again')
  await expect(page.getByTestId('line-counts')).toContainText('1 flagged')

  // Back to Normal: flagging again finds it; the plain Clear removes only that flag.
  await fold.getByLabel('Reading speed check').selectOption('normal')
  await fold.getByRole('button', { name: 'Flag dense lines' }).click()
  await expect(fold.getByTestId('flag-result-dense')).toHaveText('Flagged 1 line.')
  await expect(page.getByTestId('line-counts')).toContainText('2 flagged')
  await fold.getByRole('button', { name: 'Clear reading-speed flags' }).click()
  await fold.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByTestId('line-counts')).toContainText('2 flagged')
  await fold.getByRole('button', { name: 'Clear reading-speed flags' }).click()
  await fold.getByRole('button', { name: 'Confirm' }).click()
  await expect(fold.getByTestId('flag-result-clear')).toContainText('Cleared 1 reading-speed flag.')
  await expect(page.getByTestId('line-counts')).toContainText('1 flagged')

  // Other flags are untouched, and the snapshot is in the line history.
  const flags = await page.evaluate(async () => (await (await fetch('/api/review/dramas/3/lines?page=1&page_size=40')).json()).lines.map((l: { flag: string | null }) => l.flag))
  expect(flags).toEqual([null, 'timing_overlap', null])
  const history = await page.evaluate(async () => (await (await fetch('/api/review/dramas/3/history')).json()).map((h: { label: string }) => h.label))
  expect(history).toContain('before clearing reading-speed flags')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}
