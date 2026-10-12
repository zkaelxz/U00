import { expect, test, type Page } from '@playwright/test'

import { mockDomains } from './sourceDomainsMocks'
import { installHitArea } from './hitArea'
import { mockSources, openManage } from './sourcesMocks'

// Phone project (390x844, touch): Manage sources. Every call is mocked.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

// Visible buttons, links, selects, number and text inputs, and the labelled On switches are at least 44 px tall.
async function tallTargets(page: Page) {
  const small = await page.locator('.sources-page').evaluate((root) => {
    const sel = 'button:not(.field-help-btn):not(.toggle), a.btn, [role="tab"], select, input[type="number"], input[type="text"], input[type="url"], .source-on'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

test('phone: every tab fits in light and dark and its targets are 44 px', async ({ page }) => {
  const s = await mockSources(page)
  await mockDomains(page)
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await openManage(page)
    for (const name of ['Find', 'New chapters', 'Manage sources']) {
      await page.getByRole('tab', { name }).click()
      await expect(page.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
      await noSideways(page)
      await tallTargets(page)
      await page.screenshot({ path: `test-results/screens/sources-phone-${name.split(' ')[0].toLowerCase()}-${scheme}.png`, fullPage: true })
    }
  }
  expect(s.unmocked).toEqual([])
})

test('phone: the Advanced timing fields fit and the Save reason shows', async ({ page }) => {
  await mockSources(page)
  const manage = await openManage(page, 'timing')
  await manage.locator('summary', { hasText: 'Pacing' }).click()
  await manage.getByText('Advanced timing').click()
  await expect(manage.getByRole('spinbutton', { name: 'Rest length, to (s)' })).toBeVisible()
  await manage.getByRole('spinbutton', { name: 'Retry attempts' }).fill('9')
  await expect(manage.getByText('Fix the values marked in Advanced timing first.')).toBeVisible()
  await noSideways(page)
  await tallTargets(page)
})

test('phone: the Find tab link is 44 px and leads to the Manage sources tab', async ({ page }) => {
  await mockSources(page)
  await page.goto('/#/sources')
  const link = page.getByRole('tabpanel').getByRole('link', { name: 'Manage sources' })
  expect(await link.evaluate((e) => window.hitHeight(e as HTMLElement))).toBeGreaterThanOrEqual(44)
  await noSideways(page)
  await link.click()
  await expect(page.getByRole('tab', { name: 'Manage sources' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.sources-manage-panel')).toBeVisible()
})
