import { expect, test } from '@playwright/test'

import { mockDomains } from './sourceDomainsMocks'
import { mockSources } from './sourcesMocks'
import { installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the address editor fits and has 44 px targets.
test('phone: source addresses fit, 44 px targets', async ({ page }) => {
  await mockSources(page)
  await mockDomains(page)
  await page.goto('/#/sources')
  await page.getByText('Source settings', { exact: true }).first().click()
  await page.getByText('Source addresses').first().click()
  const alpha = page.getByRole('group', { name: 'Alpha Comics addresses' })
  await expect(alpha.getByRole('button', { name: 'Confirm alpha-new.example' })).toBeVisible()
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
  const small = await page.locator('.source-domains').evaluateAll((els) =>
    els.flatMap((el) => [...el.querySelectorAll<HTMLElement>('button, input')])
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), t: (e.getAttribute('aria-label') || e.textContent || '').slice(0, 30) }))
      .filter((x) => x.h < 44))
  expect(small).toEqual([])
  await page.screenshot({ path: 'test-results/source-domains-phone.png', fullPage: true })
})
