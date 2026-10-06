import { expect, test, type Page } from '@playwright/test'

// A flag's full note must stay reachable: on the chip's title, and expanded in
// full by clicking it (touch has no hover). Lines are mocked and every write is
// aborted, so the shared library is never touched.

const NOTE = 'Reading speed · 11.5 characters/second -- more than ~8.5/s is hard to read in time. Shorten the line or extend its time.'
const FULL = `Reading speed · ${NOTE.split(' · ')[1]}`

const line = (id: number, flagged: boolean) => ({
  id, idx: id - 1, start: id, end: id + 1, zh: `句${id}`, en: `Line ${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag: flagged ? 'reading_speed' : null,
  flag_note: flagged ? NOTE.split(' · ')[1] : null, dub_filename: null, lang: null,
})

async function open(page: Page) {
  const lines = [line(1, false), line(2, true), line(3, false)]
  await page.route('**/api/**', (route) => (route.request().method() === 'GET' ? route.continue() : route.abort()))
  await page.route('**/api/review/dramas/3/lines?*', (route) =>
    route.fulfill({ json: { lines, page: 1, page_size: 40, total: 3, flagged_count: 1, untranslated_count: 0 } }))
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('desktop: chip title holds the full note; click expands, Esc collapses', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await open(page)
  const row = page.locator('.review-line[data-line-id="2"]')
  const chip = row.getByRole('button', { name: /Reading speed/ })
  await expect(chip).toHaveAttribute('title', new RegExp('Shorten the line or extend its time'))
  await expect(chip).toHaveAttribute('aria-expanded', 'false')
  await chip.click()
  await expect(chip).toHaveAttribute('aria-expanded', 'true')
  await expect(row.getByRole('note')).toHaveText(FULL.replace(/^./, (c) => c))
  await chip.press('Escape')
  await expect(row.getByRole('note')).toHaveCount(0)
  await chip.click()
  await chip.click()
  await expect(row.getByRole('note')).toHaveCount(0)
})

test('phone: the active row’s flag line expands, wraps inside the screen, number stays on one line', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await open(page)
  const row = page.locator('.review-line[data-line-id="2"]')
  await row.locator('.review-zh').click()
  await row.getByRole('button', { name: /Flagged: Reading speed/ }).click()
  const note = row.getByRole('note')
  await expect(note).toBeVisible()
  await expect(note).toContainText('Shorten the line or extend its time')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const idx = await row.locator('.review-idx').boundingBox()
  const time = await row.locator('.review-time').boundingBox()
  expect(idx!.height).toBeLessThan(24)
  expect(time!.height).toBeLessThan(24)
})
