import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page, Request } from '@playwright/test'

import { ME } from './authMocks'
import { seedCheckLines } from './compareSelectedHelpers'
import { expect, test } from './fixtures'

// The Watch page (#/watch/<id>) against the seeded API: drama 3 (Signal, a Korean video drama) with 24 lines two
// seconds apart and, unless a test says otherwise, a minute of sound. Lines, saves and the media stream are real.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const python = (code: string) =>
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })

test.afterAll(() => {
  // Other specs expect drama 3 as the server seeds it, with no sound and no lines, unless they add them.
  python('db.update_drama(3, audio_filename=None)\ndb.save_lines(3, [])')
})

// Headless Chromium cannot open a Document Picture-in-Picture window, so Pop out gets an iframe that acts as one
// (the same stub as review-popout.spec.ts).
const PIP_STUB = () => {
  Object.defineProperty(window, 'documentPictureInPicture', { configurable: true, value: {
    requestWindow: async () => {
      const frame = document.createElement('iframe')
      frame.id = 'pip-stub'
      frame.style.cssText = 'position:fixed;right:0;bottom:0;width:420px;height:300px;z-index:99'
      document.body.appendChild(frame)
      const win = frame.contentWindow as Window
      win.close = () => {
        frame.remove()
        win.dispatchEvent(new Event('pagehide'))
      }
      return win
    },
  } })
}

const list = (page: Page) => page.getByRole('region', { name: 'Lines', exact: true }).getByRole('list')
const row = (page: Page, n: number) => list(page).getByRole('listitem').nth(n - 1)
// The row's button carries aria-current when the title plays.
const rowButton = (page: Page, n: number) => row(page, n).getByRole('button')
const now = (page: Page) => page.getByRole('region', { name: 'Line being spoken' })
const time = (page: Page) => page.getByTestId('player-time')
const playButton = (page: Page) => page.getByRole('button', { name: 'Play', exact: true })
const pauseButton = (page: Page) => page.getByRole('button', { name: 'Pause', exact: true })
const field = (page: Page) => now(page).getByRole('textbox', { name: 'English' })
const audioTime = (page: Page) => page.locator('audio').evaluate((a) => (a as HTMLAudioElement).currentTime)
const isSave = (r: Request) => r.method() === 'POST' && /\/api\/lines\/dramas\/3\/lines\/\d+$/.test(new URL(r.url()).pathname)
// The keys answer once the title is known to play, which can be after the lines are listed. The page takes its
// newest key handler just after the draw that shows both, so a key sent the instant they show can still be missed:
// two frames later it cannot.
const twoFrames = (page: Page) => page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))
const ready = async (page: Page) => {
  await expect(list(page).getByRole('listitem')).toHaveCount(24)
  await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
  await twoFrames(page)
}
// Only the page's own whole-list read: the Library, which this test passes through, asks the same route for its
// Need a look count (page_size=1), at a moment that varies with how fast the page behind it settles.
const isLinesList = (r: Request) => {
  const url = new URL(r.url())
  return r.method() === 'GET' && /\/api\/review\/dramas\/3\/lines$/.test(url.pathname) && url.searchParams.get('only') === 'all'
}
const SAVE_URL = /\/api\/lines\/dramas\/3\/lines\/\d+$/
const PARKED = 'baihe.watch.draft.3'
const parked = (page: Page) => page.evaluate((key) => sessionStorage.getItem(key), PARKED)
const RESTORED = 'Your fix from before was not saved. It is back in the box.'
const CONFLICT = 'This line was changed while you were fixing it. What it says now is shown below, and your text is still in the box. Save again if it still needs your fix.'
// The title's lines as the server has them now.
const serverLines = async (page: Page) => (await (await page.request.get('/api/review/dramas/3/lines?page=1&page_size=200&only=all')).json()).lines as { id: number; idx: number; en: string }[]
const serverEnglish = async (page: Page, n: number) => (await serverLines(page)).find((l) => l.idx === n - 1)!.en
const leaveForLibrary = async (page: Page) => {
  await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Library', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Titles' })).toBeVisible()
}
// The audio's time at its next seeked event (null if none comes within 5 s), listened for from when this returns.
const nextSeek = async (page: Page) => {
  await page.locator('audio').evaluate((a: HTMLAudioElement) => {
    ;(window as unknown as { nextSeek: Promise<number | null> }).nextSeek = new Promise((done) => {
      const timer = setTimeout(() => done(null), 5000)
      a.addEventListener('seeked', () => {
        clearTimeout(timer)
        done(a.currentTime)
      }, { once: true })
    })
  })
  return () => page.evaluate(() => (window as unknown as { nextSeek: Promise<number | null> }).nextSeek)
}
const seekDone = (page: Page) => expect.poll(() => page.locator('audio').evaluate((a: HTMLAudioElement) => a.seeking)).toBe(false)
// Every save the page sends from here on, by its body.
const saves = (page: Page) => {
  const bodies: unknown[] = []
  page.on('request', (r) => {
    if (isSave(r)) bodies.push(r.postDataJSON())
  })
  return bodies
}
// Each row's top and bottom, and the list's, in one read.
const boxes = (page: Page) => list(page).evaluate((ol) => ({
  list: ol.getBoundingClientRect().toJSON() as DOMRect,
  rows: [...ol.children].map((li) => li.getBoundingClientRect().toJSON() as DOMRect),
  scrollTop: ol.scrollTop,
  maxScroll: ol.scrollHeight - ol.clientHeight,
}))
const currentRow = (page: Page) => list(page).evaluate((ol) => [...ol.children].findIndex((li) => li.classList.contains('is-current')) + 1)
// The audio's time every 250 ms for `ms`, read inside the page so the samples are evenly spaced.
const sampleTimes = (page: Page, ms: number) => page.locator('audio').evaluate((a: HTMLAudioElement, ms) => new Promise<number[]>((done) => {
  const out: number[] = []
  const timer = setInterval(() => {
    out.push(a.currentTime)
    if (out.length * 250 >= ms) {
      clearInterval(timer)
      done(out)
    }
  }, 250)
}), ms)
// From the next change of the spoken row, for 800 ms: the list's distance from its end and row `n`'s distance inside
// the list's top and bottom edges, read every frame inside the page, so a glide between two reads cannot be missed.
const afterNextLine = (page: Page, n: number) => list(page).evaluate((ol, n) => new Promise<{ gap: number; top: number; bottom: number }[]>((done) => {
  const spoken = () => [...ol.children].findIndex((li) => li.classList.contains('is-current'))
  const from = spoken()
  const out: { gap: number; top: number; bottom: number }[] = []
  let changedAt = 0
  const frame = (t: number) => {
    if (!changedAt && spoken() !== from) changedAt = t
    if (changedAt) {
      const box = ol.getBoundingClientRect()
      const row = ol.children[n - 1].getBoundingClientRect()
      out.push({ gap: ol.scrollHeight - ol.clientHeight - ol.scrollTop, top: row.top - box.top, bottom: box.bottom - row.bottom })
      if (t - changedAt > 800) return done(out)
    }
    requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
}), n)
// The spoken row's top is where following puts it, 0.3 down the list, within `timeout`.
const spokenRowAtFollowPlace = (page: Page, timeout: number) => expect(async () => {
  const spoken = await currentRow(page)
  const b = await boxes(page)
  expect((b.rows[spoken - 1].top - b.list.top) / b.list.height).toBeCloseTo(0.3, 1)
}).toPass({ timeout })

test.describe('with sound', () => {
  test.beforeEach(() => seedCheckLines({ warnings: false, audio: true }))

  test("a Library card's Watch link opens the Watch page with the player, the hint and the lines", async ({ page }) => {
    await page.goto('/')
    await page.getByRole('region', { name: 'Titles' }).getByRole('link', { name: 'Watch Signal', exact: true }).click()
    await expect(page).toHaveURL(/#\/watch\/3$/)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Signal')
    await expect(page.locator('.page-meta')).toHaveText('Video drama · Korean to English')
    const crumbs = page.getByRole('navigation', { name: 'Breadcrumb' })
    await expect(crumbs.getByRole('link', { name: 'Signal' })).toHaveAttribute('href', '#/drama/3')
    await expect(crumbs.locator('[aria-current="page"]')).toHaveText('Watch')
    await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
    await expect(page.getByRole('slider', { name: 'Seek' })).toBeVisible()
    // The player is the page here: it cannot be folded away.
    await expect(page.getByRole('button', { name: 'Hide player' })).toHaveCount(0)
    await expect(now(page)).toHaveText(/Press play\. The line being spoken shows here\./)
    await expect(list(page).getByRole('listitem')).toHaveCount(24)
    await expect(row(page, 3)).toContainText('#3 · 0:04.0')
    await expect(row(page, 3)).toContainText('第3句')
    await expect(row(page, 3)).toContainText('Line 3')

    await expect(page.getByRole('link', { name: 'Work on this title' })).toHaveAttribute('href', '#/drama/3')
    await page.locator('.watch-page').getByText('More', { exact: true }).click()
    await expect(page.getByRole('link', { name: 'Follow along with sound from another app' })).toHaveAttribute('href', '#/follow/3')
    await expect(page.getByRole('link', { name: 'Read the lines' })).toHaveAttribute('href', '#/read/3')
    await expect(page.getByRole('link', { name: 'Save subtitles' })).toHaveAttribute('href', '#/drama/3/export')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('link', { name: 'Save subtitles' })).toBeHidden()
  })

  test("clicking the third row moves the player's time to that line and marks the row", async ({ page }) => {
    await page.goto('/#/watch/3')
    await expect(list(page).getByRole('listitem')).toHaveCount(24)
    await row(page, 3).getByRole('button').click()
    // It plays on from there; pause straight away so the next line does not take over while this is checked.
    await page.getByRole('button', { name: 'Pause' }).click()
    await expect(time(page)).toHaveText(/^0:0[45]\.\d\d/)
    await expect(rowButton(page, 3)).toHaveAttribute('aria-current', 'true')
    await expect(row(page, 3)).toHaveClass('is-current')
    await expect(list(page).locator('[aria-current="true"]')).toHaveCount(1)
    await expect(now(page).getByTestId('watch-now-meta')).toHaveText('Line #3 · 0:04.0')
    await expect(now(page).getByTestId('watch-now-box')).toHaveText('Line 3')
    // Down and Up step between lines without playing.
    await page.keyboard.press('ArrowDown')
    await expect(rowButton(page, 4)).toHaveAttribute('aria-current', 'true')
    await expect(time(page)).toHaveText(/^0:06\.00/)
    await page.keyboard.press('ArrowUp')
    await page.keyboard.press('ArrowUp')
    await expect(rowButton(page, 2)).toHaveAttribute('aria-current', 'true')
    await expect(playButton(page)).toBeVisible()
  })

  test('E opens the fix field on the current line; Save sends it with the English that was seen', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await expect(rowButton(page, 2)).toHaveAttribute('aria-current', 'true')
    await page.keyboard.press('e')
    await expect(field(page)).toBeFocused()
    await expect(field(page)).toHaveValue('Line 2')
    await field(page).fill('Line 2, fixed by ear')
    await seekDone(page)
    const seeked = await nextSeek(page)
    const sent = page.waitForRequest(isSave)
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    expect((await sent).postDataJSON()).toEqual({ en: 'Line 2, fixed by ear', expected: { en: 'Line 2' } })
    await expect(row(page, 2)).toContainText('Line 2, fixed by ear')
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    await expect(field(page)).toHaveCount(0)
    // The sound goes on from the line's start (line 2 starts at 0:02), read at the seek itself, however slow the run.
    await expect(pauseButton(page)).toBeVisible()
    const at = await seeked()
    expect(at).not.toBeNull()
    expect(Math.abs(at! - 2)).toBeLessThan(0.05)
    await pauseButton(page).click()

    // Esc cancels without sending, also with focus on a button of the fix rather than in the box.
    const sentAfter = saves(page)
    await page.keyboard.press('e')
    await field(page).fill('Not wanted')
    await now(page).getByRole('button', { name: 'Save and play' }).focus()
    await page.keyboard.press('Escape')
    await expect(field(page)).toHaveCount(0)
    await expect(now(page).getByRole('button', { name: 'Fix Line' })).toBeFocused()
    await expect(row(page, 2)).toContainText('Line 2, fixed by ear')
    expect(sentAfter).toEqual([])
  })

  test('Space pauses and plays again after Save and play, and after a click on a row, instead of pressing a button', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, by ear')
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    await expect(pauseButton(page)).toBeVisible()
    await page.keyboard.press(' ')
    await expect(playButton(page)).toBeVisible()
    await expect(field(page)).toHaveCount(0)
    await page.keyboard.press(' ')
    await expect(pauseButton(page)).toBeVisible()

    // A row pressed with the pointer plays from its start; the next Space pauses there rather than starting it over.
    await rowButton(page, 3).click()
    await expect(time(page)).toHaveText(/^0:0(4\.[5-9]|[5-9]\.)/)
    await page.keyboard.press(' ')
    await expect(playButton(page)).toBeVisible()
    expect(await audioTime(page)).toBeGreaterThanOrEqual(4.5)
  })

  test('Alt+Space plays or pauses from a button and from the fix box, and L turns Loop line on or off', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    // From a button that Space would press: Alt+Space plays and pauses, and does not press it. (On the Play button a
    // press would also play, so the key's own handling would not show.)
    const loop = page.getByRole('switch', { name: 'Loop line' })
    await loop.focus()
    await page.keyboard.press('Alt+Space')
    await expect(pauseButton(page)).toBeVisible()
    await page.keyboard.press('Alt+Space')
    await expect(playButton(page)).toBeVisible()
    await expect(loop).toHaveAttribute('aria-checked', 'false')

    await page.keyboard.press('e')
    await expect(field(page)).toBeFocused()
    const typed = await field(page).inputValue()
    await page.keyboard.press('Alt+Space')
    await expect(pauseButton(page)).toBeVisible()
    await page.keyboard.press('Alt+Space')
    await expect(playButton(page)).toBeVisible()
    await expect(field(page)).toHaveValue(typed)
    await page.keyboard.press('Escape')

    await expect(loop).toHaveAttribute('aria-checked', 'false')
    await page.keyboard.press('l')
    await expect(loop).toHaveAttribute('aria-checked', 'true')
    await page.keyboard.press('l')
    await expect(loop).toHaveAttribute('aria-checked', 'false')
    // In the fix box, L is a letter.
    await page.keyboard.press('e')
    await page.keyboard.press('End')
    await page.keyboard.press('l')
    await expect(field(page)).toHaveValue(`${typed}l`)
    await expect(loop).toHaveAttribute('aria-checked', 'false')
  })

  test('G, X and M mark the line being spoken, pressing again clears it, and N and P jump between marked or flagged lines', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await expect(rowButton(page, 2)).toHaveAttribute('aria-current', 'true')

    await page.keyboard.press('x')
    await expect(now(page).getByTestId('line-mark')).toContainText('Wrong')
    await expect(row(page, 2).getByTestId('line-mark')).toContainText('Wrong')
    await expect(now(page).getByRole('status')).toHaveText('Marked wrong.')
    await expect(now(page).getByRole('button', { name: /Wrong/ })).toHaveAttribute('aria-pressed', 'true')
    await page.keyboard.press('x')
    await expect(now(page).getByTestId('line-mark')).toHaveCount(0)
    await expect(now(page).getByRole('status')).toHaveText('Mark cleared.')

    // Good checks the line, Mixed takes the check off and marks it, and Good again clears that mark.
    await page.keyboard.press('g')
    await expect(now(page).getByTestId('watch-now-checked')).toBeVisible()
    await expect(now(page).getByRole('status')).toHaveText('Marked as looks right.')
    await page.keyboard.press('m')
    await expect(now(page).getByTestId('line-mark')).toContainText('Mixed')
    await expect(now(page).getByTestId('watch-now-checked')).toHaveCount(0)
    await expect(now(page).getByRole('status')).toHaveText('Marked mixed.')
    await page.keyboard.press('g')
    await expect(now(page).getByTestId('line-mark')).toHaveCount(0)
    await expect(now(page).getByTestId('watch-now-checked')).toBeVisible()
    await expect(now(page).getByRole('status')).toHaveText('Marked as looks right.')
    // Back to wrong for the jump below.
    await page.keyboard.press('x')
    await expect(now(page).getByTestId('line-mark')).toContainText('Wrong')

    for (let n = 0; n < 4; n++) await page.keyboard.press('ArrowDown')
    await expect(rowButton(page, 6)).toHaveAttribute('aria-current', 'true')
    await page.keyboard.press('m')
    await expect(row(page, 6).getByTestId('line-mark')).toContainText('Mixed')

    await page.keyboard.press('p')
    await expect(rowButton(page, 2)).toHaveAttribute('aria-current', 'true')
    await page.keyboard.press('p')
    await expect(now(page).getByRole('status')).toHaveText('No marked or flagged line before this one.')
    await now(page).getByRole('button', { name: /Next Flag/ }).click()
    await expect(rowButton(page, 6)).toHaveAttribute('aria-current', 'true')
    await page.keyboard.press('n')
    await expect(now(page).getByRole('status')).toHaveText('No marked or flagged line after this one.')
  })

  test('a mark made after a saved fix says so, instead of the old Saved.', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, fixed by ear')
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    await page.keyboard.press('x')
    await expect(now(page).getByRole('status')).toHaveText('Marked wrong.')
  })

  test('a person who may not edit lines has no mark buttons and no mark keys, but can jump to marked lines', async ({ page }) => {
    await page.route('**/api/auth/me', (r) => r.fulfill({ json: { ...ME.signedIn, permissions: ['library.read'] } }))
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await expect(now(page).getByRole('button', { name: /Wrong/ })).toHaveCount(0)
    const saved = saves(page)
    await page.keyboard.press('x')
    await expect(now(page).getByTestId('line-mark')).toHaveCount(0)
    expect(saved).toEqual([])
    await expect(now(page).getByRole('button', { name: /Next Flag/ })).toBeVisible()
  })

  test('a person who may not edit lines gets no box to press and no Fix Line, and E does nothing', async ({ page }) => {
    await page.route('**/api/auth/me', (r) => r.fulfill({ json: { ...ME.signedIn, permissions: ['library.read'] } }))
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await expect(now(page).getByTestId('watch-now-en')).toHaveText('Line 1')
    await expect(now(page).getByTestId('watch-now-box')).toHaveCount(0)
    await expect(now(page).getByRole('button', { name: 'Fix Line' })).toHaveCount(0)
    await page.keyboard.press('e')
    await expect(now(page).getByRole('textbox')).toHaveCount(0)
    await expect(now(page).getByRole('button', { name: 'Listen', exact: true })).toBeVisible()
  })

  test('leaving inside the app with a fix typed saves it, with the English that was seen', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, saved on the way out')
    const sent = saves(page)
    const answered = page.waitForResponse((r) => isSave(r.request()))
    await page.getByRole('link', { name: 'Work on this title' }).click()
    await expect(page).toHaveURL(/#\/drama\/3/)
    expect((await answered).ok()).toBe(true)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(row(page, 2)).toContainText('Line 2, saved on the way out')
    await expect(field(page)).toHaveCount(0)
    await expect(now(page).getByRole('status')).toHaveText('')
    expect(sent).toEqual([{ en: 'Line 2, saved on the way out', expected: { en: 'Line 2' } }])
    expect(await page.evaluate(() => sessionStorage.getItem('baihe.watch.draft.3'))).toBeNull()
  })

  test('a fix whose save on leaving failed is back in the box on the next visit, and Save sends it', async ({ page }) => {
    await page.route(/\/api\/lines\/dramas\/3\/lines\/\d+$/, (r) => r.request().method() === 'POST'
      ? r.fulfill({ status: 500, json: { error: { code: 'internal_error', message: 'boom' } } })
      : r.fallback())
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, kept for later')
    const refused = page.waitForResponse((r) => isSave(r.request()))
    await page.getByRole('link', { name: 'Work on this title' }).click()
    expect((await refused).status()).toBe(500)
    await page.unroute(/\/api\/lines\/dramas\/3\/lines\/\d+$/)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(field(page)).toHaveValue('Line 2, kept for later')
    await expect(now(page).getByTestId('watch-now-meta')).toHaveText('Line #2 · 0:02.0')
    await expect(now(page).getByRole('status')).toHaveText('Your fix from before was not saved. It is back in the box.')
    await expect(playButton(page)).toBeVisible()
    await expect(row(page, 2)).toContainText('Line 2')
    await expect(row(page, 2)).not.toContainText('kept for later')

    const sent = page.waitForRequest(isSave)
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    expect((await sent).postDataJSON()).toEqual({ en: 'Line 2, kept for later', expected: { en: 'Line 2' } })
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    await expect(row(page, 2)).toContainText('Line 2, kept for later')
  })

  test('a fix brought back and then cancelled is dropped, and the sentence about it goes too', async ({ page }) => {
    await page.route(/\/api\/lines\/dramas\/3\/lines\/\d+$/, (r) => r.request().method() === 'POST'
      ? r.fulfill({ status: 500, json: { error: { code: 'internal_error', message: 'boom' } } })
      : r.fallback())
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, not wanted after all')
    const refused = page.waitForResponse((r) => isSave(r.request()))
    await page.getByRole('link', { name: 'Work on this title' }).click()
    expect((await refused).status()).toBe(500)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(field(page)).toHaveValue('Line 2, not wanted after all')
    await expect(now(page).getByRole('status')).toHaveText('Your fix from before was not saved. It is back in the box.')
    await now(page).getByRole('button', { name: 'Cancel' }).click()
    await expect(field(page)).toHaveCount(0)
    await expect(now(page).getByRole('status')).toHaveText('')
    await expect(row(page, 2)).not.toContainText('not wanted')
    expect(await page.evaluate(() => sessionStorage.getItem('baihe.watch.draft.3'))).toBeNull()
  })

  test('a fix whose save on leaving went through, though its answer was lost, is not offered back', async ({ page }) => {
    // The server saves the line; the page never hears so.
    await page.route(/\/api\/lines\/dramas\/3\/lines\/\d+$/, async (r) => {
      if (r.request().method() !== 'POST') return r.fallback()
      await r.fetch()
      await r.abort()
    })
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, saved unheard')
    const lost = page.waitForEvent('requestfailed', isSave)
    await page.getByRole('link', { name: 'Work on this title' }).click()
    await lost
    expect(await page.evaluate(() => sessionStorage.getItem('baihe.watch.draft.3'))).not.toBeNull()
    await page.unroute(/\/api\/lines\/dramas\/3\/lines\/\d+$/)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(row(page, 2)).toContainText('Line 2, saved unheard')
    await expect(field(page)).toHaveCount(0)
    await expect(now(page).getByRole('status')).toHaveText('')
    expect(await page.evaluate(() => sessionStorage.getItem('baihe.watch.draft.3'))).toBeNull()
  })

  test('closing the tab with a fix typed asks first, and not when the box holds what the line says', async ({ context }) => {
    // A tab of its own, since this test closes it.
    const tab = await context.newPage()
    await tab.goto('/#/watch/3')
    await ready(tab)
    await tab.keyboard.press('ArrowDown')
    await tab.keyboard.press('e')
    await field(tab).fill('Mine')
    const asked = tab.waitForEvent('dialog')
    await tab.close({ runBeforeUnload: true })
    const dialog = await asked
    expect(dialog.type()).toBe('beforeunload')
    await dialog.dismiss()
    expect(tab.isClosed()).toBe(false)

    await field(tab).fill('Line 1')
    let askedAgain = false
    tab.on('dialog', (d) => {
      askedAgain = true
      void d.accept()
    })
    const closed = tab.waitForEvent('close')
    await tab.close({ runBeforeUnload: true })
    await closed
    expect(askedAgain).toBe(false)
  })

  test('a fix refused because the line changed shows what it says now, keeps the draft, and saves over the new text', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await now(page).getByRole('button', { name: 'Fix Line' }).click()
    const field = now(page).getByRole('textbox', { name: 'English' })
    await field.fill('Mine')
    // Someone else changes the line while the field is open, so the save is refused for real.
    python(`[l] = [l for l in db.load_line_objects(3) if l.idx == 1]\nassert db.update_line_fields_if(3, l.id, {'en': 'Theirs'}, {'en': 'Line 2'})`)
    const refused = page.waitForResponse((r) => r.request().method() === 'POST' && /\/api\/lines\/dramas\/3\/lines\/\d+$/.test(new URL(r.url()).pathname))
    await field.press('Control+Enter')
    expect((await refused).status()).toBe(409)
    await expect(now(page).getByRole('alert')).toHaveText('This line was changed while you were fixing it. What it says now is shown below, and your text is still in the box. Save again if it still needs your fix.')
    await expect(now(page).locator('.watch-now-theirs')).toHaveText('It now saysTheirs')
    await expect(now(page).getByText('Theirs', { exact: true })).toHaveAttribute('lang', 'en')
    await expect(field).toHaveValue('Mine')
    await expect(row(page, 2)).toContainText('Theirs')

    const sent = page.waitForRequest((r) => r.method() === 'POST' && /\/api\/lines\/dramas\/3\/lines\/\d+$/.test(new URL(r.url()).pathname))
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    expect((await sent).postDataJSON()).toEqual({ en: 'Mine', expected: { en: 'Theirs' } })
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    await expect(row(page, 2)).toContainText('Mine')
    await expect(now(page).getByText('It now says')).toHaveCount(0)
  })

  test('a refused fix whose new text cannot be read back says so and shows nothing below the alert', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await now(page).getByRole('button', { name: 'Fix Line' }).click()
    const field = now(page).getByRole('textbox', { name: 'English' })
    await field.fill('Mine')
    await page.route(/\/api\/lines\/dramas\/3\/lines\/\d+$/, (r) => r.request().method() === 'POST'
      ? r.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'The line changed.' } } })
      : r.fallback())
    await page.route(/\/api\/review\/dramas\/3\/lines\?/, (r) => r.fulfill({ status: 503, json: { error: { code: 'unavailable', message: 'Busy.' } } }))
    await field.press('Control+Enter')
    await expect(now(page).getByRole('alert')).toHaveText('This line was changed while you were fixing it, and its new text could not be loaded. Your text is still in the box. Try Save again in a moment.')
    await expect(now(page).getByText('It now says')).toHaveCount(0)
    await expect(field).toHaveValue('Mine')
    // Refused again from the button, which is disabled while saving: focus comes back to the box.
    const again = page.waitForResponse((r) => isSave(r.request()))
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    expect((await again).status()).toBe(409)
    await expect(now(page).getByRole('button', { name: 'Save and play' })).toBeEnabled()
    await expect(field).toBeFocused()
  })

  test('a sound-only title shows its line in the card only: no caption strip, no Subtitles choice, no Go to line', async ({ page }) => {
    await page.addInitScript(PIP_STUB)
    await page.goto('/#/watch/3')
    await expect(list(page).getByRole('listitem')).toHaveCount(24)
    const player = page.getByRole('group', { name: 'Player' })
    await expect(player.getByRole('slider', { name: 'Seek' })).toBeVisible()
    await expect(player.getByLabel('Speed')).toBeVisible()
    await row(page, 3).getByRole('button').click()
    await page.getByRole('button', { name: 'Pause' }).click()
    await expect(now(page).getByTestId('watch-now-box')).toHaveText('Line 3')
    await expect(page.getByTestId('player-caption')).toHaveCount(0)
    await expect(page.getByLabel('Subtitles')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /^Go to line/ })).toHaveCount(0)

    // In the pop-out window the strip is the only text, so it is there, with its choice.
    await player.getByRole('button', { name: 'Pop out' }).click()
    const frame = page.frameLocator('#pip-stub')
    await expect(frame.getByLabel('Subtitles')).toBeVisible()
    await expect(frame.getByTestId('player-caption')).toBeVisible()
    await frame.getByLabel('Jump to time').fill('0:04.5')
    await frame.getByRole('button', { name: 'Jump' }).click()
    await expect(frame.getByTestId('player-caption')).toHaveText('Line 3')
    await page.evaluate(() => (document.getElementById('pip-stub') as HTMLIFrameElement).contentWindow?.close())
    await expect(player.getByRole('slider', { name: 'Seek' })).toBeVisible()
    await expect(page.getByTestId('player-caption')).toHaveCount(0)
  })

  test('without the pop-out window, the floating box keeps the caption strip and the Subtitles choice', async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, 'documentPictureInPicture', { configurable: true, value: undefined })
    })
    await page.goto('/#/watch/3')
    await expect(list(page).getByRole('listitem')).toHaveCount(24)
    await page.getByRole('group', { name: 'Player' }).getByRole('button', { name: 'Pop out' }).click()
    const floatBox = page.getByRole('group', { name: 'Floating player' })
    await expect(floatBox.getByTestId('player-caption')).toBeVisible()
    await expect(floatBox.getByLabel('Subtitles')).toBeVisible()
    await floatBox.getByRole('button', { name: 'Return player' }).click()
    await expect(page.getByTestId('player-caption')).toHaveCount(0)
    await expect(page.getByLabel('Subtitles')).toHaveCount(0)
  })

  test('the seek bar takes the whole row, with the tools under it', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/#/watch/3')
    // Read once the player knows its length, and all in one go, so a row still laying out cannot be measured.
    await expect(page.getByRole('slider', { name: 'Seek' })).toBeEnabled()
    const { seek, controls, speed } = await page.evaluate(() => {
      const box = (el: Element | null | undefined) => el!.getBoundingClientRect().toJSON() as DOMRect
      const speedLabel = [...document.querySelectorAll('.watch-player label.review-subs')].find((l) => l.textContent?.startsWith('Speed'))
      return {
        seek: box(document.querySelector('.watch-player .review-seek')),
        controls: box(document.querySelector('.watch-player .review-player-controls')),
        speed: box(speedLabel?.querySelector('select')),
      }
    })
    expect(seek.width).toBeGreaterThan(controls.width - 2)
    expect(speed.y).toBeGreaterThan(seek.y + seek.height - 1)
    // Beside the player, scrolling past the list's end does not move the page under it.
    expect(await list(page).evaluate((ol) => getComputedStyle(ol).overscrollBehaviorY)).toBe('contain')
  })

  test('a media question that fails offers Retry instead of saying there is nothing to play', async ({ page }) => {
    await page.route(/\/api\/reader\/dramas\/3\/media$/, (r) => r.fulfill({ status: 500, json: { error: { code: 'internal_error', message: 'boom' } } }))
    await page.goto('/#/watch/3')
    const banner = page.locator('.retry-banner')
    await expect(banner).toBeVisible()
    await expect(page.getByText(/no sound or video/)).toHaveCount(0)
    await expect(page.getByRole('group', { name: 'Player' })).toHaveCount(0)
    await page.unroute(/\/api\/reader\/dramas\/3\/media$/)
    await banner.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
    await expect(banner).toHaveCount(0)
    await expect(page.getByText(/no sound or video/)).toHaveCount(0)
  })

  test('a saved position comes back paused, and Start from the beginning returns to 0:00', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.watch.at.3', '20'))
    await page.goto('/#/watch/3')
    await expect(page.getByText('Back where you stopped, at 0:20.')).toBeVisible()
    await expect(time(page)).toHaveText(/^0:20\.00/)
    await expect(rowButton(page, 11)).toHaveAttribute('aria-current', 'true')
    await expect(playButton(page)).toBeVisible()
    await page.getByRole('button', { name: 'Start from the beginning' }).click()
    await expect(time(page)).toHaveText(/^0:00\.00/)
    await expect(page.getByText('Back where you stopped')).toHaveCount(0)
    // The first line starts at 0:00, so it is the one shown.
    await expect(now(page).getByTestId('watch-now-meta')).toHaveText('Line #1 · 0:00.0')
  })

  test('the place sentence goes as soon as the person moves the playhead, and stays through a wait', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.watch.at.3', '20'))
    await page.goto('/#/watch/3')
    await ready(page)
    const said = page.getByText('Back where you stopped, at 0:20.')
    await expect(said).toBeVisible()
    await page.waitForTimeout(600)
    await expect(said).toBeVisible()
    await page.keyboard.press('ArrowDown')
    await expect(time(page)).toHaveText(/^0:22\.00/)
    await expect(said).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Start from the beginning' })).toHaveCount(0)
  })

  test('a drag of the seek bar while paused takes the place sentence away too', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.watch.at.3', '20'))
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(page.getByText('Back where you stopped, at 0:20.')).toBeVisible()
    const seek = (await page.getByRole('slider', { name: 'Seek' }).boundingBox())!
    await page.mouse.click(seek.x + seek.width * 0.8, seek.y + seek.height / 2)
    await expect(playButton(page)).toBeVisible()
    await expect(page.getByText('Back where you stopped')).toHaveCount(0)
  })

  test('a saved place reached before the lines are in still marks its line, and Down goes on from there', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.watch.at.3', '20'))
    let release!: () => void
    const held = new Promise<void>((r) => { release = r })
    await page.route(/\/api\/review\/dramas\/3\/lines\?/, async (r) => {
      await held
      await r.fallback()
    })
    await page.goto('/#/watch/3')
    await expect(time(page)).toHaveText(/^0:20\.00/)
    await expect(page.getByText('Back where you stopped, at 0:20.')).toBeVisible()
    // The time reads 0:20.00 from the seek's start; the seek must have landed (seeked) before the lines come in.
    await expect.poll(() => page.locator('audio').evaluate((a: HTMLAudioElement) => !a.seeking && a.currentTime === 20)).toBe(true)
    release()
    await expect(rowButton(page, 11)).toHaveAttribute('aria-current', 'true')
    await expect(now(page).getByTestId('watch-now-meta')).toHaveText('Line #11 · 0:20.0')
    await expect(now(page).getByTestId('watch-now-box')).toHaveText('Line 11')
    await twoFrames(page)
    await page.keyboard.press('ArrowDown')
    await expect(rowButton(page, 12)).toHaveAttribute('aria-current', 'true')
    await expect(time(page)).toHaveText(/^0:22\.00/)
  })

  test('a pause keeps the place in this browser', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await rowButton(page, 6).click()
    await expect(time(page)).toHaveText(/^0:1[0-9]\./)
    await pauseButton(page).click()
    await expect(playButton(page)).toBeVisible()
    const at = await audioTime(page)
    expect(at).toBeGreaterThanOrEqual(10)
    expect(Number(await page.evaluate(() => localStorage.getItem('baihe.pref.watch.at.3')))).toBeCloseTo(at, 3)
  })

  test('leaving before the sound has loaded leaves the saved place as it was', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.watch.at.3', '20'))
    // The sound never answers, so the saved place is never read back.
    await page.route(/\/api\/media\/dramas\/3\/audio$/, () => new Promise<void>(() => {}))
    await page.goto('/#/watch/3')
    await ready(page)
    await page.goto('/#/library')
    await expect(page.getByRole('region', { name: 'Titles' })).toBeVisible()
    expect(await page.evaluate(() => localStorage.getItem('baihe.pref.watch.at.3'))).toBe('20')
  })

  test('the list follows the sound with the coming lines in view, waits while it is scrolled by hand, and comes back on a press', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/#/watch/3')
    await ready(page)
    // In the middle of the title, the spoken row sits near the top of the list with the coming lines under it. (Reached
    // by Jump rather than a click on the row, which Playwright would first scroll into view itself.)
    await page.getByLabel('Jump to time').fill('0:22')
    await page.getByRole('button', { name: 'Jump', exact: true }).click()
    await expect(rowButton(page, 12)).toHaveAttribute('aria-current', 'true')
    await expect(async () => {
      const b = await boxes(page)
      expect(b.rows[11].top).toBeGreaterThanOrEqual(b.list.top)
      expect(b.rows[13].bottom).toBeLessThanOrEqual(b.list.bottom)
      expect((b.rows[11].top - b.list.top) / b.list.height).toBeCloseTo(0.3, 1)
    }).toPass()

    // Scrolled to its end by hand while the sound plays, the list stays there when the next line starts.
    await rowButton(page, 2).click()
    await list(page).hover()
    await page.mouse.wheel(0, 5000)
    await expect.poll(async () => { const b = await boxes(page); return b.maxScroll - b.scrollTop }).toBeLessThan(1)
    const before = await currentRow(page)
    await expect.poll(() => currentRow(page)).toBeGreaterThan(before)
    const b = await boxes(page)
    expect(b.maxScroll - b.scrollTop).toBeLessThan(1)

    // A press on a row brings it back at once, straight after the wheel: within 3 s, well inside the five seconds that
    // following waits after a scroll by hand, so that wait running out cannot be what brings it back.
    await page.mouse.wheel(0, 5000)
    await rowButton(page, 17).click()
    await spokenRowAtFollowPlace(page, 3000)
    // And it follows on from there: row 17 was scrolled into view for the press, and the end of that scroll comes
    // during the page's own glide, which must not then count as the person's.
    const pressed = await currentRow(page)
    await expect.poll(() => currentRow(page)).toBeGreaterThan(pressed)
    await spokenRowAtFollowPlace(page, 1500)
  })

  test('following the sound scrolls only the list, never the page', async ({ page }) => {
    // The list under the player, partly below the screen, so a scrollIntoView would move the page.
    await page.setViewportSize({ width: 900, height: 700 })
    await page.goto('/#/watch/3')
    await ready(page)
    await list(page).evaluate((ol) => window.scrollTo(0, ol.getBoundingClientRect().top + window.scrollY - 200))
    const y = await page.evaluate(() => window.scrollY)
    expect(y).toBeGreaterThan(0)
    await list(page).evaluate((ol) => ol.querySelectorAll('button')[7].click())
    // On past a line change, however slowly the sound starts.
    await expect.poll(() => currentRow(page), { timeout: 15_000 }).toBeGreaterThan(8)
    await expect.poll(async () => (await boxes(page)).scrollTop).toBeGreaterThan(0)
    expect(await page.evaluate(() => window.scrollY)).toBe(y)
  })

  test('a refused fix is not saved by leaving: it comes back refused, against what the line says then, and Save sends it over that', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Mine, kept through a refusal')
    // Someone else changes the line through the API while the box is open, so Save is refused for real.
    const id = (await serverLines(page)).find((l) => l.idx === 1)!.id
    const changed = await page.request.post(`/api/lines/dramas/3/lines/${id}`, { data: { en: 'Theirs', expected: { en: 'Line 2' } }, headers: { 'X-Baihe-Local': '1' } })
    expect(changed.ok()).toBe(true)
    const sent = saves(page)
    const refused = page.waitForResponse((r) => isSave(r.request()))
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    expect((await refused).status()).toBe(409)
    await expect(now(page).locator('.watch-now-theirs')).toHaveText('It now saysTheirs')

    await leaveForLibrary(page)
    expect(sent).toHaveLength(1)
    expect(await serverEnglish(page, 2)).toBe('Theirs')
    expect(JSON.parse((await parked(page))!)).toMatchObject({ draft: 'Mine, kept through a refusal', refused: true })

    await page.goto('/#/watch/3')
    await ready(page)
    await expect(field(page)).toHaveValue('Mine, kept through a refusal')
    await expect(now(page).getByRole('alert')).toHaveText(CONFLICT)
    await expect(now(page).locator('.watch-now-theirs')).toHaveText('It now saysTheirs')
    await expect(now(page).getByRole('status')).toHaveText(RESTORED)
    expect(sent).toHaveLength(1)
    const again = page.waitForRequest(isSave)
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    expect((await again).postDataJSON()).toEqual({ en: 'Mine, kept through a refusal', expected: { en: 'Theirs' } })
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    expect(await serverEnglish(page, 2)).toBe('Mine, kept through a refusal')
  })

  test('a fix brought back and then saved as the line already says closes without the sentence about it', async ({ page }) => {
    await page.route(SAVE_URL, (r) => r.request().method() === 'POST'
      ? r.fulfill({ status: 500, json: { error: { code: 'internal_error', message: 'boom' } } })
      : r.fallback())
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, then thought better of')
    const refused = page.waitForResponse((r) => isSave(r.request()))
    await leaveForLibrary(page)
    expect((await refused).status()).toBe(500)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(now(page).getByRole('status')).toHaveText(RESTORED)
    const sent = saves(page)
    await field(page).fill('Line 2')
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    await expect(field(page)).toHaveCount(0)
    await expect(now(page).getByRole('status')).toHaveText('')
    expect(sent).toEqual([])
  })

  test('an unsaved edit whose line is gone says so on the page', async ({ page }) => {
    await page.addInitScript((key) => sessionStorage.setItem(key, JSON.stringify({ lineId: 987654, seen: 'Gone', draft: 'Mine' })), PARKED)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(now(page).getByRole('status')).toHaveText('An unsaved edit could not be brought back: its line no longer exists.')
    await expect(field(page)).toHaveCount(0)
    expect(await parked(page)).toBeNull()
  })

  test('leaving while a Save is still out sends no second save', async ({ page }) => {
    let answer!: () => void
    const held = new Promise<void>((r) => { answer = r })
    await page.route(SAVE_URL, async (r) => {
      if (r.request().method() !== 'POST') return r.fallback()
      await held
      await r.fallback()
    })
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, saved once')
    const sent = saves(page)
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    await expect.poll(() => sent.length).toBe(1)
    await leaveForLibrary(page)
    const answered = page.waitForResponse((r) => isSave(r.request()))
    answer()
    expect((await answered).ok()).toBe(true)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(row(page, 2)).toContainText('Line 2, saved once')
    await expect(field(page)).toHaveCount(0)
    expect(sent).toHaveLength(1)
  })

  test('leaving with the box open and nothing changed sends nothing', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await expect(field(page)).toHaveValue('Line 1')
    const sent = saves(page)
    await leaveForLibrary(page)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(field(page)).toHaveCount(0)
    expect(sent).toEqual([])
    expect(await parked(page)).toBeNull()
  })

  test('opening the page again while the save on leaving is still out waits for it before listing the lines, and does not offer the fix back', async ({ page }) => {
    let answer!: () => void
    const held = new Promise<void>((r) => { answer = r })
    await page.route(SAVE_URL, async (r) => {
      if (r.request().method() !== 'POST') return r.fallback()
      await held
      await r.fallback()
    })
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, saved while away')
    const order: string[] = []
    page.on('request', (r) => {
      if (isSave(r)) order.push('save sent')
      else if (isLinesList(r)) order.push('lines asked')
    })
    page.on('response', (r) => {
      if (isSave(r.request())) order.push('save answered')
    })
    await leaveForLibrary(page)
    await expect.poll(() => order).toEqual(['save sent'])
    // Back in the same tab and the same page load, so the save is still out.
    await page.goBack()
    await expect(page).toHaveURL(/#\/watch\/3$/)
    await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Lines', exact: true })).toContainText('Loading…')
    await twoFrames(page)
    expect(order).toEqual(['save sent'])
    answer()
    await ready(page)
    expect(order).toEqual(['save sent', 'save answered', 'lines asked'])
    await expect(row(page, 2)).toContainText('Line 2, saved while away')
    await expect(field(page)).toHaveCount(0)
    await expect(now(page).getByRole('status')).toHaveText('')
  })

  test('Esc closes the fix with focus on Cancel and on Listen', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    const sent = saves(page)
    for (const name of ['Cancel', 'Listen']) {
      await page.keyboard.press('e')
      await field(page).fill('Not wanted')
      await now(page).getByRole('button', { name }).focus()
      await page.keyboard.press('Escape')
      await expect(field(page)).toHaveCount(0)
      await expect(now(page).getByTestId('watch-now-box')).toHaveText('Line 1')
    }
    expect(sent).toEqual([])
  })

  test('after a save from the keyboard the card it leaves focus on shows it', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 1, saved from the keys')
    await page.keyboard.press('Control+Enter')
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    await expect(now(page)).toBeFocused()
    expect(await now(page).evaluate((el) => getComputedStyle(el).outlineStyle)).not.toBe('none')
  })

  test('E opens the fix with focus on the seek bar', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    const seek = page.getByRole('slider', { name: 'Seek' })
    await expect(seek).toBeEnabled()
    await seek.focus()
    await page.keyboard.press('e')
    await expect(field(page)).toBeFocused()
    await expect(field(page)).toHaveValue('Line 1')
  })

  test('a fix typed when the page is put away comes back in the box when the tab is loaded again, with nothing sent', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 2, typed before the phone slept')
    const sent = saves(page)
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    // Parked while hidden, before anything unloads.
    expect(JSON.parse((await parked(page))!)).toMatchObject({ draft: 'Line 2, typed before the phone slept', refused: false })
    page.on('dialog', (d) => void d.accept())
    await page.reload()
    await ready(page)
    await expect(field(page)).toHaveValue('Line 2, typed before the phone slept')
    await expect(now(page).getByRole('status')).toHaveText(RESTORED)
    expect(sent).toEqual([])
  })

  test('a fix parked while the page was put away is dropped again when it is shown, so a cancelled fix is not offered back', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('e')
    await field(page).fill('Line 1, then cancelled')
    const visibility = (state: 'hidden' | 'visible') => page.evaluate((state) => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
      document.dispatchEvent(new Event('visibilitychange'))
    }, state)
    await visibility('hidden')
    expect(await parked(page)).not.toBeNull()
    await visibility('visible')
    expect(await parked(page)).toBeNull()
    await now(page).getByRole('button', { name: 'Cancel' }).click()
    const sent = saves(page)
    await leaveForLibrary(page)
    await page.goto('/#/watch/3')
    await ready(page)
    await expect(field(page)).toHaveCount(0)
    await expect(now(page).getByRole('status')).toHaveText('')
    expect(sent).toEqual([])
  })

  test('Loop line repeats the line being spoken, lets the sound run on when turned off, and repeats the line a row press lands on', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    const loop = page.getByRole('switch', { name: 'Loop line' })
    // Line 3 runs from 0:04 to 0:05.5, line 5 from 0:08 to 0:09.5.
    await rowButton(page, 3).click()
    await expect(pauseButton(page)).toBeVisible()
    await page.keyboard.press('l')
    await expect(loop).toHaveAttribute('aria-checked', 'true')
    const looped = await sampleTimes(page, 4000)
    expect(Math.max(...looped)).toBeLessThanOrEqual(5.7)
    expect(looped.some((t, i) => i > 0 && t < looped[i - 1])).toBe(true)
    await expect(now(page).getByTestId('watch-now-meta')).toHaveText('Line #3 · 0:04.0')

    await page.keyboard.press('l')
    await expect(loop).toHaveAttribute('aria-checked', 'false')
    await expect.poll(() => audioTime(page), { timeout: 3000 }).toBeGreaterThan(6)

    await page.keyboard.press('l')
    await expect(loop).toHaveAttribute('aria-checked', 'true')
    await rowButton(page, 5).click()
    const fifth = await sampleTimes(page, 3500)
    expect(Math.min(...fifth)).toBeGreaterThanOrEqual(8)
    expect(Math.max(...fifth)).toBeLessThanOrEqual(9.7)
  })

  test('Loop line starts off on this page whatever Review left it at, and turning it on or off here leaves that preference as it was', async ({ page }) => {
    await page.addInitScript(() => {
      if (sessionStorage.getItem('seeded')) return
      sessionStorage.setItem('seeded', '1')
      localStorage.setItem('baihe.pref.review.loop', 'true')
    })
    await page.goto('/#/watch/3')
    await ready(page)
    const loop = page.getByRole('switch', { name: 'Loop line' })
    await expect(loop).toHaveAttribute('aria-checked', 'false')
    await playButton(page).click()
    // Line 1 runs from 0:00 to 0:01.5: with Loop line on, the sound would never leave it.
    await expect.poll(() => audioTime(page), { timeout: 5000 }).toBeGreaterThan(2.5)
    await loop.click()
    await expect(loop).toHaveAttribute('aria-checked', 'true')
    await loop.click()
    await expect(loop).toHaveAttribute('aria-checked', 'false')
    expect(await page.evaluate(() => localStorage.getItem('baihe.pref.review.loop'))).toBe('true')
  })

  test('Loop line turned on here is not stored for Review', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.getByRole('switch', { name: 'Loop line' }).click()
    await expect(page.getByRole('switch', { name: 'Loop line' })).toHaveAttribute('aria-checked', 'true')
    expect(await page.evaluate(() => localStorage.getItem('baihe.pref.review.loop'))).toBeNull()
  })

  test.describe('following, 1440x900 with the sound playing', () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto('/#/watch/3')
      await ready(page)
    })
    // From the middle of the title, by Jump and Play, so that nothing touches the list.
    const playFrom = async (page: Page, at: string) => {
      await page.getByLabel('Jump to time').fill(at)
      await page.getByRole('button', { name: 'Jump', exact: true }).click()
      await playButton(page).click()
      await expect(pauseButton(page)).toBeVisible()
    }
    // Set the list's scroll from the test, as a drag of its scroll bar does (no wheel, touch or key), and wait for its
    // scroll event, so what the test does next comes after it.
    const scrollListTo = (page: Page, where: 'top' | 'end') => list(page).evaluate((ol, where) => new Promise<void>((done) => {
      ol.addEventListener('scroll', () => done(), { once: true })
      ol.scrollTop = where === 'top' ? 0 : ol.scrollHeight
    }), where)

    test('a scroll of the list from its scroll bar waits for the person, and Listen brings it back', async ({ page }) => {
      await playFrom(page, '0:18')
      await spokenRowAtFollowPlace(page, 3000)
      await scrollListTo(page, 'end')
      const after = await afterNextLine(page, 20)
      expect(Math.max(...after.map((a) => a.gap))).toBeLessThanOrEqual(2)
      await now(page).getByRole('button', { name: 'Listen', exact: true }).click()
      await spokenRowAtFollowPlace(page, 3000)
    })

    test('a wheel that ends the first glide early does not leave a later scroll-bar drag uncounted', async ({ page }) => {
      await playFrom(page, '0:14')
      await spokenRowAtFollowPlace(page, 3000)
      const box = (await boxes(page)).list
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
      // A glide that a wheel cancels never ends where the page aimed, so the page's mark of it is never cleared by its
      // scrollend; a scrollBy that does nothing leaves the same mark, without depending on how a browser cuts a glide.
      await list(page).evaluate((ol) => { ol.scrollBy = () => {} })
      const before = await currentRow(page)
      await expect.poll(() => currentRow(page)).toBeGreaterThan(before)
      await list(page).evaluate((ol) => { delete (ol as Partial<HTMLElement>).scrollBy })
      await page.mouse.wheel(0, -30)
      const wheeled = Date.now()
      // The drag comes inside the five seconds the wheel started; the wait it starts must outlast the wheel's.
      await page.waitForTimeout(3000)
      await scrollListTo(page, 'top')
      await page.waitForTimeout(wheeled + 6500 - Date.now())
      expect((await boxes(page)).scrollTop).toBeLessThan(2)
    })

    test('Tab to a row out of view waits for the person: the focused row stays in sight', async ({ page }) => {
      await rowButton(page, 2).click()
      await expect(pauseButton(page)).toBeVisible()
      const out = (await boxes(page))
      expect(out.rows[19].top).toBeGreaterThan(out.list.bottom)
      await rowButton(page, 19).evaluate((b) => (b as HTMLElement).focus({ preventScroll: true }))
      await page.keyboard.press('Tab')
      await expect(rowButton(page, 20)).toBeFocused()
      const after = await afterNextLine(page, 20)
      for (const a of after) {
        expect(a.top).toBeGreaterThanOrEqual(0)
        expect(a.bottom).toBeGreaterThanOrEqual(0)
      }
    })

    test("the page's own following never counts as the person's: four lines in a row, each one is put in its place", async ({ page }) => {
      await playFrom(page, '0:22')
      for (let i = 0; i < 4; i++) {
        const before = await currentRow(page)
        await expect.poll(() => currentRow(page)).toBeGreaterThan(before)
        await spokenRowAtFollowPlace(page, 1500)
      }
    })

    test('after a scroll by hand, Down, Up and a drag of the seek bar each bring the list back at once', async ({ page }) => {
      await playFrom(page, '0:14')
      await spokenRowAtFollowPlace(page, 3000)
      await scrollListTo(page, 'top')
      await page.keyboard.press('ArrowDown')
      await spokenRowAtFollowPlace(page, 3000)
      await scrollListTo(page, 'top')
      await page.keyboard.press('ArrowUp')
      await spokenRowAtFollowPlace(page, 3000)
      await scrollListTo(page, 'top')
      const seek = (await page.getByRole('slider', { name: 'Seek' }).boundingBox())!
      await page.mouse.move(seek.x + seek.width * 0.3, seek.y + seek.height / 2)
      await page.mouse.down()
      await page.mouse.move(seek.x + seek.width * 0.33, seek.y + seek.height / 2, { steps: 4 })
      await page.mouse.up()
      await spokenRowAtFollowPlace(page, 3000)
    })
  })
})

test('a title without sound says there is nothing to play and still lists its lines', async ({ page }) => {
  seedCheckLines({ warnings: false })
  await page.goto('/#/watch/3')
  await expect(page.getByText('This title has no sound or video on this PC, so there is nothing to play here.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Follow along', exact: true })).toHaveAttribute('href', '#/follow/3')
  await expect(page.getByRole('link', { name: 'Read the lines', exact: true })).toHaveAttribute('href', '#/read/3')
  await expect(page.getByRole('group', { name: 'Player' })).toHaveCount(0)
  await expect(list(page).getByRole('listitem')).toHaveCount(24)
})

test('a novel reached by this address says it is read, not watched, with a button to the Reader', async ({ page }) => {
  await page.goto('/#/watch/2')
  await expect(page.getByText('This title is read, not watched.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open the Reader' })).toHaveAttribute('href', '#/read/2')
})
