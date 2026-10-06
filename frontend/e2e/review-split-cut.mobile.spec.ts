import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Phone project: the split dialog's cut marker, 44px nudge buttons and the
// suggested translation cut, with the same request payload as before.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=4.5, zh='谢谢朋友们', en='Thanks, dear friends'),
])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

test('phone: nudge buttons move the cut, 44px targets, payload unchanged', async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/api\/restructure\//.test(r.url())) bodies.push(r.postDataJSON())
  })
  await page.goto('/#/drama/3/review')
  await page.getByRole('button', { name: 'More actions for line 2' }).click()
  await page.getByRole('dialog', { name: 'Line #2' }).getByRole('button', { name: 'Split line…' }).click()
  const split = page.getByRole('dialog', { name: 'Split line #2' })

  const pieces = async (n: number) => {
    const box = split.locator('.split-cut-text').nth(n)
    return [(await box.locator('.split-cut-a').allTextContents()).join(''), (await box.locator('.split-cut-b').allTextContents()).join('')]
  }
  // 5 chars: the default cut is the middle (round(2.5) = 3).
  await expect(split.getByLabel('Break after (chars)')).toHaveValue('3')
  await split.getByRole('button', { name: 'Source: cut 1 char earlier' }).click()
  await expect(split.getByLabel('Break after (chars)')).toHaveValue('2')
  expect(await pieces(0)).toEqual(['谢谢', '朋友们'])
  for (const name of ['Source: cut 1 char earlier', 'Source: cut 1 char later', 'Source: previous punctuation or space', 'Source: next punctuation or space']) {
    const box = await split.getByRole('button', { name }).boundingBox()
    expect(box?.height).toBeGreaterThanOrEqual(44)
    expect(box?.width).toBeGreaterThanOrEqual(44)
  }

  await split.getByLabel('Also split the translation').click()
  // 2/5 of 20 chars = 8, snapped to the nearest word start: 'Thanks, ' | 'dear friends'.
  expect(await pieces(1)).toEqual(['Thanks, ', 'dear friends'])
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)

  await split.getByRole('button', { name: 'Split line' }).click()
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0]).toMatchObject({ at_char: 2, en_at_char: 8 })
})
