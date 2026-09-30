import { expect, test } from '@playwright/test'

import { withTranslateLines } from './stageLineMocks'

// The run and job endpoints are mocked: nothing is translated. Config,
// estimate, glossary and characters reads hit the real seeded API.

const job = (status: string, extra: object = {}) => ({
  job_id: 'tr-job', status, progress: 0.5, message: 'translating', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...extra,
})

test('shows config, estimates, and starts a run with the chosen options', async ({ page }) => {
  const config = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  const other = config.engines.find((e: { name: string }) => e.name !== config.translation_engine).name

  const bodies: Record<string, unknown>[] = []
  let cancelled = false
  await page.route('**/api/translate-run/dramas/1/run', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({
      json: { job_id: 'tr-job', drama_id: 1, engine: config.translation_engine, model: null, target_line_count: 0, fallback_engines: [other] },
    })
  })
  await page.route('**/api/jobs/tr-job', (route) =>
    route.fulfill({ json: cancelled ? job('cancelled', { finished_at: 2 }) : job('running') }))
  await page.route('**/api/jobs/tr-job/cancel', (route) => {
    cancelled = true
    return route.fulfill({ json: { job_id: 'tr-job', cancel_requested: true, status: 'cancelled' } })
  })

  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByRole('button', { name: /^Translate \d+ lines?$/ })).toBeEnabled()
  await expect(run.getByLabel('Engine', { exact: true })).toBeVisible()
  // Options live in a collapsed Advanced section with a summary of non-default values.
  await expect(run.getByText('defaults', { exact: true })).toBeVisible()
  await expect(run.getByLabel('Batch size', { exact: true })).toBeHidden()

  await run.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(page.getByTestId('estimate').or(page.getByRole('alert'))).toBeVisible()

  await run.getByText('Advanced', { exact: true }).click()

  // Out-of-range values are caught before any request.
  await run.getByLabel('Batch size', { exact: true }).fill('500')
  await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
  await expect(run.getByRole('alert')).toContainText('Batch size')
  expect(bodies).toEqual([])

  await run.getByLabel('Batch size', { exact: true }).fill('10')
  await run.getByLabel('Cost cap', { exact: true }).fill('2.5')
  await run.getByRole('button', { name: 'Add fallback engine' }).click()
  await run.getByLabel('Fallback engine 1').selectOption(other)
  await run.getByRole('switch', { name: 'Re-translate existing' }).click()
  // Re-translate needs its confirmation: the primary is disabled and says so.
  await expect(run.getByRole('button', { name: /^Translate \d+ lines?$/ })).toBeDisabled()
  await expect(run.getByTestId('translate-blocker')).toContainText('confirm replacing the existing English')
  expect(bodies).toEqual([])

  await run.getByLabel(/I understand this replaces/).check()
  await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  expect(bodies[0]).toMatchObject({
    batch_size: 10,
    job_cost_cap_usd: 2.5,
    force_retranslate: true,
    fallback_chain: [{ engine: other }],
  })
  expect('line_ids' in bodies[0]).toBe(false)

  await page.getByRole('button', { name: 'Cancel job' }).click()
  await expect(page.getByTestId('job-status')).toContainText('cancelled')
})

test('a 409 on start says a translate job is already running', async ({ page }) => {
  await page.route('**/api/translate-run/dramas/1/run', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'busy' } } }))
  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  await page.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
  await expect(page.getByText('A translate job is already running for this drama.')).toBeVisible()
})

test('with no lines, Translate is disabled and links to Source (rule 22)', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByRole('button', { name: 'Translate 0 lines' })).toBeDisabled()
  const blocker = run.getByTestId('translate-blocker')
  await expect(blocker).toContainText('Still needed: lines to translate.')
  await blocker.getByRole('link', { name: 'Go to Source' }).click()
  await expect(page).toHaveURL(/#\/drama\/1\/source$/)
})

test('with every line translated, the reason offers Re-translate in one tap', async ({ page }) => {
  await withTranslateLines(page, 1, 4, 0)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByRole('button', { name: 'Translate 0 lines' })).toBeDisabled()
  await expect(run.getByTestId('translate-blocker')).toContainText('All 4 lines have English.')
  await run.getByRole('button', { name: 'Re-translate existing…' }).click()
  await run.getByLabel(/I understand this replaces/).check()
  await expect(run.getByRole('button', { name: 'Translate 4 lines' })).toBeEnabled()
  await expect(run.getByTestId('translate-blocker')).toHaveCount(0)
})

test('glossary and characters panels load; a drama without a series is told it cannot hold terms (X09)', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  // Both panels are collapsed Sections with a count badge; open them to reach the body.
  await page.locator('details.section', { hasText: 'Glossary' }).first().locator(':scope > summary').click()
  await page.locator('details.section', { hasText: 'Characters' }).first().locator(':scope > summary').click()
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expect(glossary.getByLabel('Project instructions')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Characters' })).toBeVisible()

  // Terms belong to a series: Add term waits for one, and the reason says so.
  await expect(glossary.getByTestId('series-assign')).toContainText("can't hold glossary terms")
  await expect(glossary.getByRole('button', { name: 'Add term' })).toBeDisabled()
  await expect(glossary.getByText('Still needed: a series (above).')).toBeVisible()
  await expect(glossary.getByRole('button', { name: /^Create series/ })).toBeVisible()
})

test('in a series, the term form checks required fields and shows a failed save', async ({ page }) => {
  // The drama read says series 7; the term save is mocked to fail, so nothing is written.
  const drama = await (await page.request.get('/api/library/dramas/1')).json()
  await page.route('**/api/library/dramas/1', (r) => r.fulfill({ json: { ...drama, series_id: 7 } }))
  await page.route('**/api/glossary/dramas/1/terms', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({ json: [] })
      : r.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'That term already exists.' } } }))
  await page.goto('/#/drama/1/translate')
  await page.locator('details.section', { hasText: 'Glossary' }).first().locator(':scope > summary').click()
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await glossary.getByRole('button', { name: 'Add term' }).click()
  await glossary.getByRole('button', { name: 'Save term' }).click()
  await expect(glossary.getByRole('alert')).toContainText('required')
  await glossary.getByRole('textbox', { name: 'Original', exact: true }).fill('Wei')
  await glossary.getByRole('textbox', { name: 'Translation', exact: true }).fill('Wei Wuxian')
  await glossary.getByRole('button', { name: 'Save term' }).click()
  await expect(glossary.getByRole('alert')).toBeVisible()
})

test('the last run\'s failed batches show a notice; Dismiss clears it (X01)', async ({ page }) => {
  const real = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  const errors = [
    { batch_index: 0, lines: [0, 1, 2], error: 'engine timed out' },
    { batch_index: 2, lines: [8], error: 'engine timed out' },
  ]
  await page.route('**/api/translate-run/dramas/1/config', (route) =>
    route.fulfill({ json: { ...real, last_translate_errors: errors } }))
  const dismissed: string[] = []
  await page.route('**/api/translate-run/dramas/1/errors/dismiss', (route) => {
    dismissed.push(route.request().method())
    return route.fulfill({ json: { drama_id: 1, dismissed: true } })
  })

  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Failed batches' })
  await expect(notice).toContainText('2 failed batches; lines 1-3, 9 are still untranslated')
  await expect(notice.getByRole('listitem')).toHaveText(['engine timed out'])
  await notice.getByRole('button', { name: 'Dismiss notice' }).click()
  await expect(notice).toHaveCount(0)
  expect(dismissed).toEqual(['POST'])
})

test('a failed dismiss keeps the notice and shows the error (X01)', async ({ page }) => {
  const real = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  await page.route('**/api/translate-run/dramas/1/config', (route) =>
    route.fulfill({ json: { ...real, last_translate_errors: [{ batch_index: 0, lines: [4] }] } }))
  await page.route('**/api/translate-run/dramas/1/errors/dismiss', (route) =>
    route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'You do not have permission to do that.' } } }))

  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Failed batches' })
  await expect(notice).toContainText('1 failed batch; line 5 is still untranslated')
  await notice.getByRole('button', { name: 'Dismiss notice' }).click()
  await expect(notice.getByRole('alert')).toContainText('Not allowed')
  await expect(notice.getByRole('button', { name: 'Dismiss notice' })).toBeEnabled()
})
