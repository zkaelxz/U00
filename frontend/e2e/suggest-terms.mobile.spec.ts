import { expect, test } from '@playwright/test'
import { withExportLines } from './stageLineMocks'

// Phone project (390x844, touch): the Suggest terms bar and the start card
// fit the width and keep 44px touch targets.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('the bar and the start card fit the phone with 44px targets', async ({ page }) => {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: [] }))
  await withExportLines(page, 1, 3)
  await page.goto('/#/drama/1/translate')
  const card = page.getByTestId('glossary-start')
  await expect(card).toBeVisible()
  const targets = [
    page.getByRole('combobox', { name: 'Suggest terms from' }),
    page.getByRole('region', { name: 'Glossary' }).locator('.suggest-bar').getByRole('button'),
    card.getByRole('button', { name: 'Suggest terms from the transcript' }),
    card.getByRole('button', { name: 'Add a term' }),
    card.getByRole('button', { name: 'Import a file' }),
  ]
  for (const t of targets) {
    const box = await t.boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
