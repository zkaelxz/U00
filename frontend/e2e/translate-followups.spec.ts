import { expect, test, type Page } from '@playwright/test'

// Reads hit the real seeded API; the follow-up endpoints are mocked, so nothing is translated.

const CONFIG = '**/api/translate-run/dramas/1/config'
const started = { job_id: 'tr-job', drama_id: 1, engine: 'claude', model: null, target_line_count: 2, fallback_engines: [], line_ids: [11, 12] }

async function withConfig(page: Page, extra: object) {
  const real = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  await page.route(CONFIG, (route) => route.fulfill({ json: { ...real, ...extra } }))
}

test('failed lines that kept older English are not called untranslated, and can be retried', async ({ page }) => {
  await withConfig(page, {
    last_translate_errors: [
      { batch_index: 7, lines: [467, 468], error: '60 of 60 lines got no translation (reply may have been cut off)' },
      { batch_index: 31, lines: [1842], error: 'timeout' },
    ],
    failed_untranslated_numbers: [],
    failed_kept_earlier_numbers: [468, 469, 1843],
    failed_retryable: true,
  })
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/translate-run/dramas/1/retry-failed/run', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: started })
  })
  await page.route('**/api/jobs/tr-job', (route) =>
    route.fulfill({ json: { job_id: 'tr-job', status: 'running', progress: 0.1, message: 'translating', error: null, description: null, gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1 } }))

  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Failed batches' })
  await expect(notice).toContainText('2 failed batches; lines 468-469, 1843 kept their earlier translation')
  await expect(notice).not.toContainText('untranslated')

  await notice.getByRole('button', { name: 'Retry these 3 lines' }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  expect(bodies).toHaveLength(1)
  for (const key of ['line_ids', 'force_retranslate', 'bulk', 'reflect']) expect(bodies[0]).not.toHaveProperty(key)
})

test('empty failed lines still read untranslated, with the retry button', async ({ page }) => {
  await withConfig(page, {
    last_translate_errors: [{ batch_index: 0, lines: [0, 1, 2, 8], error: 'engine timed out' }],
    failed_untranslated_numbers: [1, 2, 3, 9],
    failed_kept_earlier_numbers: [],
    failed_retryable: true,
  })
  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Failed batches' })
  await expect(notice).toContainText('1 failed batch; lines 1-3, 9 are still untranslated')
  await expect(notice.getByRole('button', { name: 'Retry these 4 lines' })).toBeEnabled()
})

test('a record from an older version shows the notice with no Retry button', async ({ page }) => {
  await withConfig(page, {
    last_translate_errors: [{ batch_index: 0, lines: [4], error: 'timeout' }],
    failed_untranslated_numbers: [], failed_kept_earlier_numbers: [], failed_retryable: false,
  })
  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Failed batches' })
  await expect(notice).toContainText('line 5 is still untranslated')
  await expect(notice).toContainText('older version')
  await expect(notice.getByRole('button', { name: /^Retry/ })).toHaveCount(0)
  await expect(notice.getByRole('button', { name: 'Dismiss notice' })).toBeEnabled()
})

test('a failed retry shows the error and keeps the notice', async ({ page }) => {
  await withConfig(page, {
    last_translate_errors: [{ batch_index: 0, lines: [4], error: 'timeout' }],
    failed_untranslated_numbers: [], failed_kept_earlier_numbers: [5], failed_retryable: true,
  })
  await page.route('**/api/translate-run/dramas/1/retry-failed/run', (route) =>
    route.fulfill({ status: 400, json: { error: { code: 'unsupported_operation', message: 'The monthly cap is used up.' } } }))
  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Failed batches' })
  await notice.getByRole('button', { name: 'Retry this line' }).click()
  await expect(notice.getByRole('alert')).toBeVisible()
  await expect(notice.getByRole('button', { name: 'Retry this line' })).toBeEnabled()
})

test('lines flagged for a missing glossary name offer a redo with the stronger engine', async ({ page }) => {
  await withConfig(page, { glossary_flagged_count: 2, glossary_redo_engine: 'claude' })
  let posted = 0
  await page.route('**/api/translate-run/dramas/1/glossary-flagged/run', (route) => {
    posted += 1
    return route.fulfill({ json: started })
  })
  await page.route('**/api/jobs/tr-job', (route) =>
    route.fulfill({ json: { job_id: 'tr-job', status: 'running', progress: 0.1, message: 'translating', error: null, description: null, gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1 } }))
  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Glossary check' })
  await expect(notice).toContainText('2 lines still miss a glossary name')
  await notice.getByRole('button', { name: /^Redo these with / }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  expect(posted).toBe(1)
})

test('without a stronger engine the glossary notice points to Settings and has no button', async ({ page }) => {
  await withConfig(page, { glossary_flagged_count: 1, glossary_redo_engine: null })
  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Glossary check' })
  await expect(notice).toContainText('Pick a stronger engine in Settings')
  await expect(notice.getByRole('button')).toHaveCount(0)
})

test('Details: Generate summary fills the episode summary box without saving it', async ({ page }) => {
  await page.route('**/api/translate-run/dramas/1/episode-summary', (route) =>
    route.fulfill({ json: { engine: 'ollama', engines: ['ollama', 'claude'] } }))
  const bodies: unknown[] = []
  await page.route('**/api/translate-run/dramas/1/episode-summary/generate', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { drama_id: 1, summary: 'They met at the lake.', engine: 'claude' } })
  })
  await page.goto('/#/drama/1/source')
  await page.getByText('Details and credits', { exact: true }).click()
  await page.getByText('Edit details', { exact: true }).click()
  const generate = page.getByTestId('episode-summary-generate')
  // Open by default when the title already has a summary.
  if (!(await generate.getByLabel('Summary engine').isVisible())) await page.getByText('More details', { exact: true }).click()
  await generate.getByLabel('Summary engine').selectOption('claude')
  await generate.getByRole('button', { name: 'Generate summary' }).click()
  await expect(page.getByRole('textbox', { name: 'Running episode summary' })).toHaveValue('They met at the lake.')
  expect(bodies).toEqual([{ engine: 'claude' }])
})
