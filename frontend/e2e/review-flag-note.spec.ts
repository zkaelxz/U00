import { expect, test, type Page } from '@playwright/test'

// On desktop a flag's whole note is on screen, wrapped, without hovering or
// clicking; on a phone the active row's one-line chip expands on tap. Lines are mocked and every write is
// aborted, so the shared library is never touched.

const NOTE = 'Reading speed · 11.5 characters/second -- more than ~8.5/s is hard to read in time. Shorten the line or extend its time. Splitting it at a pause also works, and so does giving the cue a longer window on screen so the viewer can finish reading it before the next one.'
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

test('desktop: the whole note is visible and wraps without shifting the row', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await open(page)
  const row = page.locator('.review-line[data-line-id="2"]')
  const note = row.getByTestId('line-flag')
  await expect(note).toContainText('Shorten the line or extend its time')
  await expect(note).toHaveAttribute('title', new RegExp('Shorten the line or extend its time'))
  await expect(row.getByRole('button', { name: /Reading speed/ })).toHaveCount(0)
  const clipped = await note.evaluate((el) => ({
    ellipsis: getComputedStyle(el).textOverflow === 'ellipsis',
    whole: el.scrollWidth <= el.clientWidth && el.scrollHeight <= el.clientHeight,
  }))
  expect(clipped).toEqual({ ellipsis: false, whole: true })
  const lineHeight = await note.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight))
  const box = (await note.boundingBox())!
  expect(box.height).toBeGreaterThan(lineHeight * 1.5)
  const idx = (await row.locator('.review-idx').boundingBox())!
  const time = (await row.locator('.review-time').boundingBox())!
  expect(idx.height).toBeLessThan(24)
  expect(time.height).toBeLessThan(24)
  const meta = (await row.locator('.review-line-meta').boundingBox())!
  expect(box.y).toBeGreaterThanOrEqual(meta.y + meta.height - 1)
  const zh = (await row.locator('.review-zh').boundingBox())!
  const en = (await row.locator('.review-en').boundingBox())!
  expect(zh.y).toBeGreaterThanOrEqual(box.y + box.height - 1)
  expect(en.y).toBeGreaterThanOrEqual(box.y + box.height - 1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
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
