import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, type Page } from '@playwright/test'

// Shared by review-undo.spec.ts and review-undo.mobile.spec.ts: three real lines
// (plus an optional over-long one) written straight into the throwaway library
// the test server uses; structure edits and undo then go through the real API.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

export const SENTENCE = '我今天早上很早就起床了然后去公园跑步。'

export function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

export function seedLines(withLong = false) {
  python(`
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=3.0, zh='再见朋友', en='Bye friend'),
    Line(idx=2, start=3.0, end=4.5, zh='谢谢', en='Thanks, friend'),
${withLong ? `    Line(idx=3, start=5.0, end=35.0, zh=${JSON.stringify(SENTENCE.repeat(3))}, en=''),` : ''}
])
`)
}

/** Changes a line's English straight in the library (another device editing), keeping every id. */
export function editEnglishBehindTheUi(zh: string, en: string) {
  python(`
lines = db.load_line_objects(3)
for ln in lines:
    if ln.zh == ${JSON.stringify(zh)}:
        ln.en = ${JSON.stringify(en)}
db.save_lines(3, lines, fields=('en',))
`)
}

export const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

export async function openReview(page: Page, count: number) {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(count)
}

/** Splits line 2 ("再见朋友") after two characters from its actions sheet. */
export async function splitSecondLine(page: Page) {
  await rows(page).nth(1).getByRole('button', { name: 'More actions for line 2' }).click()
  await page.getByRole('dialog', { name: 'Line #2' }).getByRole('button', { name: 'Split line…' }).click()
  const split = page.getByRole('dialog', { name: 'Split line #2' })
  await split.getByLabel('Break after (chars)').fill('2')
  await split.getByRole('button', { name: 'Split line' }).click()
  await expect(rows(page)).toHaveCount(4)
}

export const zhTexts = (page: Page) => rows(page).locator('.review-zh').allTextContents()
