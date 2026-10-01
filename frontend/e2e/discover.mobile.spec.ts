import { expect, test, type Page } from '@playwright/test'

import { mockDiscover } from './discoverMocks'

// Phone project (390x844, touch): the Discover page. Every /api/discover call is mocked.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

// Visible buttons, selects, text inputs, summaries and checkbox rows are at least 44 px tall.
// (The Toggle's track is 24 px; its hit area is widened by CSS, checked below.)
async function tallTargets(page: Page) {
  const small = await page.locator('.discover-page').evaluate((root) => {
    const sel =
      'button:not(.link):not(.field-help-btn):not(.toggle), select, input:not([type="checkbox"]), textarea, summary, .discover-review label, a.btn'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

test('phone: every section open, no sideways scroll, 44 px targets', async ({ page }) => {
  const s = await mockDiscover(page, { bulk: 'done', nav: 'done' })
  await page.goto('/#/discover')
  await expect(page.getByTestId('catalog-count')).toHaveText('2 of 2 saved titles')
  for (const name of ['Search baihehub', 'Open a site or explain a page', 'Bulk import from listing pages']) {
    await page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${name}$`) }) }).click()
  }
  await expect(page.getByTestId('bulk-review')).toBeVisible()
  await expect(page.getByTestId('nav-result')).toBeVisible()
  await page.getByTestId('catalog-list').getByText('Details').first().click()
  await page.getByText('Fill in page URLs from a pattern').click()
  await page.getByText('Known official platforms').click()
  await page.getByRole('searchbox', { name: 'Title to find' }).fill('长公主')
  await page.getByRole('button', { name: 'Find', exact: true }).click()
  await expect(page.getByTestId('search-links')).toBeVisible()
  await page.getByTestId('catalog-list').getByRole('button', { name: 'Add 女将军和长公主 to Library' }).click()
  await expect(page.getByRole('link', { name: 'In your Library — open' })).toHaveAttribute('href', '#/drama/42')
  await page.getByLabel('Page URL', { exact: true }).fill('https://www.jjwxc.net/a/very/long/path/that/should/not/push/the/page/sideways/at/all')
  await noSideways(page)
  await tallTargets(page)
  const hit = await page.getByRole('switch', { name: 'Translate English to Chinese first' }).evaluate(
    (el) => parseFloat(getComputedStyle(el, '::after').height) || el.getBoundingClientRect().height,
  )
  expect(hit).toBeGreaterThanOrEqual(44)
  await page.screenshot({ path: 'test-results/discover-phone.png', fullPage: true })
  expect(s.unmocked).toEqual([])
})
