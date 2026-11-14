import { expect, test, type Locator, type Page } from '@playwright/test'

import { seedCheckLines } from './compareSelectedHelpers'
import { expectNoSidewaysScroll } from './mockHelpers'
import { clearLines } from './resegmentLlmMocks'

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

// Title 3's seeded lines would open later phone specs on its Review page.
test.describe(() => {
  test.afterAll(() => clearLines())

  test('the check queue on a phone is one card at a time, with Skip for now, wrapped reasons and no sideways scroll', async ({ page }) => {
    seedCheckLines()
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    await page.goto('/#/drama/3/review')
    const cards = page.locator('.review-line')
    const position = page.getByTestId('queue-position')
    const bar = page.getByRole('toolbar', { name: 'Line actions' })
    await expect(position).toHaveText('1 of 3 to look at')
    await expect(cards).toHaveCount(1)
    await expect(cards.locator('.review-idx')).toHaveText('#24')
    await expect(page.getByText('Next: line 7, Whisper was unsure · line 12, reading speed')).toBeVisible()
    await expect(cards.getByRole('link', { name: 'Open glossary' })).toBeVisible()

    await bar.getByRole('button', { name: 'Skip for now' }).tap()
    await expect(position).toHaveText('2 of 3 to look at')
    await expect(cards.locator('.review-idx')).toHaveText('#7')
    const reason = cards.getByTestId('queue-reason')
    await expect(reason).toHaveText('Whisper was unsure of this line (50% sure)')

    // The first card starts near the top: measured 211px at 390 and 239px at 320, with the steps folded into one line.
    const cardTopAtMost = { 390: 225, 320: 255 }
    for (const width of [390, 320] as const) {
      await page.setViewportSize({ width, height: 844 })
      await page.evaluate(() => window.scrollTo(0, 0))
      await expect(page.locator('.review-toolbar.is-collapsed')).toHaveCount(0)
      const top = await cards.evaluate((el) => el.getBoundingClientRect().top + window.scrollY)
      expect(top, `the first card's top at ${width}px`).toBeLessThanOrEqual(cardTopAtMost[width])
      expect(await reason.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), `the reason wraps at ${width}px`).toBe(true)
      for (const name of ['Skip for now', 'Looks right, next']) await expectTall(bar.getByRole('button', { name }), `${name} at ${width}px`)
      // The radios are visually hidden; the visible control is their label.
      await expectTall(page.locator('label').filter({ hasText: /^Need a look 3$/ }), `Need a look at ${width}px`)
      await expectNoSidewaysScroll(page)
    }

    // One line stands for the step tiles; it opens them as they are on the title page, with the title's controls, and closes again.
    const fold = page.getByRole('button', { name: 'Step 4 of 5 · Check' })
    await expectTall(fold, 'the steps line')
    await expect(fold).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('list', { name: 'Steps' })).toBeHidden()
    await fold.tap()
    await expect(fold).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByRole('list', { name: 'Steps' }).getByRole('link')).toHaveCount(5)
    await expect(page.getByRole('button', { name: 'Check the 3 lines', exact: true })).toBeVisible()
    await expect(page.locator('summary', { hasText: 'More' })).toBeVisible()
    await expect(page.getByRole('radio', { name: 'Simple', exact: true })).toBeChecked()
    await expect(page.getByRole('button', { name: 'Open folder' })).toBeVisible()
    await expectNoSidewaysScroll(page)
    await page.setViewportSize({ width: 390, height: 844 })
    await expectNoSidewaysScroll(page)
    await fold.tap()
    await expect(page.getByRole('list', { name: 'Steps' })).toBeHidden()

    // Looks right, next checks #7 and opens #12, which is now the last of two.
    await bar.getByRole('button', { name: 'Looks right, next' }).tap()
    await expect(position).toHaveText('2 of 2 to look at')
    await expect(cards.locator('.review-idx')).toHaveText('#12')
    await expect(page.getByTestId('checked-undo')).toContainText('Line #7 marked checked.')
    await expectNoSidewaysScroll(page)

    // Skip for now on the last card goes back to the start of the queue.
    await bar.getByRole('button', { name: 'Skip for now' }).tap()
    await expect(position).toHaveText('1 of 2 to look at')

    await page.getByTestId('checked-undo').getByRole('button', { name: 'Undo' }).tap()
    await expect(position).toHaveText('2 of 3 to look at')
    await expect(cards.locator('.review-idx')).toHaveText('#7')
  })
})
