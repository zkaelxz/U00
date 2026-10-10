import { expect, test } from '@playwright/test'
import { mockFinishedTranscribeWithNotice } from './ollamaNoticeMocks'

test('the Ollama notice fits a phone with no sideways scroll', async ({ page }) => {
  await mockFinishedTranscribeWithNotice(page)
  await page.goto('/#/jobs')
  const row = page.getByTestId('job-row-transcribe_1').first()
  await expect(row).toContainText('ollama stop gemma4:26b')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
