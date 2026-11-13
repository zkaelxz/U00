import { expect, test, type Locator, type Page } from '@playwright/test'

import { expectNoSidewaysScroll } from './mockHelpers'

// The Simple title view at phone width. Title 1 is the seeded audio drama.

const progress = {
  drama_id: 1, stage_index: 1, stage: 'translate', line_count: 10, untranslated_count: 10,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [],
}

const main = 'Translate 10 lines'
// On a phone these three sit behind the More menu.
const more = ['Add media', 'Redo transcription', 'Glossary']

test.beforeEach(async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress }))
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

// Height is the repo's touch-target check; the short "Full" label is under 44px wide but sits flush against its neighbour.
async function expectTall(loc: Locator, what: string) {
  await expect(loc).toBeVisible()
  const box = await loc.boundingBox()
  expect(box?.height ?? 0, `${what} is too short to tap`).toBeGreaterThanOrEqual(44)
}

// In Full on a phone the switch lives in the Title actions sheet, which keeps the sticky strip one row.
async function chooseSimple(page: Page) {
  await page.getByRole('button', { name: 'Title actions' }).tap()
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
}

test('Simple fits a phone: switch and the step actions visible, tall enough to tap, no sideways scroll', async ({ page }) => {
  await page.goto('/#/drama/1')
  await chooseSimple(page)
  await expect(page.getByRole('link', { name: 'Translate 10 lines', exact: true })).toBeVisible()

  // The radios are visually hidden; the visible control is their label.
  for (const name of ['Simple', 'Full']) {
    await expectTall(page.locator('label').filter({ hasText: new RegExp(`^${name}$`) }).first(), `${name} switch`)
  }
  await expectTall(page.getByRole('link', { name: main, exact: true }), main)
  await expectTall(page.locator('summary', { hasText: 'More' }), 'More')
  await page.locator('summary', { hasText: 'More' }).tap()
  for (const name of more) await expectTall(page.getByRole('link', { name, exact: true }), name)
  const tabs = page.getByRole('list', { name: 'Steps' }).getByRole('link')
  await expect(tabs).toHaveCount(5)
  for (let n = 0; n < 5; n++) await expectTall(tabs.nth(n), `step tab ${n + 1}`)
  await expectNoSidewaysScroll(page)
})

test('choosing Simple then reloading keeps Simple', async ({ page }) => {
  await page.goto('/#/drama/1')
  await chooseSimple(page)
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('link', { name: 'Translate 10 lines', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await expect(page.getByRole('radio', { name: 'Simple', exact: true })).toBeChecked()
})

test('with lines the phone shows the bar, the steps and long lines wrapped, with no sideways scroll', async ({ page }) => {
  const long = '这是一句很长很长的中文台词，用来确认在窄屏幕上整句都会换行显示，而不是被截断成省略号。'.repeat(2)
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: { ...progress, untranslated_count: 4 } }))
  await page.route('**/api/review/dramas/1/lines?*', (route) => route.fulfill({
    json: { total: 10, page: 1, page_size: 8, lines: [{ id: 1, idx: 1, zh: long, en: 'A long line that has to wrap on a phone and still be readable in full.', start: 1, end: 2, speaker: null, reviewed: false }] },
  }))
  await page.goto('/#/drama/1')
  await chooseSimple(page)
  await expect(page.getByRole('progressbar', { name: 'Lines translated' })).toBeVisible()
  const orig = page.locator('.title-line-orig').first()
  await expect(orig).toBeVisible()
  const fits = await orig.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
  expect(fits, 'the original line wraps inside its box').toBe(true)
  await expectTall(page.getByRole('link', { name: 'Open all 10 lines' }), 'Open all lines')
  await expectNoSidewaysScroll(page)
})

test('the step tabs and buttons on a stage page are tall enough to tap and do not scroll sideways', async ({ page }) => {
  await page.goto('/#/drama/1')
  await chooseSimple(page)
  await page.goto('/#/drama/1/translate')
  const links = page.getByRole('list', { name: 'Steps' }).getByRole('link')
  await expect(links).toHaveCount(5)
  for (let n = 0; n < 5; n++) await expectTall(links.nth(n), `step tab ${n + 1}`)
  await expectTall(page.getByRole('link', { name: main, exact: true }), main)
  await page.locator('summary', { hasText: 'More' }).tap()
  for (const name of more) await expectTall(page.getByRole('link', { name, exact: true }), name)
  await expectNoSidewaysScroll(page)
})
