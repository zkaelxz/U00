import { expect, test, type Page } from '@playwright/test'

import { mockMakeSubtitles } from './makeSubtitlesMocks'
import { navLink, openSettingsGroups } from './settingsNav'

// Quick translate has its own page in the left menu (Tools). The run itself is
// covered in make-subtitles.spec.ts; these check where the page lives.

const noSideways = async (page: Page) => {
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('the left menu opens the page; it keeps Before you run, Options and the primary', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/')
  await navLink(page, 'Quick translate').click()
  await expect(page).toHaveURL(/#\/make-subtitles$/)
  await expect(page.getByRole('heading', { name: 'Quick translate', level: 1 })).toBeVisible()
  const c = page.getByRole('region', { name: 'Quick translate' })
  await expect(c.getByLabel('Audio or video file')).toBeVisible()
  await expect(c.getByLabel('Source language')).toBeVisible()
  await expect(c.getByLabel('Translator')).toBeVisible()
  // Everything is ready for the default translator, so the list stays out of the way until one is not.
  await expect(page.getByRole('region', { name: 'Before you run' })).toHaveCount(0)
  await page.getByRole('region', { name: 'Quick translate' }).getByLabel('Translator').selectOption('claude')
  await expect(page.getByRole('region', { name: 'Before you run' }).getByTestId('preflight-key')).toBeVisible()
  await expect(c.getByText('Options')).toBeVisible()
  await expect(c.getByRole('button', { name: 'Make subtitles' })).toBeDisabled()
  await expect(c.getByText('Still needed: an audio or video file.')).toBeVisible()
})

for (const scheme of ['light', 'dark'] as const) {
  test(`1280x800 ${scheme}: no sideways scroll`, async ({ page }) => {
    await mockMakeSubtitles(page)
    await page.emulateMedia({ colorScheme: scheme })
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/#/make-subtitles')
    await expect(page.getByRole('region', { name: 'Quick translate' })).toBeVisible()
    await noSideways(page)
  })
}

test('Menu items can hide it; the address still loads', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Account and display')
  const sw = page.getByRole('region', { name: 'Menu items' }).getByRole('switch', { name: 'Quick translate' })
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await expect(navLink(page, 'Quick translate')).toHaveCount(0)
  await page.goto('/#/make-subtitles')
  await expect(page.getByRole('region', { name: 'Quick translate' })).toBeVisible()
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Account and display')
  await page.getByRole('region', { name: 'Menu items' }).getByRole('switch', { name: 'Quick translate' }).click()
  await expect(navLink(page, 'Quick translate')).toBeVisible()
})
