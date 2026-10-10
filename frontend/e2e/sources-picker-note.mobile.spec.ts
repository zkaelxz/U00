import { expect as baseExpect, test, type Page } from '@playwright/test'

import { COMIC_PAGE_PREVIEW } from './sourcesExtractionMocks'
import { DRAMAS, NOVEL_PREVIEW, mockImports } from './sourcesImportMocks'
import { mockSources, posted } from './sourcesMocks'
import { installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// "Import into" lists only titles the import can write to, and says how many
// others are left out. Phone project (390x844, touch): the note wraps with no sideways scroll and 44 px targets. Every call is mocked.
const expect = baseExpect.configure({ timeout: 15_000 })

const LIBRARY = [DRAMAS[0], DRAMAS[1]]

async function paste(page: Page, url: string) {
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill(url)
  await page.getByRole('button', { name: 'Preview' }).click()
  return page.getByRole('article', { name: 'Link preview' })
}

async function fits(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  const small = await page.locator('.sources-page').evaluate((root) => {
    const sel = 'button:not(.link):not(.field-help-btn):not(.toggle), select'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

test('comic link: only the comic is listed and the note counts the hidden title', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: COMIC_PAGE_PREVIEW, dramas: LIBRARY })
  const card = await paste(page, 'https://comics.example/read/5')
  const into = card.getByRole('combobox', { name: 'Import into' })
  await expect(into.locator('option')).toHaveText(['Choose a title…', 'Alpha Comic', 'New title…'])
  await expect(card.getByText(/^Showing comic titles only\. 1 other title isn’t listed because manga pages/)).toBeVisible()
  await fits(page)

  await into.selectOption({ label: 'New title…' })
  await card.getByRole('group', { name: 'New title' }).getByRole('button', { name: 'Create title' }).click()
  await expect.poll(() => posted(s, '/api/dramas').length).toBe(1)
  await expect(into).toHaveValue('21')
})

test('novel link: only the novel is listed and the note counts the hidden title', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: NOVEL_PREVIEW, dramas: LIBRARY })
  const card = await paste(page, 'https://novels.example/book/5')
  const into = card.getByRole('combobox', { name: 'Import into' })
  await expect(into.locator('option')).toHaveText(['Choose a title…', 'Heaven Novel', 'New title…'])
  await expect(card.getByText(/^Showing novel titles only\. 1 other title isn’t listed because chapter text/)).toBeVisible()
  await fits(page)

  await into.selectOption({ label: 'New title…' })
  await card.getByRole('group', { name: 'New title' }).getByRole('button', { name: 'Create title' }).click()
  await expect.poll(() => posted(s, '/api/dramas').length).toBe(1)
  await expect(into).toHaveValue('21')
})
