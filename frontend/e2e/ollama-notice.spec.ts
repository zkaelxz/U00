import { expect, test } from '@playwright/test'
import { mockFinishedTranscribeWithNotice } from './ollamaNoticeMocks'

test.use({ viewport: { width: 1280, height: 800 } })

test('the Jobs list shows the Ollama notice on a finished transcription', async ({ page }) => {
  await mockFinishedTranscribeWithNotice(page)
  await page.goto('/#/jobs')
  const row = page.getByTestId('job-row-transcribe_1').first()
  await expect(row).toContainText('Finished with problems')
  await expect(row).toContainText('Ollama still has a model loaded (gemma4:26b)')
  await expect(row).toContainText('ollama stop gemma4:26b')
})
