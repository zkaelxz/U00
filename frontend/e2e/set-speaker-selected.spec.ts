import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { tickBox } from './compareSelectedHelpers'

// Review: tick lines far down the page, press "Set speaker…" in the selection
// bar; the panel shows where the person is looking and gives every ticked line
// the chosen speaker.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function seedSpokenLines(count: number): void {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}', speaker='Anok' if i % 2 else 'Yamizuki Fuyu') for i in range(${count})])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('tick lines low on the page, Set speaker opens in view and changes them all', async ({ page }) => {
  seedSpokenLines(50)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (const n of [35, 36, 37, 38, 39]) {
    await tickBox(page, n).scrollIntoViewIfNeeded()
    await tickBox(page, n).check()
  }
  const bar = page.getByTestId('selection-bar')
  await expect(bar).toContainText('5 selected')
  await bar.getByRole('button', { name: 'Set speaker…' }).click()

  const panel = page.getByTestId('set-speaker')
  await expect(panel).toBeVisible()
  await expect(panel).toBeInViewport()
  // The field is a text box until the title's speakers load, then a list.
  await expect(panel.getByRole('combobox', { name: 'Speaker for 5 lines' })).toBeVisible()
  await panel.getByRole('combobox', { name: 'Speaker for 5 lines' }).selectOption('Anok')
  await panel.getByRole('button', { name: 'Set speaker', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('Set the speaker of 5 lines to Anok.')
  for (const n of [35, 36, 37, 38, 39]) {
    const row = page.locator('.review-line').filter({ has: tickBox(page, n) })
    await expect(row).toContainText('Anok')
    await expect(row).not.toContainText('Yamizuki Fuyu')
  }
  await expect(page.locator('.review-line').filter({ has: tickBox(page, 33) })).toContainText('Yamizuki Fuyu')

  // Pressed again after scrolling back down to the lines, the open panel comes back into view.
  await tickBox(page, 39).scrollIntoViewIfNeeded()
  await expect(panel).not.toBeInViewport()
  await bar.getByRole('button', { name: 'Set speaker…' }).click()
  await expect(panel).toBeInViewport()

  // After Clear, ticking a line low on the page brings the panel back without jumping up to it.
  await bar.getByRole('button', { name: 'Clear' }).click()
  await expect(panel).toHaveCount(0)
  await tickBox(page, 39).scrollIntoViewIfNeeded()
  await tickBox(page, 39).check()
  await expect(panel).toBeAttached()
  await expect(tickBox(page, 39)).toBeInViewport()
})
