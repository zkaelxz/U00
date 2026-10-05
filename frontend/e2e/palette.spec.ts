import { expect, test } from '@playwright/test'

import { expectFilterAndEnter, expectHiddenItemNotListed, expectNoMatches, palette, paletteInput, searchButton } from './paletteHelpers'

// Ctrl+K quick search at the desktop viewport (left rail), against the seeded API (auth off).

const viaShortcut = (page: import('@playwright/test').Page) => async () => {
  // The listener exists once the session has answered and the header has rendered.
  await expect(searchButton(page)).toBeVisible()
  await page.keyboard.press('Control+k')
  await expect(palette(page)).toBeVisible()
}
const viaButton = (page: import('@playwright/test').Page) => async () => {
  await searchButton(page).click()
  await expect(palette(page)).toBeVisible()
}

test('opens with Ctrl+K, filters and Enter goes to the page', async ({ page }) => {
  await expectFilterAndEnter(page, viaShortcut(page))
})

test('opens with the header button, and Esc closes it and returns focus to the button', async ({ page }) => {
  await page.goto('/#/library')
  await searchButton(page).click()
  await expect(palette(page)).toBeVisible()
  await expect(paletteInput(page)).toBeFocused()
  await expect(palette(page).getByRole('listbox')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(palette(page)).toBeHidden()
  await expect(searchButton(page)).toBeFocused()
})

test('Ctrl+K from a text field opens it and Esc gives focus back to that field', async ({ page }) => {
  await page.goto('/#/library')
  const field = page.locator('input[type="search"], input[type="text"]').first()
  await field.focus()
  await expect(searchButton(page)).toBeVisible()
  await page.keyboard.press('Control+k')
  await expect(palette(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(field).toBeFocused()
})

test('a plain k typed in a text field does not open it, and Ctrl+K again closes it', async ({ page }) => {
  await page.goto('/#/library')
  await page.locator('input[type="search"], input[type="text"]').first().focus()
  await expect(searchButton(page)).toBeVisible()
  await page.keyboard.press('k')
  await expect(palette(page)).toBeHidden()
  await page.keyboard.press('Control+k')
  await expect(palette(page)).toBeVisible()
  await page.keyboard.press('Control+k')
  await expect(palette(page)).toBeHidden()
})

test('arrow keys move the selection and Tab stays inside the dialog', async ({ page }) => {
  await page.goto('/#/library')
  await viaShortcut(page)()
  const first = await paletteInput(page).getAttribute('aria-activedescendant')
  await page.keyboard.press('ArrowDown')
  expect(await paletteInput(page).getAttribute('aria-activedescendant')).not.toBe(first)
  await page.keyboard.press('ArrowUp')
  await expect(paletteInput(page)).toHaveAttribute('aria-activedescendant', first!)
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press('Tab')
    expect(await page.evaluate(() => !!document.activeElement?.closest('dialog.palette')), `Tab ${i}`).toBe(true)
  }
})

test('inside a title the stages are listed first and Enter opens one', async ({ page }) => {
  await page.goto('/#/drama/2/source')
  await viaShortcut(page)()
  await paletteInput(page).fill('review')
  await paletteInput(page).press('Enter')
  await expect(page).toHaveURL(/#\/drama\/2\/review$/)
})

test('shows a no-matches state', async ({ page }) => {
  await expectNoMatches(page, viaShortcut(page))
})

test('a page hidden through Customize menu is not listed, but its address still works', async ({ page }) => {
  await expectHiddenItemNotListed(page, viaShortcut(page))
})
