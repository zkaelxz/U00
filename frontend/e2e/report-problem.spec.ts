import { expect, test, type Page, type Request } from '@playwright/test'

// "Report a problem" (desktop): the header dialog and Diagnostics > Bug
// reports. Every /api request is fulfilled or aborted here: /api/meta and
// the bug-report calls are mocked, any other GET gets a mocked 404 and any
// other call is aborted and recorded (nothing reaches the seeded API).

test.use({ viewport: { width: 1280, height: 800 } })

const SHOTS = process.env.SHOT_DIR

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` })
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const NOT_MOCKED = { error: { code: 'not_found', message: 'Not mocked in this test.' } }

async function guard(page: Page, local = true): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'GET' && path === '/api/meta') {
      return route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local } })
    }
    if (r.method() === 'GET') return route.fulfill({ status: 404, json: NOT_MOCKED })
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

const SAVED = {
  stamp: '20260929T100000Z',
  issue_markdown: '## What happened\n\nThe list went blank.\n\n## Server\n\nServer details are saved with the report on the PC.\n',
  what_happened: 'The list went blank.',
  expected: 'My dramas.',
  title: '[Bug] The list went blank.',
}

/** The `report` part of a multipart body, parsed. */
function reportPart(req: Request): Record<string, unknown> {
  const raw = req.postDataBuffer()!.toString('latin1')
  const m = /name="report"\r\n(?:[^\r\n]+\r\n)*\r\n([\s\S]*?)\r\n--/.exec(raw)
  return JSON.parse(Buffer.from(m![1], 'latin1').toString('utf8'))
}

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')

test('Report a problem: send, saved as #N, copy, GitHub link', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const unmocked = await guard(page)
  let posted: Request | null = null
  await page.route('**/api/diagnostics/bug-reports', (route) => {
    if (route.request().method() !== 'POST') return route.abort()
    posted = route.request()
    return route.fulfill({ json: { ...SAVED, id: 7,
      markdown: '## What happened\n\nThe list went blank.\n\n### Server log (last 1 lines, redacted)\n\nADMIN-ONLY-LOG-LINE\n' } })
  })
  // A failed API call the capture should record (method, path, status, code only).
  await page.route('**/api/library/stats', (r) =>
    r.fulfill({ status: 500, json: { error: { code: 'internal_error', message: 'secret body text' } } }))

  await page.goto('/#/library')
  await page.goto('/#/diagnostics')
  await page.evaluate(() => console.error('boom from test', { line: 'private line text' }))

  const open = page.getByRole('button', { name: 'Report a problem' })
  await open.click()
  const dialog = page.getByRole('dialog', { name: 'Report a problem' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByLabel(/What happened\?/)).toBeFocused()
  await expect(dialog.getByLabel('Include recent server log')).toBeChecked()

  // Required field.
  await dialog.getByRole('button', { name: 'Send report' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('Say what happened.')
  expect(posted).toBeNull()

  await dialog.getByLabel(/What happened\?/).fill('The list went blank.')
  await dialog.getByLabel('What did you expect?').fill('My dramas.')
  await dialog.getByLabel(/Screenshot/).setInputFiles({ name: 'shot.png', mimeType: 'image/png', buffer: PNG })
  await shot(page, 'desktop-dialog')
  await dialog.getByRole('button', { name: 'Send report' }).click()

  await expect(dialog.getByRole('status')).toHaveText('Saved as report #7.')
  const body = reportPart(posted!)
  expect(body).toMatchObject({ what_happened: 'The list went blank.', expected: 'My dramas.', include_server_log: true,
    route: '/diagnostics', mode: 'pc' })
  expect((body.route_history as { route: string }[]).map((r) => r.route)).toEqual(['/library', '/diagnostics'])
  expect(body.failed_requests).toEqual(expect.arrayContaining([
    expect.objectContaining({ method: 'GET', path: '/api/library/stats', status: 500, code: 'internal_error' })]))
  expect(body.console).toEqual(expect.arrayContaining([
    expect.objectContaining({ level: 'error', message: 'boom from test [object]' })]))
  const text = JSON.stringify(body)
  for (const bad of ['secret body text', 'private line text', 'X-Baihe-Local', 'Cookie']) expect(text).not.toContain(bad)
  expect(posted!.postDataBuffer()!.toString('latin1')).toContain('name="screenshot"; filename="shot.png"')

  const link = dialog.getByRole('link', { name: 'Open GitHub issue' })
  const href = new URL((await link.getAttribute('href'))!)
  expect(href.origin + href.pathname).toBe('https://github.com/zkaelxz/U00/issues/new')
  expect(href.searchParams.get('template')).toBe('bug.yml')
  expect(href.searchParams.get('title')).toBe('[Bug] The list went blank.')
  expect(href.searchParams.get('area')).toBe('Diagnostics')
  expect(href.searchParams.get('report')).toBe(SAVED.issue_markdown)
  expect(href.toString()).not.toContain('ADMIN-ONLY-LOG-LINE')           // server log never in the link
  expect(await link.getAttribute('target')).toBe('_blank')

  await dialog.getByRole('button', { name: 'Copy report' }).click()
  await expect(dialog.getByText('Copied.')).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('ADMIN-ONLY-LOG-LINE')
  await shot(page, 'desktop-saved')

  await dialog.getByRole('button', { name: 'Done' }).click()
  await expect(dialog).toBeHidden()
  await expect(open).toBeFocused()
  expect(unmocked).toEqual([])
})

test('Report a problem: server down still offers Copy and GitHub', async ({ page }) => {
  const unmocked = await guard(page)
  await page.route('**/api/diagnostics/bug-reports', (route) => route.abort('connectionrefused'))
  await page.goto('/#/library')
  await page.getByRole('button', { name: 'Report a problem' }).click()
  const dialog = page.getByRole('dialog', { name: 'Report a problem' })
  const key = 'sk-ant-api03-SECRETSECRETSECRET123456'
  const win = String.raw`C:\Users\kaewinuser\AppData\Local\Baihe\library.db`
  await dialog.getByLabel(/What happened\?/).fill(`Broke with ${key} at ${win} ${'x'.repeat(4000)} 漢字`)
  await page.keyboard.press('Tab')
  await dialog.getByRole('button', { name: 'Send report' }).click()
  await expect(dialog.getByRole('alert')).toContainText("Couldn't save the report on the server.")
  await expect(dialog.getByRole('button', { name: 'Copy report' })).toBeVisible()
  const href = (await dialog.getByRole('link', { name: 'Open GitHub issue' }).getAttribute('href'))!
  expect(href.length).toBeLessThanOrEqual(6000)
  for (const bad of ['SECRETSECRET', 'kaewinuser', 'AppData']) expect(decodeURIComponent(href)).not.toContain(bad)
  await dialog.getByText('Report text').click()
  await expect(dialog.getByLabel('Report text')).toHaveValue(/not saved \(the server could not be reached\)/)
  await expect(dialog.getByLabel('Report text')).not.toHaveValue(/SECRETSECRET|kaewinuser/)
  await shot(page, 'desktop-server-down')
  // Esc closes it.
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  expect(unmocked).toEqual([])
})

test('Diagnostics > Bug reports: list, copy, PC-only delete', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const unmocked = await guard(page)
  let items = [
    { id: 2, stamp: '20260929T100000Z', created_at: '2026-09-29 10:00:00 UTC', summary: 'Export hangs', route: '/drama/1/export', mode: 'lan',
      has_screenshot: true, has_server_log: true },
    { id: 1, stamp: '20260928T090000Z', created_at: '2026-09-28 09:00:00 UTC', summary: 'Blank list', route: '/library', mode: 'pc',
      has_screenshot: false, has_server_log: false },
  ]
  let deleted: unknown = null
  await page.route('**/api/diagnostics/bug-reports', (r) =>
    r.request().method() === 'GET' ? r.fulfill({ json: items }) : r.abort())
  await page.route('**/api/diagnostics/bug-reports/2', (r) =>
    r.fulfill({ json: { id: 2, stamp: '20260929T100000Z', markdown: '## What happened\n\nExport hangs\n' } }))
  await page.route('**/api/diagnostics/bug-reports/2/delete', (r) => {
    deleted = r.request().postDataJSON()
    items = items.filter((i) => i.id !== 2)
    return r.fulfill({ json: { id: 2, deleted: true } })
  })

  await page.goto('/#/diagnostics')
  await page.locator('summary', { hasText: /^Bug reports/ }).click()
  const list = page.getByTestId('bug-reports')
  await expect(list).toContainText('Export hangs')
  await expect(list).toContainText('screenshot on the PC')
  await list.getByRole('button', { name: 'Copy report #2' }).click()
  await expect(list.getByText('Copied report #2.')).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('Export hangs')

  await list.getByRole('button', { name: 'Delete report #2' }).click()
  await list.getByRole('button', { name: 'Confirm delete report #2' }).click()
  await expect(list).not.toContainText('Export hangs')
  expect(deleted).toEqual({ confirm: true, stamp: '20260929T100000Z' })
  await expect(list).toContainText('Blank list')
  await shot(page, 'desktop-diagnostics-bug-reports')
  expect(unmocked).toEqual([])
})

test('Report a problem off the PC: no screenshot field, text-only report', async ({ page }) => {
  const unmocked = await guard(page, false)
  let posted: Request | null = null
  await page.route('**/api/diagnostics/bug-reports', (route) => {
    if (route.request().method() !== 'POST') return route.abort()
    posted = route.request()
    return route.fulfill({ json: { ...SAVED, id: 8, markdown: SAVED.issue_markdown } })
  })
  await page.goto('/#/library')
  await page.getByRole('button', { name: 'Report a problem' }).click()
  const dialog = page.getByRole('dialog', { name: 'Report a problem' })
  await expect(dialog.getByTestId('report-shot-pc-only')).toHaveText(/only be attached at the main PC/)
  await expect(dialog.getByLabel(/Screenshot/)).toHaveCount(0)
  await dialog.getByLabel(/What happened\?/).fill('The list went blank.')
  await dialog.getByRole('button', { name: 'Send report' }).click()
  await expect(dialog.getByRole('status')).toHaveText('Saved as report #8.')
  const raw = posted!.postDataBuffer()!.toString('latin1')
  expect(raw).not.toContain('name="screenshot"')
  expect(reportPart(posted!)).toMatchObject({ mode: 'lan' })
  expect(unmocked).toEqual([])
})
