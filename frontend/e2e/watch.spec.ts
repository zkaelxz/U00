import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { seedCheckLines } from './compareSelectedHelpers'
import { expect, test } from './fixtures'

// The Watch page (#/watch/<id>) against the seeded API: drama 3 (Signal, a Korean video drama) with 24 lines two
// seconds apart and, unless a test says otherwise, a minute of sound. Lines, saves and the media stream are real.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.afterAll(() => {
  // Other specs expect drama 3 as the server seeds it, with no sound and no lines, unless they add them.
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\ndb.update_drama(3, audio_filename=None)\ndb.save_lines(3, [])`], { cwd: repoRoot })
})

const list = (page: import('@playwright/test').Page) => page.getByRole('list', { name: 'Lines' })
const row = (page: import('@playwright/test').Page, n: number) => list(page).getByRole('listitem').nth(n - 1)
const now = (page: import('@playwright/test').Page) => page.getByRole('region', { name: 'Line being spoken' })
const time = (page: import('@playwright/test').Page) => page.getByTestId('player-time')

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
    await expect(list(page).getByRole('listitem')).toHaveCount(24)
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

  test('a fix refused because the line changed keeps the draft and shows the new text', async ({ page }) => {
    await page.route(/\/api\/lines\/dramas\/3\/lines\/\d+$/, (r) => r.request().method() === 'POST'
      ? r.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'The line changed.' } } })
      : r.fallback())
    await page.goto('/#/watch/3')
    await expect(list(page).getByRole('listitem')).toHaveCount(24)
    await page.keyboard.press('ArrowDown')
    await now(page).getByRole('button', { name: 'Fix this line' }).click()
    const field = now(page).getByRole('textbox', { name: 'English' })
    await field.fill('Mine')
    const reloaded = page.waitForRequest((r) => r.method() === 'GET' && r.url().includes('/api/review/dramas/3/lines?'))
    await field.press('Control+Enter')
    await reloaded
    await expect(now(page).getByRole('alert')).toHaveText('This line changed since the page loaded. Its new text is shown now; check it and save again if it still needs the fix.')
    await expect(field).toHaveValue('Mine')
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
