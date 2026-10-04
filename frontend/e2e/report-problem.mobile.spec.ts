import { expect, test, type Page } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// "Report a problem" on a phone (390x844, touch): the header button, the
// dialog as a bottom sheet with 44px targets and no sideways scroll, and
// Copy a report and Saved reports (Delete… beside Copy). Every /api request
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
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent ?? e.getAttribute('type') ?? '').trim().slice(0, 30) }))
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
  expect((await hitHeight(open))).toBeGreaterThanOrEqual(44)
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

test('Report a problem on a phone: Copy a report and Saved reports fit, Delete… stays beside Copy', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const unmocked = await guard(page)
  const long = 'x'.repeat(400)
  const report = `Python: 3.12.4\nModel/engine versions:\n  - faster-whisper: 1.1.0\nRecent errors:\n  12:00 ERROR ${long}`
  await page.route('**/api/diagnostics/support-report', (r) => r.fulfill({ json: { report } }))
  await page.route('**/api/diagnostics/bug-reports', (r) => r.request().method() === 'GET'
    ? r.fulfill({ json: [{ id: 12, stamp: '20260929T100000Z', created_at: '2026-09-29 10:00:00 UTC',
      summary: 'Export hangs on a long drama', route: '/drama/1/export', mode: 'lan',
      has_screenshot: false, has_server_log: true }] })
    : r.abort())
  await page.goto('/#/library')
  await page.getByRole('button', { name: 'Report a problem' }).click()
  const dialog = page.getByRole('dialog', { name: 'Report a problem' })
  const card = dialog.getByRole('region', { name: 'Copy a report for a bug' })
  await card.scrollIntoViewIfNeeded()
  await card.getByRole('button', { name: 'Copy report' }).click()
  await expect(card.getByTestId('report-note')).toHaveText('Copied. Paste it into your bug report.')
  await card.locator('summary', { hasText: "What's in it" }).click()
  await expect(card.getByTestId('report-list')).toContainText('faster-whisper')

  await dialog.locator('summary', { hasText: /^Saved reports/ }).click()
  const list = dialog.getByTestId('bug-reports')
  const copy = list.getByRole('button', { name: 'Copy report #12' })
  const del = list.getByRole('button', { name: 'Delete report #12' })
  await expect(del).toBeVisible()
  const [a, b] = [(await copy.boundingBox())!, (await del.boundingBox())!]
  expect(Math.abs((a.y + a.height / 2) - (b.y + b.height / 2))).toBeLessThan(4) // same row (centres: the two differ in height)
  expect(b.x).toBeGreaterThan(a.x)
  expect(await smallTargets(page)).toEqual([])
  await noSideways(page)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/phone-dialog-extras.png` })
  expect(unmocked).toEqual([])
})
