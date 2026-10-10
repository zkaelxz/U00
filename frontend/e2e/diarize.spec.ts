import { expect, test } from '@playwright/test'
import { openTranscribeOptions } from './sourceHelpers'

// The "Detect speakers only" control sends a min/max speaker
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
  // Speaker counts and "Detect speakers only" live in the Speakers section.
  await openTranscribeOptions(page)
  await page.locator('.section-title', { hasText: /^More options$/ }).click()
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
  await expect(page.getByTestId('job-status')).toContainText('Running')
  expect(urls).toHaveLength(1)
  const sent = new URL(urls[0]).searchParams
  expect(sent.get('min_speakers')).toBe('2')
  expect(sent.get('max_speakers')).toBe('4')
  expect(sent.get('expected_speakers')).toBeNull()
})

// The Min/Max range also goes with "Detect speakers
// after transcribing" (sent in the transcribe run body).
test('transcribe with speaker detection sends the speaker range', async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/transcribe/dramas/1/run', async (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({ json: { job_id: 'fake-transcribe' } })
  })
  await page.route('**/api/jobs/fake-transcribe', (route) =>
    route.fulfill({
      json: {
        job_id: 'fake-transcribe', status: 'running', progress: 0, message: 'working', error: null,
        description: null, gpu_touching: true, started_at: 1, finished_at: null, updated_at: 1,
      },
    }))
  await page.route('**/api/jobs/fake-transcribe/cancel', (route) => route.abort())

  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('region', { name: 'Transcribe' })).toBeVisible()
  await openTranscribeOptions(page)
  const detectAfter = page.getByRole('switch', { name: 'Detect speakers after transcribing' })
  if (!(await detectAfter.isChecked())) await detectAfter.click()
  await page.locator('.section-title', { hasText: /^More options$/ }).click()
  await page.getByLabel('Expected speakers', { exact: true }).fill('')
  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')

  // An inverted range is caught before anything is sent.
  await page.getByLabel('Min speakers', { exact: true }).fill('4')
  await page.getByLabel('Max speakers', { exact: true }).fill('2')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('more than max')
  expect(bodies).toEqual([])

  await page.getByLabel('Min speakers', { exact: true }).fill('2')
  await page.getByLabel('Max speakers', { exact: true }).fill('4')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0]).toMatchObject({ run_diarize: true, min_speakers: 2, max_speakers: 4 })
  expect(bodies[0].expected_speakers).toBeUndefined()
})
