import { expect, test, type Page } from '@playwright/test'
import { openTranscribeOptions } from './sourceHelpers'

// Phone project: the (i) text stays inside the screen. Opened with focus(),
// like review-bulk.spec.ts: a tap focuses then clicks, and Field's click
// toggle closes what the focus just opened.
async function openAdvanced(page: Page) {
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  await page.locator('.section-title', { hasText: /^Advanced$/ }).click()
}

async function expectOnScreen(page: Page, text: ReturnType<Page['getByRole']>) {
  const box = await text.boundingBox()
  const width = await page.evaluate(() => document.documentElement.clientWidth)
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(width)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
}

test('the ASR backend (i) opens and fits the phone', async ({ page }) => {
  await openAdvanced(page)
  await page.getByRole('button', { name: 'Help: ASR backend' }).focus()
  const text = page.getByRole('tooltip').filter({ hasText: 'Whisper:' })
  await expect(text).toBeVisible()
  await expect(text).toContainText('Qwen3 ASR on long windows:')
  await expectOnScreen(page, text)
})

test('the Use Groq (i) opens and fits the phone', async ({ page }) => {
  await openAdvanced(page)
  await page.getByRole('button', { name: 'Help: Use Groq' }).focus()
  const text = page.getByRole('tooltip').filter({ hasText: "Groq's servers" })
  await expect(text).toBeVisible()
  await expect(text).toContainText('leaves your computer')
  await expectOnScreen(page, text)
})
