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

test('a page hidden through Menu items is not listed, but its address still works', async ({ page }) => {
  await expectHiddenItemNotListed(page, viaShortcut(page))
})

test('typing "srt" finds Make subtitles', async ({ page }) => {
  await page.goto('/#/library')
  await viaShortcut(page)()
  await paletteInput(page).fill('srt')
  await expect(palette(page).getByRole('option', { name: /^Make subtitles/ })).toBeVisible()
})

test('a settings hit opens its card on the right tab, scrolled into view', async ({ page }) => {
  await page.goto('/#/library')
  await viaShortcut(page)()
  await paletteInput(page).fill('upload size')
  await palette(page).getByRole('option', { name: /^Upload size limit/ }).click()
  await expect(page).toHaveURL(/#\/settings\?card=advanced$/)
  await expect(page.getByRole('tab', { name: 'Performance and advanced' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('region', { name: 'Offline, paths and limits' })).toBeInViewport()
})

test('"/" focuses the page search and "?" lists the keys, neither while typing', async ({ page }) => {
  await page.goto('/#/settings')
  const search = page.getByRole('searchbox', { name: 'Search settings' })
  await expect(search).toBeVisible()
  await page.keyboard.press('/')
  await expect(search).toBeFocused()
  await search.fill('a?b')
  await expect(search).toHaveValue('a?b')
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeHidden()
  await search.blur()
  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeHidden()
})

test('a title is found by its original name and opens its workspace', async ({ page }) => {
  await page.goto('/#/library')
  await viaShortcut(page)()
  await paletteInput(page).fill('魔道')
  const hit = palette(page).getByRole('option', { name: /^Grandmaster of Demonic Cultivation/ })
  await expect(hit).toBeVisible()
  await hit.click()
  await expect(page).toHaveURL(/#\/drama\/\d+$/)
})

test('inside a title, Ctrl+K finds a line and Enter opens Review searched for the words', async ({ page }) => {
  await page.route('**/api/review/dramas/1/search*', (route) =>
    route.fulfill({ json: [{ id: 501, idx: 0, start: 0, end: 1, zh: '月亮', en: 'The moon rises', speaker: null, speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null }] }),
  )
  await page.goto('/#/drama/1/review')
  await viaShortcut(page)()
  await paletteInput(page).fill('moon')
  const row = palette(page).getByRole('option', { name: /The moon rises/ })
  await expect(row).toBeVisible()
  await row.click()
  await expect(page).toHaveURL(/#\/drama\/1\/review\?find=moon$/)
  await expect(page.getByRole('searchbox').first()).toHaveValue('moon')
})
