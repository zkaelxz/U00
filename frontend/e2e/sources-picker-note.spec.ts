import { expect as baseExpect, test, type Page } from '@playwright/test'

import { COMIC_PAGE_PREVIEW } from './sourcesExtractionMocks'
import { DRAMAS, NOVEL_PREVIEW, mockImports } from './sourcesImportMocks'
import { mockSources, posted } from './sourcesMocks'
import { installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// "Import into" lists only titles the import can write to, and says how many
// others are left out. Runs on desktop and phone; every call is mocked.
const expect = baseExpect.configure({ timeout: 15_000 })

const LIBRARY = [DRAMAS[0], DRAMAS[1]]

async function paste(page: Page, url: string) {
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill(url)
  await page.getByRole('button', { name: 'Preview' }).click()
  return page.getByRole('article', { name: 'Link preview' })
}

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('comic link: only the comic is listed and the note counts the hidden title', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: COMIC_PAGE_PREVIEW, dramas: LIBRARY })
  const card = await paste(page, 'https://comics.example/read/5')
  const into = card.getByRole('combobox', { name: 'Import into' })
  await expect(into.locator('option')).toHaveText(['Choose a drama…', 'Alpha Comic', 'New drama…'])
  await expect(card.getByText(/^Showing comic titles only\. 1 other title isn’t listed because manga pages/)).toBeVisible()
  await noSideways(page)

  await into.selectOption({ label: 'New drama…' })
  await card.getByRole('group', { name: 'New drama' }).getByRole('button', { name: 'Create drama' }).click()
  await expect.poll(() => posted(s, '/api/dramas').length).toBe(1)
  await expect(into).toHaveValue('21')
})

test('novel link: only the novel is listed and the note counts the hidden title', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: NOVEL_PREVIEW, dramas: LIBRARY })
  const card = await paste(page, 'https://novels.example/book/5')
  const into = card.getByRole('combobox', { name: 'Import into' })
  await expect(into.locator('option')).toHaveText(['Choose a drama…', 'Heaven Novel', 'New drama…'])
  await expect(card.getByText(/^Showing novel titles only\. 1 other title isn’t listed because chapter text/)).toBeVisible()
  await noSideways(page)

  await into.selectOption({ label: 'New drama…' })
  await card.getByRole('group', { name: 'New drama' }).getByRole('button', { name: 'Create drama' }).click()
  await expect.poll(() => posted(s, '/api/dramas').length).toBe(1)
  await expect(into).toHaveValue('21')
})
