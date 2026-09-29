import { expect, test, type Page } from '@playwright/test'

import { mockSources, searchResult } from './sourcesMocks'

// Phone project (390x844, touch): the Sources page. Every Sources job and
// write is mocked (sourcesMocks.ts).

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

// Visible buttons, selects, text inputs and checkbox labels on the page are at least 44 px tall.
async function tallTargets(page: Page) {
  const small = await page.locator('.sources-page').evaluate((root) => {
    const sel = 'button:not(.link):not(.field-help-btn), select, input[type="search"], input[type="number"], .sources-checks label, .source-card-line label, .sources-back'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

test('phone: series replaces results, ‹ Results restores them, no sideways scroll', async ({ page }) => {
  const s = await mockSources(page, { searchBody: { ...searchResult(12), errors: {} } })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByText(/Searching…/)).toBeVisible()
  s.search = 'done'
  await expect(page.getByText('12 results', { exact: true })).toBeVisible()
  await page.getByText('Search in').click()
  await noSideways(page)
  await tallTargets(page)

  const opener = page.getByRole('button', { name: 'Open on Alpha Comics' }).nth(5)
  await opener.scrollIntoViewIfNeeded()
  await opener.click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText('Alpha Comics · 124 chapters · ongoing · zh')).toBeVisible()
  await expect(page.getByTestId('search-results')).toBeHidden()
  await expect(panel.getByRole('heading', { level: 3 })).toBeFocused()
  await noSideways(page)
  await tallTargets(page)
  const more = panel.getByRole('button', { name: 'More' })
  expect((await more.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await more.click()
  await expect(panel.getByRole('button', { name: 'Less' })).toBeVisible()

  // "‹ Results" at the top and again after the chapter list; use the bottom one.
  await expect(panel.getByRole('button', { name: '‹ Results' })).toHaveCount(2)
  await panel.getByRole('button', { name: '‹ Results' }).last().click()
  await expect(page.getByTestId('search-results')).toBeVisible()
  await expect(opener).toBeFocused()
  await expect(page.locator('img')).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('phone: source settings render as cards with 44 px toggles', async ({ page }) => {
  const s = await mockSources(page)
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').click()
  await expect(settings.locator('table')).toHaveCount(0)
  const cards = settings.locator('ul.source-cards > li')
  await expect(cards).toHaveCount(3)
  await expect(cards.first()).toContainText('OK')
  await expect(cards.nth(1)).toContainText('Sign-in saved')
  await settings.getByText('Pacing & cache').click()
  await noSideways(page)
  await tallTargets(page)
  expect(s.unmocked).toEqual([])
})

test('phone: every main nav link is inside the viewport at 360 and 390 px', async ({ page }) => {
  const s = await mockSources(page)
  for (const width of [360, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/#/sources')
    const links = page.getByRole('navigation', { name: 'Main' }).getByRole('link')
    await expect(links).toHaveCount(5)
    for (const link of await links.all()) {
      const box = (await link.boundingBox())!
      expect(box.x, `${await link.textContent()} at ${width}`).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width, `${await link.textContent()} at ${width}`).toBeLessThanOrEqual(width)
      expect(box.height).toBeGreaterThanOrEqual(44)
    }
    await noSideways(page)
  }
  expect(s.unmocked).toEqual([])
})
