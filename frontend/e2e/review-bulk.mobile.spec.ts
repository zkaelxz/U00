import { expect, test, type Page } from '@playwright/test'

import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Phone project (390x844, touch): the AI checks' rows with their Bulk
// switches fit the width, and every switch and Start button is a 44px target.

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

const section = (page: Page, title: string) =>
  page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: new RegExp(`^${title}$`) }) })

test('Bulk switches: 44px targets, no sideways scroll, the warning shows', async ({ page }) => {
  // Read the real config once: re-fetching per call can fail with "Response has been disposed".
  let real: Promise<Record<string, unknown>> | null = null
  await page.route('**/api/translate-run/dramas/3/config', async (route) => {
    real ??= route.fetch().then((r) => r.json())
    await route.fulfill({ json: { ...(await real), translation_engine: 'claude', bulk_supported_engines: ['claude', 'gemini', 'deepseek'] } })
  })
  await page.goto('/#/drama/3/review')
  const ai = section(page, 'AI review')
  // The lines list loads after the page paints and pushes this section down
  // (or the stage re-renders it), so a tap can land on the old position and
  // miss. Tap again until the section is open rather than once.
  await expect(async () => {
    if ((await ai.getAttribute('open', { timeout: 1000 })) === null) await ai.locator(':scope > summary').tap({ timeout: 2000 })
    await expect(ai).toHaveAttribute('open', '', { timeout: 1000 })
  }).toPass({ timeout: 15_000 })

  const list = ai.getByRole('list', { name: 'AI checks to run' })
  const switches = list.getByRole('switch')
  await expect(switches).toHaveCount(4)
  await expect(switches.first()).toBeEnabled() // the engine config has loaded
  for (const sw of await switches.all()) {
    const hit = await sw.evaluate((el) => {
      // elementFromPoint only sees the viewport, and the sticky toolbar and the
      // phone edit bar cover its edges: hit-test with the switch centred.
      el.scrollIntoView({ block: 'center' })
      const r = el.getBoundingClientRect()
      const cx = r.left + r.width / 2
      const cy = r.top + r.height / 2
      const at = (y: number) => el.contains(document.elementFromPoint(cx, y))
      return { top: at(cy - 21), bottom: at(cy + 21), width: r.width }
    })
    expect(hit).toEqual({ top: true, bottom: true, width: 44 })
  }
  for (const b of await list.getByRole('button', { name: /^(Check consistency|Tag emotion|Generate notes|Flag lines for a second look)$/ }).all())
    expect((await b.boundingBox())!.height).toBeGreaterThanOrEqual(44)

  await list.getByRole('switch', { name: 'Bulk: Generate notes' }).tap()
  await expect(ai.getByTestId('bulk-warning')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
})
