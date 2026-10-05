import { expect as baseExpect, test, type Page } from '@playwright/test'

import { mockImports } from './sourcesImportMocks'
import { mockSources } from './sourcesMocks'
import { VIDEO_PREVIEW, mockTools } from './sourcesToolsMocks'
import { installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone (390x844, touch): the Sources tools fit without sideways scrolling
// and their buttons are finger-sized.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function tallButtons(page: Page, root: string) {
  const small = await page.locator(root).first().evaluate((el) =>
    [...el.querySelectorAll<HTMLElement>('button:not(.field-help-btn), select, textarea')]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44),
  )
  expect(small).toEqual([])
}

test('phone: site check, pasted source and identify media', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, { previewHold: true })
  await mockTools(page, s, m, { pastedPreview: { ...VIDEO_PREVIEW, pasted: true } })
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://video.example/watch?v=1&' + 'x'.repeat(200))
  await page.getByRole('button', { name: 'Will this site work?' }).click()
  await expect(page.getByTestId('site-check')).toBeVisible()
  await noSideways(page)
  await tallButtons(page, '.sources-sitecheck')

  await page.getByRole('button', { name: 'Preview' }).click()
  m.preview = 'handoff'
  const paste = page.getByRole('group', { name: 'Continue from pasted page' })
  await paste.getByRole('textbox').fill('<html><video src="https://cdn.example/a.mp4"></video></html>')
  await noSideways(page)
  await tallButtons(page, '.sources-handoff')
  await paste.getByRole('button', { name: 'Continue from pasted page' }).click()

  const card = page.getByRole('article', { name: 'Link preview' })
  await card.getByRole('button', { name: 'Identify media on this page' }).click()
  await expect(card.getByTestId('media-identify')).toBeVisible()
  await noSideways(page)
  expect(s.unmocked).toEqual([])
})
