import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

import { seedCheckLines } from './compareSelectedHelpers'
import { expect, test } from './fixtures'
import { installHitArea } from './hitArea'

// The Watch page on phones: player, "now" card and lines in that order, the line being spoken on the first screen,
// 44px targets, no sideways scroll, and long titles and lines wrap instead of being cut off.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const LONG_TITLE = 'A very long title that keeps going well past the width of any phone screen, '.repeat(2).trim()
const LONG_LINE = 'An English line long enough to need several rows on the narrowest phone, with words like extraordinarily.'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  seedCheckLines({ warnings: false, audio: true })
})

const withTitle = (page: Page, title: string) => page.route('**/api/library/dramas/3', async (route) => {
  if (route.request().method() !== 'GET') return route.fallback()
  const res = await route.fetch()
  await route.fulfill({ response: res, json: { ...(await res.json()), title_en: title } })
})
const withLongFirstLine = (page: Page) => page.route(/\/api\/review\/dramas\/3\/lines\?/, async (route) => {
  const res = await route.fetch()
  const body = await res.json()
  body.lines = body.lines.map((l: { idx: number; en: string }) => (l.idx === 0 ? { ...l, en: LONG_LINE } : l))
  await route.fulfill({ response: res, json: body })
})

test.afterAll(() => {
  // Other specs expect drama 3 as the server seeds it, with no sound and no lines, unless they add them.
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\ndb.update_drama(3, audio_filename=None)\ndb.save_lines(3, [])`], { cwd: repoRoot })
})

// Every control on the page that is on screen and shorter than 44px to a thumb.
const smallTargets = (page: Page) => page.locator('.watch-page').locator('button, a[href], summary, select, input, textarea').evaluateAll((els) =>
  els
    .filter((e) => (e as HTMLElement).offsetParent !== null || (e as HTMLElement).checkVisibility?.())
    .filter((e) => !e.closest('[hidden]'))
    .map((e) => ({ h: Math.round(window.hitHeight(e)), what: (e.getAttribute('aria-label') || e.textContent || e.tagName).trim().slice(0, 40) }))
    .filter(({ h }) => h < 44))

// Text that is cut off: a box that hides what overflows it while its content is wider or taller than it, or any
// element that reaches past the right edge of the screen. (A box that lets overflow show cannot cut text, and the
// 44px hit areas of small buttons overflow their box on purpose.)
const clipped = (page: Page) => page.locator('.watch-page').locator('h1, p, span, a, button, li, label, summary').evaluateAll((els) =>
  els
    .filter((e) => e.getClientRects().length > 0)
    .filter((e) => {
      const st = getComputedStyle(e)
      const hides = st.overflowX === 'hidden' || st.overflowX === 'clip' || st.textOverflow === 'ellipsis'
      return (hides && (e.scrollWidth > e.clientWidth + 1 || (st.overflowY !== 'auto' && e.scrollHeight > e.clientHeight + 1)))
        || e.getBoundingClientRect().right > document.documentElement.clientWidth + 0.5
    })
    .map((e) => (e.textContent || '').trim().slice(0, 40)))

const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)

// The page is for reading the line being spoken, so on a phone it must be in view without scrolling: above the
// bottom bar, with a real title that takes two rows.
for (const [width, height] of [[390, 844], [360, 800]]) {
  test(`${width}x${height}: the line being spoken is on the first screen, above the bottom bar`, async ({ page }) => {
    await withTitle(page, 'Grandmaster of Demonic Cultivation')
    await page.setViewportSize({ width, height })
    await page.goto('/#/watch/3')
    await expect(page.getByRole('list', { name: 'Lines' }).getByRole('listitem')).toHaveCount(24)
    await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
    const heading = page.getByRole('heading', { level: 1 })
    await expect(heading).toHaveText('Grandmaster of Demonic Cultivation')
    const lineHeight = await heading.evaluate((h) => parseFloat(getComputedStyle(h).lineHeight))
    expect((await heading.boundingBox())!.height).toBeGreaterThan(lineHeight * 1.5)
    await page.keyboard.press('ArrowDown')
    const box = page.getByTestId('watch-now-box')
    await expect(box).toHaveText('Line 1')
    const bar = (await page.locator('nav.phone-bar').boundingBox())!
    expect(bar.y + bar.height).toBeGreaterThan(height - 1)
    const english = (await box.boundingBox())!
    expect(english.y + english.height).toBeLessThan(bar.y)
    // Without the page having scrolled to get there.
    expect(await page.evaluate(() => window.scrollY)).toBe(0)
  })
}

for (const width of [390, 360, 320]) {
  test(`${width}px: player, now card and lines in order, 44px targets, no sideways scroll, nothing cut off`, async ({ page }) => {
    await withTitle(page, LONG_TITLE)
    await withLongFirstLine(page)
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/#/watch/3')
    const list = page.getByRole('list', { name: 'Lines' })
    await expect(list.getByRole('listitem')).toHaveCount(24)
    await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(LONG_TITLE)
    const back = page.getByRole('link', { name: '‹ Library' })
    await expect(back).toHaveAttribute('href', '#/library')
    // One More button at the end of the back row stands for the header's buttons.
    const more = page.locator('.watch-page').getByText('More', { exact: true })
    const backBox = (await back.boundingBox())!
    const moreBox = (await more.boundingBox())!
    expect(Math.abs((backBox.y + backBox.height / 2) - (moreBox.y + moreBox.height / 2))).toBeLessThan(2)
    expect(moreBox.x + moreBox.width).toBeGreaterThan(width - 40)
    await expect(page.locator('.watch-page .page-head .actions')).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'Work on this title' })).toBeHidden()

    const player = (await page.getByRole('group', { name: 'Player' }).boundingBox())!
    const card = (await page.getByRole('region', { name: 'Line being spoken' }).boundingBox())!
    const lines = (await page.getByRole('region', { name: 'Lines' }).boundingBox())!
    expect(player.y).toBeLessThan(card.y)
    expect(card.y).toBeLessThan(lines.y)

    // The first line is current, and its long English wraps in the big box.
    await page.keyboard.press('ArrowDown')
    const box = page.getByTestId('watch-now-box')
    await expect(box).toHaveText(LONG_LINE)
    expect((await box.boundingBox())!.height).toBeGreaterThan(60)

    expect(await sideways(page)).toBeLessThanOrEqual(0)
    expect(await smallTargets(page)).toEqual([])
    expect(await clipped(page)).toEqual([])

    // The More menu's rows are thumb-sized too.
    await more.tap()
    await expect(page.getByRole('link', { name: 'Save subtitles' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Work on this title' })).toHaveAttribute('href', '#/drama/3')
    const panel = page.locator('.watch-more-panel')
    expect(await panel.getByRole('link').allTextContents()).toEqual(['Work on this title', 'Follow along with sound from another app', 'Read the lines', 'Save subtitles'])
    // (The open menu covers the player's row, so only its own rows are measured, by their hit areas.)
    const rows = await panel.getByRole('link').evaluateAll((els) => els.map((e) => Math.round(window.hitHeight(e))))
    expect(rows).toHaveLength(4)
    for (const h of rows) expect(h).toBeGreaterThanOrEqual(44)
    expect(await sideways(page)).toBeLessThanOrEqual(0)
    expect(await clipped(page)).toEqual([])
    await more.tap()

    // Fixing opens on a tap, with the same size rules.
    await page.getByRole('button', { name: 'Fix this line' }).tap()
    await expect(page.getByRole('textbox', { name: 'English' })).toHaveValue(LONG_LINE)
    expect(await smallTargets(page)).toEqual([])
    expect(await sideways(page)).toBeLessThanOrEqual(0)
    expect(await clipped(page)).toEqual([])
  })
}
