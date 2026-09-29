import { expect, test } from '@playwright/test'

// Step 105: the "Detect speakers only" control sends a min/max speaker
// range. The run and job endpoints are mocked; reads hit the seeded API.

test('detect speakers only sends a speaker range and catches a bad one', async ({ page }) => {
  const urls: string[] = []
  await page.route('**/api/diarization/dramas/1/run**', async (route) => {
    urls.push(route.request().url())
    await route.fulfill({ json: { job_id: 'fake-diarize' } })
  })
  await page.route('**/api/jobs/fake-diarize', (route) =>
    route.fulfill({
      json: {
        job_id: 'fake-diarize', status: 'running', progress: 0, message: 'working', error: null,
        description: null, gpu_touching: true, started_at: 1, finished_at: null, updated_at: 1,
      },
    }))
  await page.route('**/api/jobs/fake-diarize/cancel', (route) => route.abort())

  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('region', { name: 'Transcribe' })).toBeVisible()
  const detect = page.getByRole('button', { name: 'Detect speakers only' })

  // An inverted range is caught before anything is sent.
  await page.getByLabel('Min speakers', { exact: true }).fill('4')
  await page.getByLabel('Max speakers', { exact: true }).fill('2')
  await detect.click()
  await expect(page.getByRole('alert')).toContainText('more than max')
  expect(urls).toEqual([])

  // A count together with a range is caught too.
  await page.getByLabel('Min speakers', { exact: true }).fill('2')
  await page.getByLabel('Max speakers', { exact: true }).fill('4')
  await page.getByLabel('Expected speakers', { exact: true }).fill('3')
  await detect.click()
  await expect(page.getByRole('alert')).toContainText('not both')
  expect(urls).toEqual([])

  await page.getByLabel('Expected speakers', { exact: true }).fill('')
  await detect.click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  expect(urls).toHaveLength(1)
  const sent = new URL(urls[0]).searchParams
  expect(sent.get('min_speakers')).toBe('2')
  expect(sent.get('max_speakers')).toBe('4')
  expect(sent.get('expected_speakers')).toBeNull()
})
