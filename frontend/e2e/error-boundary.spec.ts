import { expect, test, type Page } from '@playwright/test'

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
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Settings' }).click()
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
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Library' }).click()
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByTestId('error-fallback')).toHaveCount(0)
})

test('the static note is replaced on a normal start, and shows if the script never loads', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
  await expect(page.locator('#boot-static')).toHaveCount(0)

  // Simulate the Windows text/plain case: the app bundle never runs.
  await page.route('**/assets/*.js', (route) => route.abort())
  await page.goto('/')
  const note = page.locator('#boot-static')
  await expect(note).toContainText("Baihe's screens didn't load.")
  await expect(note).toHaveCSS('opacity', '1', { timeout: 10_000 })
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-static-note.png` })
})
