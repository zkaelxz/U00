import { expect, test } from '@playwright/test'

// Phone project (390x844, touch): the error fallback fits the width and its
// actions are real touch targets. Same forced crash as error-boundary.spec.ts.

const SHOTS = process.env.SHOT_DIR

test('error fallback fits a phone and keeps 44px targets', async ({ page }) => {
  await page.route('**/api/library/stats', (route) =>
    route.fulfill({ json: { total_dramas: 1, total_lines: 0 } }),
  )
  await page.goto('/')
  const fallback = page.getByTestId('error-fallback')
  await expect(fallback).toBeVisible()

  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)

  for (const target of [
    fallback.getByRole('button', { name: 'Copy error' }),
    fallback.getByRole('button', { name: 'Reload' }),
    fallback.getByRole('link', { name: 'Open Diagnostics' }),
  ]) {
    const box = await target.boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/phone-fallback.png`, fullPage: true })
})
