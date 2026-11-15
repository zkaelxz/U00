import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

import { clearCheckLines, seedCheckLines } from './compareSelectedHelpers'
import { expect, test } from './fixtures'

// Each of Review's two views, Need a look and All, reopens on the line the person was last on in it, per title, kept in
// this browser. Title 3 gets 100 lines (three pages); three of them need a look (#24, #7 and #12, in that order).

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const inLibrary = (code: string) =>
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })

// The seeded lines' media goes too, so later specs see title 3 as seeded.
test.afterAll(() => {
  inLibrary('db.update_drama(3, audio_filename=None, source_video_filename=None)')
  clearCheckLines(3)
})

const seedPlaceLines = () => seedCheckLines({ count: 100 })
const PLACE_KEY = 'baihe.pref.review.at.3'
const row = (page: Page, n: number) => page.locator('.review-line').filter({ has: page.locator('.review-idx', { hasText: new RegExp(`^#${n}$`) }) })

const realRow = (page: Page) => page.locator('.review-line:not(.review-skeleton)').first()
const status = (page: Page) => page.locator('.review-status')
const pageNumber = (page: Page) => page.getByLabel('Page number').first()
const kept = (page: Page) => page.evaluate((k) => localStorage.getItem(k), PLACE_KEY)
// A short source video for title 3, as playerMocks.ts seedVideo writes one; false without OpenCV.
function addVideo(): boolean {
  try {
    inLibrary(`import os, cv2, numpy as np
p = os.path.join(db.drama_dir(3), 'e2e.webm')
w = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'VP80'), 10, (320, 180))
assert w.isOpened()
for i in range(120):
    w.write(np.full((180, 320, 3), (i * 2) % 255, np.uint8))
w.release()
db.update_drama(3, source_video_filename='e2e.webm')`)
    return true
  } catch {
    return false
  }
}
const queueRadio = (page: Page) => page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^Need a look/ })
const allRadio = (page: Page) => page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ })

async function leave(page: Page) {
  await page.goto('/#/library')
  await expect(page.locator('.review-line')).toHaveCount(0)
}

// The list's own pages (40 lines each), by page number, from here on.
function countPages(page: Page): Map<number, number> {
  const seen = new Map<number, number>()
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (!/\/api\/review\/dramas\/3\/lines$/.test(url.pathname) || url.searchParams.get('page_size') !== '40') return
    const n = Number(url.searchParams.get('page'))
    seen.set(n, (seen.get(n) ?? 0) + 1)
  })
  return seen
}

// Holds one request for the lines that `which` picks (the first, or the one after `skip` others) until the returned
// function is called, so the person can act while a restore is still waiting for its answer. The rest go through at once.
async function holdFirst(page: Page, which: (pageSize: number) => boolean, skip = 0): Promise<() => void> {
  let release = () => {}
  const gate = new Promise<void>((resolve) => (release = resolve))
  let seen = 0
  let held = false
  await page.route('**/api/review/dramas/3/lines?*', async (route) => {
    const url = new URL(route.request().url())
    if (!held && which(Number(url.searchParams.get('page_size'))) && seen++ === skip) {
      held = true
      await gate
    }
    await route.continue()
  })
  return release
}
// The same lines with their places moved on by `by`, ids kept: the kept line keeps its id and is found on another page.
const shiftLines = (by: number) =>
  inLibrary(`ls = db.load_line_objects(3)
for l in ls:
    l.idx = (l.idx + ${by}) % len(ls)
db.save_lines(3, ls)`)
const listPage = (size: number) => size === 40
const wholeTitle = (size: number) => size > 40
// Reading the whole title, by the requests made from here on.
function countWholeTitle(page: Page): { n: number } {
  const seen = { n: 0 }
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (/\/api\/review\/dramas\/3\/lines$/.test(url.pathname) && Number(url.searchParams.get('page_size')) > 40) seen.n += 1
  })
  return seen
}
// What a late move would have done by now: the local server answers in a few milliseconds.
const settle = (page: Page) => page.waitForTimeout(800)

async function expectInView(page: Page, n: number) {
  const box = await row(page, n).boundingBox()
  const height = page.viewportSize()!.height
  expect(box, `#${n} is drawn`).not.toBeNull()
  expect(box!.y, `#${n} is below the window's top`).toBeGreaterThanOrEqual(0)
  expect(box!.y + box!.height, `#${n} is above the window's bottom`).toBeLessThanOrEqual(height)
  // Brought clear of the bottom edge, not just into view.
  expect(box!.y, `#${n} sits in the upper half`).toBeLessThan(height / 2)
}

async function keepLine55(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(realRow(page)).toBeVisible()
  await page.getByRole('button', { name: 'Next page' }).first().click()
  await expect(row(page, 41)).toBeVisible()
  await row(page, 55).locator('.review-idx').click()
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => kept(page)).toContain('"lineNumber":55')
}

test('Full view reopens Review on the page and line it was left on, without loading page 1 first', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  await leave(page)
  const pages = countPages(page)
  await page.goto('/#/drama/3/review')
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect(pageNumber(page)).toHaveValue('2')
  await expect(row(page, 55)).toBeFocused()
  await expect(status(page)).toHaveText('Back where you stopped, on line 55.')
  await expectInView(page, 55)
  expect(pages.get(2) ?? 0).toBeGreaterThan(0)
  expect(pages.get(1) ?? 0, 'requests for page 1').toBe(0)
})

test('a link that names lines wins over the kept place and leaves it as it is', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  const before = await kept(page)
  await leave(page)
  await page.goto('/#/drama/3/review?focus=flagged')
  await expect(page.getByRole('radio', { name: /^Flagged/ })).toBeChecked()
  await expect(row(page, 12)).toHaveAttribute('aria-current', 'true')
  await expect(page.locator('.review-line')).toHaveCount(2)
  await expect(status(page)).toHaveText('')
  expect(await kept(page)).toBe(before)
  // ?focus=all names every line: All opens on its first page as it always has.
  await leave(page)
  await page.goto('/#/drama/3/review?focus=all')
  await expect(row(page, 12)).toHaveAttribute('aria-current', 'true')
  await expect(pageNumber(page)).toHaveValue('1')
  await expect(status(page)).toHaveText('')
  expect(await kept(page)).toBe(before)
})

test('a kept line whose id is gone reopens on the line that has its number now, with no error', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  await leave(page)
  // New ids for the same 100 lines, as when a title is transcribed again.
  seedPlaceLines()
  const reads = countWholeTitle(page)
  await page.goto('/#/drama/3/review')
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect(pageNumber(page)).toHaveValue('2')
  await expect(status(page)).toHaveText('Back where you stopped, on line 55.')
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(reads.n, 'reads of the whole title').toBe(1)
})

test.describe('Simple', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
  })

  test('a line kept under All waits for All: Need a look opens on its first card, and All on the kept line', async ({ page }) => {
    seedPlaceLines()
    await page.goto('/#/drama/3')
    await expect(queueRadio(page)).toBeChecked()
    await allRadio(page).check({ force: true })
    await expect(row(page, 1)).toBeVisible()
    await page.getByRole('button', { name: 'Next page' }).first().click()
    await expect(row(page, 41)).toBeVisible()
    await row(page, 55).locator('.review-idx').click()
    await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
    await expect.poll(() => kept(page)).toContain('"lines":{')
    await leave(page)

    await page.goto('/#/drama/3')
    await expect(queueRadio(page)).toBeChecked()
    await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#24', '#7', '#12'])
    await allRadio(page).check({ force: true })
    await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
    await expect(pageNumber(page)).toHaveValue('2')
    await expect(status(page)).toHaveText('Back where you stopped, on line 55.')
    await expectInView(page, 55)
  })

  test('the queue reopens on the card it was left on while that card is listed, else on its first', async ({ page }) => {
    const ids = seedPlaceLines()
    await page.goto('/#/drama/3/review')
    await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
    await row(page, 7).locator('.review-idx').click()
    await expect(row(page, 7)).toHaveAttribute('aria-current', 'true')
    await expect.poll(() => kept(page)).toContain('"queue":{')
    await leave(page)

    await page.goto('/#/drama/3/review')
    await expect(queueRadio(page)).toBeChecked()
    await expect(row(page, 7)).toHaveAttribute('aria-current', 'true')
    await expect(status(page)).toHaveText('Back where you stopped, on line 7.')
    await leave(page)

    // Checked meanwhile (another tab, another device): it has left the queue.
    const checked = await page.request.post(`/api/lines/dramas/3/lines/${ids[6]}/reviewed`, { data: { reviewed: true } })
    expect(checked.ok()).toBe(true)
    await page.goto('/#/drama/3/review')
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#24', '#12'])
    await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
    await expect(status(page)).toHaveText('')
  })
})

test('the line the page opens on by itself is not kept', async ({ page }) => {
  seedPlaceLines()
  await page.goto('/#/drama/3/review')
  await expect(realRow(page)).toBeVisible()
  await expect(row(page, 12)).toHaveAttribute('aria-current', 'true')
  await leave(page)
  expect(await kept(page)).toBeNull()
})

test('a glance at a filter or a search does not move the place in All', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  const before = await kept(page)
  // Each step waits for the line the list makes active by itself, which is when a wrong write would be made, and then
  // reads the store until it holds the same value.
  await page.getByRole('radio', { name: /^Flagged/ }).check({ force: true })
  await expect(page.locator('.review-line')).toHaveCount(2)
  await expect(row(page, 12)).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => kept(page)).toBe(before)
  await page.getByRole('radio', { name: /^All lines/ }).check({ force: true })
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => kept(page)).toBe(before)
  await page.getByRole('searchbox', { name: 'Search lines' }).fill('Line 77')
  await expect(page.locator('.review-line')).toHaveCount(1)
  await expect(row(page, 77)).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => kept(page)).toBe(before)
  await page.getByRole('button', { name: 'Clear search' }).click()
  await expect(row(page, 41)).toBeVisible()
  await expect.poll(() => kept(page)).toBe(before)
  await leave(page)
  await page.goto('/#/drama/3/review')
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect(pageNumber(page)).toHaveValue('2')
})

test('the sentence goes when the view it was said in is left', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  await leave(page)
  await page.goto('/#/drama/3/review')
  await expect(status(page)).toHaveText('Back where you stopped, on line 55.')
  await page.getByRole('radio', { name: /^Flagged/ }).check({ force: true })
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(2)
  // Well inside the eight seconds a status line stays by itself.
  await expect(status(page)).toHaveText('', { timeout: 2000 })
})

test('work in Need a look keeps the place in All, and each view reopens on its own line', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
  seedPlaceLines()
  await page.goto('/#/drama/3')
  await allRadio(page).check({ force: true })
  await expect(row(page, 1)).toBeVisible()
  await page.getByRole('button', { name: 'Next page' }).first().click()
  await expect(row(page, 41)).toBeVisible()
  await row(page, 55).locator('.review-idx').click()
  await expect.poll(() => kept(page)).toContain('"lines":{')
  const before = await kept(page)
  await queueRadio(page).check({ force: true })
  await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
  expect(await kept(page)).toBe(before)
  await row(page, 7).locator('.review-idx').click()
  await expect(row(page, 7)).toHaveAttribute('aria-current', 'true')
  await expect.poll(async () => JSON.parse((await kept(page)) ?? 'null'))
    .toMatchObject({ queue: { lineNumber: 7, page: 1 }, lines: { lineNumber: 55, page: 2 } })
  await leave(page)

  await page.goto('/#/drama/3')
  await expect(queueRadio(page)).toBeChecked()
  await expect(row(page, 7)).toHaveAttribute('aria-current', 'true')
  await allRadio(page).check({ force: true })
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect(pageNumber(page)).toHaveValue('2')
  await expect(status(page)).toHaveText('Back where you stopped, on line 55.')
  await queueRadio(page).check({ force: true })
  await expect(row(page, 7)).toHaveAttribute('aria-current', 'true')
  await allRadio(page).check({ force: true })
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
})

test('a ?find= link wins over the kept place and leaves it as it is', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  const before = await kept(page)
  await leave(page)
  await page.goto(`/#/drama/3/review?find=${encodeURIComponent('Line 77')}`)
  await expect(page.getByRole('searchbox', { name: 'Search lines' })).toHaveValue('Line 77')
  await expect(page.locator('.review-line')).toHaveCount(1)
  await expect(row(page, 77)).toHaveAttribute('aria-current', 'true')
  await settle(page)
  await expect(status(page)).toHaveText('')
  await expect(page.getByRole('searchbox', { name: 'Search lines' })).toHaveValue('Line 77')
  expect(await kept(page)).toBe(before)
})

test.describe('a restore still on its way gives way to the person', () => {
  test('a kept line looked for in the whole title is not jumped to after the person clicked another line', async ({ page }) => {
    seedPlaceLines()
    await keepLine55(page)
    await leave(page)
    seedPlaceLines()
    const release = await holdFirst(page, wholeTitle)
    const reads = countWholeTitle(page)
    await page.goto('/#/drama/3/review')
    await expect(realRow(page)).toBeVisible()
    await row(page, 60).locator('.review-idx').click()
    await expect(row(page, 60)).toHaveAttribute('aria-current', 'true')
    release()
    await settle(page)
    await expect(row(page, 60)).toHaveAttribute('aria-current', 'true')
    await expect(pageNumber(page)).toHaveValue('2')
    await expect(status(page)).toHaveText('')
    expect(reads.n, 'reads of the whole title').toBe(1)
  })

  test('a kept line found on another page is not moved to after the person clicked a line on this one', async ({ page }) => {
    seedPlaceLines()
    await keepLine55(page)
    await leave(page)
    // The kept line is now #95, on page 3, and page 2 shows other lines.
    shiftLines(40)
    const release = await holdFirst(page, listPage, 1)
    const third = page.waitForRequest((r) => new URL(r.url()).searchParams.get('page') === '3' && new URL(r.url()).searchParams.get('page_size') === '40')
    await page.goto('/#/drama/3/review')
    await expect(realRow(page)).toBeVisible()
    await third
    await row(page, 60).locator('.review-idx').click()
    await expect(row(page, 60)).toHaveAttribute('aria-current', 'true')
    release()
    await settle(page)
    await expect(row(page, 60)).toHaveAttribute('aria-current', 'true')
    await expect(pageNumber(page)).toHaveValue('2')
    await expect(status(page)).toHaveText('')
  })

  test('an edit opened on the line the page picked is not saved and left by a restore', async ({ page }) => {
    seedPlaceLines()
    await keepLine55(page)
    await leave(page)
    seedPlaceLines()
    const release = await holdFirst(page, wholeTitle)
    await page.goto('/#/drama/3/review')
    const active = page.locator('.review-line[aria-current="true"]')
    await expect(active).toHaveCount(1)
    const label = await active.locator('.review-idx').innerText()
    await active.getByTestId('line-en').click()
    await expect(active.getByLabel('Translation')).toBeVisible()
    release()
    await settle(page)
    await expect(active.locator('.review-idx')).toHaveText(label)
    await expect(active.getByLabel('Translation')).toBeVisible()
    await expect(pageNumber(page)).toHaveValue('2')
    await expect(status(page)).toHaveText('')
  })

  test('a ?find= link that arrives while the kept line is on its way wins', async ({ page }) => {
    seedPlaceLines()
    await keepLine55(page)
    await leave(page)
    const release = await holdFirst(page, listPage)
    await page.goto('/#/drama/3/review')
    await page.goto(`/#/drama/3/review?find=${encodeURIComponent('Line 77')}`)
    await expect(row(page, 77)).toBeVisible()
    release()
    await settle(page)
    await expect(page.getByRole('searchbox', { name: 'Search lines' })).toHaveValue('Line 77')
    await expect(page.locator('.review-line')).toHaveCount(1)
    await expect(row(page, 77)).toHaveAttribute('aria-current', 'true')
    await expect(status(page)).toHaveText('')
  })

  test('a view picked before the kept line arrives is not taken back to All', async ({ page }) => {
    seedPlaceLines()
    await keepLine55(page)
    await leave(page)
    const release = await holdFirst(page, listPage)
    await page.goto('/#/drama/3/review')
    await page.getByRole('radio', { name: /^Flagged/ }).check({ force: true })
    await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(2)
    release()
    await settle(page)
    await expect(page.getByRole('radio', { name: /^Flagged/ })).toBeChecked()
    await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(2)
    await expect(row(page, 12)).toHaveAttribute('aria-current', 'true')
    await expect(status(page)).toHaveText('')
  })

  test('a search typed before the kept line arrives is not wiped', async ({ page }) => {
    seedPlaceLines()
    await keepLine55(page)
    await leave(page)
    const release = await holdFirst(page, listPage)
    await page.goto('/#/drama/3/review')
    const box = page.getByRole('searchbox', { name: 'Search lines' })
    await box.fill('Line 77')
    await expect(row(page, 77)).toBeVisible()
    release()
    await settle(page)
    await expect(box).toHaveValue('Line 77')
    await expect(page.locator('.review-line')).toHaveCount(1)
    await expect(row(page, 77)).toHaveAttribute('aria-current', 'true')
    await expect(status(page)).toHaveText('')
  })

  test('a go-to that arrives while the first page loads wins over the kept line', async ({ page }) => {
    seedPlaceLines()
    await page.goto('/#/drama/3/review')
    await expect(realRow(page)).toBeVisible()
    await row(page, 20).locator('.review-idx').click()
    await expect.poll(() => kept(page)).toContain('"lineNumber":20')
    await leave(page)
    const release = await holdFirst(page, listPage)
    await page.goto('/#/drama/3/review')
    const wholeRead = page.waitForResponse((r) => new URL(r.url()).searchParams.get('only') === 'all' && Number(new URL(r.url()).searchParams.get('page_size')) > 40)
    const goTo = page.getByLabel('Go to line number')
    await goTo.fill('30')
    await goTo.press('Enter')
    await wholeRead
    // Past the point where the go-to has acted, before the page it is on has arrived.
    await page.waitForTimeout(300)
    release()
    await expect(row(page, 30)).toHaveAttribute('aria-current', 'true')
    await settle(page)
    await expect(row(page, 30)).toHaveAttribute('aria-current', 'true')
    await expect(status(page)).toHaveText('')
  })

  test('the main button still opens the first card, not the card kept in the queue', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    seedPlaceLines()
    await page.goto('/#/drama/3')
    await row(page, 7).locator('.review-idx').click()
    await expect.poll(() => kept(page)).toContain('"queue":{')
    await allRadio(page).check({ force: true })
    await expect(row(page, 1)).toBeVisible()
    await page.getByRole('button', { name: 'Check the 3 lines', exact: true }).click()
    await expect(queueRadio(page)).toBeChecked()
    await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
    await settle(page)
    await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
  })
})

// The row's box against the area it scrolls in: below the sticky toolbar and above that area's bottom edge.
async function rowClear(page: Page, n: number, area: 'window' | 'main'): Promise<boolean> {
  return row(page, n).evaluate((el, inMain) => {
    const r = el.getBoundingClientRect()
    const main = el.closest('.review-main')
    const toolbar = el.closest('.review-editor')?.querySelector('.review-toolbar')?.getBoundingClientRect()
    const bottom = inMain && main ? main.getBoundingClientRect().bottom + 1 : window.innerHeight
    return !!toolbar && r.top >= toolbar.bottom - 1 && r.bottom <= bottom
  }, area === 'main')
}

test('an audio title reopens with the kept row clear of the toolbar once the player is in', async ({ page }) => {
  seedCheckLines({ count: 100, audio: true })
  await keepLine55(page)
  await leave(page)
  await page.goto('/#/drama/3/review')
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect(page.locator('.review-toolbar').getByRole('button', { name: 'Play', exact: true }).first()).toBeVisible()
  await expect.poll(() => rowClear(page, 55, 'window'), { timeout: 5000 }).toBe(true)
})

test('a video title at 1920x1080 reopens with the kept row in view inside the side layout', async ({ page }) => {
  seedPlaceLines()
  test.skip(!addVideo(), 'OpenCV is not installed, so there is no test video')
  await page.setViewportSize({ width: 1920, height: 1080 })
  await keepLine55(page)
  await leave(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-editor.has-side')).toBeVisible()
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => rowClear(page, 55, 'main'), { timeout: 5000 }).toBe(true)
})
