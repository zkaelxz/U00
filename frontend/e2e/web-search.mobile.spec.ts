import { expect, test } from '@playwright/test'

import { mockSources } from './sourcesMocks'
import { emptySearch, mockWebSearch } from './webSearchMocks'

// Phone project (390x844, touch): the web-search fallback under an empty
// Sources search, and its Settings card.

test('phone: web results fit, targets are 44px', async ({ page }) => {
  const s = await mockSources(page, { searchBody: emptySearch() })
  await mockWebSearch(page, true)
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Nowhere Title')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  s.search = 'done'
  await page.getByRole('button', { name: 'Search the web' }).click()
  await expect(page.getByTestId('web-results')).toContainText('2 web results')
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  const small = await page.getByTestId('web-search').evaluate((root) =>
    [...root.querySelectorAll<HTMLElement>('button')]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent || '').trim() }))
      .filter((x) => x.h < 44),
  )
  expect(small).toEqual([])
  await page.getByRole('button', { name: 'Use this link' }).first().click()
  await expect(page.getByRole('textbox', { name: 'Paste a link' })).toHaveValue('https://wiki.example/nowhere')
})

test('phone: settings card fits', async ({ page }) => {
  await page.route('**/api/web-search/config', (route) => route.fulfill({ json: { enabled: true, base_url: 'http://192.168.1.20:8888' } }))
  await page.goto('/#/settings')
  const card = page.getByRole('region', { name: 'Web search' })
  await expect(card).toContainText('On')
  await card.scrollIntoViewIfNeeded()
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
  for (const name of ['Save', 'Test']) {
    const box = await card.getByRole('button', { name, exact: true }).boundingBox()
    expect(box!.height).toBeGreaterThanOrEqual(44)
  }
})
