import { expect, test, type Locator, type Page } from '@playwright/test'

import { clearCheckLines, GLOSSARY_SENTENCE, joinSeries, leaveSeries, mockPeaks, seedCheckLines } from './compareSelectedHelpers'
import { expectNoSidewaysScroll } from './mockHelpers'
import { clearLines } from './resegmentLlmMocks'

// The Simple title view at phone width. Title 1 is the seeded audio drama.

const progress = {
  drama_id: 1, stage_index: 1, stage: 'translate', line_count: 10, untranslated_count: 10,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [],
}

const main = 'Translate 10 lines'
// On a phone these three sit in the panel the steps button opens.
const more = ['Add media', 'Redo transcription', 'Glossary']
// Its name starts with the ▸ marker its CSS draws, so the pattern is not anchored.
const stepsButton = (page: Page) => page.getByRole('button', { name: /Step \d of 5 · / })

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

// In Full on a phone the switch lives in the Title actions sheet, which keeps the sticky strip one row. In Simple it is
// behind the steps button, so the click is not followed by check()'s look at a radio that is no longer on screen.
async function chooseSimple(page: Page) {
  await page.getByRole('button', { name: 'Title actions' }).tap()
  await page.getByRole('radio', { name: 'Simple', exact: true }).click({ force: true })
  await expect(stepsButton(page)).toBeVisible()
}

test('Simple fits a phone: one steps button opens the steps and the title controls, the main button under the header, no sideways scroll', async ({ page }) => {
  await page.goto('/#/drama/1')
  await chooseSimple(page)
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    const header = page.locator('.workspace-header')
    const button = stepsButton(page)
    await expect(button).toHaveText('Step 3 of 5 · Translate')
    await expectTall(button, `the steps button at ${width}px`)
    await expect(header.getByRole('button', { name: 'Step 3 of 5 · Translate' })).toHaveCount(1)
    await expect(button).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('list', { name: 'Steps' })).toBeHidden()

    // The main button spans the page directly under the header.
    const mainLink = page.getByRole('link', { name: main, exact: true })
    await expectTall(mainLink, main)
    const h = (await header.boundingBox())!
    const m = (await mainLink.boundingBox())!
    expect(m.y, `the main button is under the header at ${width}px`).toBeGreaterThanOrEqual(h.y + h.height)
    expect(m.y - (h.y + h.height), `nothing sits between the header and the main button at ${width}px`).toBeLessThan(24)
    expect(Math.abs(m.width - h.width), `the main button is full width at ${width}px`).toBeLessThanOrEqual(1)

    await button.tap()
    await expect(button).toHaveAttribute('aria-expanded', 'true')
    const panel = page.locator(`[id="${await button.getAttribute('aria-controls')}"]`)
    await expect(panel).toBeVisible()
    const tabs = panel.getByRole('list', { name: 'Steps' }).getByRole('link')
    await expect(tabs).toHaveText(['Done · Add', 'Done · Transcribe, 10 lines', 'Now · Translate, 10 of 10 left', /^Next · Check(, .+)?$/, 'Export'])
    for (let n = 0; n < 5; n++) await expectTall(tabs.nth(n), `step ${n + 1} at ${width}px`)
    for (const name of more) await expectTall(panel.getByRole('link', { name, exact: true }), `${name} at ${width}px`)
    await expectTall(panel.getByRole('button', { name: 'Search', exact: true }), `Search at ${width}px`)
    // The radios are visually hidden; the visible control is their label.
    for (const name of ['Simple', 'Full']) {
      await expectTall(panel.locator('label').filter({ hasText: new RegExp(`^${name}$`) }).first(), `${name} switch at ${width}px`)
    }
    await expectTall(panel.getByRole('button', { name: 'Open folder' }), `Open folder at ${width}px`)
    // In this order: the steps, the three links, Search, the switch, Open folder, then the source link or the way to add one.
    const order = [
      panel.getByRole('list', { name: 'Steps' }), ...more.map((name) => panel.getByRole('link', { name, exact: true })),
      panel.getByRole('button', { name: 'Search', exact: true }), panel.getByRole('group', { name: 'Title view' }), panel.getByRole('button', { name: 'Open folder' }), panel.locator('.ws-source, .ws-source-add'),
    ]
    const handles = await Promise.all(order.map((l) => l.elementHandle()))
    for (let n = 1; n < handles.length; n++) {
      const follows = await page.evaluate(([a, b]) => !!(a!.compareDocumentPosition(b!) & Node.DOCUMENT_POSITION_FOLLOWING), [handles[n - 1], handles[n]])
      expect(follows, `panel item ${n + 1} follows item ${n}`).toBe(true)
    }
    await expect(page.locator('.title-top-more')).toHaveCount(0)
    await expectNoSidewaysScroll(page)
    await button.tap()
    await expect(page.getByRole('list', { name: 'Steps' })).toBeHidden()
  }
})

test('choosing Simple then reloading keeps Simple', async ({ page }) => {
  await page.goto('/#/drama/1')
  await chooseSimple(page)
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('link', { name: 'Translate 10 lines', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  // The switch is in the panel the steps button opens.
  await stepsButton(page).tap()
  await expect(page.getByRole('radio', { name: 'Simple', exact: true })).toBeChecked()
})

test('with lines the phone shows the lines themselves, long lines wrapped, with no sideways scroll', async ({ page }) => {
  const long = '这是一句很长很长的中文台词，用来确认在窄屏幕上整句都会换行显示，而不是被截断成省略号。'.repeat(2)
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: { ...progress, untranslated_count: 4 } }))
  const line = {
    id: 1, idx: 0, zh: long, en: 'A long line that has to wrap on a phone and still be readable in full.', start: 1, end: 2, speaker: null, speaker_manual: false,
    sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null,
  }
  await page.route('**/api/review/dramas/1/lines?*', (route) => route.fulfill({
    json: { lines: [line], page: 1, page_size: 40, total: 1, flagged_count: 0, untranslated_count: 0, unreviewed_count: 1, shaky_count: 0, risk_count: 0 },
  }))
  await page.goto('/#/drama/1')
  await chooseSimple(page)
  await expect(page.getByRole('progressbar', { name: 'Lines translated' })).toHaveCount(0)
  const orig = page.locator('.review-line .review-zh').first()
  await expect(orig).toBeVisible()
  const fits = await orig.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
  expect(fits, 'the original line wraps inside its box').toBe(true)
  // The view switch the lines open under replaces the preview's "Open all lines".
  await expectTall(page.getByRole('group', { name: 'Lines to show' }).locator('label').filter({ hasText: /^All/ }), 'All')
  await expectNoSidewaysScroll(page)
})

test('the step links and buttons on a stage page are tall enough to tap and do not scroll sideways', async ({ page }) => {
  await page.goto('/#/drama/1')
  await chooseSimple(page)
  await page.goto('/#/drama/1/translate')
  await expectTall(page.getByRole('link', { name: main, exact: true }), main)
  // The open stage's step names the button.
  await expect(stepsButton(page)).toHaveText('Step 3 of 5 · Translate')
  await page.goto('/#/drama/1/export')
  await expect(stepsButton(page)).toHaveText('Step 5 of 5 · Export')
  await stepsButton(page).tap()
  const links = page.getByRole('list', { name: 'Steps' }).getByRole('link')
  await expect(links).toHaveCount(5)
  for (let n = 0; n < 5; n++) await expectTall(links.nth(n), `step link ${n + 1}`)
  for (const name of more) await expectTall(page.getByRole('link', { name, exact: true }), name)
  await expectNoSidewaysScroll(page)
})

// The first card starts near the top: measured 255px at 390 and 283px at 320 for a one-line name, with the steps folded
// into one button on its own row under the name.
const cardTopAtMost = { 390: 269, 320: 299 }

// The top of the fixed bars at the bottom (the app's and the queue's).
const barTop = (page: Page) => page.evaluate(() => Math.min(window.innerHeight, ...[...document.querySelectorAll('body *')].filter((el) => {
  const css = getComputedStyle(el)
  const r = el.getBoundingClientRect()
  return css.position === 'fixed' && css.visibility !== 'hidden' && r.height > 0 && r.top > window.innerHeight / 2
}).map((el) => el.getBoundingClientRect().top)))

// The fixed bars at the bottom must leave the end of the page reachable above them.
async function expectEndClearOfBars(page: Page, last: Locator, what: string) {
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  const bottom = await last.evaluate((el) => el.getBoundingClientRect().bottom)
  expect(bottom, `${what} ends above the bottom bar`).toBeLessThanOrEqual(await barTop(page) + 0.5)
}

// Each element's top is below the one before.
async function expectOrder(items: [string, Locator][], width: number) {
  let last = -Infinity
  for (const [what, loc] of items) {
    const box = await loc.boundingBox()
    expect(box, `${what} at ${width}px`).toBeTruthy()
    expect(box!.y, `${what} comes next at ${width}px`).toBeGreaterThan(last)
    last = box!.y
  }
}

// Title 3's seeded lines would open later phone specs on its Review page.
test.describe(() => {
  test.afterAll(() => {
    clearLines()
    leaveSeries()
  })

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
    await expect(page.getByText('Next: line 7, Whisper was unsure · line 12, too fast to read', { exact: true })).toBeVisible()
    // The card: Why in a plain sentence, the English under its label in a box, the buttons under it, and a green
    // Looks right, next beside Skip for now. Tapping the box edits, so the bar has no Edit button.
    await expect(cards.getByTestId('queue-reason')).toHaveText(`Why: ${GLOSSARY_SENTENCE}`)
    await expect(cards.locator('.review-queue-label')).toHaveText('English')
    expect(await cards.getByTestId('line-en').evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe('solid')
    await expect(cards.getByRole('link', { name: 'Open glossary' })).toBeVisible()
    for (const target of [cards.getByRole('button', { name: 'See the lines around it' }), cards.getByRole('link', { name: 'Open glossary' }),
      cards.getByRole('button', { name: 'Improve translation' }), cards.getByTestId('line-en')]) await expectTall(target, (await target.textContent()) ?? '')
    await expect(bar.getByRole('button')).toHaveText(['Skip for now', 'Looks right, next'])
    const fill = await bar.getByRole('button', { name: 'Looks right, next' }).evaluate((el) => {
      const probe = document.createElement('span')
      probe.style.color = 'var(--ok)'
      document.body.append(probe)
      const ok = getComputedStyle(probe).color
      probe.remove()
      return getComputedStyle(el).backgroundColor === ok
    })
    expect(fill, 'Looks right, next is the green button').toBe(true)
    await cards.getByTestId('line-en').tap()
    await expect(cards.getByRole('textbox', { name: 'Translation' })).toBeVisible()
    await bar.getByRole('button', { name: 'Cancel' }).tap()
    await expect(cards.getByTestId('line-en')).toBeVisible()
    await expectNoSidewaysScroll(page)

    await bar.getByRole('button', { name: 'Skip for now' }).tap()
    await expect(position).toHaveText('2 of 3 to look at')
    await expect(cards.locator('.review-idx')).toHaveText('#7')
    const reason = cards.getByTestId('queue-reason')
    await expect(reason).toHaveText('Why: Whisper was unsure of this line (50% sure)')
    await expectTall(cards.getByRole('button', { name: 'Re-transcribe…' }), 'Re-transcribe…')

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

    // One button stands for the steps; it opens them as they are on the title page, with the title's controls, and closes again.
    const fold = page.getByRole('button', { name: 'Step 4 of 5 · Check' })
    await expectTall(fold, 'the steps line')
    await expect(fold).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('list', { name: 'Steps' })).toBeHidden()
    await fold.tap()
    await expect(fold).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByRole('list', { name: 'Steps' }).getByRole('link')).toHaveCount(5)
    await expect(page.getByRole('button', { name: 'Check the 3 lines', exact: true })).toBeVisible()
    for (const name of ['Add media', 'Redo transcription', 'Glossary']) await expectTall(page.getByRole('link', { name, exact: true }), name)
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

  test('on an audio title the player and the tools fold away, and the card starts where it does without audio', async ({ page }) => {
    seedCheckLines({ audio: true })
    await mockPeaks(page)
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    await page.goto('/#/drama/3/review')
    const cards = page.locator('.review-line')
    await expect(cards.locator('.review-idx')).toHaveText('#24')
    await expect(cards.locator('.review-select')).toBeHidden()
    const player = page.getByRole('group', { name: 'Player' })
    await expect(player).toBeHidden()
    const barButtons = page.getByRole('toolbar', { name: 'Line actions' }).getByRole('button')
    for (const width of [390, 320] as const) {
      await page.setViewportSize({ width, height: 844 })
      await page.evaluate(() => window.scrollTo(0, 0))
      await expect(page.locator('.review-toolbar.is-collapsed')).toHaveCount(0)
      const top = await cards.evaluate((el) => el.getBoundingClientRect().top + window.scrollY)
      expect(top, `the first card's top at ${width}px`).toBeLessThanOrEqual(cardTopAtMost[width])
      // With Play in the bar, Looks right, next wraps onto two lines at 320px; the three stay one height.
      await expect(barButtons).toHaveText(['▶ #24', 'Skip for now', 'Looks right, next'])
      const heights = await barButtons.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().height)))
      expect(new Set(heights).size, `the bar's buttons are one height at ${width}px: ${heights.join(', ')}`).toBe(1)
      expect(heights[0], `the bar's buttons are tall enough to tap at ${width}px`).toBeGreaterThanOrEqual(44)
      await expectNoSidewaysScroll(page)
    }
    await page.setViewportSize({ width: 390, height: 844 })

    // The tools are behind one button, with no This line tab on a phone.
    const tools = page.getByRole('button', { name: 'Checks and tools' })
    await expectTall(tools, 'Checks and tools')
    await expect(tools).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('tablist', { name: 'Review tools' })).toBeHidden()
    await tools.tap()
    await expect(tools).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByRole('tablist', { name: 'Review tools' }).getByRole('tab')).toHaveText(['Look for problems', 'Change many lines', 'Finish'])
    await expectNoSidewaysScroll(page)

    // Player is not under the card any more: it is with the title's other controls in the panel the steps button opens, and
    // it opens the player above the card and shows it.
    await expect(page.locator('.review-editor button', { hasText: /^Player/ })).toHaveCount(0)
    // A page scrolled past the collapse point hides the header, steps button and all, until it is back near the top.
    await page.evaluate(() => window.scrollTo(0, 0))
    await stepsButton(page).tap()
    const toggle = page.locator('.steps-fold-controls').getByRole('button', { name: 'Player', exact: true })
    await expectTall(toggle, 'Player')
    await toggle.tap()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(player).toBeInViewport()
    await expectNoSidewaysScroll(page)
    await page.setViewportSize({ width: 320, height: 844 })
    await expectNoSidewaysScroll(page)
  })

  test('Search in the steps panel shows All and opens the lines\' search box with the cursor in it', async ({ page }) => {
    seedCheckLines()
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    await page.goto('/#/drama/3/review')
    await expect(page.locator('.review-line .review-idx')).toHaveText('#24')
    const box = page.getByRole('searchbox', { name: 'Search lines' })
    await expect(box).toHaveCount(0)
    await stepsButton(page).tap()
    await page.locator('.steps-fold-controls').getByRole('button', { name: 'Search', exact: true }).tap()
    await expect(page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ })).toBeChecked()
    await expect(box).toBeFocused()
    // The panel closed with the press, so the box is not under it.
    await expect(stepsButton(page)).toHaveAttribute('aria-expanded', 'false')
    await expectNoSidewaysScroll(page)
  })

  test('the title page on the Check step: the card first, then the footer and the tools, all clear of the bottom bar', async ({ page }) => {
    seedCheckLines({ audio: true })
    await mockPeaks(page)
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    await page.goto('/#/drama/3')
    const cards = page.locator('.review-line')
    await expect(cards.locator('.review-idx')).toHaveText('#24')
    await expect(page).toHaveURL(/#\/drama\/3$/)
    const main = page.getByRole('button', { name: 'Check the 3 lines', exact: true })
    // On the Check step the main button waits in the steps panel, so the card comes first, as on Review.
    await expect(main).toBeHidden()
    const tools = page.getByRole('button', { name: 'Checks and tools' })
    for (const width of [390, 320] as const) {
      await page.setViewportSize({ width, height: 844 })
      await page.evaluate(() => window.scrollTo(0, 0))
      await expect(page.locator('.review-toolbar.is-collapsed')).toHaveCount(0)
      const top = await cards.evaluate((el) => el.getBoundingClientRect().top + window.scrollY)
      expect(top, `the first card's top at ${width}px`).toBeLessThanOrEqual(cardTopAtMost[width])
      await expectOrder([
        ['the header', page.locator('.workspace-header')], ['the card', cards],
        ['the footer', page.getByTestId('queue-footer')], ['Checks and tools', tools],
      ], width)
      await expectNoSidewaysScroll(page)
      await expectEndClearOfBars(page, page.getByRole('region', { name: 'Checks and tools' }), `the tools at ${width}px`)
    }
    await page.evaluate(() => window.scrollTo(0, 0))
    await stepsButton(page).tap()
    await expect(stepsButton(page)).toHaveText('Step 4 of 5 · Check')
    await expectTall(main, 'the main button in the steps panel')
    await main.tap()
    await expect(cards.locator('.review-idx')).toHaveText('#24')
    await expect(page).toHaveURL(/#\/drama\/3$/)
  })

  test('a long name wraps beside the back arrow, so the card starts at most one name line lower than with a short one', async ({ page }) => {
    // Title 1's own lines, not the mocked progress the other tests here give it.
    await page.unroute('**/api/workflow/dramas/1/progress')
    seedCheckLines()
    seedCheckLines({ drama: 1 })
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    try {
      for (const width of [390, 320] as const) {
        await page.setViewportSize({ width, height: 844 })
        const tops: Record<number, number> = {}
        for (const id of [3, 1]) {
          await page.goto('about:blank')
          await page.goto(`/#/drama/${id}/review`)
          const card = page.locator('.review-line')
          await expect(card.locator('.review-idx')).toHaveText('#24')
          await page.evaluate(() => window.scrollTo(0, 0))
          tops[id] = await card.evaluate((el) => el.getBoundingClientRect().top + window.scrollY)
          const [back, name] = [(await page.getByRole('link', { name: 'Back to Library' }).boundingBox())!, (await page.getByTestId('drama-title').boundingBox())!]
          const nameLine = await page.getByTestId('drama-title').evaluate((el) => parseFloat(getComputedStyle(el).lineHeight))
          expect(back.y < name.y + nameLine && name.y < back.y + back.height, `title ${id}: the back arrow and the name's first line share a row at ${width}px`).toBe(true)
          await expectTall(stepsButton(page), `title ${id}: the steps button at ${width}px`)
          expect((await stepsButton(page).boundingBox())!.y, `title ${id}: the steps button is under the name at ${width}px`).toBeGreaterThanOrEqual(name.y + name.height - 1)
          if (id === 3) expect(tops[3], `the first card's top at ${width}px`).toBeLessThanOrEqual(cardTopAtMost[width])
          await expectNoSidewaysScroll(page)
          if (id === 1) {
            expect(tops[1] - tops[3], `title 1's card is at most one name line lower at ${width}px`).toBeLessThanOrEqual(nameLine + 1)
          }
        }
      }
    } finally {
      clearCheckLines(1)
    }
  })

  test('the title page on the Translate step: the main button under the header, then the lines and the tools with Episodes', async ({ page }) => {
    seedCheckLines({ untranslated: true, audio: true })
    joinSeries()
    await mockPeaks(page)
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    await page.goto('/#/drama/3')
    await expect(stepsButton(page)).toHaveText('Step 3 of 5 · Translate')
    await expect(page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ })).toBeChecked()
    const main = page.getByRole('link', { name: 'Translate 1 line', exact: true })
    const tools = page.getByRole('region', { name: 'Checks and tools' })
    const tabs = tools.getByRole('tablist', { name: 'Review tools' })
    await expect(tabs.getByRole('tab')).toHaveText(['Look for problems', 'Change many lines', 'Finish', 'Episodes'])
    // Line #1, not the first row: while the lines load, the list holds placeholder rows that are then replaced.
    const firstLine = page.locator('.review-line').filter({ has: page.locator('.review-idx', { hasText: /^#1$/ }) })
    await expect(firstLine).toBeVisible()
    for (const width of [390, 320] as const) {
      await page.setViewportSize({ width, height: 844 })
      await page.evaluate(() => window.scrollTo(0, 0))
      // The pass before this one ends scrolled down, where a phone folds the toolbar and hides the header; both come back a moment after the scroll.
      await expect(page.locator('.review-toolbar.is-collapsed')).toHaveCount(0)
      await expectTall(main, `${width}px: the main button`)
      await expectOrder([
        ['the header', page.locator('.workspace-header')], ['the main button', main], ['the switch', page.getByRole('group', { name: 'Lines to show' })],
        ['the first line', firstLine], ['the tools', tabs],
      ], width)
      // The tools' row keeps its tabs (no picker), each whole.
      const cut = await tabs.evaluate((row) => {
        const box = row.getBoundingClientRect()
        return [...row.querySelectorAll('[role="tab"]')].filter((t) => {
          const r = t.getBoundingClientRect()
          return t.scrollWidth > t.clientWidth + 1 || r.left < box.left - 1 || r.right > box.right + 1
        }).map((t) => t.textContent)
      })
      expect(cut, `tabs cut at ${width}px`).toEqual([])
      for (let n = 0; n < 4; n++) await expectTall(tabs.getByRole('tab').nth(n), `tab ${n + 1} at ${width}px`)
      await expectNoSidewaysScroll(page)
      // The first screen shows a whole line, its source and its English, above the bottom bar: the player is folded away.
      await page.evaluate(() => window.scrollTo(0, 0))
      await expect(page.getByRole('group', { name: 'Player' })).toBeHidden()
      const lineBottom = Math.max(...await Promise.all([firstLine.locator('.review-zh'), firstLine.getByTestId('line-en')].map(async (l) => {
        const b = (await l.boundingBox())!
        return b.y + b.height
      })))
      expect(lineBottom, `the first line ends above the bottom bar at ${width}px`).toBeLessThanOrEqual(await barTop(page))
      await expectEndClearOfBars(page, tools, `the tools at ${width}px`)
    }
    // Player is in the panel the steps button opens, so it costs the first screen nothing; it brings the player back, as in Need a look.
    await page.evaluate(() => window.scrollTo(0, 0))
    await stepsButton(page).tap()
    const toggle = page.locator('.steps-fold-controls').getByRole('button', { name: 'Player', exact: true })
    await expectTall(toggle, 'Player in the steps panel')
    await toggle.tap()
    await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
    await toggle.tap()
    await expect(page.getByRole('group', { name: 'Player' })).toBeHidden()
    await tabs.getByRole('tab', { name: 'Episodes' }).tap()
    await expect(page.getByRole('list', { name: 'Episodes' }).getByRole('link', { name: /Signal/ })).toHaveAttribute('aria-current', 'page')
    await expectNoSidewaysScroll(page)
  })

  test('under All the filter row\'s More holds Replace… only; the one Player button is in the steps panel, as tall as the buttons beside it', async ({ page }) => {
    seedCheckLines({ untranslated: true, audio: true })
    await mockPeaks(page)
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/#/drama/3')
    await expect(page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ })).toBeChecked()
    await page.locator('.review-more-toggle').tap()
    const row = page.locator('.review-extras')
    await expect(row.getByRole('button', { name: 'Replace…', exact: true })).toBeVisible()
    await expect(row.getByRole('button', { name: 'Player', exact: true })).toHaveCount(0)
    await stepsButton(page).tap()
    const panel = page.locator('.steps-fold-controls')
    const [player, folder] = [(await panel.getByRole('button', { name: 'Player', exact: true }).boundingBox())!, (await panel.getByRole('button', { name: 'Open folder' }).boundingBox())!]
    expect(player.height, 'Player is tall enough to tap').toBeGreaterThanOrEqual(44)
    expect(player.height, 'Player is the height of Open folder').toBeCloseTo(folder.height, 0)
    await expectNoSidewaysScroll(page)
    await page.setViewportSize({ width: 320, height: 844 })
    await expectNoSidewaysScroll(page)
  })

  test('under All at 320px a flagged line says why in a few words, whole and clear of ⋯, which open the sentence', async ({ page }) => {
    seedCheckLines()
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    await page.setViewportSize({ width: 320, height: 844 })
    await page.goto('/#/drama/3/review')
    await page.locator('label').filter({ hasText: /^All 24$/ }).tap()
    await expect(page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ })).toBeChecked()
    const speed = 'This subtitle is too fast to read: 25.2 characters a second, and more than about 17 is hard to read in time. Shorten the text.'
    for (const [n, short, sentence] of [[12, 'Too fast to read, 25 characters a second', speed], [24, 'Glossary name not used: 夷陵老祖', GLOSSARY_SENTENCE]] as const) {
      const row = page.locator('.review-line').filter({ has: page.locator('.review-idx', { hasText: new RegExp(`^#${n}$`) }) })
      const more = row.getByRole('button', { name: `More actions for line ${n}` })
      // Where ⋯ sits in the row before the few words appear under the meta line.
      const moreAt = () => more.evaluate((el) => {
        const [b, r] = [el.getBoundingClientRect(), el.closest('li')!.getBoundingClientRect()]
        return [b.x, b.y - r.y, b.width, b.height].map((v) => Math.round(v))
      })
      const before = await moreAt()
      // The English, not the source: a tap on the source lands on ⋯ just above it, as a finger near a button does.
      await row.getByTestId('line-en').tap()
      await expect(row).toHaveAttribute('aria-current', 'true')
      const chip = row.locator('.review-flag-toggle')
      await expect(chip).toHaveText(`Flagged: ${short}`)
      await expect(chip).toHaveAttribute('title', `Flagged: ${sentence}`)
      for (const box of [chip, row.locator('.review-flag-line')]) {
        expect(await box.evaluate((el) => el.scrollWidth <= el.clientWidth && el.scrollHeight <= el.clientHeight), `#${n}'s few words are whole`).toBe(true)
      }
      const [c, m] = await Promise.all([chip, more].map(async (l) => (await l.boundingBox())!))
      expect(c.x + c.width <= m.x || c.y >= m.y + m.height, `#${n}'s few words stay clear of ⋯`).toBe(true)
      expect(await moreAt(), `#${n}'s ⋯ stays where it was`).toEqual(before)
      await expectNoSidewaysScroll(page)
      await chip.tap()
      await expect(row.getByRole('note')).toHaveText(sentence)
      await expectNoSidewaysScroll(page)
    }
  })

  test('the few words keep a glossary name in one piece when they wrap', async ({ page }) => {
    seedCheckLines()
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    await page.goto('/#/drama/3/review')
    await page.locator('label').filter({ hasText: /^All 24$/ }).tap()
    const row = page.locator('.review-line').filter({ has: page.locator('.review-idx', { hasText: /^#24$/ }) })
    await row.getByTestId('line-en').tap()
    const chip = row.locator('.review-flag-toggle')
    await expect(chip).toHaveText('Flagged: Glossary name not used: 夷陵老祖')
    // Every width from a wide phone to a narrow one: wherever the first line ends, it must not end inside the name.
    const split: number[] = []
    const wrapped: number[] = []
    for (let width = 390; width >= 320; width -= 2) {
      await page.setViewportSize({ width, height: 844 })
      const lines = await chip.evaluate((el, name) => {
        const count = (range: Range) => new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size
        const all = document.createRange()
        all.selectNodeContents(el)
        const text = [...el.childNodes].find((n): n is Text => n.nodeType === Node.TEXT_NODE && (n as Text).data.includes(name))!
        const part = document.createRange()
        part.setStart(text, text.data.indexOf(name))
        part.setEnd(text, text.data.indexOf(name) + name.length)
        return { all: count(all), name: count(part) }
      }, '夷陵老祖')
      if (lines.all > 1) wrapped.push(width)
      if (lines.name > 1) split.push(width)
    }
    expect(wrapped, 'the few words wrap on a narrow phone').toContain(320)
    expect(split, 'widths at which the glossary name is split across lines').toEqual([])
    await expectNoSidewaysScroll(page)
  })

  test('on the Check step with nothing to look at, Export stays under the header, with the steps closed', async ({ page }) => {
    seedCheckLines({ warnings: false })
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    for (const address of ['/#/drama/3', '/#/drama/3/review']) {
      await page.goto('about:blank')
      await page.goto(address)
      await expect(stepsButton(page)).toHaveText('Step 4 of 5 · Check')
      await expect(stepsButton(page)).toHaveAttribute('aria-expanded', 'false')
      const exportButton = page.getByRole('link', { name: 'Export', exact: true })
      for (const width of [390, 320] as const) {
        await page.setViewportSize({ width, height: 844 })
        await page.evaluate(() => window.scrollTo(0, 0))
        await expectTall(exportButton, `${address}: Export at ${width}px`)
        const h = (await page.locator('.workspace-header').boundingBox())!
        expect((await exportButton.boundingBox())!.y, `${address}: Export is under the header at ${width}px`).toBeGreaterThanOrEqual(h.y + h.height)
        await expectNoSidewaysScroll(page)
      }
    }
  })
})
