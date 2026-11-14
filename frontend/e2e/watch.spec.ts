import { execFileSync } from 'node:child_process'
import path from 'node:path'

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

const list = (page: import('@playwright/test').Page) => page.getByRole('list', { name: 'Lines' })
const row = (page: import('@playwright/test').Page, n: number) => list(page).getByRole('listitem').nth(n - 1)
const now = (page: import('@playwright/test').Page) => page.getByRole('region', { name: 'Line being spoken' })
const time = (page: import('@playwright/test').Page) => page.getByTestId('player-time')
// The keys answer once the title is known to play, which can be after the lines are listed.
const ready = async (page: import('@playwright/test').Page) => {
  await expect(list(page).getByRole('listitem')).toHaveCount(24)
  await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
}

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
    await expect(row(page, 3)).toContainText('3 · 0:04.0')
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
    await expect(row(page, 3)).toHaveAttribute('aria-current', 'true')
    await expect(list(page).locator('[aria-current="true"]')).toHaveCount(1)
    await expect(now(page).getByTestId('watch-now-meta')).toHaveText('Line 3 · 0:04.0')
    await expect(now(page).getByTestId('watch-now-box')).toHaveText('Line 3')
    // Down and Up step between lines without playing.
    await page.keyboard.press('ArrowDown')
    await expect(row(page, 4)).toHaveAttribute('aria-current', 'true')
    await expect(time(page)).toHaveText(/^0:06\.00/)
    await page.keyboard.press('ArrowUp')
    await page.keyboard.press('ArrowUp')
    await expect(row(page, 2)).toHaveAttribute('aria-current', 'true')
    await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeVisible()
  })

  test('E opens the fix field on the current line; Save sends it with the English that was seen', async ({ page }) => {
    await page.goto('/#/watch/3')
    await ready(page)
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await expect(row(page, 2)).toHaveAttribute('aria-current', 'true')
    await page.keyboard.press('e')
    const field = now(page).getByRole('textbox', { name: 'English' })
    await expect(field).toBeFocused()
    await expect(field).toHaveValue('Line 2')
    await field.fill('Line 2, fixed by ear')
    const sent = page.waitForRequest((r) => r.method() === 'POST' && /\/api\/lines\/dramas\/3\/lines\/\d+$/.test(new URL(r.url()).pathname))
    await now(page).getByRole('button', { name: 'Save and play' }).click()
    expect((await sent).postDataJSON()).toEqual({ en: 'Line 2, fixed by ear', expected: { en: 'Line 2' } })
    await expect(row(page, 2)).toContainText('Line 2, fixed by ear')
    await expect(now(page).getByRole('status')).toHaveText('Saved.')
    await expect(field).toHaveCount(0)
    // The sound goes on from the line's start.
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
    await page.getByRole('button', { name: 'Pause' }).click()

    // Esc cancels without sending.
    await page.keyboard.press('e')
    await expect(field).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(field).toHaveCount(0)
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
    const seek = (await page.getByRole('slider', { name: 'Seek' }).boundingBox())!
    const controls = (await page.locator('.watch-player .review-player-controls').boundingBox())!
    const speed = (await page.getByLabel('Speed').boundingBox())!
    expect(seek.width).toBeGreaterThan(controls.width - 2)
    expect(speed.y).toBeGreaterThan(seek.y + seek.height - 1)
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
    await expect(row(page, 11)).toHaveAttribute('aria-current', 'true')
    await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Start from the beginning' }).click()
    await expect(time(page)).toHaveText(/^0:00\.00/)
    await expect(page.getByText('Back where you stopped')).toHaveCount(0)
    // The first line starts at 0:00, so it is the one shown.
    await expect(now(page).getByTestId('watch-now-meta')).toHaveText('Line 1 · 0:00.0')
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

test('a novel reached by this address is sent to the Reader', async ({ page }) => {
  await page.goto('/#/watch/2')
  await expect(page.getByText('This title is read, not watched.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open the Reader' })).toHaveAttribute('href', '#/read/2')
})
