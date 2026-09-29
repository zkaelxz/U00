import { expect, test, type Page } from '@playwright/test'

// "Report a problem" on a phone (390x844, touch): the header button, the
// dialog as a bottom sheet with 44px targets and no sideways scroll, and
// Diagnostics > Bug reports keeping Delete… beside Copy. Every /api request
// is fulfilled or aborted here: /api/meta and the bug-report calls are
// mocked, any other GET gets a mocked 404, any other call is aborted.

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

async function guard(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'GET' && path === '/api/meta') {
      return route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: true } })
    }
    if (r.method() === 'GET') {
      return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'Not mocked in this test.' } } })
    }
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

test('Report a problem on a phone: bottom sheet, 44px targets, no sideways scroll', async ({ page }) => {
  const unmocked = await guard(page)
  const md = `## What happened\n\nTapping Save does nothing.\n${'x'.repeat(300)}`
  await page.route('**/api/diagnostics/bug-reports', (route) => route.request().method() === 'POST'
    ? route.fulfill({ json: { id: 3, stamp: '20260929T100000Z', markdown: md, issue_markdown: md,
      what_happened: 'Tapping Save does nothing.', expected: '', title: '[Bug] Tapping Save does nothing.' } })
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

test('Diagnostics > Bug reports on a phone: Delete… stays beside Copy', async ({ page }) => {
  const unmocked = await guard(page)
  await page.route('**/api/diagnostics/bug-reports', (r) => r.request().method() === 'GET'
    ? r.fulfill({ json: [{ id: 12, stamp: '20260929T100000Z', created_at: '2026-09-29 10:00:00 UTC',
      summary: 'Export hangs on a long drama', route: '/drama/1/export', mode: 'lan',
      has_screenshot: false, has_server_log: true }] })
    : r.abort())
  await page.goto('/#/diagnostics')
  await page.locator('summary', { hasText: /^Bug reports/ }).click()
  const list = page.getByTestId('bug-reports')
  const copy = list.getByRole('button', { name: 'Copy report #12' })
  const del = list.getByRole('button', { name: 'Delete report #12' })
  await expect(del).toBeVisible()
  const [a, b] = [(await copy.boundingBox())!, (await del.boundingBox())!]
  expect(Math.abs(a.y - b.y)).toBeLessThan(4)                 // same row
  expect(b.x).toBeGreaterThan(a.x)
  await noSideways(page)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/phone-diagnostics-bug-reports.png` })
  expect(unmocked).toEqual([])
})
