import { expect, test } from '@playwright/test'

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

  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByTestId('translate-counts')).toContainText('lines have no English yet')
  await expect(run.getByLabel('Engine', { exact: true })).toContainText('key configured')

  await run.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(page.getByTestId('estimate').or(page.getByRole('alert'))).toBeVisible()

  // Out-of-range values are caught before any request.
  await run.getByLabel('Batch size (1-200)').fill('500')
  await run.getByRole('button', { name: 'Start translation' }).click()
  await expect(run.getByRole('alert')).toContainText('Batch size')
  expect(bodies).toEqual([])

  await run.getByLabel('Batch size (1-200)').fill('10')
  await run.getByLabel(/Cost cap/).fill('2.5')
  await run.getByRole('button', { name: 'Add fallback engine' }).click()
  await run.getByLabel('Fallback engine 1').selectOption(other)
  await run.getByLabel('Re-translate lines that already have English').check()
  await run.getByRole('button', { name: 'Start translation' }).click()
  await expect(run.getByRole('alert')).toContainText('Tick the confirmation')
  expect(bodies).toEqual([])

  await run.getByLabel(/I understand this replaces/).check()
  await run.getByRole('button', { name: 'Start translation' }).click()
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
  await page.goto('/#/drama/1/translate')
  await page.getByRole('button', { name: 'Start translation' }).click()
  await expect(page.getByText('A translate job is already running for this drama.')).toBeVisible()
})

test('glossary and characters panels load; a term for a drama without a series shows a banner', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expect(glossary.getByLabel('Project instructions')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Characters' })).toBeVisible()

  await glossary.getByRole('button', { name: 'Add term' }).click()
  await glossary.getByRole('button', { name: 'Save term' }).click()
  await expect(glossary.getByRole('alert')).toContainText('required')

  await glossary.getByRole('textbox', { name: 'Original', exact: true }).fill('Wei')
  await glossary.getByRole('textbox', { name: 'Translation', exact: true }).fill('Wei Wuxian')
  await glossary.getByRole('button', { name: 'Save term' }).click()
  await expect(glossary.getByRole('alert')).toBeVisible()
})
