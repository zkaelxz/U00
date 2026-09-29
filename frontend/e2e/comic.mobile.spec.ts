import { expect, test, type Page } from '@playwright/test'

import { mockComic, SHOTS_DIR } from './comicMocks'

// Phone project (390x844, touch): the comic viewer. Every /api call is mocked
// (comicMocks.ts); a catch-all aborts anything else and must stay unused.

const label = (page: Page) => page.getByTestId('comic-page-label')
const stage = (page: Page) => page.getByTestId('comic-stage')

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

// Visible controls in the viewer's bars are at least 44 px tall.
async function tallTargets(page: Page) {
  const small = await page.locator('.comic').evaluate((root) => {
    const sel = '.comic-head a, .comic-head button, .comic-bottom button, .comic-bottom input'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

// Two touch points through the browser's own input pipeline (CDP), spreading apart.
async function pinchOut(page: Page, cx: number, cy: number) {
  const cdp = await page.context().newCDPSession(page)
  const pts = (d: number) => [{ x: cx - d, y: cy, id: 0 }, { x: cx + d, y: cy, id: 1 }]
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: pts(30) })
  for (const d of [45, 60, 80, 100]) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: pts(d) })
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await cdp.detach()
}

test('phone: vertical manhwa is full width, no sideways scroll, 44 px bars, text in a sheet', async ({ page }) => {
  const s = await mockComic(page, { mediaType: 'manhwa', lastPage: 2 })
  await page.goto('/#/comic/7')
  await expect(page).toHaveURL(/page=2$/)
  await expect(label(page)).toHaveText('2 / 8')
  await expect(stage(page)).toHaveClass(/comic-stage-vertical/)
  const box = (await stage(page).boundingBox())!
  expect(Math.round(box.width)).toBe(390)
  expect(Math.round(box.x)).toBe(0)
  await noSideways(page)
  await tallTargets(page)

  // Text: boxes are numbered on the page and the lines are in a bottom sheet.
  await page.getByRole('button', { name: 'Text', exact: true }).click()
  const sheet = page.getByRole('dialog', { name: 'Page 2 text' })
  await expect(sheet).toBeVisible()
  await expect(sheet.getByTestId('comic-lines').locator('li')).toHaveCount(3)
  await expect(sheet.getByTestId('comic-lines').locator('li').first()).toContainText('Where did you go last night? (p2)')
  await sheet.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByTestId('comic-page').nth(1).locator('.comic-box')).toHaveCount(3)
  // On a phone the boxes carry numbers only.
  await expect(page.getByTestId('comic-page').nth(1).locator('.comic-box-line')).toHaveCount(0)
  await noSideways(page)

  // Pinch zoom: the page scales; a double tap goes back to fit.
  await pinchOut(page, 195, 500)
  await expect(page.getByTestId('comic-zoom-layer')).toHaveAttribute('style', /scale\((1\.[5-9]|[2-4])/)
  await expect(stage(page)).toHaveClass(/comic-zoomed/)
  await noSideways(page)
  await page.touchscreen.tap(195, 500)
  await page.touchscreen.tap(195, 500)
  await expect(stage(page)).not.toHaveClass(/comic-zoomed/)
  expect(s.unmocked).toEqual([])
})

test('phone: manga pages right to left with tap thirds; the middle hides the bars', async ({ page }) => {
  const s = await mockComic(page, { id: 9, mediaType: 'manga', pageCount: 4, lastPage: 1 })
  await page.goto('/#/comic/9')
  await expect(page).toHaveURL(/page=1$/)
  await expect(label(page)).toHaveText('1 / 4')
  await expect(stage(page)).toHaveClass(/comic-stage-paged/)
  await expect(page.getByTestId('comic-page').locator('img')).toHaveJSProperty('complete', true)
  const box = (await stage(page).boundingBox())!
  const y = Math.min(box.y + box.height / 2, 600)
  await page.touchscreen.tap(box.x + box.width * 0.12, y)
  await expect(label(page)).toHaveText('2 / 4')
  await page.touchscreen.tap(box.x + box.width * 0.88, y)
  await expect(label(page)).toHaveText('1 / 4')
  await page.touchscreen.tap(box.x + box.width * 0.12, y)
  await expect(label(page)).toHaveText('2 / 4')
  await page.touchscreen.tap(box.x + box.width * 0.5, y)
  await expect(page.locator('.comic-bottom')).toBeHidden()
  await page.touchscreen.tap(box.x + box.width * 0.5, y)
  await expect(page.locator('.comic-bottom')).toBeVisible()
  await noSideways(page)
  await tallTargets(page)
  // Saved a second after the page settles (page 1 was already saved, so never re-sent).
  await expect.poll(() => s.progressPosts.at(-1), { timeout: 5000 }).toBe(2)
  expect(s.progressPosts).not.toContain(1)
  expect(s.unmocked).toEqual([])
})

test('phone: one page at a time at original size scrolls to both edges of a wide page', async ({ page }) => {
  const s = await mockComic(page, { id: 15, mediaType: 'manga', pageCount: 3, lastPage: 1, width: 800, height: 1200 })
  await page.goto('/#/comic/15')
  await expect(stage(page)).toHaveClass(/comic-stage-paged/)
  await page.getByRole('button', { name: 'View settings' }).click()
  const sheet = page.getByRole('dialog', { name: 'View settings' })
  await sheet.getByLabel('Fit', { exact: true }).selectOption('original')
  await sheet.getByRole('button', { name: 'Close' }).click()
  const fig = page.getByTestId('comic-page')
  await expect(fig).toHaveClass(/comic-fit-original/)
  await expect(fig.locator('img')).toHaveJSProperty('complete', true)
  const edges = (to: 'start' | 'end') =>
    fig.evaluate((f, end) => {
      f.scrollLeft = end ? f.scrollWidth : 0
      const img = f.querySelector('img')!.getBoundingClientRect()
      const box = f.getBoundingClientRect()
      return { scrollLeft: f.scrollLeft, figLeft: box.left, figRight: box.right, imgLeft: img.left, imgRight: img.right, imgWidth: img.width }
    }, to === 'end')
  const start = await edges('start')
  expect(start.imgWidth).toBe(800)
  expect(start.scrollLeft).toBe(0)
  // The page's left edge is reachable (not pushed off to the left by centring).
  expect(start.imgLeft).toBeGreaterThanOrEqual(start.figLeft - 0.5)
  const end = await edges('end')
  expect(end.imgRight).toBeLessThanOrEqual(end.figRight + 0.5)
  await noSideways(page)
  if (SHOTS_DIR) {
    await edges('start')
    await page.screenshot({ path: `${SHOTS_DIR}/phone-paged-original.png` })
  }
  expect(s.unmocked).toEqual([])
})

test('phone: a 403 image shows the permission message, no sideways scroll', async ({ page }) => {
  const s = await mockComic(page, { id: 12, imagesForbidden: true })
  await page.goto('/#/comic/12?page=1')
  await expect(page.getByTestId('comic-forbidden')).toHaveText('Needs media playback permission — ask the owner.')
  await noSideways(page)
  expect(s.unmocked).toEqual([])
})

test('screenshots: phone vertical, paged right to left and the text sheet', async ({ page }) => {
  test.skip(!SHOTS_DIR, 'set COMIC_SCREENS_DIR to save screenshots')
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    const v = await mockComic(page, { id: 21, mediaType: 'manhwa', lastPage: 2, rendered: [1, 2] })
    await page.goto('/#/comic/21')
    await expect(page.getByTestId('comic-page').nth(1).locator('img')).toHaveJSProperty('complete', true)
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS_DIR}/phone-vertical-${scheme}.png` })

    await page.unrouteAll({ behavior: 'ignoreErrors' })
    const p = await mockComic(page, { id: 22, mediaType: 'manga', pageCount: 6, lastPage: 1 })
    await page.goto('/#/comic/22?page=2')
    await expect(page.getByTestId('comic-page').locator('img')).toHaveJSProperty('complete', true)
    await stage(page).scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS_DIR}/phone-paged-rtl-${scheme}.png` })

    await page.getByRole('button', { name: 'Text', exact: true }).click()
    await expect(page.getByRole('dialog', { name: 'Page 2 text' }).locator('li')).toHaveCount(3)
    await page.screenshot({ path: `${SHOTS_DIR}/phone-text-${scheme}.png` })
    await page.evaluate(() => localStorage.clear())
    expect([...v.unmocked, ...p.unmocked]).toEqual([])
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  }
})
