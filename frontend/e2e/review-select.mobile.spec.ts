import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { phoneBarAction } from './compareSelectedHelpers'

// Phone: the tick box is a 44px target, sits in the row's own gutter and does
// not take over taps on the row.

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

test('phone: 44px tick box, range, select all, bar, and taps still reach the row', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await expect(rows).toHaveCount(40)

  const target = page.locator('.review-select').first()
  const b = await target.boundingBox()
  expect(Math.round(b!.width)).toBeGreaterThanOrEqual(44)
  expect(Math.round(b!.height)).toBeGreaterThanOrEqual(44)

  const heightBefore = (await rows.nth(1).boundingBox())!.height
  await box(page, 2).tap()
  await expect(page.getByTestId('selection-bar')).toContainText('1 selected')
  expect((await rows.nth(1).boundingBox())!.height).toBe(heightBefore)

  await page.getByRole('button', { name: 'Select all shown' }).tap()
  await expect(page.getByTestId('selection-bar')).toContainText('40 selected')
  await page.getByTestId('selection-bar').getByRole('button', { name: 'Clear' }).tap()
  await expect(page.getByTestId('selection-bar')).toHaveCount(0)

  await rows.nth(7).locator('.review-body').tap()
  await expect(rows.nth(7)).toHaveAttribute('aria-current', 'true')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})

test('phone: keyboard reaches the active row box and then the selection bar', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  let reached = false
  for (let i = 0; i < 60 && !reached; i++) {
    await page.keyboard.press('Tab')
    reached = (await page.evaluate(() => document.activeElement?.getAttribute('aria-label'))) === 'Select line #1'
  }
  expect(reached).toBe(true)
  await page.keyboard.press('Space')
  await expect(page.getByTestId('selection-bar')).toContainText('1 selected')
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab')
    if (await page.evaluate(() => !!document.activeElement?.closest('[data-testid=selection-bar]'))) break
  }
  await expect(page.getByTestId('selection-bar').locator(':focus')).toHaveCount(1)
})

test('phone: the selection bar stays one row and lists its actions in a sheet', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (const n of [1, 2, 3]) await box(page, n).tap()
  const bar = page.getByTestId('selection-bar')
  await expect(bar).toContainText('3 selected')
  // One row of 44px buttons plus the bar's padding: the ticked lines keep the screen.
  expect((await bar.boundingBox())!.height).toBeLessThanOrEqual(64)

  await bar.getByRole('button', { name: 'Actions…' }).tap()
  const sheet = page.getByRole('dialog', { name: '3 selected' })
  await expect(sheet.getByRole('list', { name: 'Actions for the selected lines' }).getByRole('button')).toHaveText(['Copy line numbers', 'Compare transcription…', 'Re-time with Qwen3 aligner…', 'Set speaker…', 'Re-transcribe selected'])
  await sheet.getByRole('button', { name: 'Set speaker…' }).tap()
  await expect(sheet).toBeHidden()
  const panel = page.getByTestId('set-speaker')
  await expect(panel).toBeInViewport()
  for (const name of ['Set new speaker', 'Close']) expect((await panel.getByRole('button', { name, exact: true }).boundingBox())!.height).toBeGreaterThanOrEqual(44)
})

test("phone: Copy line numbers from the sheet copies, also through the page's fallback copy", async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  // Some phone browsers refuse the clipboard API, and the fallback selects text in the page behind the sheet.
  await page.addInitScript(() => {
    navigator.clipboard.writeText = () => Promise.reject(new Error('refused'))
  })
  await page.setViewportSize({ width: 360, height: 780 })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (const n of [1, 2, 3]) await box(page, n).tap()
  await (await phoneBarAction(page, 'Copy line numbers')).tap()
  await expect(page.getByText('Copied 3 line numbers.')).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('#1-#3')
})

test("phone, Simple view: the selection bar sits above the check queue's taller line bar", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
  // A small phone: the queue bar's Looks right, next takes two lines, and the page is longer than the screen, so the selection bar sticks.
  await page.setViewportSize({ width: 320, height: 568 })
  // With audio the queue's bar has its Play button too.
  const code = `import db, os, wave
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
with wave.open(os.path.join(db.drama_dir(3), 'e2e.wav'), 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b'\\0\\0' * 8000 * 6)
db.update_drama(3, audio_filename='e2e.wav')
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}', flag='uncertain' if i % 3 == 0 else '') for i in range(40)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
  await page.goto('/#/drama/3/review')
  // The queue's cards have no tick box: a line ticked under All stays ticked when the queue is shown again.
  await page.locator('label').filter({ hasText: /^All 40$/ }).tap()
  await page.locator('.review-line input[type=checkbox]').first().tap()
  await page.locator('label').filter({ hasText: /^Need a look 14$/ }).tap()
  await expect(page.getByTestId('queue-position')).toHaveText('1 of 14 to look at')
  const bar = page.getByTestId('selection-bar')
  await expect(bar).toContainText('1 selected')
  const sel = (await bar.boundingBox())!
  const lineBar = (await page.getByRole('toolbar', { name: 'Line actions' }).boundingBox())!
  // Taller than the plain line bar's 56px, which is what the selection bar would clear without the queue bar's own height.
  expect(lineBar.height).toBeGreaterThan(56)
  expect(sel.y + sel.height).toBeLessThanOrEqual(lineBar.y + 0.5)
  // Stuck right above it, not further up the page where any bar would clear it.
  expect(sel.y + sel.height).toBeGreaterThan(lineBar.y - 2)
})

test('phone: the speaker name is a 44px button whose list stays on screen, and picking a speaker saves it', async ({ page }) => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}', speaker='Anvil' if i % 2 else 'Yamizuki Fuyu') for i in range(6)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
  for (const width of [390, 320] as const) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/#/drama/3/review')
    await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(6)
    const name = page.getByRole('button', { name: /Speaker of line 2: Anvil/ })
    expect((await name.boundingBox())!.height, `the name button at ${width}px`).toBeGreaterThanOrEqual(44)
    await name.tap()
    const list = page.getByRole('group', { name: 'Choose a speaker' })
    const at = (await list.boundingBox())!
    expect(at.x, `the list's left edge at ${width}px`).toBeGreaterThanOrEqual(0)
    expect(at.x + at.width, `the list's right edge at ${width}px`).toBeLessThanOrEqual(width)
    for (const b of await list.getByRole('button').all()) expect((await b.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `sideways scroll at ${width}px`).toBeLessThanOrEqual(0)
    await page.keyboard.press('Escape')
  }
  await page.getByRole('button', { name: /Speaker of line 2: Anvil/ }).tap()
  await page.getByRole('group', { name: 'Choose a speaker' }).getByRole('button', { name: 'Yamizuki Fuyu' }).tap()
  await expect(page.getByRole('button', { name: /Speaker of line 2: Yamizuki Fuyu/ })).toBeVisible()
})
