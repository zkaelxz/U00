import { expect, test, type Page } from '@playwright/test'

import { CURRENT_ROWS, OLDER_PRESET, RETIRED_PRESET, guardWrites, status } from './modelHealthMocks'

// Model health card on Diagnostics (Step 40, desktop). The real e2e API has
// no keys, so the first test only checks the card against the real status
// route; the rest mock /api/models/* so the provider check and the preset
// switch never touch the shared seeded library (a catch-all fails the test
// on any other non-GET /api call). The provider check is never automatic.
// Set MODEL_HEALTH_SHOTS_DIR=<dir> to save a 1440x900 screenshot.

const SHOTS = process.env.MODEL_HEALTH_SHOTS_DIR

const card = (page: Page) => page.getByRole('region', { name: 'Model health' })

test('the card reads the real status route and never checks providers by itself', async ({ page, request }) => {
  const real = await (await request.get('/api/models/status')).json()
  expect(Array.isArray(real.items)).toBe(true)
  expect(typeof real.warnings).toBe('number')
  expect(real.items.some((i: { kind: string }) => i.kind === 'default')).toBe(true)
  expect(JSON.stringify(real)).not.toMatch(/api[_-]?key|sk-/i)

  const posts: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/models/') && r.method() !== 'GET') posts.push(r.url())
  })
  await page.goto('/#/diagnostics')
  await expect(card(page).getByRole('heading', { name: 'Model health' })).toBeVisible()
  await expect(card(page).getByTestId('model-health-badge')).toBeVisible()
  await expect(card(page).getByRole('button', { name: 'Check providers now' })).toBeEnabled()
  expect(posts).toEqual([])
})

test('retired and older rows come first with their notes; the rest fold away', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await page.route('**/api/models/status', (r) => r.fulfill({ json: status() }))
  await page.goto('/#/diagnostics')
  const c = card(page)

  await expect(c.getByTestId('model-health-badge')).toHaveText('2 warnings')
  await expect(c.getByTestId('model-health-badge').locator('.pill')).toHaveClass(/pill-bad/)
  await expect(c.getByTestId('model-health-checked')).toHaveText('Providers not checked yet')
  await expect(c).toContainText('Nothing switches automatically')

  const rows = c.getByRole('list', { name: 'Models that need attention' }).locator(':scope > li')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toContainText('Retired')
  await expect(rows.nth(0)).toContainText('Preset: Old DeepSeek')
  await expect(rows.nth(0)).toContainText("DeepSeek's legacy alias")
  await expect(rows.nth(1)).toContainText('Deprecated')
  await expect(rows.nth(1)).toContainText('Built into the app — update the app to change it.')
  await expect(rows.nth(1).getByRole('button')).toHaveCount(0)
  await expect(rows.nth(2)).toContainText('Older model')
  await expect(rows.nth(2).getByRole('button', { name: 'Switch Preset: Drama A to claude-sonnet-5' })).toBeVisible()

  // Current / not-checked rows are folded.
  const fold = c.locator('details', { has: page.locator('summary', { hasText: 'Other configured models' }) })
  await expect(fold.locator('summary')).toContainText(String(CURRENT_ROWS.length))
  await expect(fold.getByRole('list')).toBeHidden()
  await fold.locator('summary').click()
  await expect(fold.getByRole('list').locator(':scope > li')).toHaveCount(CURRENT_ROWS.length)
  await expect(fold).toContainText('Claude: built-in default')

  if (SHOTS) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fold.locator('summary').click()
    await c.scrollIntoViewIfNeeded()
    await c.screenshot({ path: `${SHOTS}/model-health-desktop-card.png` })
    await page.screenshot({ path: `${SHOTS}/model-health-desktop-1440x900.png` })
  }
  expect(unmocked).toEqual([])
})

test('Check providers now shows the check time and per-engine errors; a second check too soon says so', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await page.route('**/api/models/status', (r) => r.fulfill({ json: status() }))
  let checks = 0
  await page.route('**/api/models/check', (r) => {
    checks += 1
    expect(r.request().headers()['x-baihe-local']).toBe('1')
    if (checks > 1) {
      return r.fulfill({ status: 429, json: { error: { code: 'rate_limited', message: 'Models were checked less than a minute ago.' } } })
    }
    return r.fulfill({
      json: status({
        checked_at: '2026-09-30T14:03:11.5',
        engines_checked: {
          claude: { ok: true, model_count: 12, error: null },
          gemini: { ok: false, model_count: 0, error: 'HTTPError: 403 Forbidden' },
        },
      }),
    })
  })
  await page.goto('/#/diagnostics')
  const c = card(page)
  await expect(c.getByTestId('model-health-badge')).toHaveText('2 warnings')
  expect(checks).toBe(0)

  await c.getByRole('button', { name: 'Check providers now' }).click()
  await expect(c.getByTestId('model-health-checked')).toHaveText('Providers last checked 2026-09-30 14:03 UTC')
  const lines = c.getByRole('list', { name: 'Last provider check' }).locator('li')
  await expect(lines).toHaveText(["Gemini: Couldn't check: HTTPError: 403 Forbidden", 'Claude: 12 models listed'])
  await expect(c.getByTestId('model-health-notice')).toHaveText('Checked. The list below is up to date.')

  await c.getByRole('button', { name: 'Check providers now' }).click()
  await expect(c.getByRole('alert')).toHaveText('Models were checked less than a minute ago.')
  expect(checks).toBe(2)
  expect(unmocked).toEqual([])
})

test('switching a preset takes a second press, sends the model seen, then refreshes', async ({ page }) => {
  const unmocked = await guardWrites(page)
  let switched = false
  await page.route('**/api/models/status', (r) =>
    r.fulfill({
      json: status({
        items: switched
          ? [RETIRED_PRESET, { ...OLDER_PRESET, model: 'claude-sonnet-5', status: 'current', severity: 0, replacement: null, can_switch: false, message: 'claude-sonnet-5 (claude) is current.' }, ...CURRENT_ROWS]
          : [RETIRED_PRESET, OLDER_PRESET, ...CURRENT_ROWS],
      }),
    }),
  )
  const bodies: unknown[] = []
  await page.route('**/api/models/presets/*/switch', (r) => {
    bodies.push({ url: new URL(r.request().url()).pathname, body: r.request().postDataJSON() })
    switched = true
    return r.fulfill({ json: { preset_id: 7, engine: 'claude', from_model: 'claude-sonnet-4-6', to_model: 'claude-sonnet-5' } })
  })
  await page.goto('/#/diagnostics')
  const c = card(page)
  const row = c.getByRole('list', { name: 'Models that need attention' }).locator(':scope > li', { hasText: 'Preset: Drama A' })

  await row.getByRole('button', { name: 'Switch Preset: Drama A to claude-sonnet-5' }).click()
  expect(bodies).toEqual([])
  await row.getByRole('button', { name: 'Confirm switch to claude-sonnet-5' }).click()
  await expect(c.getByTestId('model-health-notice')).toHaveText('Preset: Drama A now uses claude-sonnet-5.')
  expect(bodies).toEqual([{ url: '/api/models/presets/7/switch', body: { from_model: 'claude-sonnet-4-6', to_model: 'claude-sonnet-5', confirm: true } }])
  // Refreshed: the preset is current now, so only the retired row needs attention.
  await expect(c.getByRole('list', { name: 'Models that need attention' }).locator(':scope > li')).toHaveCount(1)
  await expect(c.getByTestId('model-health-badge')).toHaveText('1 warning')
  expect(unmocked).toEqual([])
})

test('a stale switch (409) shows the server sentence and reloads the list', async ({ page }) => {
  const unmocked = await guardWrites(page)
  let reads = 0
  await page.route('**/api/models/status', (r) => {
    reads += 1
    return r.fulfill({ json: status({ items: [RETIRED_PRESET, ...CURRENT_ROWS] }) })
  })
  await page.route('**/api/models/presets/*/switch', (r) =>
    r.fulfill({ status: 409, json: { error: { code: 'conflict', message: "The preset's model changed since you looked; refresh and try again." } } }),
  )
  await page.goto('/#/diagnostics')
  const c = card(page)
  await c.getByRole('button', { name: 'Switch Preset: Old DeepSeek to deepseek-v4-flash' }).click()
  await c.getByRole('button', { name: 'Confirm switch to deepseek-v4-flash' }).click()
  await expect(c.getByRole('alert')).toHaveText("The preset's model changed since you looked; refresh and try again.")
  await expect.poll(() => reads).toBe(2)
  expect(unmocked).toEqual([])
})

test('Compare in Benchmark Lab sets up both models in the run form', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await page.route('**/api/models/status', (r) => r.fulfill({ json: status() }))
  await page.goto('/#/diagnostics')
  const row = card(page).getByRole('list', { name: 'Models that need attention' }).locator(':scope > li', { hasText: 'Preset: Drama A' })
  const link = row.getByRole('link', { name: 'Compare claude-sonnet-4-6 with claude-sonnet-5 in Benchmark Lab' })
  await expect(link).toHaveAttribute('href', '#/benchmark?compare=claude:claude-sonnet-4-6,claude:claude-sonnet-5')
  await link.click()

  const run = page.getByRole('region', { name: 'Run a benchmark' })
  await expect(run.getByTestId('bench-compare-note')).toContainText(
    'Set up from Model health: Claude · claude-sonnet-4-6 vs Claude · claude-sonnet-5.',
  )
  await expect(run.getByLabel('Stage', { exact: true })).toHaveValue('translation')
  await expect(run.getByLabel('Engine 1', { exact: true })).toHaveValue('claude')
  await expect(run.getByLabel('Model 1', { exact: true })).toHaveValue('claude-sonnet-4-6')
  await expect(run.getByLabel('Engine 2', { exact: true })).toHaveValue('claude')
  await expect(run.getByLabel('Model 2', { exact: true })).toHaveValue('claude-sonnet-5')
  // The query leaves the address bar, so a reload keeps later changes.
  await expect(page).toHaveURL(/#\/benchmark$/)
  await run.getByRole('button', { name: 'Dismiss' }).click()
  await expect(run.getByTestId('bench-compare-note')).toHaveCount(0)

  // A retired model the app no longer offers is left out, with a note.
  await page.goto('/#/benchmark?compare=claude:claude-old-9,claude:claude-haiku-4-5-20251001')
  await expect(run.getByTestId('bench-compare-note')).toContainText("Claude · claude-old-9 isn't offered in this app any more")
  await expect(run.getByLabel('Model 1', { exact: true })).toHaveValue('claude-haiku-4-5-20251001')
  await expect(run.getByLabel('Engine 2', { exact: true })).toHaveCount(0)
  expect(unmocked).toEqual([])
})
