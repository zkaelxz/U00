import { expect, test } from '@playwright/test'

import { mockTranscribeCard, type MockJob } from './transcribeEstimateMocks'

// The Transcribe card's rough time estimate, and the running job's elapsed
// time and "about N min left". Everything the card reads is mocked, and the
// page clock is driven by hand so 30+ seconds of progress take no real time.

test.use({ viewport: { width: 1440, height: 900 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('the estimate line says rough, names model and device, and notes a download and separation', async ({ page }) => {
  await mockTranscribeCard(page, { durationSeconds: 3600, cached: false, separate: true }, { progress: 0, message: '' })
  await page.goto('/#/drama/1/source')
  const line = page.getByTestId('transcribe-estimate')
  await expect(line).toContainText('Rough estimate: about 1.5-4 h for this audio (large-v3 on CPU).')
  await expect(line).toContainText('First use also downloads the model.')
  await expect(page.getByTestId('separation-note')).toHaveText('Vocal separation adds time, a lot on CPU.')
})

test('no estimate (and no error) when the audio length is unknown', async ({ page }) => {
  await mockTranscribeCard(page, { durationSeconds: 0 }, { progress: 0, message: '' })
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeVisible()
  await expect(page.getByTestId('transcribe-estimate')).toHaveCount(0)
  await expect(page.locator('.error-banner')).toHaveCount(0)
})

test('the job card shows elapsed, then "about N min left", and only elapsed in a no-percent step', async ({ page }) => {
  const job: MockJob = { progress: 0, message: 'Transcribing... starting; the percent appears once the first lines are found' }
  await page.clock.install()
  await mockTranscribeCard(page, { durationSeconds: 3600 }, job)
  await page.goto('/#/drama/1/source')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()

  const elapsed = page.getByTestId('job-elapsed')
  await expect(elapsed).toContainText('elapsed')
  await expect(elapsed).not.toContainText('left')

  // 1% every 5 s from 10%: not shown before 30 s have passed since the first percent...
  job.progress = 0.1
  job.message = 'Transcribing... 10%'
  await page.clock.runFor(2000)
  for (let i = 1; i <= 4; i++) {
    job.progress = 0.1 + i * 0.01
    job.message = `Transcribing... ${Math.round(job.progress * 100)}%`
    await page.clock.runFor(5000)
  }
  await expect(page.getByTestId('job-percent')).toHaveText('14%')
  await expect(elapsed).not.toContainText('left')
  // ...and shown after.
  for (let i = 5; i <= 10; i++) {
    job.progress = 0.1 + i * 0.01
    job.message = `Transcribing... ${Math.round(job.progress * 100)}%`
    await page.clock.runFor(5000)
  }
  await expect(elapsed).toContainText(/about \d+ min left/)

  // A step with no percent of its own: elapsed only.
  job.progress = 0.85
  job.message = 'Re-transcribing with Qwen3-ASR (step 2 of 2; no percent until the first batch finishes) (elapsed 5s). This stage can take several minutes; no progress is available.'
  await page.clock.runFor(3000)
  await expect(page.getByTestId('job-status')).toContainText('elapsed 5s')
  await expect(elapsed).toHaveCount(0)
  await expect(page.getByText(/min left/)).toHaveCount(0)
})
