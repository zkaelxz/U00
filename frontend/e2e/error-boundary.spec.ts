import { expect, test, type Page } from '@playwright/test'
import { gearLink, openGear } from './settingsNav'

// A page that crashes while rendering shows the error fallback instead of a
// blank window, the header and nav stay usable, and leaving the route
// clears it. The crash is forced with a malformed /api/library/stats body
// (no "usage"), which the Library stats strip reads during render.

const SHOTS = process.env.SHOT_DIR

async function breakLibraryStats(page: Page) {
  await page.route('**/api/library/stats', (route) =>
    route.fulfill({ json: { total_dramas: 1, translated_lines: 0, total_lines: 0 } }),
  )
}

test('a page render error shows the fallback, and navigating away recovers', async ({ page }) => {
  await breakLibraryStats(page)
  await page.goto('/')

  const fallback = page.getByTestId('error-fallback')
  await expect(fallback).toBeVisible()
  await expect(fallback.getByRole('heading', { name: 'This page hit an error.' })).toBeVisible()
  await expect(page.getByTestId('error-fallback-text')).toContainText('estimated_cost_usd')
  await expect(fallback.getByRole('button', { name: 'Copy error' })).toBeVisible()
  await expect(fallback.getByRole('button', { name: 'Reload' })).toBeVisible()
  await expect(fallback.getByRole('link', { name: 'Open Diagnostics' })).toHaveAttribute('href', '#/diagnostics')
  // The header survived the crash.
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-fallback.png`, fullPage: true })

  await fallback.getByRole('button', { name: 'Copy error' }).click()
  // Headless may refuse the clipboard; either way the button reports back.
  await expect(fallback.locator('button').first()).toHaveText(/^(Copied|Copy failed)/)

  // Navigating resets the boundary: Settings renders normally.
  await openGear(page)
  await gearLink(page, 'Settings').click()
  await expect(page).toHaveURL(/#\/settings$/)
  await expect(page.getByTestId('error-fallback')).toHaveCount(0)

  // With well-formed stats, the Library renders again too.
  await page.unroute('**/api/library/stats')
  await page.route('**/api/library/stats', (route) =>
    route.fulfill({
      json: {
        total_dramas: 1, by_status: {}, by_media_type: {}, total_lines: 0, translated_lines: 0,
        usage: { estimated_cost_usd: 0 },
      },
    }),
  )
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Library', exact: true }).click()
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByTestId('error-fallback')).toHaveCount(0)
})

// The app bundle never runs (the Windows text/plain case, or a download that
// stalls). The note says "Still loading…" at 4 s and only swaps to the help
// text at 15 s; the 15 s stage is reached by seeking the note's CSS
// animations rather than waiting.
async function checkStalledStart(page: Page) {
  await page.route('**/assets/*.js', (route) => route.abort())
  await page.goto('/')
  const note = page.locator('#boot-static')
  const wait = note.locator('.boot-wait')
  const help = note.locator('.boot-fail')
  await expect(note).toHaveAttribute('role', 'alert')
  await expect(note).toHaveCSS('opacity', '0')
  await expect(note).toHaveCSS('opacity', '1', { timeout: 10_000 })
  await expect(wait).toHaveText('Still loading…')
  await expect(wait).toHaveCSS('visibility', 'visible')
  await expect(help).toHaveCSS('visibility', 'hidden')

  await page.evaluate(() => {
    for (const a of document.getAnimations()) a.currentTime = 16_000
  })
  await expect(wait).toHaveCSS('visibility', 'hidden')
  await expect(help).toHaveCSS('visibility', 'visible')
  await expect(help).toContainText("Baihe's screens didn't load.")
}

test('the static note is replaced on a normal start', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
  await expect(page.locator('#boot-static')).toHaveCount(0)
})

test('a stalled start says "Still loading…" first, then shows the help', async ({ page }) => {
  await checkStalledStart(page)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-static-note.png` })
})

test('both stages still appear with reduced motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await checkStalledStart(page)
})
