import { expect, test, type Page } from '@playwright/test'
import { openTranscribeOptions } from './sourceHelpers'

async function openAdvanced(page: Page) {
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  await page.locator('.section-title', { hasText: /^Advanced$/ }).click()
}

function help(page: Page) {
  return {
    button: page.getByRole('button', { name: 'Help: ASR backend' }),
    text: page.getByRole('tooltip').filter({ hasText: 'Whisper:' }),
  }
}

test('hover opens the (i) and leaving closes it', async ({ page }) => {
  await openAdvanced(page)
  const { button, text } = help(page)
  await button.hover()
  await expect(text).toBeVisible()
  await page.mouse.move(0, 0)
  await expect(text).toBeHidden()
})

test('a mouse click on the hovered (i) keeps it open', async ({ page }) => {
  await openAdvanced(page)
  const { button, text } = help(page)
  await button.click()
  await expect(text).toBeVisible()
  await expect(button).toHaveAttribute('aria-expanded', 'true')
})

test('keyboard: Tab opens, Enter and Space toggle, Escape and blur close', async ({ page }) => {
  await openAdvanced(page)
  const { button, text } = help(page)
  await button.focus()
  await expect(text).toBeVisible()
  expect(await button.getAttribute('aria-describedby')).toBe(await text.getAttribute('id'))
  await page.keyboard.press('Enter')
  await expect(text).toBeHidden()
  await page.keyboard.press('Space')
  await expect(text).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(text).toBeHidden()

  await page.keyboard.press('Enter')
  await expect(text).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(text).toBeHidden()
})

test('a click outside closes it', async ({ page }) => {
  await openAdvanced(page)
  const { button, text } = help(page)
  await button.focus()
  await expect(text).toBeVisible()
  await page.locator('.section-title', { hasText: /^Advanced$/ }).click()
  await expect(text).toBeHidden()
})
