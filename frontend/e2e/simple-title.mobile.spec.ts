import { expect, test, type Locator, type Page } from '@playwright/test'

import { GLOSSARY_SENTENCE, joinSeries, leaveSeries, mockPeaks, seedCheckLines } from './compareSelectedHelpers'
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
// Its name starts with the drawn ▸ marker, so the pattern is not anchored.
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
    // The radios are visually hidden; the visible control is their label.
    for (const name of ['Simple', 'Full']) {
      await expectTall(panel.locator('label').filter({ hasText: new RegExp(`^${name}$`) }).first(), `${name} switch at ${width}px`)
    }
    await expectTall(panel.getByRole('button', { name: 'Open folder' }), `Open folder at ${width}px`)
    // In this order: the steps, the three links, the switch, Open folder, then the source link or the way to add one.
    const order = [
      panel.getByRole('list', { name: 'Steps' }), ...more.map((name) => panel.getByRole('link', { name, exact: true })),
      panel.getByRole('group', { name: 'Title view' }), panel.getByRole('button', { name: 'Open folder' }), panel.locator('.ws-source, .ws-source-add'),
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

// The first card starts near the top: measured 211px at 390 and 239px at 320, with the steps folded into one line.
const cardTopAtMost = { 390: 225, 320: 255 }

// The fixed bars at the bottom (the app's and the queue's) must leave the end of the page reachable above them.
async function expectEndClearOfBars(page: Page, last: Locator, what: string) {
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  const bottom = await last.evaluate((el) => el.getBoundingClientRect().bottom)
  const barTop = await page.evaluate(() => Math.min(window.innerHeight, ...[...document.querySelectorAll('body *')].filter((el) => {
    const css = getComputedStyle(el)
    const r = el.getBoundingClientRect()
    return css.position === 'fixed' && css.visibility !== 'hidden' && r.height > 0 && r.top > window.innerHeight / 2
  }).map((el) => el.getBoundingClientRect().top)))
  expect(bottom, `${what} ends above the bottom bar`).toBeLessThanOrEqual(barTop + 0.5)
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
    await expect(page.getByText('Next: line 7, Whisper was unsure · line 12, fast to read, 25 characters a second')).toBeVisible()
    // The drawn card: Why in a plain sentence, the English under its label in a box, the buttons under it, and a green
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
    for (const width of [390, 320] as const) {
      await page.setViewportSize({ width, height: 844 })
      await page.evaluate(() => window.scrollTo(0, 0))
      await expect(page.locator('.review-toolbar.is-collapsed')).toHaveCount(0)
      const top = await cards.evaluate((el) => el.getBoundingClientRect().top + window.scrollY)
      expect(top, `the first card's top at ${width}px`).toBeLessThanOrEqual(cardTopAtMost[width])
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

    // Player sits under the card, after the Next line and before the footer; it opens the player above the card and shows it.
    const toggle = page.getByRole('button', { name: 'Player', exact: true })
    await expectTall(toggle, 'Player')
    const [next, footer, t] = await Promise.all([page.locator('.check-queue-next').boundingBox(), page.getByTestId('queue-footer').boundingBox(), toggle.boundingBox()])
    expect(t!.y).toBeGreaterThan(next!.y)
    expect(t!.y).toBeLessThan(footer!.y)
    expect(t!.width, 'Player is full width').toBeGreaterThan(300)
    await toggle.tap()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(player).toBeInViewport()
    await expectNoSidewaysScroll(page)
    await page.setViewportSize({ width: 320, height: 844 })
    await expectNoSidewaysScroll(page)
  })

  test('the title page on the Check step: the card first, then Player, the footer and the tools, all clear of the bottom bar', async ({ page }) => {
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
        ['the header', page.locator('.workspace-header')], ['the card', cards], ['Player', page.getByRole('button', { name: 'Player', exact: true })],
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
    for (const width of [390, 320] as const) {
      await page.setViewportSize({ width, height: 844 })
      await page.evaluate(() => window.scrollTo(0, 0))
      await expectTall(main, `${width}px: the main button`)
      await expectOrder([
        ['the header', page.locator('.workspace-header')], ['the main button', main], ['the switch', page.getByRole('group', { name: 'Lines to show' })],
        ['the first line', page.locator('.review-line').first()], ['the tools', tabs],
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
      await expectEndClearOfBars(page, tools, `the tools at ${width}px`)
    }
    await tabs.getByRole('tab', { name: 'Episodes' }).tap()
    await expect(page.getByRole('list', { name: 'Episodes' }).getByRole('link', { name: /Signal/ })).toHaveAttribute('aria-current', 'page')
    await expectNoSidewaysScroll(page)
  })
})
