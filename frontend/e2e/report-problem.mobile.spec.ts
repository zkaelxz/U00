import { expect, test, type Page } from '@playwright/test'

// "Report a problem" on a phone (390x844, touch): the header button, the
// dialog as a bottom sheet with 44px targets and no sideways scroll. The
// POST is mocked; a catch-all aborts any other non-GET /api call.

const SHOTS = process.env.SHOT_DIR

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function smallTargets(page: Page) {
  return page.locator('dialog button, dialog a, dialog summary, dialog input[type=file], dialog label:has(input[type=checkbox])')
    .evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? e.getAttribute('type') ?? '').trim().slice(0, 30) }))
      .filter(({ h }) => h < 44))
}

test('Report a problem on a phone: bottom sheet, 44px targets, no sideways scroll', async ({ page }) => {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route('**/api/diagnostics/bug-reports', (route) => route.request().method() === 'POST'
    ? route.fulfill({ json: { id: 3, markdown: `## What happened\n\nTapping Save does nothing.\n${'x'.repeat(300)}` } })
    : route.abort())

  await page.goto('/#/library')
  const open = page.getByRole('button', { name: 'Report a problem' })
  expect((await open.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await noSideways(page)
  await open.click()
  const dialog = page.getByRole('dialog', { name: 'Report a problem' })
  await expect(dialog).toBeVisible()
  const box = (await dialog.boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(389)          // full-width bottom sheet
  expect(box.y + box.height).toBeGreaterThanOrEqual(843)
  expect(await smallTargets(page)).toEqual([])
  await noSideways(page)
  await dialog.getByLabel(/What happened\?/).fill('Tapping Save does nothing.')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/phone-dialog.png` })

  await dialog.getByRole('button', { name: 'Send report' }).click()
  await expect(dialog.getByRole('status')).toHaveText('Saved as report #3.')
  expect(await smallTargets(page)).toEqual([])
  await noSideways(page)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/phone-saved.png` })
  expect(unmocked).toEqual([])
})
