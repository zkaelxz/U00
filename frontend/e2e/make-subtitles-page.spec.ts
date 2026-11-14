import { expect, test, type Page } from '@playwright/test'

import { mockMakeSubtitles } from './makeSubtitlesMocks'
import { navLink, openSettingsGroups } from './settingsNav'

// Add has its own page in the left menu (Find and add). The run itself is
// covered in make-subtitles.spec.ts; these check where the page lives.

const AUDIO = { name: 'a.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') }

const noSideways = async (page: Page) => {
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('the left menu opens the page; a file shows Before you run, Change options and the primary', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/')
  await navLink(page, 'Add').click()
  await expect(page).toHaveURL(/#\/make-subtitles$/)
  await expect(page.getByRole('heading', { name: 'Add', level: 1 })).toBeVisible()
  // The floating menu stays open while the pointer is over it, covering the start of the box.
  await page.mouse.move(1000, 400)
  await expect(page.locator('.app-rail')).not.toHaveClass(/is-open/)
  const c = page.getByRole('region', { name: 'Add' })
  await expect(c.getByRole('textbox', { name: 'Paste a link' })).toBeVisible()
  await expect(c.getByRole('button', { name: 'Choose a file' })).toHaveAttribute('title', 'Or drop an audio or video file on this box')
  await expect(c.getByRole('link', { name: 'Open Text' })).toHaveAttribute('href', '#/translate')
  await c.getByLabel('Audio or video file').setInputFiles(AUDIO)
  await c.getByText('Change options', { exact: true }).click()
  await expect(c.getByLabel('Source language')).toBeVisible()
  await expect(c.getByLabel('Translator')).toBeVisible()
  // Everything is ready for the default translator, so the list stays out of the way until one is not.
  await expect(page.getByRole('region', { name: 'Before you run' })).toHaveCount(0)
  await c.getByLabel('Translator').selectOption('claude')
  await expect(page.getByRole('region', { name: 'Before you run' }).getByTestId('preflight-key')).toBeVisible()
  await expect(c.getByRole('button', { name: 'Make English subtitles' })).toBeDisabled()
  await expect(c.getByText('Still needed: add a Claude key.')).toBeVisible()
})

test('a file dropped on the box is taken like a chosen one', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.goto('/#/make-subtitles')
  const c = page.getByRole('region', { name: 'Add' })
  await expect(c).toBeVisible()
  const data = await page.evaluateHandle(() => {
    const dt = new DataTransfer()
    dt.items.add(new File(['abc'], 'Dropped.wav', { type: 'audio/wav' }))
    return dt
  })
  await c.dispatchEvent('dragover', { dataTransfer: data })
  await expect(c).toHaveClass(/is-dragging/)
  await c.dispatchEvent('drop', { dataTransfer: data })
  await expect(c.getByTestId('add-found')).toHaveText('Found: an audio file, “Dropped.wav”, 3 B.')
  await expect(c).not.toHaveClass(/is-dragging/)
})

for (const scheme of ['light', 'dark'] as const) {
  test(`1280x800 ${scheme}: no sideways scroll`, async ({ page }) => {
    await mockMakeSubtitles(page)
    await page.emulateMedia({ colorScheme: scheme })
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/#/make-subtitles')
    await expect(page.getByRole('region', { name: 'Add' })).toBeVisible()
    await noSideways(page)
  })
}

test('Menu items can hide it; the address still loads', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Account and display')
  const sw = page.getByRole('region', { name: 'Menu items' }).getByRole('switch', { name: 'Add' })
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await expect(navLink(page, 'Add')).toHaveCount(0)
  await page.goto('/#/make-subtitles')
  await expect(page.getByRole('region', { name: 'Add' })).toBeVisible()
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Account and display')
  await page.getByRole('region', { name: 'Menu items' }).getByRole('switch', { name: 'Add' }).click()
  await expect(navLink(page, 'Add')).toBeVisible()
})
