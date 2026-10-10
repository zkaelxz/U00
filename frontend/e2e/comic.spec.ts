import { expect, test, type Page } from '@playwright/test'

import { mockComic, pageIdOf, SHOTS_DIR } from './comicMocks'

// Comic viewer (#/comic/<id>) at the desktop viewport. Every /api call is
// mocked in comicMocks.ts; a catch-all aborts anything else, and each test
// ends by checking that nothing fell through to it.

const label = (page: Page) => page.getByTestId('comic-page-label')
const stage = (page: Page) => page.getByTestId('comic-stage')

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('manhua: resumes at the saved page in vertical scroll and saves progress', async ({ page }) => {
  const s = await mockComic(page, { mediaType: 'manhua', lastPage: 3 })
  await page.goto('/#/comic/7')
  await expect(page).toHaveURL(/#\/comic\/7\?page=3$/)
  await expect(page.getByRole('status')).toHaveText(/Resumed at page 3\./)
  await expect(label(page)).toHaveText('Page 3 of 8')
  await expect(stage(page)).toHaveClass(/comic-stage-vertical/)
  // Every page is laid out at its real size before it loads (no jump), lazily.
  const figs = page.getByTestId('comic-page')
  await expect(figs).toHaveCount(8)
  const img = figs.nth(2).locator('img')
  await expect(img).toHaveAttribute('width', '800')
  await expect(img).toHaveAttribute('height', '1200')
  await expect(figs.nth(7).locator('img')).toHaveAttribute('loading', 'lazy')
  expect(await img.evaluate((e) => getComputedStyle(e).imageOrientation)).toBe('none')
  await expect(page.getByTestId('comic-page').nth(2)).toBeInViewport()

  // Scrolling moves the current page; the hash follows without a history entry per page.
  await page.getByTestId('comic-page').nth(4).scrollIntoViewIfNeeded()
  await page.mouse.wheel(0, 200)
  await expect(label(page)).toHaveText(/Page [5-6] of 8/)
  const shown = Number((await label(page).textContent())!.match(/Page (\d)/)![1])
  await expect(page).toHaveURL(new RegExp(`page=${shown}$`))
  await expect.poll(() => s.progressPosts.at(-1), { timeout: 5000 }).toBe(shown)
  expect(s.calls.filter((c) => c.method === 'POST').every((c) => JSON.stringify(c.body) === `{"page":${(c.body as { page: number }).page}}`)).toBe(true)
  // The next pages are fetched ahead of time.
  await expect.poll(() => s.images.includes(`${pageIdOf(7, shown + 1)}:original`)).toBe(true)

  // End jumps to the last page.
  await page.keyboard.press('End')
  await expect(label(page)).toHaveText('Page 8 of 8')
  await expect(page.getByTestId('comic-page').nth(7)).toBeInViewport()
  await noSideways(page)
  expect(s.unmocked).toEqual([])
})

// A scroll callback that lands after the user left (the router re-renders a
// moment after hashchange) must not rewrite the hash back to the comic.
test('leaving the comic mid-scroll stays on the library', async ({ page }) => {
  const s = await mockComic(page, { mediaType: 'manhua', lastPage: 1 })
  await page.addInitScript(() => {
    const w = window as unknown as { __io: IntersectionObserverCallback[] }
    const Orig = window.IntersectionObserver
    w.__io = []
    window.IntersectionObserver = class extends Orig {
      constructor(cb: IntersectionObserverCallback, o?: IntersectionObserverInit) {
        super(cb, o)
        w.__io.push(cb)
      }
    } as typeof IntersectionObserver
  })
  await page.goto('/#/comic/7?page=1')
  await expect(label(page)).toHaveText('Page 1 of 8')
  await page.evaluate(() => {
    const w = window as unknown as { __io: IntersectionObserverCallback[] }
    window.location.hash = '#/library'
    const target = document.querySelector('[data-page="4"]')!
    w.__io[w.__io.length - 1]([{ isIntersecting: true, target } as unknown as IntersectionObserverEntry], null as never)
  })
  await expect(page).toHaveURL('/#/library')
  // Proving a non-event: a late redirect back to the comic would arrive after the fake intersection, so give it a window.
  await page.waitForTimeout(300)
  await expect(page).toHaveURL('/#/library')
})

test('manga: one page at a time, right to left, keys, taps and the scrubber', async ({ page }) => {
  const s = await mockComic(page, { id: 9, mediaType: 'manga', pageCount: 5, lastPage: 1 })
  await page.goto('/#/comic/9?page=1')
  await expect(label(page)).toHaveText('Page 1 of 5')
  await expect(stage(page)).toHaveClass(/comic-stage-paged/)
  await expect(page.getByTestId('comic-page')).toHaveCount(1)
  await expect(page.getByRole('status')).toHaveCount(0)
  await expect(page.getByLabel('Page', { exact: true })).toHaveAttribute('dir', 'rtl')

  // Right to left: ArrowLeft goes forward, ArrowRight back.
  await page.keyboard.press('ArrowLeft')
  await expect(label(page)).toHaveText('Page 2 of 5')
  await expect(page).toHaveURL(/page=2$/)
  await page.keyboard.press('ArrowRight')
  await expect(label(page)).toHaveText('Page 1 of 5')
  await page.keyboard.press('Space')
  await expect(label(page)).toHaveText('Page 2 of 5')

  // Paged mode preloads the next page and the one before it.
  await expect.poll(() => s.images.includes(`${pageIdOf(9, 2)}:original`)).toBe(true)
  await expect.poll(() => s.images.includes(`${pageIdOf(9, 0)}:original`)).toBe(true)

  // The left third of the page goes forward; the middle hides the bars.
  const box = (await stage(page).boundingBox())!
  await page.mouse.click(box.x + box.width * 0.1, box.y + box.height / 2)
  await expect(label(page)).toHaveText('Page 3 of 5')
  await page.mouse.click(box.x + box.width * 0.9, box.y + box.height / 2)
  await expect(label(page)).toHaveText('Page 2 of 5')
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height / 2)
  await expect(label(page)).toBeHidden()
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height / 2)
  await expect(label(page)).toBeVisible()

  // Mirrored pager: "Next page" sits on the left.
  const next = await page.getByRole('button', { name: 'Next page' }).boundingBox()
  const prev = await page.getByRole('button', { name: 'Previous page' }).boundingBox()
  expect(next!.x).toBeLessThan(prev!.x)
  await page.getByLabel('Page', { exact: true }).fill('5')
  await expect(label(page)).toHaveText('Page 5 of 5')
  await expect(page.getByRole('button', { name: 'Next page' })).toBeDisabled()
  await page.keyboard.press('Home')
  await expect(label(page)).toHaveText('Page 1 of 5')

  // Zoom from the bar; a page turn resets it.
  await page.getByRole('button', { name: 'Zoom in' }).click()
  await expect(page.getByRole('button', { name: 'Reset zoom' })).toHaveText('125%')
  await expect(page.getByTestId('comic-zoom-layer')).toHaveAttribute('style', /scale\(1\.25\)/)
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByRole('button', { name: 'Reset zoom' })).toHaveText('100%')

  // The view choice is remembered for this drama.
  await page.getByRole('button', { name: 'View settings' }).click()
  await page.getByLabel('Reading mode', { exact: true }).selectOption('vertical')
  await expect(stage(page)).toHaveClass(/comic-stage-vertical/)
  await expect(label(page)).toHaveText('Page 2 of 5')
  const stored = await page.evaluate(() => localStorage.getItem('baihe.pref.comic.view.9'))
  expect(JSON.parse(stored!)).toMatchObject({ mode: 'vertical', rtl: true })
  await page.reload()
  await expect(stage(page)).toHaveClass(/comic-stage-vertical/)
  expect(s.unmocked).toEqual([])
})

test('Typeset and Text: rendered pages, boxes and the side panel, text only', async ({ page }) => {
  const s = await mockComic(page, { id: 11, mediaType: 'manga', pageCount: 3, rendered: [0], firstPageText: '<b>Bold?</b> & more' })
  await page.goto('/#/comic/11?page=1')
  const img = page.getByTestId('comic-page').locator('img')
  await expect(img).toHaveAttribute('src', /variant=rendered&v=1700000000$/)
  const typeset = page.getByRole('button', { name: 'Typeset' })
  await expect(typeset).toHaveAttribute('aria-pressed', 'true')
  await typeset.click()
  await expect(img).toHaveAttribute('src', /variant=original/)
  await typeset.click()

  await page.getByRole('button', { name: 'Text' }).click()
  const boxes = page.getByTestId('comic-boxes').locator('.comic-box')
  await expect(boxes).toHaveCount(3)
  // Box 1 (idx 0) sits at 55% / 6% of the page.
  const style = await boxes.first().getAttribute('style')
  expect(style).toContain('left: 55%')
  expect(style).toContain('top: 6%')
  const panel = page.getByRole('complementary', { name: 'Page text' })
  const lines = panel.getByTestId('comic-lines').locator('li')
  await expect(lines).toHaveCount(3)
  // Server text is shown literally, never as markup.
  await expect(lines.first()).toContainText('<b>Bold?</b> & more')
  await expect(page.locator('.comic b')).toHaveCount(0)
  await expect(lines.nth(1)).toContainText('Nowhere. I was reading. (p1)')
  await expect(lines.nth(1)).toContainText('哪儿也没去，我在看书。')

  // Page 2 has no typeset image: it falls back to the original.
  await page.keyboard.press('ArrowLeft')
  await expect(img).toHaveAttribute('src', /pages\/1102\/image\?variant=original/)
  await expect(panel.getByRole('heading')).toHaveText('Page 2 text')
  await expect(lines.first()).toContainText('(p2)')
  expect(s.calls.filter((c) => c.path.endsWith('/regions')).length).toBe(2)
  expect(s.unmocked).toEqual([])
})

test('a failed saved-page read never overwrites it; moving still saves', async ({ page }) => {
  // One page at a time: sitting on page 1 sends nothing, turning to page 2 saves 2.
  const s = await mockComic(page, { id: 14, mediaType: 'manga', pageCount: 5, lastPage: 4, progressFails: true })
  await page.goto('/#/comic/14')
  await expect(page).toHaveURL(/#\/comic\/14\?page=1$/)
  await expect(page.getByTestId('comic-page').locator('img')).toHaveJSProperty('complete', true)
  // Proving a non-event: no progress save may follow arrival, and the save is debounced, so wait past it.
  await page.waitForTimeout(2500)
  expect(s.calls.filter((c) => c.method === 'GET' && c.path.endsWith('/progress')).length).toBe(1)
  expect(s.progressPosts).toEqual([])
  await page.getByRole('button', { name: 'Next page' }).click()
  await expect(label(page)).toHaveText('Page 2 of 5')
  await expect.poll(() => s.progressPosts, { timeout: 5000 }).toEqual([2])
  expect(s.unmocked).toEqual([])

  // Vertical scroll: page 1 in view on arrival is not a move either.
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  const v = await mockComic(page, { id: 16, mediaType: 'manhua', pageCount: 6, lastPage: 4, progressFails: true })
  await page.goto('/#/comic/16')
  await expect(page).toHaveURL(/#\/comic\/16\?page=1$/)
  await expect(page.getByTestId('comic-page').first().locator('img')).toHaveJSProperty('complete', true)
  // Proving a non-event: same as above for the vertical reader.
  await page.waitForTimeout(2500)
  expect(v.progressPosts).toEqual([])
  await page.keyboard.press('End')
  await expect(label(page)).toHaveText('Page 6 of 6')
  await expect.poll(() => v.progressPosts.at(-1), { timeout: 5000 }).toBe(6)
  expect(v.progressPosts).not.toContain(1)
  expect(v.unmocked).toEqual([])
})

test('a 403 image says media playback permission is needed', async ({ page }) => {
  const s = await mockComic(page, { id: 12, imagesForbidden: true, lastPage: null })
  await page.goto('/#/comic/12')
  // No saved progress allowed: starts at page 1.
  await expect(page).toHaveURL(/#\/comic\/12\?page=1$/)
  await expect(page.getByTestId('comic-forbidden')).toHaveText('Needs media playback permission — ask the owner.')
  await expect(page.getByTestId('comic-page').first()).toContainText('Needs media playback permission — ask the owner.')
  await expect(page.getByTestId('comic-page').first().locator('img')).toHaveCount(0)
  expect(s.calls.some((c) => c.method === 'HEAD')).toBe(true)
  expect(s.unmocked).toEqual([])
})

test('the Reader sends a comic with no lines to the comic viewer', async ({ page }) => {
  const s = await mockComic(page, { id: 13, mediaType: 'manhwa', lastPage: 2 })
  const json = (body: unknown) => (route: import('@playwright/test').Route) => route.fulfill({ json: body })
  await page.route(/\/api\/reader\/dramas\/13\/overview$/, json({ line_count: 0, last_page: 1, last_line_idx: null, percent_complete: 0, word_count: 0, char_count: 0 }))
  await page.route(/\/api\/reader\/dramas\/13\/(media|vocab)$/, (route) => route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'x' } } }))
  await page.route(/\/api\/glossary\/dramas\/13\/terms$/, json([]))
  await page.route(/\/api\/translate\/engines$/, json({ items: [] }))
  await page.goto('/#/read/13')
  await expect(page).toHaveURL(/#\/comic\/13\?page=2$/)
  await expect(label(page)).toHaveText('Page 2 of 8')
  await expect(page.getByRole('link', { name: 'Library' }).first()).toHaveAttribute('aria-current', 'page')
  expect(s.unmocked).toEqual([])
})

test('screenshots: desktop vertical, paged right to left and the text panel', async ({ page }) => {
  test.skip(!SHOTS_DIR, 'set COMIC_SCREENS_DIR to save screenshots')
  await page.setViewportSize({ width: 1440, height: 900 })
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    const v = await mockComic(page, { id: 21, mediaType: 'manhwa', lastPage: 2, rendered: [1, 2] })
    await page.goto('/#/comic/21')
    await expect(page.getByTestId('comic-page').nth(1).locator('img')).toHaveJSProperty('complete', true)
    await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
    await page.screenshot({ path: `${SHOTS_DIR}/desktop-vertical-${scheme}.png` })

    await page.unrouteAll({ behavior: 'ignoreErrors' })
    const p = await mockComic(page, { id: 22, mediaType: 'manga', pageCount: 6, lastPage: 1 })
    await page.goto('/#/comic/22?page=2')
    await expect(page.getByTestId('comic-page').locator('img')).toHaveJSProperty('complete', true)
    await page.screenshot({ path: `${SHOTS_DIR}/desktop-paged-rtl-${scheme}.png` })

    await page.getByRole('button', { name: 'Text' }).click()
    await expect(page.getByTestId('comic-lines').locator('li')).toHaveCount(3)
    await page.screenshot({ path: `${SHOTS_DIR}/desktop-text-${scheme}.png` })
    await page.evaluate(() => localStorage.clear())
    expect([...v.unmocked, ...p.unmocked]).toEqual([])
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  }
})

test('the top bar does not move when the pages finish loading', async ({ page }) => {
  await mockComic(page, { pagesDelayMs: 1500 })
  await page.goto('/#/comic/7')
  const translate = page.getByRole('button', { name: 'Translate', exact: true })
  await expect(label(page)).toHaveText('Page - of -')
  await expect(page.getByRole('navigation', { name: 'Pages' }).getByRole('button', { name: 'Next page' })).toBeDisabled()
  const before = await translate.boundingBox()
  await expect(label(page)).toHaveText('Page 1 of 8')
  const after = await translate.boundingBox()
  expect(before).not.toBeNull()
  expect(after).toEqual(before)
  await noSideways(page)
})
