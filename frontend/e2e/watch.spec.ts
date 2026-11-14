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
// The keys answer once the title is known to play, which can be after the lines are listed.
const ready = async (page: Page) => {
  await expect(list(page).getByRole('listitem')).toHaveCount(24)
  await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
}
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
    const sent = page.waitForRequest(isSave)
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    expect((await sent).postDataJSON()).toEqual({ en: 'Line 2, fixed by ear', expected: { en: 'Line 2' } })
    await expect(row(page, 2)).toContainText('Line 2, fixed by ear')
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    await expect(field(page)).toHaveCount(0)
    // The sound goes on from the line's start (line 2 runs from 0:02 to 0:03.5).
    await expect(pauseButton(page)).toBeVisible()
    await pauseButton(page).click()
    const at = await audioTime(page)
    expect(at).toBeGreaterThanOrEqual(2)
    expect(at).toBeLessThan(3.5)

    // Esc cancels without sending, also with focus on a button of the fix rather than in the box.
    const sentAfter = saves(page)
    await page.keyboard.press('e')
    await field(page).fill('Not wanted')
    await now(page).getByRole('button', { name: 'Save and play' }).focus()
    await page.keyboard.press('Escape')
    await expect(field(page)).toHaveCount(0)
    await expect(now(page).getByRole('button', { name: 'Fix this line' })).toBeFocused()
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
    await playButton(page).focus()
    await page.keyboard.press('Alt+Space')
    await expect(pauseButton(page)).toBeVisible()
    await page.keyboard.press('Alt+Space')
    await expect(playButton(page)).toBeVisible()

    await page.keyboard.press('e')
    await expect(field(page)).toBeFocused()
    const typed = await field(page).inputValue()
    await page.keyboard.press('Alt+Space')
    await expect(pauseButton(page)).toBeVisible()
    await page.keyboard.press('Alt+Space')
    await expect(playButton(page)).toBeVisible()
    await expect(field(page)).toHaveValue(typed)
    await page.keyboard.press('Escape')

    const loop = page.getByRole('switch', { name: 'Loop line' })
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

  test('a person who may not edit lines gets no box to press and no Fix this line, and E does nothing', async ({ page }) => {
    await page.route('**/api/auth/me', (r) => r.fulfill({ json: { ...ME.signedIn, permissions: ['library.read'] } }))
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await expect(now(page).getByTestId('watch-now-en')).toHaveText('Line 1')
    await expect(now(page).getByTestId('watch-now-box')).toHaveCount(0)
    await expect(now(page).getByRole('button', { name: 'Fix this line' })).toHaveCount(0)
    await page.keyboard.press('e')
    await expect(now(page).getByRole('textbox')).toHaveCount(0)
    await expect(now(page).getByRole('button', { name: 'Hear it again' })).toBeVisible()
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
    await now(page).getByRole('button', { name: 'Fix this line' }).click()
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
    await now(page).getByRole('button', { name: 'Fix this line' }).click()
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
    release()
    await expect(rowButton(page, 11)).toHaveAttribute('aria-current', 'true')
    await expect(now(page).getByTestId('watch-now-meta')).toHaveText('Line #11 · 0:20.0')
    await expect(now(page).getByTestId('watch-now-box')).toHaveText('Line 11')
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

    // A press on a row brings it back: the next line starts and the list moves with it.
    await rowButton(page, 17).click()
    await expect.poll(() => currentRow(page)).toBe(18)
    await expect(async () => {
      const b = await boxes(page)
      expect((b.rows[17].top - b.list.top) / b.list.height).toBeCloseTo(0.3, 1)
    }).toPass()
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
    await expect.poll(() => currentRow(page)).toBe(10)
    await expect.poll(async () => (await boxes(page)).scrollTop).toBeGreaterThan(0)
    expect(await page.evaluate(() => window.scrollY)).toBe(y)
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
