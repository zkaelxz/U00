import { expect, test, type Page } from '@playwright/test'
import { nextFrames, openAdvanced } from './sourceHelpers'

// Real touch taps (touch events, then the emulated mouse, focus and click).
// The focus()-based specs never exercise that sequence.
async function openOptions(page: Page) {
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
}

test('a tap opens the (i), keeps it open, and a second tap closes it', async ({ page }) => {
  await openOptions(page)
  const button = page.getByRole('button', { name: 'Help: ASR backend' })
  const text = page.getByRole('tooltip').filter({ hasText: 'Whisper:' })

  // The emulated click arrives after focus and is what could wrongly close it: wait for that click itself.
  const clicked = button.evaluate((el) => new Promise<void>((done) => el.addEventListener('click', () => done(), { once: true })))
  await button.tap()
  await expect(text).toBeVisible()
  await expect(button).toHaveAttribute('aria-expanded', 'true')
  await clicked
  await nextFrames(page)
  await expect(text).toBeVisible()

  await button.tap()
  await expect(text).toBeHidden()
  await expect(button).toHaveAttribute('aria-expanded', 'false')
})

test('tapping outside closes the (i)', async ({ page }) => {
  await openOptions(page)
  const button = page.getByRole('button', { name: 'Help: ASR backend' })
  const text = page.getByRole('tooltip').filter({ hasText: 'Whisper:' })
  await button.tap()
  await expect(text).toBeVisible()

  await page.getByRole('heading', { name: 'Advanced options' }).tap()
  await expect(text).toBeHidden()
})

test('tapping the tooltip text keeps the (i) open', async ({ page }) => {
  await openOptions(page)
  const text = page.getByRole('tooltip').filter({ hasText: 'Whisper:' })
  await page.getByRole('button', { name: 'Help: ASR backend' }).tap()
  await text.tap()
  await expect(text).toBeVisible()
})

test('Escape closes a tapped (i)', async ({ page }) => {
  await openOptions(page)
  const text = page.getByRole('tooltip').filter({ hasText: 'Whisper:' })
  await page.getByRole('button', { name: 'Help: ASR backend' }).tap()
  await expect(text).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(text).toBeHidden()
})
