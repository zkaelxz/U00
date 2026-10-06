import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Tick boxes on Review rows: tick, Shift-range, select all shown, the count,
// the selection bar, and that rows and their actions still work around them.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}') for i in range(50)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

const box = (page: import('@playwright/test').Page, n: number) => page.getByRole('checkbox', { name: `Select line #${n}`, exact: true })

test('tick, shift-range, select all shown, count and bar', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  const bar = page.getByTestId('selection-bar')
  await expect(bar).toHaveCount(0)

  await box(page, 3).check()
  await expect(box(page, 3)).toHaveAttribute('aria-checked', 'true')
  await expect(bar).toContainText('1 selected')

  await box(page, 6).click({ modifiers: ['Shift'] })
  await expect(bar).toContainText('4 selected')
  await expect(page.getByTestId('selection-count')).toHaveText('4 selected')

  await bar.getByRole('button', { name: 'Copy line numbers' }).click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('#3-#6')

  await box(page, 3).uncheck()
  await expect(bar).toContainText('3 selected')

  await page.getByRole('button', { name: 'Select all shown' }).click()
  await expect(bar).toContainText('40 selected')

  await page.getByRole('group', { name: 'Select lines' }).getByRole('button', { name: 'Clear' }).click()
  await expect(bar).toHaveCount(0)
})

test('the selection survives a page change; Esc clears it', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await box(page, 2).check()
  await page.getByRole('button', { name: 'Next page' }).first().click()
  await expect(page.getByTestId('selection-bar')).toContainText('1 selected')
  await page.getByRole('button', { name: 'Previous page' }).first().click()
  await expect(box(page, 2)).toBeChecked()
  await page.locator('.review-line[data-line-id]').first().focus()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('selection-bar')).toHaveCount(0)
})

test('Space on the focused tick box ticks it, and typing in the editor does not', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  const first = box(page, 1)
  await first.focus()
  await page.keyboard.press('Space')
  await expect(first).toBeChecked()
  await page.getByTestId('line-en').first().click()
  await page.getByLabel('Translation').fill('a b c')
  await expect(page.getByLabel('Translation')).toHaveValue('a b c')
  await expect(page.getByTestId('selection-bar')).toContainText('1 selected')
})

test('row clicks and row actions still work with boxes on', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const row = page.locator('[data-testid^="line-"][data-line-id]').nth(4)
  await row.locator('.review-zh').click()
  await expect(row).toHaveAttribute('aria-current', 'true')
  await row.getByTestId('line-en').click()
  await expect(row.getByLabel('Translation')).toBeVisible()
  await expect(page.getByTestId('selection-bar')).toHaveCount(0)
  await row.getByRole('button', { name: /More actions for line/ }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
})

test('desktop: the tick target is 32px', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const label = page.locator('.review-select').first()
  await expect(label).toBeAttached()
  const b = await label.boundingBox()
  expect(Math.round(b!.width)).toBe(32)
  expect(Math.round(b!.height)).toBe(32)
})

// Keyboard path: only the active row's controls are Tab stops (j/k moves the
// active row), so a ticked box leads to the selection bar in a few presses.
async function tabTo(page: import('@playwright/test').Page, name: string, max = 60) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press('Tab')
    if ((await page.evaluate(() => document.activeElement?.getAttribute('aria-label'))) === name) return
  }
  throw new Error(`Tab never reached "${name}" within ${max} presses`)
}

test('keyboard: Tab reaches the active row box, Space ticks it, the bar is next', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await tabTo(page, 'Select line #1')
  await page.keyboard.press('Space')
  const bar = page.getByTestId('selection-bar')
  await expect(bar).toContainText('1 selected')

  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab')
    if (await page.evaluate(() => !!document.activeElement?.closest('[data-testid=selection-bar]'))) break
  }
  await expect(bar.locator(':focus')).toHaveCount(1)
})

test('keyboard: j moves the active row and its box takes the Tab stop; Shift+Space ticks a range', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  await box(page, 1).check()
  await page.locator('.review-line[data-line-id]').first().focus()
  for (let i = 0; i < 3; i++) await page.keyboard.press('j')
  await expect(box(page, 4)).toHaveAttribute('tabindex', '0')
  await expect(box(page, 1)).toHaveAttribute('tabindex', '-1')
  await box(page, 4).focus()
  await page.keyboard.press('Shift+Space')
  await expect(page.getByTestId('selection-bar')).toContainText('4 selected')
})
