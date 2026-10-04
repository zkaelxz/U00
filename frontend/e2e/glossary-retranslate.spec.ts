import { expect, test, type Page } from '@playwright/test'

import { ME } from './authMocks'

// "Re-translate lines affected by the glossary…" on the Translate stage. Drama
// reads hit the real seeded API (drama 1, reported as in series 7); the
// preview, the run and the job poll are mocked, so no engine is called.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const estimate = (usd: number, n: number) => ({
  engine: 'claude', model: null, estimated_usd: usd, target_line_count: n, free: false,
  cap_applies: true, effective_cap_usd: null, monthly_refusal: false, estimate_above_cap: false,
})
const match = { term_id: 3, term_original: '林晚', term_translation: 'Lin Wan', reason: 'source' }
const preview = (hash: string) => ({
  drama_id: 1,
  has_glossary: true,
  terms: [{ id: 3, term_original: '林晚', term_translation: 'Lin Wan' }],
  selected_term_ids: [3],
  lines: [
    { id: 11, idx: 0, start: 0, end: 2, zh: '林晚来了', en: 'Lin came', hand_edited: false, matched_terms: [match] },
    { id: 12, idx: 4, start: 9, end: 11, zh: '林晚走了', en: 'My own wording', hand_edited: true, matched_terms: [match] },
    { id: 13, idx: 7, start: 15, end: 17, zh: '她笑了', en: 'Lin Wanwan smiled', hand_edited: false,
      matched_terms: [{ ...match, reason: 'banned' }] },
  ],
  hand_edited_count: 1,
  preview_hash: hash,
  estimate: estimate(0.02, 2),
  estimate_with_hand_edited: estimate(0.03, 3),
})

async function base(page: Page) {
  await page.route(/\/api\/auth\/me$/, (route) => route.fulfill({ json: ME.authOff }))
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  const state = { previews: 0, hash: 'h1', runs: [] as Record<string, unknown>[], stale: false }
  await page.route('**/api/translate-run/dramas/1/glossary-affected?**', (route) => {
    state.previews += 1
    return route.fulfill({ json: preview(state.hash) })
  })
  await page.route('**/api/translate-run/dramas/1/glossary-affected', (route) => {
    state.previews += 1
    return route.fulfill({ json: preview(state.hash) })
  })
  await page.route('**/api/translate-run/dramas/1/glossary-affected/run', (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    state.runs.push(body)
    if (state.stale) {
      return route.fulfill({
        status: 409,
        json: { error: { code: 'conflict', message: 'changed', details: { reason: 'stale_preview' } } },
      })
    }
    return route.fulfill({
      json: { job_id: 'gl-job', drama_id: 1, engine: 'claude', model: null, target_line_count: 2,
        fallback_engines: [], reflect: false, bulk: false, line_ids: body.line_ids, skipped_hand_edited_count: 0 },
    })
  })
  await page.route('**/api/jobs/gl-job', (route) =>
    route.fulfill({
      json: { job_id: 'gl-job', status: 'running', progress: 0.5, message: 'Translating 1 of 2', error: null,
        description: null, gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1 },
    }),
  )
  return state
}

test('preview ticks machine lines, locks hand-edited ones, and starts only the chosen lines', async ({ page }) => {
  const state = await base(page)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await run.getByRole('button', { name: 'Re-translate lines affected by the glossary…' }).click()

  const panel = page.getByTestId('glossary-retranslate')
  await expect(panel).toContainText('3 lines affected, 1 hand-edited.')
  await expect(panel).toContainText('Now: My own wording')
  await expect(panel).toContainText('Uses a banned translation of 林晚')
  const line1 = panel.getByRole('checkbox', { name: /^Line 1\b/ })
  const line5 = panel.getByRole('checkbox', { name: /^Line 5 hand-edited/i })
  await expect(line1).toBeChecked()
  await expect(line5).toBeDisabled()
  await expect(line5).not.toBeChecked()
  await expect(panel.getByTestId('glossary-retranslate-estimate')).toContainText('2 lines with')
  await expect(panel.getByTestId('glossary-retranslate-estimate')).toContainText('about $0.02')

  // Including hand-edited lines warns plainly and unlocks them (still unticked).
  await panel.getByRole('checkbox', { name: 'Include hand-edited lines' }).check()
  await expect(panel).toContainText('will lose your edits. A snapshot is saved first')
  await expect(line5).toBeEnabled()
  await line5.check()
  await expect(panel.getByTestId('glossary-retranslate-estimate')).toContainText('about $0.03')
  await line1.uncheck()
  await expect(panel.getByTestId('glossary-retranslate-estimate')).toContainText('at most $0.03')

  await panel.getByRole('button', { name: 'Re-translate 2 lines' }).click()
  await expect.poll(() => state.runs.length).toBe(1)
  expect(state.runs[0]).toMatchObject({ line_ids: [12, 13], preview_hash: 'h1', include_hand_edited: true })
  expect(state.runs[0]).not.toHaveProperty('force_retranslate')
  await expect(panel).toBeHidden()
  await expect(page.getByText('Translating 1 of 2')).toBeVisible()
})

test('cancel makes no request, and a stale preview is refused with a refresh', async ({ page }) => {
  const state = await base(page)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  const open = run.getByRole('button', { name: 'Re-translate lines affected by the glossary…' })
  await open.click()
  const panel = page.getByTestId('glossary-retranslate')
  await expect(panel).toContainText('3 lines affected')
  await panel.getByRole('button', { name: 'Cancel' }).click()
  await expect(panel).toBeHidden()
  expect(state.runs).toHaveLength(0)

  await open.click()
  state.stale = true
  await panel.getByRole('button', { name: 'Re-translate 2 lines' }).click()
  await expect(panel.getByRole('alert')).toContainText('changed since this list was made, so nothing was started')
  const before = state.previews
  state.hash = 'h2'
  state.stale = false
  await panel.getByRole('button', { name: 'Refresh list' }).click()
  await expect.poll(() => state.previews).toBe(before + 1)
  await panel.getByRole('button', { name: 'Re-translate 2 lines' }).click()
  await expect.poll(() => state.runs.length).toBe(2)
  expect(state.runs[1]).toMatchObject({ preview_hash: 'h2', include_hand_edited: false, line_ids: [11, 13] })
})

test('fits a phone screen with no sideways scroll', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await base(page)
  await page.goto('/#/drama/1/translate')
  await page.getByRole('button', { name: 'Re-translate lines affected by the glossary…' }).click()
  await expect(page.getByTestId('glossary-retranslate')).toContainText('3 lines affected')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
