import { expect, test } from '@playwright/test'
import { openFillIn } from './source-groups'

// "Research online": the grounded-research endpoints are mocked
// (no Gemini, no network); the drama read hits the real seeded API.

const budget = {
  free_daily_limit: 500, used_today: 2, free_monthly_limit: 5000, used_this_month: 2,
  free_remaining: 498, paid_price_per_search_usd: 0.035, free_lookup_min: 5,
  free_tier_key: true, key_configured: true, monthly_cap_usd: 0, month_spend_usd: 0,
  models: ['gemini-flash-lite-latest', 'gemini-flash-latest'], modes: ['quick', 'deep', 'verify'],
  estimates_usd: { quick: { 'gemini-flash-lite-latest': 0.002, 'gemini-flash-latest': 0.004 } },
}
const result = {
  drama_id: 1, research_id: 'a'.repeat(64), cached: false, mode: 'quick', model: 'gemini-flash-lite-latest',
  retrieved_at: '2026-09-29T10:00:00Z', cost_usd: 0,
  fields: [
    { field: 'title_en', value: 'Researched Title', current: 'Old', status: 'conflict', confidence: 0.82,
      sources: [{ title: 'example.org', url: 'https://example.org/a' }] },
    { field: 'studio', value: 'Mock Studio', current: null, status: 'new', confidence: null, sources: [] },
  ],
  sources: [{ title: 'example.org', url: 'https://example.org/a' }],
  related: [{ title: 'Baihe (manga)', relation: 'manga adaptation' }],
  search_queries: ['白河 title cast'],
  budget: { ...budget, used_today: 3, free_remaining: 497 },
}

test('research shows per-field sources, never pre-chooses an overwrite, applies only chosen fields', async ({ page }) => {
  const applied: unknown[] = []
  await page.route('**/api/metadata/research/budget', (route) => route.fulfill({ json: budget }))
  await page.route('**/api/metadata/dramas/1/research', (route) => route.fulfill({ json: result }))
  await page.route('**/api/metadata/dramas/1/research/apply', (route) => {
    applied.push(route.request().postDataJSON())
    return route.fulfill({ json: { drama_id: 1, replaced: [], saved_alternates: ['title_en'], confirmed: [], kept: [] } })
  })
  await page.goto('/#/drama/1/source')
  await openFillIn(page, 'Research online')
  await expect(page.getByText('498 free searches left today')).toBeVisible()
  await expect(page.getByTestId('research-cost')).toHaveText('Free (free-tier Gemini key).')
  await page.getByRole('button', { name: 'Research online' }).click()

  const list = page.getByRole('list', { name: 'Researched metadata' })
  await expect(list).toContainText('Existing: Old')
  await expect(list.getByRole('link', { name: 'example.org' })).toHaveAttribute('href', 'https://example.org/a')
  await expect(list).toContainText('No source cited for this value.')
  await expect(page.getByRole('list', { name: 'Related works' })).toContainText('Baihe (manga)')
  await expect(page.getByRole('link', { name: '白河 title cast' })).toHaveAttribute('href', /google\.com\/search\?q=/)
  const title = page.getByRole('group', { name: 'English title: what to do' })
  await expect(title.getByRole('radio', { name: 'Keep existing' })).toBeChecked()
  const apply = page.getByRole('button', { name: 'Apply choices' })
  await expect(apply).toBeDisabled()
  await title.getByRole('radio', { name: 'Save both' }).check()
  await apply.click()
  await expect(page.getByRole('status').filter({ hasText: 'kept beside the existing value' })).toBeVisible()
  expect(applied).toEqual([{ research_id: 'a'.repeat(64), choices: { title_en: 'save_both' }, seen: { title_en: 'Old' } }])
})

test('used-up free searches need the paid toggle before a lookup', async ({ page }) => {
  let calls = 0
  await page.route('**/api/metadata/research/budget', (route) =>
    route.fulfill({ json: { ...budget, free_tier_key: false, used_today: 500, free_remaining: 0 } }),
  )
  await page.route('**/api/metadata/dramas/1/research', (route) => { calls += 1; return route.fulfill({ json: result }) })
  await page.goto('/#/drama/1/source')
  await openFillIn(page, 'Research online')
  const run = page.getByRole('button', { name: 'Research online' })
  await expect(run).toBeDisabled()
  await page.getByRole('switch', { name: 'Allow paid searches' }).click()
  await expect(page.getByTestId('research-cost')).toContainText('includes the search fees')
  await expect(run).toBeEnabled()
  await run.click()
  await expect(page.getByRole('list', { name: 'Researched metadata' })).toBeVisible()
  expect(calls).toBe(1)
})

test('stored sources show as a quiet note without URLs; a failed read shows nothing', async ({ page }) => {
  await page.route('**/api/metadata/research/budget', (route) => route.fulfill({ json: budget }))
  await page.route('**/api/metadata/dramas/1/provenance', (route) => route.fulfill({ json: { drama_id: 1, fields: [
    { id: 1, field: 'studio', value: 'Mock Studio', source: 'applied', source_url: 'https://example.org/a', status: 'applied',
      retrieved_at: '2026-09-29T10:00:00Z', last_verified: '2026-09-29T10:00:00Z', sources: [{ title: 'Example Wiki', url: 'https://example.org/a' }] },
  ] } }))
  await page.goto('/#/drama/1/source')
  await openFillIn(page, 'Research online')
  await page.getByText('Where saved details came from (1)').click()
  const list = page.getByRole('list', { name: 'Saved sources' })
  await expect(list).toContainText('Studio: Filled from Example Wiki on')
  await expect(list.getByRole('link')).toHaveCount(0)
  await expect(list).not.toContainText('example.org')
})

test('a failed provenance read does not block research', async ({ page }) => {
  await page.route('**/api/metadata/research/budget', (route) => route.fulfill({ json: budget }))
  await page.route('**/api/metadata/dramas/1/provenance', (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }))
  await page.goto('/#/drama/1/source')
  await openFillIn(page, 'Research online')
  await expect(page.getByRole('button', { name: 'Research online' })).toBeEnabled()
  await expect(page.getByText('Where saved details came from')).toHaveCount(0)
})

test('a lookup comes back with its form after leaving the page, without a new search', async ({ page }) => {
  let searches = 0
  let cachedMode = ''
  let ran = false
  await page.route('**/api/metadata/research/budget', (route) => route.fulfill({ json: budget }))
  await page.route('**/api/metadata/dramas/1/research', (route) => {
    searches += 1
    ran = true
    return route.fulfill({ json: result })
  })
  // What the server's cache holds: nothing until a lookup ran, then that lookup's result.
  await page.route('**/api/metadata/dramas/1/research/cached**', (route) => {
    cachedMode = new URL(route.request().url()).searchParams.get('mode') ?? ''
    return ran
      ? route.fulfill({ json: { ...result, cached: true } })
      : route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No earlier lookup.' } } })
  })
  await page.goto('/#/drama/1/source')
  await openFillIn(page, 'Research online')
  await page.locator('select:has(option[value="deep"])').selectOption('deep')
  await page.getByRole('button', { name: 'Research online' }).click()
  await expect(page.getByRole('list', { name: 'Researched metadata' })).toContainText('Researched Title')

  await page.goto('/#/library')
  await expect(page.getByRole('heading', { name: 'Library' }).first()).toBeVisible()
  await page.goto('/#/drama/1/source')
  await openFillIn(page, 'Research online')

  await expect(page.getByRole('list', { name: 'Researched metadata' })).toContainText('Researched Title')
  await expect(page.getByTestId('research-when')).toContainText('no search used')
  await expect(page.locator('select:has(option[value="deep"])')).toHaveValue('deep')
  expect(cachedMode).toBe('deep')
  expect(searches).toBe(1)
})

test('applying a result re-shown from the cache names the lookup it was shown, not an id', async ({ page }) => {
  const applied: unknown[] = []
  await page.route('**/api/metadata/research/budget', (route) => route.fulfill({ json: budget }))
  await page.route('**/api/metadata/dramas/1/research/cached**', (route) =>
    route.fulfill({ json: { ...result, cached: true, research_id: null } }))
  await page.route('**/api/metadata/dramas/1/research/apply', (route) => {
    applied.push(route.request().postDataJSON())
    return route.fulfill({ json: { drama_id: 1, replaced: [], saved_alternates: ['title_en'], confirmed: [], kept: [] } })
  })
  await page.goto('/#/drama/1/source')
  await openFillIn(page, 'Research online')
  const title = page.getByRole('group', { name: 'English title: what to do' })
  await title.getByRole('radio', { name: 'Save both' }).check()
  await page.getByRole('button', { name: 'Apply choices' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'kept beside the existing value' })).toBeVisible()
  expect(applied).toEqual([{
    lookup_mode: 'quick', lookup_model: 'gemini-flash-lite-latest', retrieved_at: '2026-09-29T10:00:00Z',
    choices: { title_en: 'save_both' }, seen: { title_en: 'Old' },
  }])
})

test('a remembered model the server no longer knows falls back to the default, and says so', async ({ page }) => {
  const asked: string[] = []
  await page.route('**/api/metadata/research/budget', (route) => route.fulfill({ json: budget }))
  await page.route('**/api/metadata/dramas/1/research/cached**', (route) => {
    const model = new URL(route.request().url()).searchParams.get('model') ?? ''
    asked.push(model)
    return model === 'gemini-retired'
      ? route.fulfill({ status: 422, json: { error: { code: 'invalid_input', message: 'That model cannot run grounded research.' } } })
      : route.fulfill({ json: { ...result, cached: true, research_id: null } })
  })
  await page.addInitScript(() => {
    localStorage.setItem('baihe.draft.1.research', JSON.stringify({
      v: 1, values: { mode: 'quick', model: 'gemini-retired', allowPaid: false },
    }))
  })
  await page.goto('/#/drama/1/source')
  await openFillIn(page, 'Research online')
  await expect(page.getByText('no longer available, so the default model is selected')).toBeVisible()
  await expect(page.getByRole('list', { name: 'Researched metadata' })).toContainText('Researched Title')
  expect(asked).toEqual(['gemini-retired', 'gemini-flash-lite-latest'])
})
