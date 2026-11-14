import { expect, test, type Page } from '@playwright/test'

import { mockMakeSubtitles } from './makeSubtitlesMocks'
import { navLink, openMenu } from './settingsNav'

// Phone (390x844): the Add page from the menu drawer.

const AUDIO = { name: 'a.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') }

const noSideways = async (page: Page) => {
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('the drawer opens the page', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.goto('/')
  await openMenu(page)
  await navLink(page, 'Add').click()
  await expect(page).toHaveURL(/#\/make-subtitles$/)
  await expect(page.getByRole('heading', { name: 'Add', level: 1 })).toBeVisible()
  const c = page.getByRole('region', { name: 'Add' })
  await c.getByLabel('Audio or video file').setInputFiles(AUDIO)
  await c.getByText('Change options', { exact: true }).tap()
  // Everything is ready for the default translator, so the list stays out of the way until one is not.
  await expect(page.getByRole('region', { name: 'Before you run' })).toHaveCount(0)
  await c.getByLabel('Translator').selectOption('claude')
  await expect(page.getByRole('region', { name: 'Before you run' }).getByTestId('preflight-key')).toBeVisible()
})

for (const scheme of ['light', 'dark'] as const) {
  test(`${scheme}: no sideways scroll, before and after choosing a file`, async ({ page }) => {
    await mockMakeSubtitles(page)
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/#/make-subtitles')
    const c = page.getByRole('region', { name: 'Add' })
    await expect(c).toBeVisible()
    await noSideways(page)
    await c.getByLabel('Audio or video file').setInputFiles({ name: 'a-rather-long-file-name-for-a-phone-screen.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
    await noSideways(page)
  })
}
