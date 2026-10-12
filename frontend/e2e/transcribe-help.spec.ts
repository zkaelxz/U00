import { expect, test, type Page } from '@playwright/test'
import { openAdvanced } from './sourceHelpers'

// The (i) beside "ASR backend" and "Use Groq" on the Transcribe stage.
async function openOptions(page: Page) {
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
}

test('the ASR backend (i) lists every backend', async ({ page }) => {
  await openOptions(page)
  const tip = page.getByRole('button', { name: 'Help: ASR backend' })
  await tip.focus()
  const text = page.getByRole('tooltip').filter({ hasText: 'Whisper:' })
  await expect(text).toBeVisible()
  for (const name of ['Whisper:', 'Qwen3 ASR:', 'Qwen3 ASR with speech detection:', 'Qwen3 ASR on long windows:']) {
    await expect(text).toContainText(name)
  }
  await tip.press('Escape')
  await expect(text).toBeHidden()
})

test('the Use Groq (i) explains the upload', async ({ page }) => {
  await openOptions(page)
  await page.getByRole('button', { name: 'Help: Use Groq' }).focus()
  const text = page.getByRole('tooltip').filter({ hasText: 'Groq' })
  await expect(text).toBeVisible()
  await expect(text).toContainText("Your audio is uploaded to Groq's servers")
  await expect(text).toContainText('Groq API key in Settings')
  await expect(text).toContainText('leaves your computer')
})
