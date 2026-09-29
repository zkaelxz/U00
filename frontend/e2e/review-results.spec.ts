import { expect, test, type Page } from '@playwright/test'

import { clearReviewResults, deleteLine, seedReviewResults } from './reviewResultsSeed'

// Review stage results and checks against the real API: stored consistency
// and emotion results, coverage/pacing, tendencies, version compare, the
// notes link, per-line provenance, and the fix-flagged start options (job
// starts are mocked).

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

const SHOTS = process.env.REVIEW_SHOTS

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')
const section = (page: Page, title: string) =>
  page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: new RegExp(`^${title}$`) }) })

async function open(page: Page, title: string) {
  const s = section(page, title)
  if ((await s.getAttribute('open')) === null) await s.locator(':scope > summary').click()
  await expect(s).toHaveAttribute('open', '')
  return s
}

const job = (id: string, status: string) => ({
  job_id: id, status, progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1,
})

test('consistency lines and emotion tags open their line in the editor', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)

  const c = await open(page, 'Consistency')
  await expect(c).toContainText('魏婴')
  await expect(c).toContainText('Wei Ying / Wei Wuxian')
  await c.getByRole('button', { name: 'Show lines' }).click()
  await expect(c.locator('.review-findings button')).toHaveText(['#2', '#3'])
  await c.getByRole('button', { name: '#3' }).click()
  await expect(rows(page).nth(2)).toHaveAttribute('aria-current', 'true')

  const e = await open(page, 'Emotion')
  await expect(e.getByTestId('emotion-list').locator('li').first()).toContainText('anger')
  await expect(e.getByRole('button', { name: '#2' })).toHaveAttribute('title', 'Opens line #2')
  await e.getByRole('button', { name: '#2' }).click()
  await expect(rows(page).nth(1)).toHaveAttribute('aria-current', 'true')
})

test('emotion tags follow their line after a line before them is deleted', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  const e = await open(page, 'Emotion')
  await expect(e.getByTestId('emotion-list').locator('button')).toHaveText(['#2', '#1']) // anger, calm

  // Delete line #1 (the calm one) with the real structure edit.
  await rows(page).nth(0).getByRole('button', { name: /More actions for line/ }).click()
  const sheet = page.getByRole('dialog', { name: /^Line #/ })
  await sheet.getByRole('button', { name: 'Delete line…' }).click()
  await sheet.getByRole('button', { name: /^Confirm delete #/ }).click()
  await expect(rows(page)).toHaveCount(3)

  // The anger tag was on "Wei Ying is here", now line #1.
  await expect(e.getByTestId('emotion-list').locator('button')).toHaveText(['#1'])
  await e.getByRole('button', { name: '#1' }).click()
  await expect(rows(page).nth(0)).toHaveAttribute('aria-current', 'true')
  await expect(rows(page).nth(0).getByTestId('line-en')).toHaveText('Wei Ying is here')
})

test('open consistency lines refresh after a save', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  const c = await open(page, 'Consistency')
  await c.getByRole('button', { name: 'Show lines' }).click()
  await expect(c.getByTestId('consistency-list')).toContainText('Wei Ying is here')
  const row = rows(page).nth(1)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Wei Ying has come')
  await row.getByLabel('Translation').press('Control+s')
  await expect(c.getByTestId('consistency-list')).toContainText('Wei Ying has come')
})

test('a finding link says why its line cannot be opened', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  const cov = await open(page, 'Coverage and pacing')

  // An unsaved edit that cannot be saved (the save is refused) blocks the jump.
  await page.route('**/api/lines/dramas/3/lines/*', (route) =>
    route.fulfill({ status: 500, json: { error: { code: 'internal_error', message: 'x' } } }))
  await rows(page).nth(0).getByTestId('line-en').click()
  await rows(page).nth(0).getByLabel('Translation').fill('unsaved change')
  await cov.getByTestId('pacing-list').getByRole('button', { name: '#4' }).click()
  await expect(cov.getByTestId('pacing-list').getByRole('status')).toHaveText('Could not open that line: your edit to #1 is not saved yet.')
  await page.unroute('**/api/lines/dramas/3/lines/*')
  await rows(page).nth(0).getByLabel('Translation').press('Escape')

  deleteLine(4)
  await cov.getByTestId('pacing-list').getByRole('button', { name: '#4' }).click()
  await expect(cov.getByTestId('pacing-list').getByRole('status')).toHaveText('That line no longer exists.')
})

test('opening a finding from a filtered view shows all lines and says so', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  await page.getByRole('radio', { name: /^Flagged/ }).check()
  await expect(rows(page)).toHaveCount(1)
  const cov = await open(page, 'Coverage and pacing')
  await cov.getByTestId('pacing-list').getByRole('button', { name: '#1' }).click()
  await expect(rows(page)).toHaveCount(4)
  await expect(rows(page).nth(0)).toHaveAttribute('aria-current', 'true')
  await expect(page.getByText('Showing all lines to open #1.')).toBeVisible()
})

test('coverage, pacing, tendencies, version compare and the notes link', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)

  const cov = await open(page, 'Coverage and pacing')
  await expect(cov.locator('h4').first()).toHaveText('Long lines')
  await expect(cov).toContainText('17.0s for 2 characters')
  await expect(cov.getByTestId('pacing-list')).toContainText('Too long for its time slot')
  await cov.getByTestId('pacing-list').getByRole('button', { name: '#1' }).click()
  await expect(rows(page).nth(0)).toHaveAttribute('aria-current', 'true')

  const t = await open(page, 'Edit tendencies')
  await expect(t.getByTestId('tendency-stats')).toContainText('1 of your edits: 0 shortened, 1 expanded')

  const v = section(page, 'Compare versions')
  await expect(v.locator(':scope > summary')).toContainText('First pass → Second pass')
  await open(page, 'Compare versions')
  await v.getByRole('button', { name: 'Show differences' }).click()
  await expect(v.getByTestId('compare-count')).toHaveText('1 of 4 lines differ.')
  await expect(v.getByTestId('compare-list')).toContainText('Older: Wei Ying arrives')

  const n = await open(page, 'Notes export')
  await expect(n.getByTestId('notes-markdown')).toHaveAttribute('href', /\/api\/review\/dramas\/3\/notes\/markdown$/)
  const md = await page.request.get('/api/review/dramas/3/notes/markdown')
  expect(await md.text()).toContain('A plain greeting.')
})

test('line details show where the line came from', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const row = rows(page).nth(0)
  await row.locator('.review-idx').click()
  await row.getByRole('button', { name: 'Edit details' }).click()
  const origin = row.getByTestId('line-origin')
  await origin.locator('summary').click()
  await expect(origin).toContainText('gemini · m2')
  await expect(origin).toContainText('calm')
  // No raw transcription saved: nothing is said about it.
  await expect(origin.getByTestId('original-text')).toHaveCount(0)
})

test('fix flagged lines: the cap is checked as typed and only set options are sent', async ({ page }) => {
  const bodies: unknown[] = []
  await page.route('**/api/review-jobs/dramas/3/fix-flagged', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { job_id: 'fx', drama_id: 3, kind: 'fix-flagged', engine: 'gemini', model: null, line_count: 1 } })
  })
  await page.route('**/api/jobs/fx', (route) => route.fulfill({ json: job('fx', 'done') }))
  await page.goto('/#/drama/3/review')
  const ai = await open(page, 'AI review')
  const fix = ai.getByRole('group', { name: 'Fix flagged lines' })
  await expect(fix.getByRole('button').first()).toHaveText('Fix flagged lines')
  await open(page, 'Options')
  const cap = fix.getByRole('textbox', { name: 'Cost cap' })

  // Values a number field would silently turn into "" (no cap) are refused.
  for (const bad of ['5$', '1,5', '-', '-1']) {
    await cap.fill(bad)
    await fix.getByRole('button', { name: 'Fix flagged lines' }).click()
    await expect(fix.getByRole('alert')).toHaveText(/number of dollars, 0 or more/)
  }
  expect(bodies).toHaveLength(0)

  await cap.fill('0.5')
  await fix.getByRole('button', { name: 'Fix flagged lines' }).click()
  await expect(page.getByTestId('job-panel')).toBeVisible()
  expect(bodies).toEqual([{ job_cost_cap_usd: 0.5 }])
})

test('AI checks send the chosen engine and model; emotion sends the audio-cues choice (R50/R33)', async ({ page }) => {
  const config = await (await page.request.get('/api/translate-run/dramas/3/config')).json()
  const eng = config.engines.find((e: { name: string; models: string[] | null }) => e.models && e.models.length > 0)
  const bodies: Record<string, unknown> = {}
  await page.route(/\/api\/review-jobs\/dramas\/3\/(consistency|emotion|notes|flag)$/, async (route) => {
    const kind = route.request().url().split('/').pop() as string
    bodies[kind] = route.request().postDataJSON()
    await route.fulfill({ json: { job_id: `k-${kind}`, drama_id: 3, kind, engine: eng.name, model: null, line_count: 4 } })
  })
  await page.route('**/api/jobs/k-*', (route) => route.fulfill({ json: job(route.request().url().split('/').pop() as string, 'done') }))
  await page.goto('/#/drama/3/review')
  const ai = await open(page, 'AI review')

  // Nothing chosen: every field is left to the server's defaults.
  await ai.getByRole('button', { name: 'Check consistency' }).click()
  await expect.poll(() => bodies.consistency).toEqual({})

  const opts = await open(page, 'Check options')
  await opts.getByRole('combobox', { name: 'Engine' }).selectOption(eng.name)
  await opts.getByRole('combobox', { name: 'Model' }).selectOption(eng.models[0])
  const cues = opts.getByRole('checkbox', { name: /audio delivery cues/ })
  const wasOn = await cues.isChecked()
  await cues.setChecked(!wasOn)

  await ai.getByRole('button', { name: 'Tag emotion' }).click()
  await expect.poll(() => bodies.emotion).toEqual({ engine: eng.name, model: eng.models[0], use_audio_cues: !wasOn })
  await ai.getByRole('button', { name: 'Generate notes' }).click()
  await expect.poll(() => bodies.notes).toEqual({ engine: eng.name, model: eng.models[0] })
  await ai.getByRole('button', { name: 'Flag lines for a second look' }).click()
  await expect.poll(() => bodies.flag).toEqual({ engine: eng.name, model: eng.models[0] })
})

test('fix flagged lines needs a flagged line', async ({ page }) => {
  await page.route('**/api/review/dramas/3/lines?*', async (route) => {
    const r = await route.fetch()
    const body = await r.json()
    await route.fulfill({ response: r, json: { ...body, flagged_count: 0 } })
  })
  await page.goto('/#/drama/3/review')
  const fix = (await open(page, 'AI review')).getByRole('group', { name: 'Fix flagged lines' })
  await expect(fix.getByRole('button', { name: 'Fix flagged lines' })).toBeDisabled()
  await expect(fix).toContainText('Still needed: a flagged line.')
})

test('a running job and a start error show above the AI review section', async ({ page }) => {
  let refuse = false
  await page.route('**/api/review-jobs/dramas/3/consistency', (route) =>
    refuse
      ? route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Another job is running for this drama.' } } })
      : route.fulfill({ json: { job_id: 'cj', drama_id: 3, kind: 'consistency', engine: 'gemini', model: null, line_count: 4 } }))
  await page.route('**/api/jobs/cj', (route) => route.fulfill({ json: job('cj', 'running') }))
  await page.goto('/#/drama/3/review')
  const ai = await open(page, 'AI review')
  const group = page.getByRole('group', { name: 'AI checks' })

  await ai.getByRole('button', { name: 'Check consistency' }).click()
  await expect(ai.getByText('A review job is running.')).toBeVisible()
  await expect(ai.getByRole('button', { name: 'Tag emotion' })).toBeDisabled()
  await expect(group.locator(':scope > *').first()).toHaveAttribute('data-testid', 'job-panel')
  if (SHOTS) {
    await page.setViewportSize({ width: 1280, height: 800 })
    await group.scrollIntoViewIfNeeded()
    await group.screenshot({ path: `${SHOTS}/desktop-light-ai-running.png` })
  }

  await page.goto('about:blank')
  refuse = true
  await page.goto('/#/drama/3/review')
  await section(page, 'AI review').getByRole('button', { name: 'Check consistency' }).click()
  const banner = group.locator(':scope > .error-banner')
  await expect(banner).toBeVisible()
  if (SHOTS) {
    await group.scrollIntoViewIfNeeded()
    await group.screenshot({ path: `${SHOTS}/desktop-light-ai-start-error.png` })
  }
})

// Screenshots for review only (REVIEW_SHOTS=<dir>): desktop, light and dark.
test('screenshots', async ({ page }) => {
  test.skip(!SHOTS, 'REVIEW_SHOTS not set')
  seedReviewResults(25)
  await page.setViewportSize({ width: 1280, height: 800 })
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review')
    await expect(rows(page).first()).toBeVisible()
    for (const t of ['AI review', 'Options', 'Consistency', 'Emotion', 'Coverage and pacing', 'Compare versions']) await open(page, t)
    await section(page, 'Consistency').getByRole('button', { name: 'Show lines' }).click()
    await section(page, 'Compare versions').getByRole('button', { name: 'Show differences' }).click()
    await page.getByRole('group', { name: 'AI checks' }).screenshot({ path: `${SHOTS}/desktop-${scheme}-ai-review.png` })
    await section(page, 'Coverage and pacing').screenshot({ path: `${SHOTS}/desktop-${scheme}-coverage-over-20.png` })
    const row = rows(page).nth(0)
    await row.locator('.review-idx').click()
    await row.getByRole('button', { name: 'Edit details' }).click()
    await row.getByTestId('line-origin').locator('summary').click()
    await expect(row.getByTestId('line-origin')).toContainText('Engine')
    await row.screenshot({ path: `${SHOTS}/desktop-${scheme}-line-origin.png` })
  }
})
