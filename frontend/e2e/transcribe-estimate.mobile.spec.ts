import { expect, test } from '@playwright/test'

import { mockTranscribeCard, type MockJob } from './transcribeEstimateMocks'

// Phone (390px): the estimate line, separation note and running job card fit
// the width without sideways scroll.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('estimate line and job card fit at phone width', async ({ page }) => {
  const job: MockJob = { progress: 0.3, message: 'Transcribing... 30%' }
  await mockTranscribeCard(page, { durationSeconds: 7200, cached: false, separate: true }, job)
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('transcribe-estimate')).toContainText('Rough estimate')
  await expect(page.getByTestId('separation-note')).toBeVisible()
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByTestId('job-elapsed')).toContainText('elapsed')
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
